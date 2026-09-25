#!/usr/bin/env python3
"""
Генератор конфигураций для Keenetic роутеров
Автоматическое создание готовых файлов для загрузки в роутер
"""

import json
import base64
import zipfile
import tempfile
import os
from typing import Dict, List
from datetime import datetime

class KeeneticConfigGenerator:
    """Генератор конфигураций для Keenetic роутеров"""
    
    def __init__(self, server_ip: str, server_port: int, uuid: str, 
                 sni_hostname: str, public_key: str, short_id: str):
        """
        :param server_ip: IP адрес VPS сервера
        :param server_port: Порт (обычно 443)
        :param uuid: UUID пользователя
        :param sni_hostname: SNI донор
        :param public_key: Публичный ключ Reality
        :param short_id: Short ID для Reality
        """
        self.server_ip = server_ip
        self.server_port = server_port
        self.uuid = uuid
        self.sni_hostname = sni_hostname
        self.public_key = public_key
        self.short_id = short_id
    
    def generate_xray_config(self) -> Dict:
        """Генерация конфигурации Xray для Keenetic"""
        
        config = {
            "log": {
                "loglevel": "warning"
            },
            "dns": {
                "servers": [
                    "1.1.1.1",
                    "1.0.0.1",
                    {
                        "address": "8.8.8.8",
                        "port": 53,
                        "domains": [
                            "geosite:geolocation-!cn"
                        ]
                    }
                ]
            },
            "inbounds": [
                {
                    "port": 10808,
                    "protocol": "socks",
                    "sniffing": {
                        "enabled": True,
                        "destOverride": ["http", "tls"]
                    },
                    "settings": {
                        "auth": "noauth",
                        "udp": True
                    }
                },
                {
                    "port": 10809,
                    "protocol": "http",
                    "settings": {
                        "timeout": 0
                    }
                }
            ],
            "outbounds": [
                {
                    "protocol": "vless",
                    "settings": {
                        "vnext": [
                            {
                                "address": self.server_ip,
                                "port": self.server_port,
                                "users": [
                                    {
                                        "id": self.uuid,
                                        "encryption": "none",
                                        "flow": "xtls-rprx-vision"
                                    }
                                ]
                            }
                        ]
                    },
                    "streamSettings": {
                        "network": "tcp",
                        "security": "reality",
                        "realitySettings": {
                            "serverName": self.sni_hostname,
                            "fingerprint": "chrome",
                            "publicKey": self.public_key,
                            "shortId": self.short_id
                        }
                    },
                    "tag": "proxy"
                },
                {
                    "protocol": "freedom",
                    "tag": "direct"
                },
                {
                    "protocol": "blackhole",
                    "tag": "block"
                }
            ],
            "routing": {
                "domainStrategy": "IPIfNonMatch",
                "rules": [
                    {
                        "type": "field",
                        "outboundTag": "proxy",
                        "domain": [
                            "geosite:category-all"
                        ]
                    },
                    {
                        "type": "field",
                        "outboundTag": "direct",
                        "ip": [
                            "geoip:private"
                        ]
                    }
                ]
            }
        }
        
        return config
    
    def generate_singbox_config(self) -> Dict:
        """Генерация конфигурации Sing-box для Keenetic"""
        
        config = {
            "log": {
                "level": "warn"
            },
            "dns": {
                "servers": [
                    {
                        "tag": "google",
                        "address": "8.8.8.8",
                        "detour": "direct"
                    },
                    {
                        "tag": "local",
                        "address": "223.5.5.5",
                        "detour": "direct"
                    }
                ],
                "rules": [
                    {
                        "outbound": "any",
                        "server": "local"
                    }
                ]
            },
            "inbounds": [
                {
                    "type": "tun",
                    "tag": "tun-in",
                    "inet4_address": "172.19.0.1/30",
                    "auto_route": True,
                    "strict_route": True,
                    "stack": "system",
                    "sniff": True
                }
            ],
            "outbounds": [
                {
                    "type": "vless",
                    "tag": "proxy",
                    "server": self.server_ip,
                    "server_port": self.server_port,
                    "uuid": self.uuid,
                    "flow": "xtls-rprx-vision",
                    "tls": {
                        "enabled": True,
                        "server_name": self.sni_hostname,
                        "utls": {
                            "enabled": True,
                            "fingerprint": "chrome"
                        },
                        "reality": {
                            "enabled": True,
                            "public_key": self.public_key,
                            "short_id": self.short_id
                        }
                    }
                },
                {
                    "type": "direct",
                    "tag": "direct"
                },
                {
                    "type": "block",
                    "tag": "block"
                },
                {
                    "type": "dns",
                    "tag": "dns-out"
                }
            ],
            "route": {
                "rules": [
                    {
                        "protocol": "dns",
                        "outbound": "dns-out"
                    },
                    {
                        "geoip": ["private"],
                        "outbound": "direct"
                    },
                    {
                        "geosite": ["category-ads-all"],
                        "outbound": "block"
                    }
                ],
                "final": "proxy",
                "auto_detect_interface": True
            }
        }
        
        return config
    
    def generate_hydraroute_rules(self, blocked_domains: List[str] = None) -> str:
        """Генерация правил HydraRoute для раздельной маршрутизации"""
        
        # Стандартный список доменов для проксирования
        if not blocked_domains:
            blocked_domains = [
                # Социальные сети
                "instagram.com",
                "facebook.com", 
                "twitter.com",
                "tiktok.com",
                
                # Мессенджеры
                "telegram.org",
                "t.me",
                "discord.com",
                "discordapp.com",
                
                # Медиа
                "youtube.com",
                "youtu.be",
                "ytimg.com",
                "yt3.ggpht.com",
                
                # Новости
                "bbc.com",
                "cnn.com",
                "dw.com",
                "rferl.org",
                
                # Другое
                "linkedin.com",
                "reddit.com",
                "medium.com",
                "github.com",
                "gitlab.com"
            ]
        
        # Формируем правила в формате HydraRoute
        rules = f"""# HydraRoute правила для Keenetic
# Сгенерировано: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
# SNI донор: {self.sni_hostname}
# 
# Инструкция:
# 1. Загрузите этот файл в HydraRoute
# 2. Выберите VPN-туннель для этих доменов
# 3. Остальной трафик пойдёт напрямую

# === Социальные сети ===
"""
        
        for domain in blocked_domains:
            rules += f"{domain}\n"
        
        rules += """
# === Конец списка ===
# Добавьте свои домены по необходимости
"""
        
        return rules
    
    def generate_keenetic_cli_config(self) -> str:
        """Генерация команд для настройки через CLI Keenetic"""
        
        cli_config = f"""! Автоматическая конфигурация Keenetic
! Сгенерировано: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
! SNI донор: {self.sni_hostname}

! Настройка Xray/Sing-box клиента
! Выполните через CLI (SSH или Telnet):

! 1. Создаём профиль Xray
xray profile add name "VLESS-Reality"

! 2. Настраиваем профиль
xray profile VLESS-Reality
 protocol vless
 server {self.server_ip}
 port {self.server_port}
 uuid {self.uuid}
 flow xtls-rprx-vision
 sni {self.sni_hostname}
 fingerprint chrome
 public-key {self.public_key}
 short-id {self.short_id}
 exit

! 3. Создаём интерфейс
interface Xray0
 description "VLESS Reality VPN"
 security-level private
 ip address 192.168.200.1 255.255.255.252
 up

! 4. Настраиваем маршрут
ip route default {self.server_ip} Xray0

! 5. Сохраняем конфигурацию
system configuration save

! Готово! Проверьте подключение командой:
! show interface Xray0

! Для HydraRoute настройки используйте отдельный файл правил
"""
        
        return cli_config
    
    def generate_vless_url(self) -> str:
        """Генерация VLESS URL для импорта в клиенты"""
        
        # Формат: vless://uuid@server:port?параметры#название
        
        params = f"type=tcp&security=reality&pbk={self.public_key}&fp=chrome&sni={self.sni_hostname}&sid={self.short_id}&flow=xtls-rprx-vision"
        
        url = f"vless://{self.uuid}@{self.server_ip}:{self.server_port}?{params}#VLESS-Reality-{self.sni_hostname}"
        
        return url
    
    def create_complete_package(self, output_path: str = None) -> str:
        """Создание полного пакета конфигураций для пользователя"""
        
        # Безопасный временный файл (mkstemp вместо небезопасного mktemp)
        fd, output_path = tempfile.mkstemp(suffix='.zip', prefix='config_')
        os.close(fd)
        
        with zipfile.ZipFile(output_path, 'w') as zipf:
            # 1. Конфиг Xray
            xray_config = self.generate_xray_config()
            zipf.writestr("xray_client.json", json.dumps(xray_config, indent=2))
            
            # 2. Конфиг Sing-box
            singbox_config = self.generate_singbox_config()
            zipf.writestr("singbox_client.json", json.dumps(singbox_config, indent=2))
            
            # 3. Правила HydraRoute
            hydra_rules = self.generate_hydraroute_rules()
            zipf.writestr("hydraroute_rules.txt", hydra_rules)
            
            # 4. CLI команды
            cli_config = self.generate_keenetic_cli_config()
            zipf.writestr("keenetic_cli.txt", cli_config)
            
            # 5. VLESS URL
            vless_url = self.generate_vless_url()
            zipf.writestr("vless_url.txt", vless_url)
            
            # 6. Полная инструкция
            instruction = self._generate_full_instruction()
            zipf.writestr("README.txt", instruction)
        
        return output_path
    
    def _generate_full_instruction(self) -> str:
        """Генерация полной инструкции для пользователя"""
        
        instruction = f"""
╔════════════════════════════════════════════════════════════╗
║     АВТОМАТИЧЕСКАЯ НАСТРОЙКА KEENETIC                     ║
║     VLESS Reality + HydraRoute                            ║
╚════════════════════════════════════════════════════════════╝

ДАННЫЕ ДЛЯ ПОДКЛЮЧЕНИЯ:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• Сервер: {self.server_ip}:{self.server_port}
• UUID: {self.uuid}
• SNI (маскировка): {self.sni_hostname}
• Public Key: {self.public_key}
• Short ID: {self.short_id}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

СОДЕРЖИМОЕ АРХИВА:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
1. xray_client.json      - Конфигурация для Xray
2. singbox_client.json   - Конфигурация для Sing-box
3. hydraroute_rules.txt  - Правила HydraRoute
4. keenetic_cli.txt      - Команды для CLI
5. vless_url.txt         - URL для импорта
6. README.txt            - Эта инструкция
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

ВАРИАНТЫ УСТАНОВКИ:

═══════════════════════════════════════════════════════════
ВАРИАНТ 1: Через Xray (рекомендуется)
═══════════════════════════════════════════════════════════

1. Установите Xray на Keenetic:
   • Веб-интерфейс → Приложения → Xray
   • Или через OPKG: opkg install xray

2. Загрузите конфигурацию:
   • xray_client.json → загрузите в роутер
   • Или скопируйте содержимое в веб-интерфейс

3. Включите подключение в интерфейсе

4. Настройте HydraRoute (см. ниже)

═══════════════════════════════════════════════════════════
ВАРИАНТ 2: Через Sing-box
═══════════════════════════════════════════════════════════

1. Установите Sing-box на Keenetic
2. Загрузите singbox_client.json
3. Включите подключение
4. Настройте маршрутизацию

═══════════════════════════════════════════════════════════
ВАРИАНТ 3: Через CLI (для продвинутых)
═══════════════════════════════════════════════════════════

1. Подключитесь к роутеру по SSH или Telnet
2. Выполните команды из файла keenetic_cli.txt
3. Сохраните конфигурацию

═══════════════════════════════════════════════════════════
НАСТРОЙКА HYDRAROUTE
═══════════════════════════════════════════════════════════

HydraRoute позволяет пускать через VPN только заблокированные сайты,
а остальной трафик отправлять напрямую.

УСТАНОВКА:
1. Установите HydraRoute на Keenetic
2. Загрузите файл hydraroute_rules.txt
3. Выберите VPN-туннель (VLESS Reality)
4. Примените правила

ПРИНЦИП РАБОТЫ:
• DNS запросы перехватываются
• Если домен в списке → IP добавляется в ipset
• Трафик на эти IP идёт через VPN
• Остальной трафик идёт напрямую

ВАЖНО: 
• Устройства должны быть в политике "по умолчанию"
• В политике HydraRoute только VPN-подключения

═══════════════════════════════════════════════════════════
ПРОВЕРКА РАБОТЫ
═══════════════════════════════════════════════════════════

1. Проверьте статус Xray/Sing-box
2. Откройте заблокированный сайт (например, youtube.com)
3. Проверьте IP на сайте 2ip.ru:
   • Должен быть IP вашего VPS
4. Проверьте скорость: speedtest.net

═══════════════════════════════════════════════════════════
АВТОМАТИЧЕСКИЕ ОБНОВЛЕНИЯ
═══════════════════════════════════════════════════════════

Система автоматически обновит SNI-донора при необходимости.

ПРИНЦИП:
• Мониторинг доступности донора
• Автоматическая смена при блокировке
• Обновление конфигурации в роутере

Для проверки обновлений используйте скрипт update_check.sh

═══════════════════════════════════════════════════════════
РЕШЕНИЕ ПРОБЛЕМ
═══════════════════════════════════════════════════════════

ПРОБЛЕМА: Сайты не открываются
РЕШЕНИЕ: 
• Проверьте статус Xray/Sing-box
• Проверьте правильность UUID и ключей
• Проверьте доступность сервера: ping {self.server_ip}

ПРОБЛЕМА: Медленная скорость
РЕШЕНИЕ:
• Выберите VPS ближе к вам географически
• Проверьте загрузку сервера
• Попробуйте другой SNI-донор

ПРОБЛЕМА: Блокировка оператором
РЕШЕНИЕ:
• Система автоматически сменит донора
• Или запросите обновление вручную

═══════════════════════════════════════════════════════════
ПОДДЕРЖКА
═══════════════════════════════════════════════════════════

Telegram: @beliy_obhodchik_support_bot
FAQ: /faq в боте @beliy_obhodchik_bot

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Сгенерировано автоматически: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
        
        return instruction


def quick_generate(server_ip: str, sni_hostname: str, sni_ip: str = None):
    """Быстрая генерация конфигурации с автоматическими параметрами.
    Публичный ключ — настоящий x25519 (32 байта в base64), чтобы Reality
    на сервере и в клиенте совпадали после обмена ключами."""
    
    import uuid
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
    
    # Параметры Reality: приватный ключ остаётся на сервере (setup_vps.sh),
    # клиенту отдаём его публичную часть.
    private_key = X25519PrivateKey.generate()
    public_bytes = private_key.public_key().public_bytes_raw()
    public_key = base64.b64encode(public_bytes).decode('ascii')
    short_id = uuid.uuid4().hex[:8]
    
    # Создаём генератор
    generator = KeeneticConfigGenerator(
        server_ip=server_ip,
        server_port=443,
        uuid=str(uuid.uuid4()),
        sni_hostname=sni_hostname,
        public_key=public_key,
        short_id=short_id
    )
    
    # Генерируем пакет
    archive_path = generator.create_complete_package()
    
    return archive_path, {
        'server_ip': server_ip,
        'uuid': generator.uuid,
        'sni_hostname': sni_hostname,
        'public_key': public_key,
        'short_id': short_id
    }


def main():
    """Точка входа для тестирования"""
    
    import argparse
    
    parser = argparse.ArgumentParser(description='Генератор конфигураций для Keenetic')
    parser.add_argument('--server-ip', required=True, help='IP адрес сервера')
    parser.add_argument('--sni-hostname', required=True, help='SNI донор')
    parser.add_argument('--uuid', help='UUID (если не указан, генерируется)')
    parser.add_argument('--public-key', help='Публичный ключ')
    parser.add_argument('--short-id', help='Short ID')
    parser.add_argument('--output', help='Путь к выходному файлу')
    
    args = parser.parse_args()
    
    # Если не указаны параметры - генерируем автоматически
    if not args.uuid or not args.public_key or not args.short_id:
        archive_path, params = quick_generate(args.server_ip, args.sni_hostname)
        
        print("✓ Конфигурация сгенерирована автоматически")
        print(f"UUID: {params['uuid']}")
        print(f"Public Key: {params['public_key']}")
        print(f"Short ID: {params['short_id']}")
        print(f"Архив: {archive_path}")
        
    else:
        # Используем указанные параметры
        generator = KeeneticConfigGenerator(
            server_ip=args.server_ip,
            server_port=443,
            uuid=args.uuid,
            sni_hostname=args.sni_hostname,
            public_key=args.public_key,
            short_id=args.short_id
        )
        
        archive_path = generator.create_complete_package(args.output)
        
        print(f"✓ Конфигурация создана: {archive_path}")


if __name__ == "__main__":
    main()