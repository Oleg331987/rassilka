from flask import Flask
from threading import Thread
import time

app = Flask('')

@app.route('/')
def home():
    return "✅ Telegram Bot для поиска тендеров работает! <a href='/health'>Health Check</a>"

@app.route('/health')
def health():
    return "🟢 Bot is alive and healthy"

def run():
    # Важно использовать порт 8080, так как Replit по умолчанию использует его
    app.run(host='0.0.0.0', port=8080)

def keep_alive():
    """Запускает Flask-сервер в отдельном потоке"""
    t = Thread(target=run)
    t.start()
