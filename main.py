#!/usr/bin/env python3
"""
🤖 БОТ "ТРИТИКА" (ТЕНДЕРПОИСК)
Интеллектуальный ассистент для поиска тендеров
"""

import os
import asyncio
import logging
import sqlite3
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
import json
import random

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardRemove
)
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

# Импорты для HTTP сервера Railway
from aiohttp import web

# =========== НАСТРОЙКИ ===========
BOT_TOKEN = os.getenv("BOT_TOKEN", "8120629620:AAH2ZjoCPEoE39KRIrf8x9JYhOpScphnKgo")
ADMIN_ID = int(os.getenv("ADMIN_ID", "6003624437")) if os.getenv("ADMIN_ID") else None
PORT = int(os.getenv("PORT", 8080))

# Настройки времени работы (пн-пт 9:00-17:00)
WORK_START_HOUR = 9
WORK_END_HOUR = 17
WORK_DAYS = [0, 1, 2, 3, 4]  # Пн-Пт

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

print("="*60)
print("🤖 ЗАГРУЗКА БОТА ТРИТИКА (ТЕНДЕРПОИСК)")
print("="*60)

# =========== ИНИЦИАЛИЗАЦИЯ БОТА ===========
try:
    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML)
    )
    storage = MemoryStorage()
    dp = Dispatcher(storage=storage)
    logger.info("✅ Бот инициализирован")
except Exception as e:
    logger.error(f"❌ Ошибка инициализации бота: {e}")
    exit(1)

# =========== БАЗА ДАННЫХ ===========
class Database:
    def __init__(self, db_name="tenders.db"):
        self.db_name = db_name
        self.init_db()
        self.init_mailing_topics()
    
    def init_db(self):
        """Инициализация базы данных"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        
        # Пользователи
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER UNIQUE,
            username TEXT,
            first_name TEXT,
            last_name TEXT,
            phone TEXT,
            email TEXT,
            company TEXT,
            activity TEXT,
            is_active BOOLEAN DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_mailing_date TIMESTAMP
        )
        ''')
        
        # Анкеты (отдельная таблица для истории)
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS questionnaires (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            full_name TEXT,
            company_name TEXT,
            phone TEXT,
            email TEXT,
            activity TEXT,
            region TEXT,
            budget TEXT,
            keywords TEXT,
            status TEXT DEFAULT 'new',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        ''')
        
        # Выгрузки тендеров
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS tender_exports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            questionnaire_id INTEGER,
            user_id INTEGER,
            sent_at TIMESTAMP,
            sent_by TEXT DEFAULT 'bot',
            file_path TEXT,
            status TEXT DEFAULT 'sent',
            admin_notified BOOLEAN DEFAULT 0,
            follow_up_sent BOOLEAN DEFAULT 0,
            follow_up_at TIMESTAMP,
            follow_up_response TEXT
        )
        ''')
        
        # Рассылки
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS mailings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            topic_id INTEGER,
            message_text TEXT,
            sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            opened BOOLEAN DEFAULT 0,
            responded BOOLEAN DEFAULT 0,
            response_text TEXT,
            clicked_link BOOLEAN DEFAULT 0
        )
        ''')
        
        # Темы для рассылок
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS mailing_topics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            message_text TEXT,
            link TEXT,
            question TEXT,
            delay_days INTEGER DEFAULT 3,
            is_active BOOLEAN DEFAULT 1,
            order_num INTEGER
        )
        ''')
        
        # Ответы на рассылки
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS mailing_responses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mailing_id INTEGER,
            user_id INTEGER,
            response_text TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            processed BOOLEAN DEFAULT 0
        )
        ''')
        
        conn.commit()
        conn.close()
        logger.info("✅ База данных инициализирована")
    
    def init_mailing_topics(self):
        """Инициализация тем для рассылок"""
        topics = [
            {
                'title': 'Пропущенные тендеры',
                'message_text': 'Здравствуйте! А вы знаете, что даже опытные специалисты пропускают выгодные тендеры?',
                'link': 'https://tritica.ru/articles/missed-tenders',
                'question': 'Вы сталкивались с такой ситуацией? Поделитесь в ответе — какие сложности испытываете при поиске тендеров?',
                'delay_days': 3,
                'order_num': 1
            },
            {
                'title': 'Эффективные стратегии',
                'message_text': 'Как увеличить шансы на победу в тендере с первого раза?',
                'link': 'https://tritica.ru/articles/winning-strategies',
                'question': 'Какие методы вы уже пробовали?',
                'delay_days': 3,
                'order_num': 2
            },
            {
                'title': 'Новые возможности',
                'message_text': 'Открылись новые площадки для поиска тендеров в вашем регионе',
                'link': 'https://tritica.ru/articles/new-platforms',
                'question': 'На каких площадках вы обычно ищете тендеры?',
                'delay_days': 3,
                'order_num': 3
            }
        ]
        
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        
        for topic in topics:
            cursor.execute('''
                INSERT OR IGNORE INTO mailing_topics 
                (title, message_text, link, question, delay_days, order_num)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (
                topic['title'],
                topic['message_text'],
                topic['link'],
                topic['question'],
                topic['delay_days'],
                topic['order_num']
            ))
        
        conn.commit()
        conn.close()
    
    def add_user(self, user_id: int, username: str, first_name: str, last_name: str = ""):
        """Добавление пользователя"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        
        cursor.execute('''
        INSERT OR IGNORE INTO users (user_id, username, first_name, last_name)
        VALUES (?, ?, ?, ?)
        ''', (user_id, username, first_name, last_name))
        
        conn.commit()
        conn.close()
        return True
    
    def save_questionnaire(self, user_id: int, data: dict):
        """Сохранение анкеты"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        
        cursor.execute('''
        INSERT INTO questionnaires 
        (user_id, full_name, company_name, phone, email, activity, region, budget, keywords)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            user_id,
            data.get('full_name'),
            data.get('company_name'),
            data.get('phone'),
            data.get('email'),
            data.get('activity'),
            data.get('region'),
            data.get('budget'),
            data.get('keywords')
        ))
        
        conn.commit()
        last_id = cursor.lastrowid
        conn.close()
        
        # Обновляем данные пользователя
        self.update_user_info(user_id, data)
        
        return last_id
    
    def update_user_info(self, user_id: int, data: dict):
        """Обновление информации о пользователе"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        
        cursor.execute('''
        UPDATE users 
        SET phone = ?, email = ?, company = ?, activity = ?
        WHERE user_id = ?
        ''', (
            data.get('phone'),
            data.get('email'),
            data.get('company_name'),
            data.get('activity'),
            user_id
        ))
        
        conn.commit()
        conn.close()
    
    def create_tender_export(self, questionnaire_id: int, user_id: int):
        """Создание записи о выгрузке тендеров"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        
        sent_at = datetime.now() if self.is_working_hours() else self.get_next_working_time()
        
        cursor.execute('''
        INSERT INTO tender_exports 
        (questionnaire_id, user_id, sent_at)
        VALUES (?, ?, ?)
        ''', (questionnaire_id, user_id, sent_at))
        
        conn.commit()
        export_id = cursor.lastrowid
        conn.close()
        
        return export_id
    
    def mark_export_completed(self, export_id: int, admin_name: str = "Олег"):
        """Отметка выполнения выгрузки администратором"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        
        cursor.execute('''
        UPDATE tender_exports 
        SET sent_by = ?, status = 'completed', admin_notified = 1
        WHERE id = ?
        ''', (admin_name, export_id))
        
        conn.commit()
        conn.close()
    
    def schedule_follow_up(self, export_id: int):
        """Планирование follow-up сообщения"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        
        follow_up_at = datetime.now() + timedelta(hours=1)
        if not self.is_working_hours():
            follow_up_at = self.get_next_working_time()
        
        cursor.execute('''
        UPDATE tender_exports 
        SET follow_up_at = ?
        WHERE id = ?
        ''', (follow_up_at, export_id))
        
        conn.commit()
        conn.close()
    
    def save_follow_up_response(self, export_id: int, response: str):
        """Сохранение ответа на follow-up"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        
        cursor.execute('''
        UPDATE tender_exports 
        SET follow_up_sent = 1, follow_up_response = ?
        WHERE id = ?
        ''', (response, export_id))
        
        conn.commit()
        conn.close()
    
    def get_pending_follow_ups(self):
        """Получение запланированных follow-up сообщений"""
        conn = sqlite3.connect(self.db_name)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute('''
        SELECT te.*, u.user_id, u.username, u.first_name
        FROM tender_exports te
        JOIN users u ON te.user_id = u.user_id
        WHERE te.status = 'completed' 
        AND te.follow_up_sent = 0
        AND te.follow_up_at <= datetime('now')
        ''')
        
        results = cursor.fetchall()
        conn.close()
        return results
    
    def create_mailing(self, user_id: int, topic_id: int, message_text: str):
        """Создание рассылки"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        
        cursor.execute('''
        INSERT INTO mailings (user_id, topic_id, message_text)
        VALUES (?, ?, ?)
        ''', (user_id, topic_id, message_text))
        
        conn.commit()
        mailing_id = cursor.lastrowid
        conn.close()
        
        return mailing_id
    
    def get_next_mailing_topic(self, user_id: int):
        """Получение следующей темы для рассылки"""
        conn = sqlite3.connect(self.db_name)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # Получаем последнюю рассылку пользователю
        cursor.execute('''
        SELECT topic_id FROM mailings 
        WHERE user_id = ? 
        ORDER BY sent_at DESC 
        LIMIT 1
        ''', (user_id,))
        
        last_topic = cursor.fetchone()
        
        if last_topic:
            # Берем следующую тему по порядку
            cursor.execute('''
            SELECT * FROM mailing_topics 
            WHERE order_num > (SELECT order_num FROM mailing_topics WHERE id = ?)
            AND is_active = 1
            ORDER BY order_num ASC
            LIMIT 1
            ''', (last_topic['topic_id'],))
        else:
            # Первая рассылка - берем первую тему
            cursor.execute('''
            SELECT * FROM mailing_topics 
            WHERE is_active = 1
            ORDER BY order_num ASC
            LIMIT 1
            ''')
        
        topic = cursor.fetchone()
        conn.close()
        
        return topic
    
    def get_users_for_mailing(self, days_since_last: int = 3):
        """Получение пользователей для рассылки"""
        conn = sqlite3.connect(self.db_name)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute('''
        SELECT u.* 
        FROM users u
        WHERE u.is_active = 1
        AND (
            u.last_mailing_date IS NULL 
            OR date(u.last_mailing_date, '+' || ? || ' days') <= date('now')
        )
        ORDER BY u.created_at DESC
        ''', (days_since_last,))
        
        users = cursor.fetchall()
        conn.close()
        
        return users
    
    def update_last_mailing_date(self, user_id: int):
        """Обновление даты последней рассылки"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        
        cursor.execute('''
        UPDATE users 
        SET last_mailing_date = datetime('now')
        WHERE user_id = ?
        ''', (user_id,))
        
        conn.commit()
        conn.close()
    
    def save_mailing_response(self, mailing_id: int, user_id: int, response_text: str):
        """Сохранение ответа на рассылку"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        
        cursor.execute('''
        INSERT INTO mailing_responses (mailing_id, user_id, response_text)
        VALUES (?, ?, ?)
        ''', (mailing_id, user_id, response_text))
        
        cursor.execute('''
        UPDATE mailings 
        SET responded = 1, response_text = ?
        WHERE id = ?
        ''', (response_text, mailing_id))
        
        conn.commit()
        conn.close()
    
    def get_statistics(self, days: int = 14):
        """Получение статистики за указанный период"""
        conn = sqlite3.connect(self.db_name)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        start_date = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
        
        # Новые пользователи
        cursor.execute('''
        SELECT COUNT(*) as count FROM users 
        WHERE date(created_at) >= ?
        ''', (start_date,))
        new_users = cursor.fetchone()['count']
        
        # Выполненные выгрузки
        cursor.execute('''
        SELECT COUNT(*) as count FROM tender_exports 
        WHERE date(sent_at) >= ? AND status = 'completed'
        ''', (start_date,))
        exports_completed = cursor.fetchone()['count']
        
        # Отправленные рассылки
        cursor.execute('''
        SELECT COUNT(*) as count FROM mailings 
        WHERE date(sent_at) >= ?
        ''', (start_date,))
        mailings_sent = cursor.fetchone()['count']
        
        # Реакции на рассылки
        cursor.execute('''
        SELECT 
            COUNT(DISTINCT user_id) as users_responded,
            COUNT(*) as total_responses,
            SUM(CASE WHEN clicked_link = 1 THEN 1 ELSE 0 END) as links_clicked
        FROM mailings 
        WHERE date(sent_at) >= ? AND responded = 1
        ''', (start_date,))
        reactions = cursor.fetchone()
        
        conn.close()
        
        return {
            'new_users': new_users,
            'exports_completed': exports_completed,
            'mailings_sent': mailings_sent,
            'users_responded': reactions['users_responded'],
            'total_responses': reactions['total_responses'],
            'links_clicked': reactions['links_clicked']
        }
    
    def is_working_hours(self):
        """Проверка рабочего времени"""
        now = datetime.now()
        
        if now.weekday() not in WORK_DAYS:
            return False
        
        if now.hour < WORK_START_HOUR or now.hour >= WORK_END_HOUR:
            return False
        
        return True
    
    def get_next_working_time(self):
        """Получение следующего рабочего времени"""
        now = datetime.now()
        
        # Если сейчас рабочее время
        if self.is_working_hours():
            return now
        
        # Вычисляем следующий рабочий день
        days_to_add = 1
        while (now.weekday() + days_to_add) % 7 not in WORK_DAYS:
            days_to_add += 1
        
        next_work_day = now + timedelta(days=days_to_add)
        return next_work_day.replace(hour=WORK_START_HOUR, minute=0, second=0, microsecond=0)

db = Database()

# =========== HTTP ОБРАБОТЧИКИ ДЛЯ RAILWAY ===========
async def health_check(request):
    """Health check endpoint для Railway"""
    return web.Response(text="OK", status=200)

async def status_check(request):
    """Статус бота"""
    try:
        bot_info = await bot.get_me()
        stats = db.get_statistics(7)
        
        return web.json_response({
            "status": "running",
            "bot": f"@{bot_info.username}",
            "name": bot_info.first_name,
            "statistics": stats,
            "timestamp": datetime.now().isoformat()
        })
    except Exception as e:
        return web.json_response({
            "status": "error",
            "error": str(e)
        }, status=500)

# =========== КЛАВИАТУРЫ ===========
def get_main_keyboard():
    """Главная клавиатура"""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📝 Заполнить анкету онлайн")],
            [KeyboardButton(text="📥 Скачать анкету в Word")],
            [KeyboardButton(text="📊 Мои выгрузки")],
            [KeyboardButton(text="📞 Контакты"), KeyboardButton(text="ℹ️ Помощь")]
        ],
        resize_keyboard=True,
        input_field_placeholder="Выберите действие..."
    )

def get_admin_keyboard():
    """Клавиатура администратора"""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📊 Новые анкеты"), KeyboardButton(text="✅ Отметить выгрузку")],
            [KeyboardButton(text="📈 Статистика"), KeyboardButton(text="📤 Запустить рассылку")],
            [KeyboardButton(text="👥 Пользователи"), KeyboardButton(text="⚙️ Настройки")],
            [KeyboardButton(text="👤 Режим пользователя")]
        ],
        resize_keyboard=True
    )

def get_cancel_keyboard():
    """Клавиатура отмены"""
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="❌ Отмена")]],
        resize_keyboard=True
    )

def get_follow_up_keyboard():
    """Клавиатура для follow-up"""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="✅ Да, нашел подходящее")],
            [KeyboardButton(text="❌ Нет, не нашел")],
            [KeyboardButton(text="🤔 Нужна консультация")]
        ],
        resize_keyboard=True
    )

# =========== СОСТОЯНИЯ ДЛЯ АНКЕТЫ ===========
class Questionnaire(StatesGroup):
    waiting_for_name = State()
    waiting_for_company = State()
    waiting_for_phone = State()
    waiting_for_email = State()
    waiting_for_activity = State()
    waiting_for_region = State()
    waiting_for_budget = State()
    waiting_for_keywords = State()

# =========== СИСТЕМА ЗАДАЧ ===========
class TaskScheduler:
    """Планировщик задач для follow-up и рассылок"""
    
    @staticmethod
    async def check_follow_ups():
        """Проверка запланированных follow-up сообщений"""
        while True:
            try:
                pending = db.get_pending_follow_ups()
                
                for follow_up in pending:
                    user_id = follow_up['user_id']
                    
                    keyboard = get_follow_up_keyboard()
                    
                    await bot.send_message(
                        user_id,
                        "📋 Подборка тендеров отправлена. Удалось ли найти что-то подходящее?",
                        reply_markup=keyboard
                    )
                    
                    # Отмечаем как отправленное
                    conn = sqlite3.connect("tenders.db")
                    cursor = conn.cursor()
                    cursor.execute('''
                    UPDATE tender_exports 
                    SET follow_up_sent = 1
                    WHERE id = ?
                    ''', (follow_up['id'],))
                    conn.commit()
                    conn.close()
                    
                    logger.info(f"Отправлен follow-up пользователю {user_id}")
                
                await asyncio.sleep(60)  # Проверка каждую минуту
                
            except Exception as e:
                logger.error(f"Ошибка в планировщике follow-up: {e}")
                await asyncio.sleep(300)  # Пауза 5 минут при ошибке
    
    @staticmethod
    async def send_mailings():
        """Отправка запланированных рассылок"""
        while True:
            try:
                # Получаем пользователей для рассылки (каждые 3 дня)
                users = db.get_users_for_mailing(3)
                
                for user in users:
                    user_id = user['user_id']
                    
                    # Получаем следующую тему
                    topic = db.get_next_mailing_topic(user_id)
                    
                    if topic:
                        # Формируем сообщение
                        message = f"{topic['message_text']}\n\n"
                        
                        if topic['link']:
                            message += f"Читайте в нашем материале: {topic['link']}\n\n"
                        
                        if topic['question']:
                            message += f"{topic['question']}"
                        
                        try:
                            await bot.send_message(user_id, message)
                            
                            # Сохраняем рассылку в БД
                            mailing_id = db.create_mailing(user_id, topic['id'], message)
                            db.update_last_mailing_date(user_id)
                            
                            logger.info(f"Отправлена рассылка {topic['title']} пользователю {user_id}")
                            
                        except Exception as e:
                            logger.error(f"Не удалось отправить рассылку пользователю {user_id}: {e}")
                
                # Рассылки 2 раза в неделю (проверка каждые 3 дня)
                await asyncio.sleep(259200)  # 3 дня в секундах
                
            except Exception as e:
                logger.error(f"Ошибка в планировщике рассылок: {e}")
                await asyncio.sleep(3600)  # Пауза 1 час при ошибке
    
    @staticmethod
    async def generate_reports():
        """Генерация отчетов раз в 2 недели"""
        while True:
            try:
                # Формируем отчет за 14 дней
                stats = db.get_statistics(14)
                
                report_text = f"""
📊 ОТЧЕТ ЗА 2 НЕДЕЛИ

👥 Новые пользователи: {stats['new_users']}
📋 Выполненные выгрузки: {stats['exports_completed']}
📤 Отправленные рассылки: {stats['mailings_sent']}
💬 Реакции на рассылки:
   • Ответивших пользователей: {stats['users_responded']}
   • Всего ответов: {stats['total_responses']}
   • Переходов по ссылкам: {stats['links_clicked']}

📅 Дата отчета: {datetime.now().strftime('%d.%m.%Y %H:%M')}
                """
                
                # Отправляем отчет администратору
                if ADMIN_ID:
                    try:
                        await bot.send_message(ADMIN_ID, report_text)
                        logger.info("Отчет за 2 недели отправлен администратору")
                    except Exception as e:
                        logger.error(f"Не удалось отправить отчет администратору: {e}")
                
                # Ждем 14 дней до следующего отчета
                await asyncio.sleep(1209600)  # 14 дней в секундах
                
            except Exception as e:
                logger.error(f"Ошибка при генерации отчета: {e}")
                await asyncio.sleep(86400)  # Пауза 1 день при ошибке

# =========== ОБРАБОТЧИКИ КОМАНД ===========
@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    """Обработчик команды /start"""
    await state.clear()
    
    user = message.from_user
    user_id = user.id
    
    db.add_user(user_id, user.username or "", user.first_name, user.last_name or "")
    
    is_admin = ADMIN_ID and user_id == ADMIN_ID
    
    if is_admin:
        await message.answer(
            "🛠️ <b>Панель администратора Тритика</b>\n\n"
            "Вы вошли как администратор бота.\n"
            "Используйте кнопки ниже для управления.",
            reply_markup=get_admin_keyboard()
        )
    else:
        await message.answer(
            "👋 <b>Привет! Я интеллектуальный ассистент компании Тритика.</b>\n\n"
            "Помогаю компаниям находить выгодные тендеры. "
            "Хотите бесплатно получить подборку тендеров по вашей сфере? "
            "Вам надо лишь заполнить короткую анкету.",
            reply_markup=get_main_keyboard()
        )
    
    logger.info(f"Пользователь {user_id} нажал /start")

@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    """Обработчик команды /help"""
    await message.answer(
        "🤖 <b>Помощь по боту Тритика:</b>\n\n"
        "<b>Основные команды:</b>\n"
        "/start - Главное меню\n"
        "/help - Эта справка\n"
        "/my_exports - Мои выгрузки\n\n"
        "<b>Основные функции:</b>\n"
        "• Заполнить анкету онлайн\n"
        "• Скачать анкету в Word\n"
        "• Получить бесплатную подборку тендеров\n"
        "• Консультация по участию в тендерах\n\n"
        "<b>Контакты поддержки:</b>\n"
        "📧 support@tritica.ru\n"
        "📱 +7 (XXX) XXX-XX-XX"
    )

@dp.message(Command("my_exports"))
async def cmd_my_exports(message: types.Message):
    """Мои выгрузки"""
    conn = sqlite3.connect("tenders.db")
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    cursor.execute('''
    SELECT te.*, q.company_name, q.activity
    FROM tender_exports te
    JOIN questionnaires q ON te.questionnaire_id = q.id
    WHERE te.user_id = ?
    ORDER BY te.sent_at DESC
    ''', (message.from_user.id,))
    
    exports = cursor.fetchall()
    conn.close()
    
    if not exports:
        await message.answer(
            "📭 У вас пока нет выгрузок тендеров.\n\n"
            "Хотите получить бесплатную подборку? Заполните анкету!",
            reply_markup=get_main_keyboard()
        )
        return
    
    response = f"📋 <b>Ваши выгрузки ({len(exports)}):</b>\n\n"
    
    for i, export in enumerate(exports, 1):
        date_str = export['sent_at'][:10] if export['sent_at'] else "??.??.????"
        status_icon = "✅" if export['status'] == 'completed' else "⏳"
        status_text = "Отправлена" if export['status'] == 'completed' else "В обработке"
        
        response += f"{i}. <b>{export['company_name']}</b>\n"
        response += f"   📅 {date_str} | {status_icon} {status_text}\n"
        response += f"   🎯 {export['activity'][:30]}...\n"
        
        if export['follow_up_response']:
            response += f"   💬 Ответ: {export['follow_up_response'][:20]}...\n"
        
        response += "\n"
    
    await message.answer(response)

@dp.message(Command("admin"))
async def cmd_admin(message: types.Message, state: FSMContext):
    """Вход в админ-панель"""
    user_id = message.from_user.id
    
    if ADMIN_ID and user_id == ADMIN_ID:
        await state.clear()
        await message.answer(
            "🔐 <b>Вы авторизованы как администратор</b>",
            reply_markup=get_admin_keyboard()
        )
    else:
        await message.answer("⛔ У вас нет прав доступа к панели администратора.")

# =========== ОБРАБОТЧИКИ КНОПОК ===========
@dp.message(F.text == "📝 Заполнить анкету онлайн")
async def start_online_questionnaire(message: types.Message, state: FSMContext):
    """Начало заполнения анкеты онлайн"""
    await state.clear()
    
    await message.answer(
        "📝 <b>Начинаем заполнение анкеты!</b>\n\n"
        "Заполнение займет 3-5 минут. Введите ваше <b>ФИО полностью</b>:",
        reply_markup=get_cancel_keyboard()
    )
    await state.set_state(Questionnaire.waiting_for_name)

@dp.message(F.text == "📥 Скачать анкету в Word")
async def download_questionnaire(message: types.Message):
    """Скачать анкету в Word"""
    questionnaire_text = """АНКЕТА ДЛЯ ПОИСКА ТЕНДЕРОВ
Компания: Тритика

1. ФИО полностью: ___________________
2. Название компании: ___________________
3. Телефон для связи: ___________________
4. Email для отправки тендеров: ___________________
5. Сфера деятельности компании: ___________________
6. Ключевые слова для поиска: ___________________
7. Бюджет контрактов: ___________________
8. Регионы работы: ___________________

Заполните и отправьте на: info@tritica.ru
Или перешлите менеджеру в Telegram: @tritica_manager"""
    
    await message.answer(
        "📄 <b>Скачайте анкету для заполнения</b>\n\n"
        "Вы можете заполнить анкету в Word и отправить нам.\n\n"
        "📧 <b>Email для отправки:</b> info@tritica.ru\n"
        "👨‍💼 <b>Менджер в Telegram:</b> @tritica_manager\n\n"
        "Или заполните анкету онлайн через бота (быстрее и удобнее)."
    )
    
    await message.answer(f"<pre>{questionnaire_text}</pre>")

@dp.message(F.text == "📊 Мои выгрузки")
async def my_exports_button(message: types.Message):
    """Мои выгрузки через кнопку"""
    await cmd_my_exports(message)

@dp.message(F.text == "📞 Контакты")
async def show_contacts(message: types.Message):
    """Показать контакты"""
    await message.answer(
        "📞 <b>Контакты компании Тритика</b>\n\n"
        "<b>Для клиентов:</b>\n"
        "• Телефон: +7 (XXX) XXX-XX-XX\n"
        "• Email: clients@tritica.ru\n"
        "• Telegram: @tritica_clients\n\n"
        "<b>Техническая поддержка:</b>\n"
        "• Email: support@tritica.ru\n"
        "• Telegram: @tritica_support\n\n"
        "<b>Время работы:</b>\n"
        "Пн-Пт: 9:00-18:00\n"
        "Сб: 10:00-15:00\n"
        "Вс: выходной"
    )

@dp.message(F.text == "ℹ️ Помощь")
async def show_help(message: types.Message):
    """Показать помощь"""
    await cmd_help(message)

@dp.message(F.text == "❌ Отмена")
async def cancel_action(message: types.Message, state: FSMContext):
    """Отмена действия"""
    await state.clear()
    
    is_admin = ADMIN_ID and message.from_user.id == ADMIN_ID
    if is_admin:
        await message.answer("❌ Действие отменено", reply_markup=get_admin_keyboard())
    else:
        await message.answer(
            "❌ Заполнение анкеты отменено.\n\n"
            "Вы можете начать заполнение заново в любое время.",
            reply_markup=get_main_keyboard()
        )

# =========== АДМИН ПАНЕЛЬ ===========
@dp.message(F.text == "📊 Новые анкеты")
async def show_new_questionnaires(message: types.Message):
    """Показать новые анкеты"""
    if not ADMIN_ID or message.from_user.id != ADMIN_ID:
        await message.answer("⛔ Доступ запрещен")
        return
    
    conn = sqlite3.connect("tenders.db")
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    cursor.execute('''
    SELECT q.*, u.username 
    FROM questionnaires q
    LEFT JOIN users u ON q.user_id = u.user_id
    WHERE q.id NOT IN (SELECT questionnaire_id FROM tender_exports)
    ORDER BY q.created_at DESC
    LIMIT 10
    ''')
    
    questionnaires = cursor.fetchall()
    conn.close()
    
    if not questionnaires:
        await message.answer("📭 Новых анкет нет")
        return
    
    response = f"🆕 <b>Новые анкеты ({len(questionnaires)}):</b>\n\n"
    
    for i, q in enumerate(questionnaires, 1):
        date_str = q['created_at'][:16] if q['created_at'] else "??.?? ??:??"
        response += f"<b>{i}. #{q['id']} - {q['company_name']}</b>\n"
        response += f"👤 {q['full_name']} (@{q['username'] or 'без username'})\n"
        response += f"📞 {q['phone']}\n"
        response += f"📧 {q['email']}\n"
        response += f"🎯 {q['activity'][:30]}...\n"
        response += f"⏰ {date_str}\n\n"
    
    await message.answer(response)

@dp.message(F.text == "✅ Отметить выгрузку")
async def mark_export_completed(message: types.Message):
    """Отметить выгрузку как выполненную"""
    if not ADMIN_ID or message.from_user.id != ADMIN_ID:
        await message.answer("⛔ Доступ запрещен")
        return
    
    await message.answer(
        "Введите ID анкеты, по которой выполнена выгрузка:\n"
        "<i>(ID можно взять из списка новых анкет)</i>"
    )

@dp.message(F.text.regexp(r'^\d+$'))
async def process_export_id(message: types.Message):
    """Обработка ID анкеты для отметки выгрузки"""
    if not ADMIN_ID or message.from_user.id != ADMIN_ID:
        return
    
    questionnaire_id = int(message.text)
    
    # Получаем информацию об анкете
    conn = sqlite3.connect("tenders.db")
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    cursor.execute('''
    SELECT q.*, u.user_id 
    FROM questionnaires q
    JOIN users u ON q.user_id = u.user_id
    WHERE q.id = ?
    ''', (questionnaire_id,))
    
    questionnaire = cursor.fetchone()
    conn.close()
    
    if not questionnaire:
        await message.answer("❌ Анкета с таким ID не найдена")
        return
    
    # Создаем запись о выгрузке
    export_id = db.create_tender_export(questionnaire_id, questionnaire['user_id'])
    db.mark_export_completed(export_id, message.from_user.first_name)
    
    # Планируем follow-up
    db.schedule_follow_up(export_id)
    
    # Отправляем пользователю уведомление
    time_info = ""
    if db.is_working_hours():
        time_info = "⏱️ <b>Сейчас ищу для вас актуальные тендеры. Не пройдет и часа, как я пришлю подборку на почту и (или) в телеграм.</b>"
    else:
        next_time = db.get_next_working_time()
        time_info = f"⏱️ <b>Запрос получен в нерабочее время. Вышлю с 9:00 до 17:00 {next_time.strftime('%d.%m.%Y')}.</b>"
    
    try:
        await bot.send_message(
            questionnaire['user_id'],
            f"🎉 <b>Ваша анкета #{questionnaire_id} принята в обработку!</b>\n\n"
            f"{time_info}"
        )
    except Exception as e:
        logger.error(f"Не удалось отправить уведомление пользователю: {e}")
    
    await message.answer(
        f"✅ Выгрузка по анкете #{questionnaire_id} отмечена как выполненная\n\n"
        f"👤 Пользователь: {questionnaire['full_name']}\n"
        f"🏢 Компания: {questionnaire['company_name']}\n"
        f"📧 Email: {questionnaire['email']}\n\n"
        f"Follow-up запланирован через 1 час"
    )

@dp.message(F.text == "📈 Статистика")
async def show_statistics(message: types.Message):
    """Показать статистику"""
    if not ADMIN_ID or message.from_user.id != ADMIN_ID:
        await message.answer("⛔ Доступ запрещен")
        return
    
    stats = db.get_statistics(14)
    
    response = f"""
📊 <b>Статистика за 2 недели</b>

👥 <b>Пользователи:</b>
• Новых пользователей: {stats['new_users']}

📋 <b>Выгрузки:</b>
• Выполненных выгрузок: {stats['exports_completed']}

📤 <b>Рассылки:</b>
• Отправлено рассылок: {stats['mailings_sent']}
• Ответивших пользователей: {stats['users_responded']}
• Всего ответов: {stats['total_responses']}
• Переходов по ссылкам: {stats['links_clicked']}

📅 <b>Дата отчета:</b>
{datetime.now().strftime('%d.%m.%Y %H:%M')}
"""
    
    await message.answer(response)

@dp.message(F.text == "📤 Запустить рассылку")
async def trigger_mailing(message: types.Message):
    """Запуск рассылки вручную"""
    if not ADMIN_ID or message.from_user.id != ADMIN_ID:
        await message.answer("⛔ Доступ запрещен")
        return
    
    # Запускаем рассылку в фоне
    asyncio.create_task(send_mailings_now())
    
    await message.answer("🔄 Запущена рассылка пользователям...")

async def send_mailings_now():
    """Немедленная отправка рассылок"""
    try:
        users = db.get_users_for_mailing(0)  # Все пользователи
        
        for user in users:
            user_id = user['user_id']
            topic = db.get_next_mailing_topic(user_id)
            
            if topic:
                message = f"{topic['message_text']}\n\n"
                
                if topic['link']:
                    message += f"Читайте в нашем материале: {topic['link']}\n\n"
                
                if topic['question']:
                    message += f"{topic['question']}"
                
                try:
                    await bot.send_message(user_id, message)
                    db.create_mailing(user_id, topic['id'], message)
                    db.update_last_mailing_date(user_id)
                    await asyncio.sleep(0.1)  # Небольшая пауза
                except Exception as e:
                    logger.error(f"Ошибка рассылки пользователю {user_id}: {e}")
    
    except Exception as e:
        logger.error(f"Ошибка массовой рассылки: {e}")

@dp.message(F.text == "👥 Пользователи")
async def show_all_users(message: types.Message):
    """Показать всех пользователей"""
    if not ADMIN_ID or message.from_user.id != ADMIN_ID:
        await message.answer("⛔ Доступ запрещен")
        return
    
    conn = sqlite3.connect("tenders.db")
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    cursor.execute('''
    SELECT u.*, 
           COUNT(DISTINCT q.id) as questionnaire_count,
           COUNT(DISTINCT te.id) as export_count,
           COUNT(DISTINCT m.id) as mailing_count
    FROM users u
    LEFT JOIN questionnaires q ON u.user_id = q.user_id
    LEFT JOIN tender_exports te ON q.id = te.questionnaire_id
    LEFT JOIN mailings m ON u.user_id = m.user_id
    GROUP BY u.user_id
    ORDER BY u.created_at DESC
    LIMIT 20
    ''')
    
    users = cursor.fetchall()
    conn.close()
    
    if not users:
        await message.answer("👥 Пользователей нет")
        return
    
    response = "👥 <b>Последние пользователи (20):</b>\n\n"
    
    for i, user in enumerate(users, 1):
        date_str = user['created_at'][:10] if user['created_at'] else "??.??.????"
        response += f"{i}. <b>@{user['username'] or 'без username'}</b>\n"
        response += f"   🆔 ID: {user['user_id']}\n"
        response += f"   👤 {user['first_name']} {user['last_name'] or ''}\n"
        response += f"   📋 Анкет: {user['questionnaire_count']}\n"
        response += f"   📤 Выгрузок: {user['export_count']}\n"
        response += f"   📧 Рассылок: {user['mailing_count']}\n"
        response += f"   📅 Регистрация: {date_str}\n\n"
    
    await message.answer(response)

@dp.message(F.text == "⚙️ Настройки")
async def show_settings(message: types.Message):
    """Показать настройки"""
    if not ADMIN_ID or message.from_user.id != ADMIN_ID:
        await message.answer("⛔ Доступ запрещен")
        return
    
    await message.answer(
        "⚙️ <b>Настройки бота:</b>\n\n"
        "<b>Текущие параметры:</b>\n"
        f"• Время работы: {WORK_START_HOUR}:00-{WORK_END_HOUR}:00 Пн-Пт\n"
        f"• Follow-up через: 1 час\n"
        f"• Рассылки каждые: 3 дня\n"
        f"• Отчеты каждые: 14 дней\n\n"
        "<i>Для изменения настроек обратитесь к разработчику</i>"
    )

@dp.message(F.text == "👤 Режим пользователя")
async def switch_to_user_mode(message: types.Message, state: FSMContext):
    """Переключение в режим пользователя"""
    if not ADMIN_ID or message.from_user.id != ADMIN_ID:
        await message.answer("⛔ Доступ запрещен")
        return
    
    await state.clear()
    await message.answer(
        "👤 <b>Вы перешли в режим пользователя</b>\n\n"
        "Теперь вы можете тестировать функции бота как обычный пользователь.\n\n"
        "Чтобы вернуться в панель администратора, используйте команду /admin",
        reply_markup=get_main_keyboard()
    )

# =========== ЗАПОЛНЕНИЕ АНКЕТЫ ===========
@dp.message(Questionnaire.waiting_for_name)
async def process_name(message: types.Message, state: FSMContext):
    """Обработка ФИО"""
    await state.update_data(full_name=message.text.strip())
    await message.answer(
        "✅ <b>ФИО сохранено</b>\n\n"
        "Введите <b>полное название вашей компании</b>:"
    )
    await state.set_state(Questionnaire.waiting_for_company)

@dp.message(Questionnaire.waiting_for_company)
async def process_company(message: types.Message, state: FSMContext):
    """Обработка названия компании"""
    await state.update_data(company_name=message.text.strip())
    await message.answer(
        "✅ <b>Компания сохранена</b>\n\n"
        "Введите ваш <b>телефон для связи</b> (в любом формате):"
    )
    await state.set_state(Questionnaire.waiting_for_phone)

@dp.message(Questionnaire.waiting_for_phone)
async def process_phone(message: types.Message, state: FSMContext):
    """Обработка телефона"""
    await state.update_data(phone=message.text.strip())
    await message.answer(
        "✅ <b>Телефон сохранен</b>\n\n"
        "Введите ваш <b>email для отправки тендеров</b>:"
    )
    await state.set_state(Questionnaire.waiting_for_email)

@dp.message(Questionnaire.waiting_for_email)
async def process_email(message: types.Message, state: FSMContext):
    """Обработка email"""
    await state.update_data(email=message.text.strip())
    await message.answer(
        "✅ <b>Email сохранен</b>\n\n"
        "Опишите <b>сферу деятельности</b> вашей компании:\n"
        "<i>Пример: строительство, IT-услуги, поставка продуктов</i>"
    )
    await state.set_state(Questionnaire.waiting_for_activity)

@dp.message(Questionnaire.waiting_for_activity)
async def process_activity(message: types.Message, state: FSMContext):
    """Обработка сферы деятельности"""
    await state.update_data(activity=message.text.strip())
    await message.answer(
        "✅ <b>Сфера деятельности сохранена</b>\n\n"
        "Введите <b>регионы работы</b> (города, области):\n"
        "<i>Пример: Москва, Московская область, Санкт-Петербург</i>"
    )
    await state.set_state(Questionnaire.waiting_for_region)

@dp.message(Questionnaire.waiting_for_region)
async def process_region(message: types.Message, state: FSMContext):
    """Обработка регионов"""
    await state.update_data(region=message.text.strip())
    await message.answer(
        "✅ <b>Регионы сохранены</b>\n\n"
        "Укажите <b>предпочтительный бюджет контрактов</b>:\n"
        "<i>Пример: от 100 000 до 1 000 000 руб.</i>"
    )
    await state.set_state(Questionnaire.waiting_for_budget)

@dp.message(Questionnaire.waiting_for_budget)
async def process_budget(message: types.Message, state: FSMContext):
    """Обработка бюджета"""
    await state.update_data(budget=message.text.strip())
    await message.answer(
        "✅ <b>Бюджет сохранен</b>\n\n"
        "Введите <b>ключевые слова для поиска</b> (через запятую):\n"
        "<i>Пример: строительные работы, поставка оборудования, IT-аутсорсинг</i>"
    )
    await state.set_state(Questionnaire.waiting_for_keywords)

@dp.message(Questionnaire.waiting_for_keywords)
async def process_keywords(message: types.Message, state: FSMContext):
    """Завершение анкеты"""
    user_data = await state.get_data()
    user_data['keywords'] = message.text.strip()
    user_id = message.from_user.id
    
    # Сохраняем анкету
    questionnaire_id = db.save_questionnaire(user_id, user_data)
    
    if questionnaire_id:
        # Создаем задачу на выгрузку
        export_id = db.create_tender_export(questionnaire_id, user_id)
        
        # Определяем время отправки
        if db.is_working_hours():
            time_info = "⏱️ <b>Сейчас ищу для вас актуальные тендеры. Не пройдет и часа, как я пришлю подборку на почту и (или) в телеграм.</b>"
        else:
            next_time = db.get_next_working_time()
            time_info = f"⏱️ <b>Запрос получен в нерабочее время. Вышлю с 9:00 до 17:00 {next_time.strftime('%d.%m.%Y')}.</b>"
        
        await message.answer(
            f"🎉 <b>Анкета #{questionnaire_id} сохранена!</b>\n\n"
            f"{time_info}",
            reply_markup=get_main_keyboard()
        )
        
        # Уведомление администратору
        if ADMIN_ID:
            notification = f"""
🆕 <b>НОВАЯ АНКЕТА #{questionnaire_id}</b>

👤 <b>Пользователь:</b> @{message.from_user.username or 'без username'}
🆔 <b>ID:</b> {user_id}
🏢 <b>Компания:</b> {user_data['company_name']}
👨‍💼 <b>ФИО:</b> {user_data['full_name']}
📞 <b>Телефон:</b> {user_data['phone']}
📧 <b>Email:</b> {user_data['email']}
🎯 <b>Сфера:</b> {user_data['activity']}

⏰ <b>Время:</b> {datetime.now().strftime('%H:%M %d.%m.%Y')}
"""
            
            try:
                await bot.send_message(ADMIN_ID, notification)
            except Exception as e:
                logger.error(f"Не удалось отправить уведомление админу: {e}")
        
        logger.info(f"Анкета #{questionnaire_id} сохранена")
    else:
        await message.answer(
            "❌ <b>Ошибка при сохранении анкеты</b>\n\n"
            "Пожалуйста, попробуйте еще раз позже или свяжитесь с поддержкой.",
            reply_markup=get_main_keyboard()
        )
    
    await state.clear()

# =========== ОБРАБОТКА FOLLOW-UP ОТВЕТОВ ===========
@dp.message(F.text == "✅ Да, нашел подходящее")
async def handle_positive_followup(message: types.Message):
    """Обработка положительного ответа на follow-up"""
    user_id = message.from_user.id
    
    # Ищем последнюю выгрузку пользователя
    conn = sqlite3.connect("tenders.db")
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    cursor.execute('''
    SELECT te.id 
    FROM tender_exports te
    JOIN questionnaires q ON te.questionnaire_id = q.id
    WHERE q.user_id = ?
    ORDER BY te.sent_at DESC
    LIMIT 1
    ''', (user_id,))
    
    export = cursor.fetchone()
    conn.close()
    
    if export:
        db.save_follow_up_response(export['id'], "Да, нашел подходящее")
    
    await message.answer(
        "🎉 <b>Отлично!</b>\n\n"
        "Рады, что нашли подходящие тендеры!\n\n"
        "🤝 <b>Нужна помощь с подготовкой заявки?</b>\n"
        "Мы можем проконсультировать по:\n"
        "• Подготовке документов\n"
        "• Требованиям организаторов\n"
        "• Стратегии участия\n\n"
        'Напишите "Консультация", и мы свяжемся с вами в течение 15 минут!',
        reply_markup=get_main_keyboard()
    )

@dp.message(F.text == "❌ Нет, не нашел")
async def handle_negative_followup(message: types.Message):
    """Обработка отрицательного ответа на follow-up"""
    user_id = message.from_user.id
    
    # Ищем последнюю выгрузку пользователя
    conn = sqlite3.connect("tenders.db")
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    cursor.execute('''
    SELECT te.id 
    FROM tender_exports te
    JOIN questionnaires q ON te.questionnaire_id = q.id
    WHERE q.user_id = ?
    ORDER BY te.sent_at DESC
    LIMIT 1
    ''', (user_id,))
    
    export = cursor.fetchone()
    conn.close()
    
    if export:
        db.save_follow_up_response(export['id'], "Нет, не нашел")
    
    await message.answer(
        "😕 <b>Жаль, что не нашли подходящее.</b>\n\n"
        "Мы учтем ваши пожелания и будем присылать новые тендеры по вашей сфере.\n\n"
        "📧 <b>Вы также будете получать:</b>\n"
        "• Полезные материалы по тендерам\n"
        "• Новости госзакупок\n"
        "• Советы по участию\n\n"
        "<i>Следующая рассылка через несколько дней.</i>",
        reply_markup=get_main_keyboard()
    )

@dp.message(F.text == "🤔 Нужна консультация")
async def handle_consultation_request(message: types.Message):
    """Обработка запроса на консультацию"""
    user_id = message.from_user.id
    
    await message.answer(
        "👨‍💼 <b>Запрос на консультацию принят!</b>\n\n"
        "Наш менеджер свяжется с вами в течение 15 минут.\n\n"
        "<b>Что обсудим:</b>\n"
        "• Подготовку документов для участия\n"
        "• Требования конкретных тендеров\n"
        "• Стратегию подачи заявок\n"
        "• Финансовое обеспечение\n\n"
        "⏱️ <b>Ожидайте звонка или сообщения.</b>"
    )
    
    # Уведомление администратору
    if ADMIN_ID:
        try:
            await bot.send_message(
                ADMIN_ID,
                f"📞 <b>ЗАПРОС НА КОНСУЛЬТАЦИЮ</b>\n\n"
                f"👤 Пользователь: @{message.from_user.username or 'без username'}\n"
                f"🆔 ID: {user_id}\n"
                f"📅 Время: {datetime.now().strftime('%H:%M %d.%m.%Y')}"
            )
        except Exception as e:
            logger.error(f"Не удалось отправить уведомление о консультации: {e}")

# =========== ОБРАБОТКА ОТВЕТОВ НА РАССЫЛКИ ===========
@dp.message()
async def handle_all_messages(message: types.Message):
    """Обработчик всех сообщений (включая ответы на рассылки)"""
    # Если это команда или кнопка меню - игнорируем
    if message.text and (message.text.startswith('/') or message.text in [
        "📝 Заполнить анкету онлайн", "📥 Скачать анкету в Word",
        "📊 Мои выгрузки", "📞 Контакты", "ℹ️ Помощь",
        "❌ Отмена", "✅ Да, нашел подходящее", "❌ Нет, не нашел",
        "🤔 Нужна консультация"
    ]):
        return
    
    # Проверяем, есть ли активная рассылка для пользователя
    conn = sqlite3.connect("tenders.db")
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    cursor.execute('''
    SELECT m.id, m.topic_id, mt.question
    FROM mailings m
    JOIN mailing_topics mt ON m.topic_id = mt.id
    WHERE m.user_id = ? 
    AND m.responded = 0
    AND date(m.sent_at) = date('now')
    ORDER BY m.sent_at DESC
    LIMIT 1
    ''', (message.from_user.id,))
    
    mailing = cursor.fetchone()
    conn.close()
    
    if mailing and message.text:
        # Сохраняем ответ на рассылку
        db.save_mailing_response(mailing['id'], message.from_user.id, message.text)
        
        # Благодарим за ответ
        await message.answer(
            "🙏 <b>Спасибо за ваш ответ!</b>\n\n"
            "Ваше мнение очень важно для нас. "
            "Мы учтем его в нашей дальнейшей работе."
        )
        
        # Передаем ответ администратору
        if ADMIN_ID:
            try:
                await bot.send_message(
                    ADMIN_ID,
                    f"💬 <b>ОТВЕТ НА РАССЫЛКУ</b>\n\n"
                    f"👤 Пользователь: @{message.from_user.username or 'без username'}\n"
                    f"🆔 ID: {message.from_user.id}\n"
                    f"📝 Вопрос: {mailing['question']}\n"
                    f"💭 Ответ: {message.text}\n"
                    f"📅 Время: {datetime.now().strftime('%H:%M %d.%m.%Y')}"
                )
            except Exception as e:
                logger.error(f"Не удалось отправить ответ на рассылку админу: {e}")
        
        return
    
    # Если это не ответ на рассылку и не команда
    is_admin = ADMIN_ID and message.from_user.id == ADMIN_ID
    await message.answer(
        "🤖 <b>Я вас не понял</b>\n\n"
        "Используйте кнопки меню или команды:\n"
        "/start - Главное меню\n"
        "/help - Помощь\n"
        "/my_exports - Мои выгрузки\n\n"
        "<i>Или выберите действие из меню:</i>",
        reply_markup=get_main_keyboard() if not is_admin else get_admin_keyboard()
    )

# =========== ЗАПУСК БОТА И HTTP СЕРВЕРА ===========
async def main():
    """Основная функция запуска"""
    print("\n" + "="*60)
    print("🚀 ЗАПУСК БОТА ТРИТИКА (ТЕНДЕРПОИСК)")
    print("="*60)
    
    # Проверяем бота
    try:
        bot_info = await bot.get_me()
        print(f"✅ Бот: @{bot_info.username}")
        print(f"✅ Имя: {bot_info.first_name}")
        print(f"✅ ID: {bot_info.id}")
    except Exception as e:
        print(f"❌ Ошибка проверки бота: {e}")
        print("⚠️ Проверьте токен бота")
        return
    
    # Создаем HTTP приложение для Railway healthcheck
    app = web.Application()
    app.router.add_get('/', health_check)
    app.router.add_get('/status', status_check)
    
    runner = web.AppRunner(app)
    await runner.setup()
    
    site = web.TCPSite(runner, '0.0.0.0', PORT)
    await site.start()
    
    print(f"✅ HTTP сервер запущен на порту {PORT}")
    print(f"✅ Health check доступен по пути: /")
    print(f"✅ Статус бота: /status")
    
    # Очищаем вебхуки
    await bot.delete_webhook(drop_pending_updates=True)
    print("✅ Вебхуки очищены")
    
    # Запускаем планировщики задач в фоне
    print("🔄 Запуск планировщиков задач...")
    asyncio.create_task(TaskScheduler.check_follow_ups())
    asyncio.create_task(TaskScheduler.send_mailings())
    asyncio.create_task(TaskScheduler.generate_reports())
    print("✅ Планировщики задач запущены")
    
    print("\n" + "="*60)
    print("🤖 БОТ УСПЕШНО ЗАПУЩЕН!")
    print("="*60)
    print(f"\n📱 Откройте Telegram и найдите бота:")
    print(f"   👉 https://t.me/{bot_info.username}")
    print("\n👤 Обычный режим: /start")
    print("🛠️ Админ-панель: /admin (если настроен ADMIN_ID)")
    print("\n🔄 Ожидание сообщений...")
    print(f"🌐 Health check активен на порту {PORT}\n")
    
    # Запускаем polling бота
    try:
        await dp.start_polling(bot, skip_updates=True)
    except KeyboardInterrupt:
        print("\n🛑 Бот остановлен пользователем")
    except Exception as e:
        logger.error(f"Ошибка в работе бота: {e}")
        print(f"❌ Ошибка: {e}")
    finally:
        await runner.cleanup()
        await bot.session.close()
        print("👋 Сессия бота закрыта")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n🛑 Приложение остановлено")
    except Exception as e:
        logger.error(f"Критическая ошибка при запуске: {e}")
        print(f"❌ Критическая ошибка: {e}")
