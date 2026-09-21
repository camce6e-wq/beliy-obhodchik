#!/usr/bin/env python3
"""
Простой HTTP proxy для обхода блокировок Telegram
Запускается локально и используется для подключения к Telegram API
"""

import socket
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

class TelegramProxyHandler(BaseHTTPRequestHandler):
    """Обработчик HTTP запросов с проксированием к Telegram API"""

    def __init__(self, *args, **kwargs):
        self.proxy_port = 8080
        super().__init__(*args, **kwargs)

    def log_message(self, format, *args):
        """Отключаем стандартные логи"""
        pass

    def do_CONNECT(self):
        """Обработка CONNECT-запросов (для HTTPS)"""
        host = self.path
        try:
            # Подключаемся к удаленному серверу
            self.log_message(f"CONNECT to {host}")
            self.send_response(200, 'Connection Established')
            self.end_headers()
        except Exception as e:
            self.send_error(502, f"Failed to connect to {host}: {e}")

    def do_request(self):
        """Обработка GET/POST/PUT/DELETE запросов"""
        method = self.command
        path = self.path
        headers = self.headers
        body = self.rfile.read(int(headers.get('Content-Length', 0))) if headers.get('Content-Length') else b''

        # Заменяем хост на Telegram API
        if 'api.telegram.org' in headers.get('Host', ''):
            target_host = 'api.telegram.org'
            target_port = 443
        elif 'files.telegram.org' in headers.get('Host', ''):
            target_host = 'files.telegram.org'
            target_port = 443
        else:
            # Для других запросов оставляем как есть
            target_host = headers.get('Host', self.server.server_address[0])
            target_port = 80

        try:
            # Создаем TCP соединение
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(10)

            if target_port == 443:
                # Для HTTPS используем Python SSL
                import ssl
                sock = ssl.wrap_socket(sock, cert_reqs=ssl.CERT_NONE)
                sock.connect((target_host, target_port))
            else:
                sock.connect((target_host, target_port))

            # Отправляем запрос к целевому серверу
            request = f"{method} {path} HTTP/1.1\r\n"
            for k, v in headers.items():
                request += f"{k}: {v}\r\n"
            request += f"Host: {target_host}\r\n"
            request += f"Connection: close\r\n"
            if body:
                request += f"Content-Length: {len(body)}\r\n\r\n"
            else:
                request += "\r\n"

            if body:
                request += body

            sock.sendall(request.encode())

            # Получаем ответ
            response = b''
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                response += chunk

            sock.close()

            # Отправляем ответ клиенту
            self.wfile.write(response)

        except Exception as e:
            self.send_error(502, f"Proxy error: {e}")

    def do_GET(self):
        self.do_request()

    def do_POST(self):
        self.do_request()

    def do_PUT(self):
        self.do_request()

    def do_DELETE(self):
        self.do_request()


def start_proxy():
    """Запускает HTTP proxy на порту 8080"""
    print("🚀 Запускаю HTTP proxy на порту 8080...")
    print("📍 Telegram будет подключаться через: http://127.0.0.1:8080")
    print("⏸️  Нажмите Ctrl+C для остановки\n")

    server = HTTPServer(('127.0.0.1', 8080), TelegramProxyHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n🛑 Остановил proxy")
    finally:
        server.shutdown()


if __name__ == "__main__":
    start_proxy()
