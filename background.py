from flask import Flask
from threading import Thread

app = Flask(__name__)

@app.route('/')
def home():
    return "✅ Telegram Bot ООО 'Тритика' работает! <a href='/health'>Health Check</a>"

@app.route('/health')
def health():
    return "🟢 Bot is alive and healthy"

def run():
    app.run(host='0.0.0.0', port=8080)

def keep_alive():
    """Запускает Flask сервер в отдельном потоке"""
    t = Thread(target=run)
    t.daemon = True
    t.start()
