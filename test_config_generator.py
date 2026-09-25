#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Офлайн-проверка сборки архива конфигураций Keenetic:
   1. Архив создаётся и содержит все 6 файлов
   2. README.txt не содержит плейсхолдеров и ведёт на реальный бот поддержки
   3. VLESS URL корректен и валиден для подключения"""
import os
import sys
import zipfile

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

from keenetic_config_generator import KeeneticConfigGenerator

SRV = "95.217.1.1"
UUID = "11111111-2222-3333-4444-555555555555"
PUB = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789XX"
SID = "1a2b3c4d"
SNI = "api.notion.com"

g = KeeneticConfigGenerator(
    server_ip=SRV, server_port=443,
    uuid=UUID, public_key=PUB, short_id=SID, sni_hostname=SNI,
)

z = g.create_complete_package()
try:
    zf = zipfile.ZipFile(z)
    names = sorted(zf.namelist())
    expected = {'README.txt', 'xray_client.json', 'singbox_client.json',
                'hydraroute_rules.txt', 'keenetic_cli.txt', 'vless_url.txt'}
    assert set(names) == expected, f"набор файлов в архиве: {names}"
    print("OK 1/3: архив содержит все 6 файлов")

    readme = zf.read("README.txt").decode("utf-8")
    for p in ("your-domain", "@your_support_bot", "ваш_token", "ваш_токен"):
        assert p not in readme, f"в README.txt плейсхолдер: {p}"
    assert "@beliy_obhodchik_support_bot" in readme, "в README.txt нет реального бота поддержки"
    assert "FAQ: /faq в боте @beliy_obhodchik_bot" in readme
    print("OK 2/3: README.txt без плейсхолдеров, со ссылками на реальные боты")

    vless = zf.read("vless_url.txt").decode("utf-8").strip()
    assert vless.startswith("vless://"), f"vless_url начинается неправильно: {vless[:30]}"
    assert UUID in vless and SRV in vless and "security=reality" in vless, vless[:120]
    print("OK 3/3: vless_url валиден (uuid, сервер, reality)")
finally:
    try:
        zf.close()
    except Exception:
        pass
    if os.path.exists(z):
        os.unlink(z)

print("\nВСЕ ТЕСТЫ ПРОЙДЕНЫ")