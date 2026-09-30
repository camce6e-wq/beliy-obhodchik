#!/usr/bin/env python3
"""Паритет JS-генератора (js/config_generator.js, используется сайтом) с
Python-генератором бота (keenetic_config_generator.py):
1. node собрал ZIP, zipfile его читает — архив валиден, 6 файлов, CRC сходятся
2. содержимое совпадает с Python-генератором по тем же параметрам
3. расхождение допустимо только в метке времени (генерируется в момент сборки)"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from keenetic_config_generator import KeeneticConfigGenerator

SRV = "95.217.1.1"
UUID = "11111111-2222-3333-4444-555555555555"
PUB = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789XX"
SID = "1a2b3c4d"
SNI = "api.notion.com"

EXPECTED = {
    "README.txt",
    "xray_client.json",
    "singbox_client.json",
    "hydraroute_rules.txt",
    "keenetic_cli.txt",
    "vless_url.txt",
}

TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}")

node = shutil.which("node")
if node is None:
    print("SKIP: node не найден — паритет JS-генератора не проверен")
    sys.exit(0)

ROOT = os.path.dirname(os.path.abspath(__file__))
JS = os.path.join(ROOT, "js", "config_generator.js")
if not os.path.exists(JS):
    sys.exit(f"нет файла {JS}")

params = {
    "server_ip": SRV,
    "uuid": UUID,
    "public_key": PUB,
    "short_id": SID,
    "sni_hostname": SNI,
}

fd, js_zip = tempfile.mkstemp(suffix=".zip", prefix="js_config_")
os.close(fd)
proc = subprocess.run(
    [node, JS, "--zip", json.dumps(params), js_zip],
    capture_output=True,
    text=True,
)
if proc.returncode != 0:
    sys.exit(f"node упал:\n{proc.stdout}\n{proc.stderr}")

generator = KeeneticConfigGenerator(
    server_ip=SRV,
    server_port=443,
    uuid=UUID,
    public_key=PUB,
    short_id=SID,
    sni_hostname=SNI,
)
py_zip = generator.create_complete_package()

try:
    with zipfile.ZipFile(py_zip) as zp, zipfile.ZipFile(js_zip) as zj:
        py_names = set(zp.namelist())
        js_names = set(zj.namelist())
        assert py_names == EXPECTED, f"набор файлов в Python-архиве: {sorted(py_names)}"
        assert js_names == EXPECTED, f"набор файлов в JS-архиве: {sorted(js_names)}"
        print("OK 1/3: оба архива содержат одни и те же 6 файлов")

        damaged = zj.testzip()
        assert damaged is None, f"повреждённая запись в JS-архиве: {damaged}"
        print("OK 2/3: JS-архив читается, CRC всех записей сходятся")

        for name in sorted(EXPECTED):
            expected = TIMESTAMP.sub("<TS>", zp.read(name).decode("utf-8"))
            actual = TIMESTAMP.sub("<TS>", zj.read(name).decode("utf-8"))
            assert actual == expected, (
                f"расхождение в {name}:\n"
                f"--- python ---\n{expected[:600]}\n"
                f"--- js ---\n{actual[:600]}"
            )
        print("OK 3/3: содержимое совпадает с ботом (кроме метки времени)")
finally:
    for path in (py_zip, js_zip):
        if os.path.exists(path):
            os.unlink(path)

print("\nВСЕ ТЕСТЫ ПРОЙДЕНЫ")
