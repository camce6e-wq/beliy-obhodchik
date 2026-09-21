import sys, os, types, tempfile

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

os.chdir(r"C:\Users\BacuJlu4\Documents\GitHub\beliy-obhodchik")

import bot_runner  # импортирует модуль без запуска поллинга

# Заглушки
class Chat:
    id = 123456789

class StubUser:
    id = 123456789
    username = "test_user"

class StubMessage:
    def __init__(self, text):
        self.chat = Chat()
        self.from_user = StubUser()
        self.text = text
        self.id = 1

replies = []
documents = []
bot_runner.bot.reply_to = lambda m, txt, **kw: replies.append((m.text, txt))
bot_runner.bot.send_document = lambda chat_id, f, **kw: documents.append(kw.get('caption', ''))

print("== /start ==")
m = StubMessage('/start')
bot_runner.send_welcome(m)
for _, txt in replies:
    print(txt.strip()[:400])
replies.clear()

print("\n== /status ==")
try:
    m2 = StubMessage('/status')
    bot_runner.system_status(m2)
    for _, txt in replies:
        print(txt.strip()[:400])
    replies.clear()
except Exception as e:
    print("status ERR:", repr(e))

print("\n== /test ==")
try:
    m3 = StubMessage('/test')
    bot_runner.test_generation(m3)
    print("replies:", [t[:200] for _, t in replies])
    print("docs sent:", len(documents))
    if documents:
        print("doc caption:", documents[0].strip()[:400])
except Exception as e:
    import traceback
    traceback.print_exc()

print("\n== логика quick_generate (независимо) ==")
from keenetic_config_generator import quick_generate
tf, params = quick_generate(server_ip="93.184.216.34", sni_hostname="api.notion.com")
print("param keys:", list(params.keys()))
print("file:", tf, os.path.getsize(tf), "bytes")
os.remove(tf)

print("\n== SNIDatabase.get_stats ==")
try:
    from sni_manager import SNIDatabase
    db = SNIDatabase()
    print(db.get_stats())
except Exception as e:
    print("stats ERR:", repr(e))

print("\nOK-DONE")