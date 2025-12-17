from flask import Flask
from threading import Thread
import time
import requests

app = Flask(__name__)

@app.route('/')
def home():
    return """
    <!DOCTYPE html>
    <html>
    <head>
        <title>ТРИТИКА Бот</title>
        <meta charset="utf-8">
        <style>
            body { font-family: Arial, sans-serif; margin: 40px; }
            .container { max-width: 800px; margin: 0 auto; }
            .status { color: green; font-weight: bold; }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>🤖 Telegram Bot для поиска тендеров</h1>
            <p class="status">✅ Бот ООО "ТРИТИКА" работает!</p>
            <p>Бот помогает находить тендеры и отправлять выгрузки клиентам.</p>
            <p><a href="/health">Проверить статус</a></p>
            <hr>
            <p><b>Контакты:</b></p>
            <p>Телефон: +7 (904) 653-69-87</p>
            <p>Сайт: <a href="https://tritika.ru">tritika.ru</a></p>
        </div>
    </body>
    </html>
    """

@app.route('/health')
def health():
    return "🟢 Bot is alive and healthy"

def ping_self():
    """Периодический пинг для поддержания активности"""
    while True:
        try:
            requests.get('https://your-replit-url.your-username.repl.co/health')
        except:
            pass
        time.sleep(300)  # Каждые 5 минут

def run():
    """Запуск Flask сервера"""
    app.run(host='0.0.0.0', port=8080)

def keep_alive():
    """Запуск в отдельном потоке"""
    t = Thread(target=run)
    t.daemon = True
    t.start()
    
    # Запускаем пинг в отдельном потоке
    ping_thread = Thread(target=ping_self)
    ping_thread.daemon = True
    ping_thread.start()
