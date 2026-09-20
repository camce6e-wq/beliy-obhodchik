#!/usr/bin/env python3
"""
Менеджер базы SNI-доноров для VLESS Reality
Автоматическое сканирование и обновление базы
"""

import sqlite3
import json
import logging
import time
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple
import random
import requests
import socket
import ssl
import ipaddress

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class SNIDatabase:
    """Класс для работы с базой SNI-доноров"""
    
    def __init__(self, db_path: str = "sni_database.db"):
        self.db_path = db_path
        self.init_database()
    
    def init_database(self):
        """Инициализация базы данных"""
        with sqlite3.connect(self.db_path) as conn:
            with open('database_schema.sql', 'r', encoding='utf-8') as f:
                schema = f.read()
            conn.executescript(schema)
            logger.info(f"База данных инициализирована: {self.db_path}")
    
    def add_donor(self, hostname: str, ip_address: str, 
                  country_code: str = None, tls_version: str = None,
                  http_version: str = None, certificate_issuer: str = None,
                  has_cdn: bool = False, tags: List[str] = None):
        """Добавление нового SNI-донора"""
        
        tags_str = ','.join(tags) if tags else ''
        
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO sni_donors 
                (hostname, ip_address, country_code, tls_version, http_version,
                 certificate_issuer, has_cdn, tags, last_checked)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """, (hostname, ip_address, country_code, tls_version, 
                  http_version, certificate_issuer, has_cdn, tags_str))
            
            donor_id = cursor.lastrowid
            logger.info(f"Добавлен донор: {hostname} ({ip_address})")
            return donor_id
    
    def get_best_donor(self, exclude_ids: List[int] = None) -> Optional[Dict]:
        """Получение лучшего доступного донора"""
        
        exclude_clause = ""
        params = []
        
        if exclude_ids:
            placeholders = ','.join(['?'] * len(exclude_ids))
            exclude_clause = f"AND id NOT IN ({placeholders})"
            params.extend(exclude_ids)
        
        with sqlite3.connect(self.db_path) as conn:
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
        
        with sqlite3.connect(self.db_path) as conn:
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
        
        with sqlite3.connect(self.db_path) as conn:
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
        
        with sqlite3.connect(self.db_path) as conn:
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
            
            # Пробуем подключиться
            with socket.create_connection((ip_address, port), timeout=5) as sock:
                with context.wrap_socket(sock, server_hostname=hostname) as ssock:
                    end_time = time.time()
                    result['response_time_ms'] = int((end_time - start_time) * 1000)
                    
                    # Получаем информацию о TLS
                    result['tls_version'] = ssock.version()
                    
                    # Проверяем сертификат (упрощённо)
                    cert = ssock.getpeercert()
                    if cert:
                        result['certificate_valid'] = True
                        
                        # Проверяем издателя
                        issuer = dict(x[0] for x in cert.get('issuer', []))
                        result['certificate_issuer'] = issuer.get('organizationName', 'Unknown')
                    
                    result['is_accessible'] = True
                    
                    # Проверяем HTTP/2 (упрощённо - по ALPN)
                    if hasattr(ssock, 'selected_alpn_protocol'):
                        result['http_version'] = ssock.selected_alpn_protocol()
                    
                    logger.info(f"✓ Донор доступен: {hostname} ({ip_address}) - {result['response_time_ms']}ms")
                    
        except (socket.timeout, ConnectionRefusedError, ssl.SSLError, 
                socket.gaierror, OSError) as e:
            result['error'] = str(e)
            logger.warning(f"✗ Донор недоступен: {hostname} ({ip_address}) - {e}")
        
        return result
    
    def scan_ip_range(self, start_ip: str, end_ip: str, known_hostnames: List[str] = None):
        """Сканирование диапазона IP (упрощённо)"""
        
        logger.info(f"Начинаю сканирование диапазона: {start_ip} - {end_ip}")
        
        # Преобразуем IP в числа для итерации
        start = int(ipaddress.IPv4Address(start_ip))
        end = int(ipaddress.IPv4Address(end_ip))
        
        # Ограничиваем диапазон для демонстрации
        sample_size = min(50, end - start + 1)
        sample_ips = random.sample(range(start, end + 1), sample_size)
        
        found_donors = []
        
        for ip_num in sample_ips:
            ip_address = str(ipaddress.IPv4Address(ip_num))
            
            # Пробуем стандартные хосты
            test_hostnames = known_hostnames or ['api.notion.com', 'api.github.com', 'slack.com']
            
            for hostname in test_hostnames:
                try:
                    result = self.check_donor(hostname, ip_address)
                    
                    if result['is_accessible'] and result['certificate_valid']:
                        # Определяем страну (упрощённо)
                        country_code = self._guess_country_by_ip(ip_address)
                        
                        # Проверяем CDN (упрощённо - по заголовкам)
                        has_cdn = self._check_cdn(ip_address)
                        
                        donor_info = {
                            'hostname': hostname,
                            'ip_address': ip_address,
                            'country_code': country_code,
                            'tls_version': result['tls_version'],
                            'http_version': result['http_version'],
                            'certificate_issuer': result.get('certificate_issuer', 'Unknown'),
                            'has_cdn': has_cdn,
                            'response_time_ms': result['response_time_ms']
                        }
                        
                        found_donors.append(donor_info)
                        logger.info(f"Найден донор: {hostname} на {ip_address} ({country_code})")
                        
                        # Добавляем в базу
                        self.db.add_donor(
                            hostname=hostname,
                            ip_address=ip_address,
                            country_code=country_code,
                            tls_version=result['tls_version'],
                            http_version=result['http_version'],
                            certificate_issuer=result.get('certificate_issuer'),
                            has_cdn=has_cdn,
                            tags=['scanned', 'auto']
                        )
                        
                        break  # Переходим к следующему IP
                        
                except Exception as e:
                    logger.error(f"Ошибка при проверке {hostname} на {ip_address}: {e}")
        
        logger.info(f"Сканирование завершено. Найдено доноров: {len(found_donors)}")
        return found_donors
    
    def _guess_country_by_ip(self, ip_address: str) -> str:
        """Определение страны по IP (упрощённо)"""
        # В реальной системе нужно использовать IP-to-ASN базу
        # Для демонстрации возвращаем случайные страны
        countries = ['US', 'DE', 'NL', 'FR', 'GB', 'CA', 'SG']
        return random.choice(countries)
    
    def _check_cdn(self, ip_address: str) -> bool:
        """Проверка на наличие CDN (упрощённо)"""
        # В реальной системе нужно проверять AS номер и заголовки
        # Для демонстрации возвращаем случайное значение
        return random.random() < 0.2  # 20% шанс что это CDN


class Scheduler:
    """Планировщик для автоматического сканирования и проверок"""
    
    def __init__(self, db: SNIDatabase, scanner: SNIScanner):
        self.db = db
        self.scanner = scanner
        self.running = False
    
    def run_continuous(self, check_interval: int = 3600, scan_interval: int = 86400):
        """Непрерывный запуск планировщика"""
        
        self.running = True
        last_scan = datetime.now() - timedelta(days=1)
        
        logger.info(f"Планировщик запущен. Проверка каждые {check_interval//60} мин, сканирование каждые {scan_interval//3600} часов")
        
        while self.running:
            try:
                now = datetime.now()
                
                # 1. Проверяем существующих доноров
                donors_to_check = self.db.get_donors_for_check(limit=20)
                
                for donor in donors_to_check:
                    logger.info(f"Проверяю донора: {donor['hostname']}")
                    result = self.scanner.check_donor(donor['hostname'], donor['ip_address'])
                    
                    self.db.update_success_rate(
                        donor['id'],
                        result['is_accessible'],
                        result['response_time_ms']
                    )
                
                # 2. Периодическое сканирование новых доноров
                if (now - last_scan).total_seconds() > scan_interval:
                    logger.info("Запускаю сканирование новых доноров...")
                    
                    # Примерные диапазоны популярных хостингов
                    ip_ranges = [
                        ("143.204.0.0", "143.204.255.255"),  # CloudFront
                        ("140.82.0.0", "140.82.255.255"),    # GitHub
                        ("52.85.0.0", "52.85.255.255"),      # AWS
                    ]
                    
                    for start_ip, end_ip in ip_ranges:
                        self.scanner.scan_ip_range(start_ip, end_ip)
                    
                    last_scan = now
                
                # 3. Выводим статистику
                if len(donors_to_check) > 0:
                    stats = self.db.get_stats()
                    logger.info(f"Статистика: {stats['active']}/{stats['total']} активных, средний успех: {stats['avg_success_rate']:.2%}")
                
                # Ждём до следующей проверки
                time.sleep(check_interval)
                
            except KeyboardInterrupt:
                logger.info("Получен сигнал остановки...")
                self.running = False
            except Exception as e:
                logger.error(f"Ошибка в планировщике: {e}")
                time.sleep(60)  # Ждём минуту при ошибке
    
    def stop(self):
        """Остановка планировщика"""
        self.running = False


# Утилиты для работы через API
def get_donor_api():
    """API endpoint для получения донора"""
    db = SNIDatabase()
    donor = db.get_best_donor()
    
    if donor:
        return {
            'success': True,
            'donor': {
                'hostname': donor['hostname'],
                'ip_address': donor['ip_address'],
                'country': donor['country_code'],
                'success_rate': donor['success_rate']
            }
        }
    else:
        return {'success': False, 'error': 'Нет доступных доноров'}


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
    print("\nЗапускаю тестовое сканирование...")
    scanner = SNIScanner(db)
    
    # Тестируем один донор
    test_result = scanner.check_donor("api.github.com", "140.82.121.3")
    print(f"\nТест донора: {test_result['hostname']}")
    print(f"Доступен: {test_result['is_accessible']}")
    print(f"TLS: {test_result['tls_version']}, HTTP: {test_result['http_version']}")
    
    # Показываем как получить донора через API
    print("\n=== Пример использования ===")
    api_result = get_donor_api()
    if api_result['success']:
        donor = api_result['donor']
        print(f"API вернул донора: {donor['hostname']} ({donor['ip_address']})")
    else:
        print("API: Нет доступных доноров")
    
    print("\n=== Готово ===")


if __name__ == "__main__":
    main()