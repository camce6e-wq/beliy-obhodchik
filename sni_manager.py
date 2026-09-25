#!/usr/bin/env python3
"""
Менеджер базы SNI-доноров для VLESS Reality
Автоматическое сканирование и обновление базы
"""

import sqlite3
import logging
import time
import os
from typing import List, Dict, Optional
import socket
import ssl

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Известные CDN-суффиксы/подстроки для честной (детерминированной) пометки доноров.
CDN_MARKERS = (
    "cloudfront", "cloudflare", "fastly", "cdn", "akamai", "edgekey",
    "amazonaws", "googleusercontent", "azureedge", "netlify",
)

# Хостом, чей TLS-сертификат выдан именно на них, можно маскировать трафик.
# Для Reality подходят обычные сайты с валидным сертификатом на 443.

class SNIDatabase:
    """Класс для работы с базой SNI-доноров"""
    
    def __init__(self, db_path: str = None):
        self.db_path = db_path or os.environ.get('SNI_DB_PATH') or os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "sni_database.db")
        self.init_database()
    
    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.execute("PRAGMA busy_timeout=5000")
        return conn
    
    def init_database(self):
        """Инициализация базы данных"""
        schema_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'database_schema.sql')
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            with open(schema_path, 'r', encoding='utf-8') as f:
                schema = f.read()
            conn.executescript(schema)
            logger.info(f"База данных инициализирована: {self.db_path}")
    
    def add_donor(self, hostname: str, ip_address: str, 
                  country_code: str = None, tls_version: str = None,
                  http_version: str = None, certificate_issuer: str = None,
                  has_cdn: bool = None, tags: List[str] = None):
        """Добавление нового SNI-донора.
        При повторном добавлении того же hostname обновляем только сведения о TLS/CDN
        и НЕ сбрасываем накопленную статистику (success_rate/проверки)."""
        
        tags_str = ','.join(tags) if tags else ''
        
        # Честно определяем CDN по имени хоста, если не сказано явно
        if has_cdn is None:
            has_cdn = any(m in hostname.lower() for m in CDN_MARKERS)
        
        with self._connect() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO sni_donors 
                (hostname, ip_address, country_code, tls_version, http_version,
                 certificate_issuer, has_cdn, tags, last_checked)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(hostname) DO UPDATE SET
                    ip_address = excluded.ip_address,
                    country_code = excluded.country_code,
                    tls_version = excluded.tls_version,
                    http_version = excluded.http_version,
                    certificate_issuer = excluded.certificate_issuer,
                    has_cdn = excluded.has_cdn,
                    tags = excluded.tags,
                    last_checked = CURRENT_TIMESTAMP
            """, (hostname, ip_address, country_code, tls_version, 
                  http_version, certificate_issuer, has_cdn, tags_str))
            
            donor_id = cursor.lastrowid
            if cursor.rowcount == 1:
                logger.info(f"Добавлен донор: {hostname} ({ip_address})")
            else:
                # ON CONFLICT UPDATE вернул не 1 — донор был, обновили метаданные
                logger.info(f"Обновлён донор: {hostname} ({ip_address})")
            return donor_id
    
    def get_best_donor(self, exclude_ids: List[int] = None) -> Optional[Dict]:
        """Получение лучшего доступного донора"""
        
        exclude_clause = ""
        params = []
        
        if exclude_ids:
            placeholders = ','.join(['?'] * len(exclude_ids))
            exclude_clause = f"AND id NOT IN ({placeholders})"
            params.extend(exclude_ids)
        
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            cursor.execute(f"""
                SELECT * FROM sni_donors 
                WHERE is_active = TRUE 
                    AND has_cdn = FALSE
                    AND success_rate > 0.7
                    {exclude_clause}
                ORDER BY success_rate DESC, last_checked DESC
                LIMIT 1
            """, params)
            
            row = cursor.fetchone()
            return dict(row) if row else None
    
    def get_random_donor(self) -> Optional[Dict]:
        """Получение случайного донора (для распределения нагрузки)"""
        
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT * FROM sni_donors 
                WHERE is_active = TRUE 
                    AND has_cdn = FALSE
                    AND success_rate > 0.6
                ORDER BY RANDOM()
                LIMIT 1
            """)
            
            row = cursor.fetchone()
            return dict(row) if row else None
    
    def update_success_rate(self, donor_id: int, is_success: bool, response_time_ms: int = None):
        """Обновление статистики донора"""
        
        with self._connect() as conn:
            cursor = conn.cursor()
            
            # Получаем текущие значения
            cursor.execute("""
                SELECT success_rate, total_checks, failure_count 
                FROM sni_donors WHERE id = ?
            """, (donor_id,))
            
            result = cursor.fetchone()
            if not result:
                return
            
            old_rate, total_checks, failure_count = result
            
            # Обновляем статистику
            total_checks += 1
            if not is_success:
                failure_count += 1
            
            # Рассчитываем новый success_rate
            success_rate = (total_checks - failure_count) / total_checks if total_checks > 0 else 0
            
            # Обновляем запись
            cursor.execute("""
                UPDATE sni_donors 
                SET success_rate = ?, 
                    total_checks = ?, 
                    failure_count = ?,
                    last_checked = CURRENT_TIMESTAMP,
                    is_active = CASE WHEN failure_count > 10 THEN FALSE ELSE TRUE END
                WHERE id = ?
            """, (success_rate, total_checks, failure_count, donor_id))
            
            # Записываем в историю
            cursor.execute("""
                INSERT INTO check_history 
                (donor_id, is_success, response_time_ms, check_time)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            """, (donor_id, is_success, response_time_ms))
            
            logger.debug(f"Обновлена статистика донора {donor_id}: success_rate={success_rate:.2f}")
    
    def get_donors_for_check(self, limit: int = 10) -> List[Dict]:
        """Получение доноров для проверки (тех, что давно не проверялись)"""
        
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT * FROM sni_donors 
                WHERE next_check <= datetime('now')
                    AND is_active = TRUE
                ORDER BY next_check ASC
                LIMIT ?
            """, (limit,))
            
            return [dict(row) for row in cursor.fetchall()]
    
    def search_donors(self, 
                     min_success_rate: float = 0.7,
                     exclude_cdn: bool = True,
                     country: str = None,
                     tags: List[str] = None) -> List[Dict]:
        """Поиск доноров по критериям"""
        
        query = """
            SELECT * FROM sni_donors 
            WHERE is_active = TRUE 
                AND success_rate >= ?
        """
        params = [min_success_rate]
        
        if exclude_cdn:
            query += " AND has_cdn = FALSE"
        
        if country:
            query += " AND country_code = ?"
            params.append(country)
        
        if tags:
            tag_conditions = []
            for tag in tags:
                tag_conditions.append("tags LIKE ?")
                params.append(f'%{tag}%')
            query += " AND (" + " OR ".join(tag_conditions) + ")"
        
        query += " ORDER BY success_rate DESC, last_checked DESC"
        
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(query, params)
            
            return [dict(row) for row in cursor.fetchall()]
    
    def get_stats(self) -> Dict:
        """Получение статистики по базе"""
        
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            stats = {}
            
            # Общая статистика
            cursor.execute("SELECT COUNT(*) FROM sni_donors")
            stats['total'] = cursor.fetchone()[0]
            
            cursor.execute("SELECT COUNT(*) FROM sni_donors WHERE is_active = TRUE")
            stats['active'] = cursor.fetchone()[0]
            
            cursor.execute("SELECT AVG(success_rate) FROM sni_donors WHERE is_active = TRUE")
            stats['avg_success_rate'] = cursor.fetchone()[0] or 0
            
            # Доноры по странам
            cursor.execute("""
                SELECT country_code, COUNT(*) as count 
                FROM sni_donors 
                WHERE is_active = TRUE
                GROUP BY country_code 
                ORDER BY count DESC
            """)
            stats['by_country'] = dict(cursor.fetchall())
            
            # Доноры по тегам
            cursor.execute("""
                SELECT DISTINCT tags FROM sni_donors WHERE tags IS NOT NULL
            """)
            all_tags = []
            for row in cursor.fetchall():
                if row[0]:
                    all_tags.extend(row[0].split(','))
            
            from collections import Counter
            stats['by_tags'] = dict(Counter(all_tags))
            
            return stats


class SNIScanner:
    """Сканер SNI-доноров (упрощённая версия RealiTLScanner)"""
    
    def __init__(self, db: SNIDatabase):
        self.db = db
        self.results_cache = {}
    
    def check_donor(self, hostname: str, ip_address: str, port: int = 443) -> Dict:
        """Проверка одного донора"""
        
        result = {
            'hostname': hostname,
            'ip_address': ip_address,
            'is_accessible': False,
            'tls_version': None,
            'http_version': None,
            'certificate_valid': False,
            'has_cdn': False,
            'response_time_ms': None,
            'error': None
        }
        
        start_time = time.time()
        
        try:
            # Создаём SSL контекст
            context = ssl.create_default_context()
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE  # Упрощённая проверка
            try:
                context.set_alpn_protocols(['h2', 'http/1.1'])
            except (NotImplementedError, OSError):
                pass
            
            # Пробуем подключиться
            with socket.create_connection((ip_address, port), timeout=5) as sock:
                with context.wrap_socket(sock, server_hostname=hostname) as ssock:
                    end_time = time.time()
                    result['response_time_ms'] = int((end_time - start_time) * 1000)
                    
                    # TLS-версия установлена — сервер реально отвечает по TLS на 443
                    tls_version = ssock.version()
                    result['tls_version'] = tls_version
                    # Валидность: TLS-рукопожатие завершилось. Имя издателя извлекаем по-возможности.
                    result['certificate_valid'] = bool(tls_version)
                    cert = ssock.getpeercert()
                    if cert:
                        issuer = dict(x[0] for x in cert.get('issuer', []))
                        result['certificate_issuer'] = issuer.get('organizationName', 'Unknown')
                    
                    result['is_accessible'] = True
                    
                    # HTTP/2 (по ALPN)
                    if hasattr(ssock, 'selected_alpn_protocol'):
                        result['http_version'] = ssock.selected_alpn_protocol()
                    
                    logger.info(f"✓ Донор доступен: {hostname} ({ip_address}) - {result['response_time_ms']}ms")
                    
        except (socket.timeout, ConnectionRefusedError, ssl.SSLError, 
                socket.gaierror, OSError) as e:
            result['error'] = str(e)
            logger.warning(f"✗ Донор недоступен: {hostname} ({ip_address}) - {e}")
        
        return result


def main():
    """Основная функция для тестирования"""
    
    print("=== SNI Database Manager ===\n")
    
    # Инициализируем базу
    db = SNIDatabase()
    
    # Получаем статистику
    stats = db.get_stats()
    print(f"База загружена. Доноров: {stats['total']}, активных: {stats['active']}")
    
    # Получаем лучшего донора
    best_donor = db.get_best_donor()
    if best_donor:
        print(f"\nЛучший донор: {best_donor['hostname']} ({best_donor['ip_address']})")
        print(f"Успешность: {best_donor['success_rate']:.2%}, Страна: {best_donor['country_code']}")
    
    # Поиск по критериям
    print("\nПоиск стабильных доноров (успешность > 80%):")
    stable_donors = db.search_donors(min_success_rate=0.8)
    
    for i, donor in enumerate(stable_donors[:5], 1):
        print(f"{i}. {donor['hostname']}: {donor['success_rate']:.2%} ({donor['country_code']})")
    
    # Запускаем тестовое сканирование
    print("\nПроверка одного донора...")
    scanner = SNIScanner(db)
    
    # Тестируем один донор
    test_result = scanner.check_donor("api.notion.com", "143.204.68.34")
    print(f"\nТест донора: {test_result['hostname']}")
    print(f"Доступен: {test_result['is_accessible']}")
    print(f"TLS: {test_result['tls_version']}, HTTP: {test_result['http_version']}")
    
    print("\n=== Готово ===")


if __name__ == "__main__":
    main()