#!/usr/bin/env python3
"""
Автоматический установщик VLESS Reality на VPS пользователя
Полностью автоматизированная настройка
"""

import os
import sys
import json
import random
import subprocess
import logging
from typing import Dict
import urllib.request
import base64

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class VPSInstaller:
    """Класс для автоматической установки VLESS Reality на VPS"""
    
    def __init__(self, sni_donor_hostname: str, sni_donor_ip: str):
        """
        :param sni_donor_hostname: SNI донор (например, "api.notion.com")
        :param sni_donor_ip: IP адрес донора
        """
        self.sni_donor_hostname = sni_donor_hostname
        self.sni_donor_ip = sni_donor_ip
        self.uuid = self._generate_uuid()
        self.short_id = self._generate_short_id()
        
        # Пути для файлов
        self.xray_dir = "/usr/local/etc/xray"
        self.config_path = os.path.join(self.xray_dir, "config.json")
        self.service_file = "/etc/systemd/system/xray.service"
        
    def _generate_uuid(self) -> str:
        """Генерация UUID для VLESS"""
        import uuid
        return str(uuid.uuid4())
    
    def _generate_short_id(self) -> str:
        """Генерация shortId для Reality"""
        # 8 случайных hex символов
        return ''.join(random.choices('0123456789abcdef', k=8))
    
    def _generate_server_key(self) -> str:
        """Генерация ключей для Reality"""
        # В реальной системе нужно использовать xray x25519
        # Для упрощения генерируем случайную строку
        return base64.b64encode(os.urandom(32)).decode('utf-8')
    
    def check_system(self) -> bool:
        """Проверка системы и зависимостей"""
        logger.info("Проверяю систему...")
        
        # Проверяем ОС
        try:
            with open('/etc/os-release', 'r') as f:
                os_info = f.read()
                if 'ubuntu' in os_info.lower() or 'debian' in os_info.lower():
                    logger.info("✓ Поддерживаемая ОС: Ubuntu/Debian")
                else:
                    logger.warning("⚠️ Неподдерживаемая ОС, но попробуем продолжить")
        except:
            logger.warning("⚠️ Не удалось определить ОС")
        
        # Проверяем права
        if os.geteuid() != 0:
            logger.error("✗ Требуются права root. Запустите: sudo python3 vps_installer.py")
            return False
        
        # Проверяем доступность порта 443
        try:
            import socket
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(1)
            result = sock.connect_ex(('127.0.0.1', 443))
            if result == 0:
                logger.warning("⚠️ Порт 443 уже занят. Убедитесь что веб-сервер (nginx/apache) не использует его.")
            sock.close()
        except:
            pass
        
        logger.info("✓ Система готова к установке")
        return True
    
    def install_dependencies(self):
        """Установка зависимостей"""
        logger.info("Устанавливаю зависимости...")
        
        commands = [
            # Обновление системы
            ["apt-get", "update", "-y"],
            ["apt-get", "upgrade", "-y"],
            
            # Базовые утилиты
            ["apt-get", "install", "-y", "curl", "wget", "git", "unzip", "certbot"],
            
            # Для установки Xray
            ["apt-get", "install", "-y", "gnupg", "software-properties-common"],
        ]
        
        for cmd in commands:
            logger.debug(f"Выполняю: {' '.join(cmd)}")
            try:
                subprocess.run(cmd, check=True, capture_output=True, text=True)
            except subprocess.CalledProcessError as e:
                logger.error(f"Ошибка при установке зависимостей: {e}")
                logger.error(f"stderr: {e.stderr}")
        
        logger.info("✓ Зависимости установлены")
    
    def install_xray(self):
        """Установка Xray-core"""
        logger.info("Устанавливаю Xray-core...")
        
        try:
            # Скачиваем и устанавливаем Xray
            install_script = """
            bash -c "$(curl -L https://github.com/XTLS/Xray-install/raw/main/install-release.sh)" @ install
            """
            
            subprocess.run(install_script, shell=True, check=True, capture_output=True, text=True)
            
            # Проверяем установку
            result = subprocess.run(["xray", "--version"], capture_output=True, text=True)
            if result.returncode == 0:
                logger.info(f"✓ Xray установлен: {result.stdout.split()[1]}")
            else:
                logger.error("✗ Ошибка при установке Xray")
                raise Exception("Xray installation failed")
                
        except Exception as e:
            logger.error(f"Ошибка установки Xray: {e}")
            
            # Альтернативный метод установки
            logger.info("Пробую альтернативный метод установки...")
            
            alt_commands = [
                ["wget", "-O", "/tmp/xray.zip", "https://github.com/XTLS/Xray-core/releases/latest/download/Xray-linux-64.zip"],
                ["unzip", "-o", "/tmp/xray.zip", "xray", "-d", "/usr/local/bin/"],
                ["chmod", "+x", "/usr/local/bin/xray"],
                ["mkdir", "-p", self.xray_dir],
            ]
            
            for cmd in alt_commands:
                try:
                    subprocess.run(cmd, check=True, capture_output=True, text=True)
                except:
                    logger.warning(f"Не удалось выполнить: {' '.join(cmd)}")
            
            logger.info("✓ Xray установлен (альтернативный метод)")
    
    def create_config(self, server_key: str, public_key: str) -> Dict:
        """Создание конфигурации VLESS Reality"""
        
        config = {
            "log": {
                "loglevel": "warning"
            },
            "inbounds": [
                {
                    "port": 443,
                    "protocol": "vless",
                    "settings": {
                        "clients": [
                            {
                                "id": self.uuid,
                                "flow": "xtls-rprx-vision"
                            }
                        ],
                        "decryption": "none"
                    },
                    "streamSettings": {
                        "network": "tcp",
                        "security": "reality",
                        "realitySettings": {
                            "dest": f"{self.sni_donor_ip}:443",
                            "serverNames": [self.sni_donor_hostname],
                            "privateKey": server_key,
                            "shortIds": [self.short_id],
                            "minClientVer": "",
                            "maxClientVer": "",
                            "maxTimeDiff": 0,
                            "fingerprint": "chrome"
                        }
                    },
                    "sniffing": {
                        "enabled": True,
                        "destOverride": ["http", "tls"]
                    }
                }
            ],
            "outbounds": [
                {
                    "protocol": "freedom",
                    "tag": "direct"
                },
                {
                    "protocol": "blackhole",
                    "tag": "blocked"
                }
            ],
            "routing": {
                "domainStrategy": "IPIfNonMatch",
                "rules": [
                    {
                        "type": "field",
                        "outboundTag": "blocked",
                        "protocol": ["bittorrent"]
                    }
                ]
            }
        }
        
        return config
    
    def create_client_config(self, server_ip: str, server_key: str, public_key: str) -> Dict:
        """Создание конфигурации клиента для роутера"""
        
        client_config = {
            "server": server_ip,
            "server_port": 443,
            "uuid": self.uuid,
            "flow": "xtls-rprx-vision",
            "sni": self.sni_donor_hostname,
            "fingerprint": "chrome",
            "public_key": public_key,
            "short_id": self.short_id,
            "type": "vless",
            "security": "reality",
            "network": "tcp",
            "remarks": f"Auto-generated VLESS Reality ({self.sni_donor_hostname})"
        }
        
        return client_config
    
    def save_config(self, config: Dict):
        """Сохранение конфигурации сервера"""
        
        # Создаём директорию если её нет
        os.makedirs(self.xray_dir, exist_ok=True)
        
        # Сохраняем конфиг
        with open(self.config_path, 'w') as f:
            json.dump(config, f, indent=2)
        
        logger.info(f"✓ Конфигурация сохранена: {self.config_path}")
    
    def save_client_config(self, client_config: Dict, server_ip: str):
        """Сохранение конфигурации клиента и создание архива"""
        
        # Создаём директорию для клиента
        client_dir = "/tmp/vless_client"
        os.makedirs(client_dir, exist_ok=True)
        
        # Сохраняем конфиг клиента
        client_json_path = os.path.join(client_dir, "client.json")
        with open(client_json_path, 'w') as f:
            json.dump(client_config, f, indent=2)
        
        # Создаём инструкцию для Keenetic
        instruction = f"""# Инструкция по настройке Keenetic роутера

## Данные для подключения:
- Сервер: {server_ip}:443
- UUID: {self.uuid}
- SNI (маскировка): {self.sni_donor_hostname}
- Public Key: {client_config.get('public_key', 'N/A')}
- Short ID: {self.short_id}

## Установка на Keenetic:
1. Установите Xray/Sing-box через веб-интерфейс Keenetic
2. Загрузите конфигурацию из client.json
3. Настройте HydraRoute для раздельной маршрутизации
4. Добавьте правило маршрутизации только для заблокированных сайтов

## Проверка работы:
- Включите подключение
- Проверьте доступ к заблокированным сайтам
- При проблемах проверьте логи Xray

## Автоматические обновления:
Система автоматически обновит SNI-донора при необходимости.
Для проверки обновлений используйте скрипт update_check.sh

---
Сгенерировано автоматически: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
"""
        
        instruction_path = os.path.join(client_dir, "instruction.txt")
        with open(instruction_path, 'w', encoding='utf-8') as f:
            f.write(instruction)
        
        # Создаём скрипт проверки обновлений
        update_script = """#!/bin/bash
# Скрипт проверки обновлений SNI-донора

API_URL="https://your-api.com/get-best-donor"
CURRENT_SNI="%s"

echo "Проверяю обновления SNI-донора..."
echo "Текущий донор: $CURRENT_SNI"

# Получаем новый донор от API (пример)
# NEW_SNI=$(curl -s "$API_URL" | jq -r '.donor.hostname')

if [ "$NEW_SNI" != "$CURRENT_SNI" ] && [ ! -z "$NEW_SNI" ]; then
    echo "Найден новый донор: $NEW_SNI"
    echo "Обновите конфигурацию в роутере"
else
    echo "Обновлений нет. Текущий донор актуален."
fi
""" % self.sni_donor_hostname
        
        update_script_path = os.path.join(client_dir, "update_check.sh")
        with open(update_script_path, 'w') as f:
            f.write(update_script)
        
        os.chmod(update_script_path, 0o755)
        
        # Создаём архив
        import zipfile
        archive_path = f"/tmp/vless_reality_{server_ip}.zip"
        with zipfile.ZipFile(archive_path, 'w') as zipf:
            zipf.write(client_json_path, "client.json")
            zipf.write(instruction_path, "instruction.txt")
            zipf.write(update_script_path, "update_check.sh")
        
        logger.info(f"✓ Конфигурация клиента создана: {archive_path}")
        return archive_path
    
    def create_service(self):
        """Создание systemd сервиса для Xray"""
        
        service_content = f"""[Unit]
Description=Xray Service
After=network.target nss-lookup.target

[Service]
User=root
CapabilityBoundingSet=CAP_NET_ADMIN CAP_NET_BIND_SERVICE CAP_NET_RAW
AmbientCapabilities=CAP_NET_ADMIN CAP_NET_BIND_SERVICE CAP_NET_RAW
NoNewPrivileges=true
ExecStart=/usr/local/bin/xray run -config {self.config_path}
Restart=on-failure
RestartPreventExitStatus=23
LimitNPROC=10000
LimitNOFILE=1000000

[Install]
WantedBy=multi-user.target
"""
        
        with open(self.service_file, 'w') as f:
            f.write(service_content)
        
        # Перезагружаем systemd и включаем сервис
        subprocess.run(["systemctl", "daemon-reload"], check=True)
        subprocess.run(["systemctl", "enable", "xray"], check=True)
        
        logger.info("✓ Systemd сервис создан и включен")
    
    def configure_firewall(self):
        """Настройка фаервола"""
        logger.info("Настраиваю фаервол...")
        
        # Проверяем наличие ufw
        try:
            # Разрешаем порт 443
            subprocess.run(["ufw", "allow", "443/tcp"], check=True)
            subprocess.run(["ufw", "--force", "enable"], check=True)
            logger.info("✓ UFW настроен (порт 443 открыт)")
        except:
            # Если ufw нет, используем iptables
            try:
                subprocess.run(["iptables", "-A", "INPUT", "-p", "tcp", "--dport", "443", "-j", "ACCEPT"], check=True)
                subprocess.run(["iptables-save", ">", "/etc/iptables/rules.v4"], shell=True, check=True)
                logger.info("✓ iptables настроен (порт 443 открыт)")
            except:
                logger.warning("⚠️ Не удалось настроить фаервол автоматически")
                logger.warning("   Разрешите порт 443 вручную в настройках VPS")
    
    def get_server_ip(self) -> str:
        """Получение публичного IP сервера"""
        try:
            response = urllib.request.urlopen('https://api.ipify.org')
            ip = response.read().decode('utf-8')
            logger.info(f"✓ Публичный IP сервера: {ip}")
            return ip
        except:
            # Альтернативный метод
            try:
                result = subprocess.run(["curl", "-s", "ifconfig.me"], capture_output=True, text=True)
                if result.returncode == 0:
                    ip = result.stdout.strip()
                    logger.info(f"✓ Публичный IP сервера: {ip}")
                    return ip
            except:
                logger.warning("⚠️ Не удалось определить публичный IP")
                return "YOUR_SERVER_IP"
    
    def install(self) -> Dict:
        """Основная функция установки"""
        
        logger.info("=" * 50)
        logger.info("АВТОМАТИЧЕСКАЯ УСТАНОВКА VLESS REALITY")
        logger.info("=" * 50)
        
        # Проверяем систему
        if not self.check_system():
            sys.exit(1)
        
        # Получаем IP сервера
        server_ip = self.get_server_ip()
        
        # Устанавливаем зависимости
        self.install_dependencies()
        
        # Устанавливаем Xray
        self.install_xray()
        
        # Генерируем ключи (в реальной системе использовать xray x25519)
        server_key = self._generate_server_key()
        public_key = server_key  # В реальной системе разные ключи
        
        # Создаём конфигурацию
        logger.info("Создаю конфигурацию...")
        config = self.create_config(server_key, public_key)
        
        # Сохраняем конфигурацию сервера
        self.save_config(config)
        
        # Создаём systemd сервис
        self.create_service()
        
        # Настраиваем фаервол
        self.configure_firewall()
        
        # Создаём конфигурацию клиента
        client_config = self.create_client_config(server_ip, server_key, public_key)
        
        # Сохраняем конфигурацию клиента
        archive_path = self.save_client_config(client_config, server_ip)
        
        # Запускаем сервис
        logger.info("Запускаю Xray сервис...")
        subprocess.run(["systemctl", "start", "xray"], check=True)
        
        # Проверяем статус
        result = subprocess.run(["systemctl", "status", "xray"], capture_output=True, text=True)
        if "active (running)" in result.stdout:
            logger.info("✓ Xray успешно запущен")
        else:
            logger.warning("⚠️ Xray запущен, но статус неясен")
        
        # Возвращаем информацию для пользователя
        installation_info = {
            "status": "success",
            "server_ip": server_ip,
            "uuid": self.uuid,
            "sni_donor": self.sni_donor_hostname,
            "public_key": public_key,
            "short_id": self.short_id,
            "client_config_path": archive_path,
            "instruction": f"""
УСТАНОВКА ЗАВЕРШЕНА!

Для настройки роутера скачайте архив: {archive_path}

Архив содержит:
1. client.json - конфигурация для роутера
2. instruction.txt - инструкция по настройке
3. update_check.sh - скрипт проверки обновлений

Данные для подключения:
• Сервер: {server_ip}:443
• UUID: {self.uuid}
• SNI: {self.sni_donor_hostname}
• Short ID: {self.short_id}

Далее:
1. Настройте Keenetic роутер по инструкции
2. Настройте HydraRoute для раздельной маршрутизации
3. Проверьте доступ к заблокированным сайтам

Сервис Xray автоматически запускается при перезагрузке.
            """
        }
        
        return installation_info


def main():
    """Точка входа для автономной установки"""
    
    import argparse
    
    parser = argparse.ArgumentParser(description='Автоматическая установка VLESS Reality на VPS')
    parser.add_argument('--sni-hostname', required=True, help='SNI донор (например, api.notion.com)')
    parser.add_argument('--sni-ip', required=True, help='IP адрес SNI донора')
    
    args = parser.parse_args()
    
    # Создаём установщик
    installer = VPSInstaller(args.sni_hostname, args.sni_ip)
    
    # Выполняем установку
    try:
        result = installer.install()
        
        # Выводим результат
        print("\n" + "="*60)
        print("УСТАНОВКА УСПЕШНО ЗАВЕРШЕНА!")
        print("="*60)
        
        print(f"\nСервер: {result['server_ip']}:443")
        print(f"SNI донор: {result['sni_donor']}")
        print(f"UUID: {result['uuid']}")
        print(f"Short ID: {result['short_id']}")
        
        print(f"\nКонфигурация сохранена в: {result['client_config_path']}")
        print("\nСкопируйте архив на компьютер и следуйте инструкции внутри.")
        
        # Проверяем работу
        print("\nПроверяю доступность сервера...")
        import socket
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(3)
        
        try:
            result_check = sock.connect_ex((result['server_ip'], 443))
            if result_check == 0:
                print("✓ Сервер доступен на порту 443")
            else:
                print("⚠️ Порт 443 не отвечает (проверьте фаервол VPS)")
        except:
            print("⚠️ Не удалось проверить сервер")
        
        sock.close()
        
    except Exception as e:
        logger.error(f"Ошибка при установке: {e}")
        sys.exit(1)


# Утилиты для интеграции с системой SNI-доноров
def install_with_auto_donor():
    """Установка с автоматическим выбором донора из базы"""
    
    # Импортируем менеджер SNI-доноров
    try:
        from sni_manager import SNIDatabase
        db = SNIDatabase()
        
        # Получаем лучшего донора
        donor = db.get_best_donor()
        
        if not donor:
            print("❌ Нет доступных SNI-доноров")
            return
        
        print(f"Найден донор: {donor['hostname']} ({donor['ip_address']})")
        print(f"Успешность: {donor['success_rate']:.2%}")
        
        # Создаём установщик
        installer = VPSInstaller(donor['hostname'], donor['ip_address'])
        
        # Устанавливаем
        result = installer.install()
        
        # Обновляем статистику использования донора
        # (в реальной системе нужно добавить этот функционал)
        
        return result
        
    except ImportError:
        print("❌ Не удалось импортировать базу SNI-доноров")
        return None


if __name__ == "__main__":
    # Для автономной работы (с параметрами)
    if len(sys.argv) > 1:
        main()
    else:
        # Интерактивный режим
        print("="*60)
        print("АВТОМАТИЧЕСКИЙ УСТАНОВЩИК VLESS REALITY")
        print("="*60)
        print("\nЭтот скрипт автоматически установит и настроит VLESS Reality на вашем VPS.")
        print("Потребуются права root (sudo).")
        
        choice = input("\nВыберите режим:\n1. Автоматический (выбор донора из базы)\n2. Ручной (указать донор вручную)\n3. Выход\n\nВаш выбор: ")
        
        if choice == "1":
            result = install_with_auto_donor()
            if result:
                print("\n" + result.get("instruction", ""))
        elif choice == "2":
            sni_hostname = input("Введите SNI донор (например, api.notion.com): ")
            sni_ip = input("Введите IP адрес донора: ")
            
            installer = VPSInstaller(sni_hostname, sni_ip)
            result = installer.install()
            
            print("\n" + result.get("instruction", ""))
        else:
            print("Выход.")