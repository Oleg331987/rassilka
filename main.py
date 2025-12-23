import os
import logging
import asyncio
import sys
import json
import re
import shutil
import csv
import time
import random
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
from contextlib import contextmanager, asynccontextmanager
from io import StringIO
from logging.handlers import RotatingFileHandler
from functools import lru_cache
from aiogram import Bot, Dispatcher, types, F, BaseMiddleware
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    ReplyKeyboardMarkup, KeyboardButton, 
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardRemove, BufferedInputFile,
    FSInputFile
)
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
import aiosqlite
import sqlite3
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# =========== КОНФИГУРАЦИЯ ===========
class Config:
    def __init__(self):
        # Бот и администратор
        self.BOT_TOKEN = os.getenv("BOT_TOKEN")
        self.ADMIN_ID = os.getenv("ADMIN_ID")
        
        if not self.BOT_TOKEN:
            logger.error("❌ BOT_TOKEN не установлен!")
            sys.exit(1)
            
        if not self.ADMIN_ID:
            logger.error("❌ ADMIN_ID не установлен!")
            sys.exit(1)
            
        self.ADMIN_ID = int(self.ADMIN_ID)
        
        # Настройки базы данных
        self.DB_PATH = os.getenv("DB_PATH", "tenders.db")
        self.BACKUP_DIR = os.getenv("BACKUP_DIR", "backups")
        self.LOGS_DIR = os.getenv("LOGS_DIR", "logs")
        
        # Рабочее время
        self.WORK_START_HOUR = 9
        self.WORK_END_HOUR = 17
        self.WORK_DAYS = [0, 1, 2, 3, 4]  # пн-пт (0-пн, 4-пт)
        
        # Создаем необходимые директории
        os.makedirs(self.BACKUP_DIR, exist_ok=True)
        os.makedirs(self.LOGS_DIR, exist_ok=True)
        
        # Настройки безопасности
        self.RATE_LIMIT = 1.0  # секунды между сообщениями

config = Config()

# =========== ЛОГИРОВАНИЕ ===========
def setup_logging():
    """Настройка логирования с ротацией"""
    logger = logging.getLogger(__name__)
    logger.setLevel(logging.INFO)
    
    # Форматтер
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    # Файловый обработчик с ротацией
    file_handler = RotatingFileHandler(
        filename=os.path.join(config.LOGS_DIR, 'bot.log'),
        maxBytes=10*1024*1024,  # 10 MB
        backupCount=5,
        encoding='utf-8'
    )
    file_handler.setFormatter(formatter)
    
    # Консольный обработчик
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    
    # Добавляем обработчики
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    
    # Логирование для aiogram
    logging.getLogger('aiogram').setLevel(logging.WARNING)
    
    return logger

logger = setup_logging()

# =========== MIDDLEWARE ===========
class ThrottlingMiddleware(BaseMiddleware):
    """Middleware для ограничения частоты сообщений"""
    def __init__(self, rate_limit: float = 1.0):
        super().__init__()
        self.rate_limit = rate_limit
        self.users = {}
        
    async def __call__(
        self,
        handler,
        event,
        data
    ):
        user_id = event.from_user.id
        current_time = datetime.now().timestamp()
        
        # Пропускаем команды админа
        if user_id == config.ADMIN_ID:
            return await handler(event, data)
        
        # Проверяем время последнего сообщения
        if user_id in self.users:
            last_time = self.users[user_id]
            if current_time - last_time < self.rate_limit:
                await event.answer("⏳ Пожалуйста, подождите немного перед отправкой следующего сообщения.")
                return
        
        self.users[user_id] = current_time
        return await handler(event, data)

class AdminMiddleware(BaseMiddleware):
    """Middleware для проверки прав администратора"""
    async def __call__(self, handler, event, data):
        # Для команд админа проверяем права
        if hasattr(event, 'text') and event.text in [
            "📊 Новые анкеты", "📤 Отправить тендер", "📈 Отчет эффективности",
            "📣 Начать рассылку", "📋 Скачать базу", "⚙️ Настройки", "/admin"
        ]:
            if event.from_user.id != config.ADMIN_ID:
                await event.answer("⛔ У вас нет прав для выполнения этой команды.")
                return
        
        return await handler(event, data)

# =========== ИНИЦИАЛИЗАЦИЯ БОТА ===========
bot = Bot(
    token=config.BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# Добавляем middleware
dp.message.middleware(ThrottlingMiddleware(config.RATE_LIMIT))
dp.message.middleware(AdminMiddleware())

# =========== ВАЛИДАЦИЯ ДАННЫХ ===========
def validate_phone(phone: str) -> bool:
    """Проверка формата телефона"""
    # Убираем все нецифровые символы, кроме +
    clean_phone = re.sub(r'[^\d+]', '', phone)
    
    # Проверяем российские форматы
    if clean_phone.startswith('+7') and len(clean_phone) == 12:
        return True
    elif clean_phone.startswith('8') and len(clean_phone) == 11:
        return True
    elif clean_phone.startswith('7') and len(clean_phone) == 11:
        return True
    
    return False

def validate_email(email: str) -> bool:
    """Проверка формата email"""
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return bool(re.match(pattern, email))

def validate_inn(inn: str) -> bool:
    """Проверка ИНН"""
    if not inn.isdigit():
        return False
    
    if len(inn) == 10:
        # Проверка контрольной цифры для 10-значного ИНН
        coefficients = [2, 4, 10, 3, 5, 9, 4, 6, 8]
        checksum = sum(int(inn[i]) * coefficients[i] for i in range(9)) % 11
        if checksum > 9:
            checksum = checksum % 10
        return checksum == int(inn[9])
    elif len(inn) == 12:
        # Проверка контрольных цифр для 12-значного ИНН
        coefficients1 = [7, 2, 4, 10, 3, 5, 9, 4, 6, 8]
        checksum1 = sum(int(inn[i]) * coefficients1[i] for i in range(10)) % 11
        if checksum1 > 9:
            checksum1 = checksum1 % 10
            
        coefficients2 = [3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8]
        checksum2 = sum(int(inn[i]) * coefficients2[i] for i in range(11)) % 11
        if checksum2 > 9:
            checksum2 = checksum2 % 10
            
        return checksum1 == int(inn[10]) and checksum2 == int(inn[11])
    
    return False

# =========== БАЗА ДАННЫХ ===========
class Database:
    def __init__(self, db_path: str = "tenders.db"):
        self.db_path = db_path
        
    async def init_db(self):
        """Инициализация базы данных"""
        async with aiosqlite.connect(self.db_path) as conn:
            # Таблица анкет
            await conn.execute('''
            CREATE TABLE IF NOT EXISTS questionnaires (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                username TEXT,
                full_name TEXT,
                company_name TEXT,
                inn TEXT,
                contact_person TEXT,
                phone TEXT,
                email TEXT,
                activity_sphere TEXT,
                industry TEXT,
                contract_amount TEXT,
                regions TEXT,
                status TEXT DEFAULT 'new',
                tender_sent BOOLEAN DEFAULT 0,
                tender_sent_at TEXT,
                tender_sent_by INTEGER,
                tender_file_id TEXT,
                follow_up_sent BOOLEAN DEFAULT 0,
                follow_up_at TEXT,
                follow_up_response TEXT,
                consultation_requested BOOLEAN DEFAULT 0,
                created_at TEXT,
                updated_at TEXT,
                last_mailing_date TEXT,
                mailing_group INTEGER DEFAULT 0,
                responses_count INTEGER DEFAULT 0,
                unsubscribe BOOLEAN DEFAULT 0
            )
            ''')
            
            # Таблица рассылок
            await conn.execute('''
            CREATE TABLE IF NOT EXISTS mailings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mailing_date TEXT,
                message_text TEXT,
                message_type TEXT,
                total_users INTEGER,
                successful_sends INTEGER,
                failed_sends INTEGER,
                responses INTEGER DEFAULT 0,
                clicks INTEGER DEFAULT 0
            )
            ''')
            
            # Таблица реакций на рассылки
            await conn.execute('''
            CREATE TABLE IF NOT EXISTS mailing_responses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mailing_id INTEGER,
                user_id INTEGER,
                response_text TEXT,
                created_at TEXT
            )
            ''')
            
            # Таблица статистики
            await conn.execute('''
            CREATE TABLE IF NOT EXISTS statistics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT,
                new_users INTEGER DEFAULT 0,
                questionnaires_completed INTEGER DEFAULT 0,
                tenders_sent INTEGER DEFAULT 0,
                follow_up_responses INTEGER DEFAULT 0,
                consultation_requests INTEGER DEFAULT 0,
                mailings_sent INTEGER DEFAULT 0,
                mailing_responses INTEGER DEFAULT 0
            )
            ''')
            
            # Индексы
            await conn.execute('CREATE INDEX IF NOT EXISTS idx_user_id ON questionnaires (user_id)')
            await conn.execute('CREATE INDEX IF NOT EXISTS idx_status ON questionnaires (status)')
            await conn.execute('CREATE INDEX IF NOT EXISTS idx_tender_sent ON questionnaires (tender_sent)')
            await conn.execute('CREATE INDEX IF NOT EXISTS idx_created_at ON questionnaires (created_at)')
            await conn.execute('CREATE INDEX IF NOT EXISTS idx_mailing_date ON mailings (mailing_date)')
            await conn.execute('CREATE INDEX IF NOT EXISTS idx_unsubscribe ON questionnaires (unsubscribe)')
            
            await conn.commit()
        logger.info("✅ База данных инициализирована")
    
    async def execute_query(self, query: str, params: tuple = ()):
        """Выполнение запроса"""
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            cursor = await conn.execute(query, params)
            await conn.commit()
            return cursor
    
    async def fetch_one(self, query: str, params: tuple = ()):
        """Получение одной записи"""
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            cursor = await conn.execute(query, params)
            return await cursor.fetchone()
    
    async def fetch_all(self, query: str, params: tuple = ()):
        """Получение всех записей"""
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            cursor = await conn.execute(query, params)
            return await cursor.fetchall()
    
    @lru_cache(maxsize=100)
    async def get_user_profile(self, user_id: int):
        """Получение профиля пользователя с кэшированием"""
        return await self.fetch_one(
            "SELECT * FROM questionnaires WHERE user_id = ? ORDER BY created_at DESC LIMIT 1",
            (user_id,)
        )
    
    async def save_questionnaire(self, user_data: dict) -> Optional[int]:
        """Сохранение анкеты"""
        try:
            # Проверяем, не заполнял ли пользователь анкету сегодня
            today = datetime.now().strftime("%Y-%m-%d")
            existing = await self.fetch_one(
                "SELECT id FROM questionnaires WHERE user_id = ? AND DATE(created_at) = ?",
                (user_data['user_id'], today)
            )
            
            if existing:
                return existing['id']
            
            query = '''
            INSERT INTO questionnaires 
            (user_id, username, full_name, company_name, inn, contact_person, 
             phone, email, activity_sphere, industry, contract_amount, 
             regions, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            '''
            
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            params = (
                user_data['user_id'],
                user_data['username'],
                user_data['full_name'],
                user_data['company_name'],
                user_data['inn'],
                user_data['contact_person'],
                user_data['phone'],
                user_data['email'],
                user_data['activity_sphere'],
                user_data['industry'],
                user_data['contract_amount'],
                user_data['regions'],
                now,
                now
            )
            
            cursor = await self.execute_query(query, params)
            questionnaire_id = cursor.lastrowid
            
            # Обновляем статистику
            await self.update_statistics('questionnaires_completed')
            
            logger.info(f"✅ Анкета #{questionnaire_id} сохранена для пользователя {user_data['user_id']}")
            return questionnaire_id
            
        except Exception as e:
            logger.error(f"❌ Ошибка сохранения анкеты: {e}")
            return None
    
    async def mark_tender_sent(self, questionnaire_id: int, admin_id: int, file_id: str = None):
        """Отметка отправки тендера"""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        query = '''
        UPDATE questionnaires 
        SET tender_sent = 1, tender_sent_at = ?, tender_sent_by = ?, 
            tender_file_id = ?, status = 'processed', updated_at = ?
        WHERE id = ?
        '''
        await self.execute_query(query, (now, admin_id, file_id, now, questionnaire_id))
        
        # Обновляем статистику
        await self.update_statistics('tenders_sent')
        
        logger.info(f"✅ Тендер отправлен для анкеты #{questionnaire_id}")
    
    async def update_follow_up(self, questionnaire_id: int, response: str = None):
        """Обновление follow-up"""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        if response:
            query = '''
            UPDATE questionnaires 
            SET follow_up_sent = 1, follow_up_at = ?, 
                follow_up_response = ?, updated_at = ?
            WHERE id = ?
            '''
            await self.execute_query(query, (now, response, now, questionnaire_id))
            
            if response.lower() in ['да', 'yes', 'удалось', 'да, нашел подходящее']:
                await self.update_statistics('follow_up_responses')
        else:
            query = '''
            UPDATE questionnaires 
            SET follow_up_sent = 1, follow_up_at = ?, updated_at = ?
            WHERE id = ?
            '''
            await self.execute_query(query, (now, now, questionnaire_id))
    
    async def get_pending_follow_ups(self):
        """Получение анкет для follow-up"""
        query = '''
        SELECT * FROM questionnaires 
        WHERE tender_sent = 1 
          AND follow_up_sent = 0 
          AND status = 'processed'
          AND tender_sent_at IS NOT NULL
        '''
        return await self.fetch_all(query)
    
    async def get_users_for_mailing(self, group: int = 0):
        """Получение пользователей для рассылки"""
        query = '''
        SELECT DISTINCT user_id, username 
        FROM questionnaires 
        WHERE user_id IS NOT NULL 
          AND unsubscribe = 0
          AND (follow_up_response IS NULL OR follow_up_response NOT LIKE '%да%')
          AND mailing_group = ?
        '''
        return await self.fetch_all(query, (group,))
    
    async def save_mailing(self, message_text: str, message_type: str, 
                          total_users: int, successful: int, failed: int):
        """Сохранение информации о рассылке"""
        query = '''
        INSERT INTO mailings 
        (mailing_date, message_text, message_type, total_users, 
         successful_sends, failed_sends)
        VALUES (?, ?, ?, ?, ?, ?)
        '''
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        await self.execute_query(query, (now, message_text, message_type, 
                                        total_users, successful, failed))
        
        # Обновляем статистику
        await self.update_statistics('mailings_sent')
    
    async def save_mailing_response(self, mailing_id: int, user_id: int, response_text: str):
        """Сохранение ответа на рассылку"""
        query = '''
        INSERT INTO mailing_responses (mailing_id, user_id, response_text, created_at)
        VALUES (?, ?, ?, ?)
        '''
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        await self.execute_query(query, (mailing_id, user_id, response_text, now))
        
        # Обновляем счетчик ответов в анкете
        await self.execute_query(
            "UPDATE questionnaires SET responses_count = responses_count + 1 WHERE user_id = ?",
            (user_id,)
        )
        
        # Обновляем статистику
        await self.update_statistics('mailing_responses')
    
    async def update_statistics(self, field: str):
        """Обновление статистики"""
        today = datetime.now().strftime("%Y-%m-%d")
        
        # Проверяем, есть ли запись на сегодня
        existing = await self.fetch_one(
            "SELECT id FROM statistics WHERE date = ?", 
            (today,)
        )
        
        if existing:
            query = f"UPDATE statistics SET {field} = {field} + 1 WHERE date = ?"
            await self.execute_query(query, (today,))
        else:
            query = f'''
            INSERT INTO statistics (date, {field})
            VALUES (?, 1)
            '''
            await self.execute_query(query, (today,))
    
    async def get_statistics_report(self, days: int = 14):
        """Получение отчета за период"""
        start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        
        query = '''
        SELECT 
            SUM(new_users) as new_users,
            SUM(questionnaires_completed) as questionnaires_completed,
            SUM(tenders_sent) as tenders_sent,
            SUM(follow_up_responses) as follow_up_responses,
            SUM(consultation_requests) as consultation_requests,
            SUM(mailings_sent) as mailings_sent,
            SUM(mailing_responses) as mailing_responses
        FROM statistics 
        WHERE date >= ?
        '''
        
        return await self.fetch_one(query, (start_date,))
    
    async def get_new_users_count(self, days: int = 14):
        """Количество новых пользователей"""
        start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        
        query = '''
        SELECT COUNT(DISTINCT user_id) as count 
        FROM questionnaires 
        WHERE created_at >= ?
        '''
        
        result = await self.fetch_one(query, (start_date,))
        return result['count'] if result else 0
    
    async def unsubscribe_user(self, user_id: int):
        """Отписать пользователя от рассылок"""
        query = "UPDATE questionnaires SET unsubscribe = 1 WHERE user_id = ?"
        await self.execute_query(query, (user_id,))
        return True

db = Database(config.DB_PATH)

# =========== КЛАВИАТУРЫ ===========
def get_start_keyboard():
    """Клавиатура при старте"""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📝 Заполнить анкету онлайн")],
            [KeyboardButton(text="📥 Скачать анкету")],
            [KeyboardButton(text="❓ Как это работает?")],
            [KeyboardButton(text="📞 Консультация"), KeyboardButton(text="ℹ️ Помощь")]
        ],
        resize_keyboard=True,
        input_field_placeholder="Выберите действие..."
    )

def get_cancel_keyboard():
    """Клавиатура отмены"""
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="❌ Отменить")]],
        resize_keyboard=True
    )

def get_yes_no_keyboard():
    """Клавиатура Да/Нет"""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="✅ Да, нашел подходящее")],
            [KeyboardButton(text="❌ Нет, не нашел")],
            [KeyboardButton(text="🤔 Нужна консультация")]
        ],
        resize_keyboard=True
    )

def get_admin_keyboard():
    """Клавиатура администратора"""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📊 Новые анкеты"), KeyboardButton(text="📤 Отправить тендер")],
            [KeyboardButton(text="📈 Отчет эффективности"), KeyboardButton(text="📣 Начать рассылку")],
            [KeyboardButton(text="📋 Скачать базу"), KeyboardButton(text="⚙️ Настройки")],
            [KeyboardButton(text="👤 В меню пользователя")]
        ],
        resize_keyboard=True,
        input_field_placeholder="Выберите действие..."
    )

def get_user_menu_keyboard():
    """Клавиатура меню пользователя"""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📝 Заполнить анкету онлайн")],
            [KeyboardButton(text="📊 Мои анкеты"), KeyboardButton(text="📞 Консультация")],
            [KeyboardButton(text="❓ Помощь"), KeyboardButton(text="🚫 Отписаться")]
        ],
        resize_keyboard=True
    )

# =========== СОСТОЯНИЯ ===========
class Questionnaire(StatesGroup):
    waiting_for_name = State()
    waiting_for_company = State()
    waiting_for_inn = State()
    waiting_for_contact = State()
    waiting_for_phone = State()
    waiting_for_email = State()
    waiting_for_activity = State()
    waiting_for_industry = State()
    waiting_for_amount = State()
    waiting_for_regions = State()

class AdminAction(StatesGroup):
    waiting_for_questionnaire_id = State()
    waiting_for_tender_file = State()
    waiting_for_mailing_text = State()
    waiting_for_settings = State()

# =========== ПОМОЩНИКИ ===========
@contextmanager
def timing(description: str):
    """Контекстный менеджер для измерения времени выполнения"""
    start = time.time()
    yield
    elapsed = time.time() - start
    logger.info(f"⏱️ {description}: {elapsed:.3f} сек")

def is_working_hours():
    """Проверка рабочего времени"""
    now = datetime.now()
    
    # Проверка дня недели (0-пн, 6-вс)
    if now.weekday() not in config.WORK_DAYS:
        return False
    
    # Проверка времени
    if not (config.WORK_START_HOUR <= now.hour < config.WORK_END_HOUR):
        return False
    
    return True

def get_next_working_time():
    """Получение следующего рабочего времени"""
    now = datetime.now()
    
    # Если сейчас рабочий день в рабочее время
    if is_working_hours():
        return now
    
    # Если сейчас рабочий день, но не рабочее время
    if now.weekday() in config.WORK_DAYS and now.hour >= config.WORK_END_HOUR:
        # Следующий рабочий день в 9:00
        days_to_add = 1
        while (now.weekday() + days_to_add) % 7 not in config.WORK_DAYS:
            days_to_add += 1
        
        next_day = now + timedelta(days=days_to_add)
        return next_day.replace(hour=config.WORK_START_HOUR, minute=0, second=0, microsecond=0)
    
    # Если сейчас не рабочий день
    days_to_add = 1
    while (now.weekday() + days_to_add) % 7 not in config.WORK_DAYS:
        days_to_add += 1
    
    next_day = now + timedelta(days=days_to_add)
    return next_day.replace(hour=config.WORK_START_HOUR, minute=0, second=0, microsecond=0)

async def send_notification_to_admin(message_text: str):
    """Отправка уведомления администратору"""
    try:
        await bot.send_message(config.ADMIN_ID, message_text)
    except Exception as e:
        logger.error(f"❌ Ошибка отправки уведомления админу: {e}")

async def schedule_follow_up(questionnaire_id: int, user_id: int):
    """Планирование follow-up сообщения с учетом рабочего времени"""
    try:
        # Получаем информацию о времени отправки тендера
        questionnaire = await db.fetch_one(
            "SELECT tender_sent_at FROM questionnaires WHERE id = ? AND tender_sent = 1",
            (questionnaire_id,)
        )
        
        if not questionnaire:
            logger.warning(f"Анкета #{questionnaire_id} не найдена или тендер не отправлен")
            return
        
        tender_time_str = questionnaire['tender_sent_at']
        if not tender_time_str:
            logger.warning(f"Время отправки тендера не указано для анкеты #{questionnaire_id}")
            return
        
        tender_time = datetime.strptime(tender_time_str, "%Y-%m-%d %H:%M:%S")
        now = datetime.now()
        
        # Рассчитываем время для follow-up
        if is_working_hours() and tender_time.hour >= config.WORK_START_HOUR:
            # Если тендер отправлен в рабочее время - ждем 1 час
            wait_seconds = 3600
        else:
            # Если в нерабочее время - планируем на следующее рабочее время + 1 час
            next_work_time = get_next_working_time()
            if tender_time > next_work_time:
                # Тендер отправлен позже, чем начало следующего рабочего дня
                wait_seconds = (tender_time - now).total_seconds() + 3600
            else:
                wait_seconds = (next_work_time - now).total_seconds() + 3600
        
        # Ждем нужное количество секунд
        if wait_seconds > 0:
            logger.info(f"⏰ Follow-up для анкеты #{questionnaire_id} запланирован через {wait_seconds/3600:.1f} часов")
            await asyncio.sleep(wait_seconds)
        
        # Проверяем, не был ли уже отправлен follow-up
        current_status = await db.fetch_one(
            "SELECT follow_up_sent FROM questionnaires WHERE id = ?",
            (questionnaire_id,)
        )
        
        if current_status and not current_status['follow_up_sent']:
            await bot.send_message(
                user_id,
                "📊 Подборка тендеров отправлена. Удалось ли найти что-то подходящее?",
                reply_markup=get_yes_no_keyboard()
            )
            
            await db.update_follow_up(questionnaire_id)
            logger.info(f"✅ Follow-up отправлен для анкеты #{questionnaire_id}")
            
    except Exception as e:
        logger.error(f"❌ Ошибка в schedule_follow_up для анкеты #{questionnaire_id}: {e}")

async def create_backup():
    """Создание резервной копии базы данных"""
    try:
        backup_name = f"backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
        backup_path = os.path.join(config.BACKUP_DIR, backup_name)
        
        # Копируем файл базы данных
        shutil.copy2(config.DB_PATH, backup_path)
        
        # Удаляем старые бэкапы (оставляем последние 7)
        if os.path.exists(config.BACKUP_DIR):
            backups = [f for f in os.listdir(config.BACKUP_DIR) if f.endswith('.db')]
            backups.sort(reverse=True)
            
            if len(backups) > 7:
                for old_backup in backups[7:]:
                    old_path = os.path.join(config.BACKUP_DIR, old_backup)
                    os.remove(old_path)
                    logger.info(f"🗑️ Удален старый бэкап: {old_backup}")
        
        logger.info(f"✅ Бэкап создан: {backup_name}")
        return backup_path
        
    except Exception as e:
        logger.error(f"❌ Ошибка создания бэкапа: {e}")
        return None

# =========== ОБРАБОТЧИКИ ОШИБОК ===========
@dp.errors()
async def global_error_handler(event, exception):
    """Глобальная обработка ошибок"""
    logger.error(f"🔥 Критическая ошибка: {exception}", exc_info=True)
    
    # Уведомляем администратора о критических ошибках
    try:
        await send_notification_to_admin(
            f"⚠️ <b>КРИТИЧЕСКАЯ ОШИБКА</b>\n\n"
            f"Тип: {type(exception).__name__}\n"
            f"Сообщение: {str(exception)[:200]}\n\n"
            f"⏰ Время: {datetime.now().strftime('%H:%M:%S %d.%m.%Y')}"
        )
    except Exception as e:
        logger.error(f"❌ Не удалось отправить уведомление об ошибке: {e}")
    
    return True

# =========== ОБРАБОТЧИКИ КОМАНД ===========
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    """Старт бота"""
    await message.answer(
        "🤖 <b>Привет! Я бот Тритики.</b>\n\n"
        "Помогаю компаниям находить выгодные тендеры.\n\n"
        "🎯 <b>Хотите бесплатно получить подборку тендеров по вашей сфере?</b>\n"
        "Заполните анкету - это займет всего 5 минут!\n\n"
        "⏱️ <b>Что будет дальше:</b>\n"
        "1. Вы заполняете анкету онлайн\n"
        "2. Мы ищем для вас актуальные тендеры\n"
        "3. Присылаем подборку на почту и в Telegram\n"
        "4. Помогаем с подготовкой заявки\n\n"
        "<i>Бесплатная выгрузка — наш подарок новым клиентам!</i>",
        reply_markup=get_start_keyboard()
    )

@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    """Справка по командам"""
    help_text = """
<b>🤖 Доступные команды:</b>

<b>Для всех пользователей:</b>
/start - Начать работу с ботом
/help - Показать эту справку
/unsubscribe - Отписаться от рассылок
/menu - Показать главное меню

<b>Для администратора:</b>
/admin - Панель администратора

<b>📱 Основные функции:</b>
• Заполнить анкету для поиска тендеров
• Получить подборку актуальных тендеров
• Консультация по участию в тендерах
• Полезные материалы и рассылки

<b>⏱️ Время работы:</b>
Пн-Пт с 9:00 до 17:00
"""
    await message.answer(help_text)

@dp.message(Command("admin"))
async def cmd_admin(message: types.Message):
    """Панель администратора"""
    if message.from_user.id != config.ADMIN_ID:
        await message.answer("⛔ У вас нет прав для доступа к панели администратора.")
        return
    
    await message.answer(
        "🛠️ <b>Панель администратора</b>\n\n"
        "Здесь вы можете управлять всеми функциями бота:\n"
        "• 📊 Просматривать новые анкеты\n"
        "• 📤 Отправлять тендеры пользователям\n"
        "• 📈 Смотреть отчеты эффективности\n"
        "• 📣 Настраивать и запускать рассылки\n"
        "• 📋 Экспортировать данные\n"
        "• ⚙️ Настраивать параметры бота",
        reply_markup=get_admin_keyboard()
    )

@dp.message(Command("unsubscribe"))
async def cmd_unsubscribe(message: types.Message):
    """Отписаться от рассылок"""
    success = await db.unsubscribe_user(message.from_user.id)
    
    if success:
        await message.answer(
            "✅ Вы отписались от рассылок.\n\n"
            "Вы больше не будете получать информационные сообщения и рассылки.\n\n"
            "Ваши данные сохранены для обработки текущих запросов.\n\n"
            "Если передумаете, напишите нам в поддержку."
        )
    else:
        await message.answer("❌ Произошла ошибка при отписке. Попробуйте позже.")

@dp.message(Command("menu"))
async def cmd_menu(message: types.Message):
    """Показать главное меню"""
    if message.from_user.id == config.ADMIN_ID:
        await message.answer("📋 <b>Главное меню</b>", reply_markup=get_admin_keyboard())
    else:
        await message.answer("📋 <b>Главное меню</b>", reply_markup=get_start_keyboard())

# =========== ОБРАБОТЧИКИ СОБЫТИЙ ===========
@dp.message(F.text == "👤 В меню пользователя")
async def to_user_menu(message: types.Message):
    """Переход в меню пользователя"""
    await message.answer("👤 <b>Меню пользователя</b>", reply_markup=get_start_keyboard())

@dp.message(F.text == "📝 Заполнить анкету онлайн")
async def start_online_questionnaire(message: types.Message, state: FSMContext):
    """Начало заполнения анкеты онлайн"""
    await message.answer(
        "📝 <b>Начинаем заполнение анкеты!</b>\n\n"
        "Заполнение займет 5-7 минут. Введите ваше ФИО полностью:",
        reply_markup=get_cancel_keyboard()
    )
    await state.set_state(Questionnaire.waiting_for_name)

@dp.message(F.text == "📥 Скачать анкету")
async def download_questionnaire(message: types.Message):
    """Скачать анкету для заполнения"""
    questionnaire_text = """АНКЕТА ДЛЯ ПОИСКА ТЕНДЕРОВ

1. ФИО полностью: ___________________
2. Название компании: ___________________
3. ИНН: ___________________
4. Контактное лицо: ___________________
5. Телефон: ___________________
6. Email: ___________________
7. Сфера деятельности компании: ___________________
8. Ключевые слова для поиска (через запятую): ___________________
9. Бюджет контрактов: ___________________
10. Регионы работы: ___________________

Заполните и отправьте на info@tritika.ru
Или перешлите менеджеру в Telegram"""
    
    file = BufferedInputFile(
        questionnaire_text.encode('utf-8'),
        filename="Анкета_для_тендеров.txt"
    )
    
    await message.answer_document(
        file,
        caption="📄 <b>Скачайте и заполните анкету</b>\n\n"
                "Заполните все поля и отправьте на:\n"
                "📧 <b>info@tritika.ru</b>\n\n"
                "Или перешлите заполненную анкету менеджеру в Telegram."
    )

@dp.message(F.text == "❓ Как это работает?")
async def how_it_works(message: types.Message):
    """Объяснение работы сервиса"""
    await message.answer(
        "🔄 <b>Как работает наш сервис:</b>\n\n"
        "1. <b>Заполняете анкету</b> - онлайн или скачиваете шаблон\n"
        "2. <b>Мы анализируем</b> вашу сферу деятельности и потребности\n"
        "3. <b>Ищем тендеры</b> по 50+ площадкам и базам данных\n"
        "4. <b>Формируем подборку</b> релевантных тендеров\n"
        "5. <b>Отправляем вам</b> на почту и в Telegram\n"
        "6. <b>Помогаем</b> с подготовкой документов для участия\n\n"
        "⏱️ <b>Сроки:</b>\n"
        "• Выгрузка в течение 1 часа в рабочее время\n"
        "• С 9:00 до 17:00 по будням\n"
        "• Если запрос в нерабочее время - отправим в 9:00 следующего рабочего дня\n\n"
        "💡 <b>Бесплатно:</b> первая выгрузка тендеров - наш подарок для новых клиентов!"
    )

@dp.message(F.text == "ℹ️ Помощь" or F.text == "❓ Помощь")
async def show_help(message: types.Message):
    """Показать помощь"""
    await cmd_help(message)

@dp.message(F.text == "📞 Консультация")
async def request_consultation(message: types.Message):
    """Запрос консультации"""
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
    
    # Уведомляем администратора
    await send_notification_to_admin(
        f"📞 <b>ЗАПРОС НА КОНСУЛЬТАЦИЮ</b>\n\n"
        f"👤 Пользователь: @{message.from_user.username or message.from_user.id}\n"
        f"🆔 ID: {message.from_user.id}\n"
        f"📅 Время: {datetime.now().strftime('%H:%M %d.%m.%Y')}\n"
        f"✉️ Сообщение: Пользователь запросил консультацию через меню"
    )
    
    # Обновляем статистику
    await db.update_statistics('consultation_requests')

@dp.message(F.text == "🚫 Отписаться")
async def unsubscribe_from_menu(message: types.Message):
    """Отписаться от рассылок через меню"""
    await cmd_unsubscribe(message)

@dp.message(F.text == "❌ Отменить")
async def cancel_action(message: types.Message, state: FSMContext):
    """Отмена действия"""
    await state.clear()
    await message.answer(
        "❌ Действие отменено.",
        reply_markup=get_start_keyboard()
    )

# =========== ЗАПОЛНЕНИЕ АНКЕТЫ ===========
@dp.message(Questionnaire.waiting_for_name)
async def process_name(message: types.Message, state: FSMContext):
    """Обработка ФИО"""
    await state.update_data(
        full_name=message.text.strip(),
        user_id=message.from_user.id,
        username=message.from_user.username or "Не указан"
    )
    await message.answer("✅ <b>ФИО сохранено</b>\n\nВведите полное название вашей компании:")
    await state.set_state(Questionnaire.waiting_for_company)

@dp.message(Questionnaire.waiting_for_company)
async def process_company(message: types.Message, state: FSMContext):
    """Обработка названия компании"""
    await state.update_data(company_name=message.text.strip())
    await message.answer("✅ <b>Компания сохранена</b>\n\nВведите ИНН компании (10 или 12 цифр):")
    await state.set_state(Questionnaire.waiting_for_inn)

@dp.message(Questionnaire.waiting_for_inn)
async def process_inn(message: types.Message, state: FSMContext):
    """Обработка ИНН"""
    inn = message.text.strip()
    
    if not validate_inn(inn):
        await message.answer(
            "❌ <b>Неверный ИНН!</b>\n\n"
            "Пожалуйста, введите корректный ИНН:\n"
            "• 10 цифр для организаций\n"
            "• 12 цифр для ИП\n\n"
            "ИНН должен проходить проверку контрольной суммы."
        )
        return
    
    await state.update_data(inn=inn)
    await message.answer("✅ <b>ИНН сохранен</b>\n\nВведите контактное лицо для связи:")
    await state.set_state(Questionnaire.waiting_for_contact)

@dp.message(Questionnaire.waiting_for_contact)
async def process_contact(message: types.Message, state: FSMContext):
    """Обработка контактного лица"""
    await state.update_data(contact_person=message.text.strip())
    await message.answer("✅ <b>Контакт сохранен</b>\n\nВведите телефон для связи:")
    await state.set_state(Questionnaire.waiting_for_phone)

@dp.message(Questionnaire.waiting_for_phone)
async def process_phone(message: types.Message, state: FSMContext):
    """Обработка телефона"""
    phone = message.text.strip()
    
    if not validate_phone(phone):
        await message.answer(
            "❌ <b>Неверный формат телефона!</b>\n\n"
            "Пожалуйста, введите телефон в одном из форматов:\n"
            "• +7XXXXXXXXXX\n"
            "• 8XXXXXXXXXX\n"
            "• 7XXXXXXXXXX"
        )
        return
    
    await state.update_data(phone=phone)
    await message.answer("✅ <b>Телефон сохранен</b>\n\nВведите email для отправки тендеров:")
    await state.set_state(Questionnaire.waiting_for_email)

@dp.message(Questionnaire.waiting_for_email)
async def process_email(message: types.Message, state: FSMContext):
    """Обработка email"""
    email = message.text.strip()
    
    if not validate_email(email):
        await message.answer("❌ <b>Неверный формат email!</b>\n\nВведите корректный email:")
        return
    
    await state.update_data(email=email)
    await message.answer("✅ <b>Email сохранен</b>\n\nВведите сферу деятельности компании:")
    await state.set_state(Questionnaire.waiting_for_activity)

@dp.message(Questionnaire.waiting_for_activity)
async def process_activity(message: types.Message, state: FSMContext):
    """Обработка сферы деятельности"""
    await state.update_data(activity_sphere=message.text.strip())
    await message.answer("✅ <b>Сфера сохранена</b>\n\nВведите ключевые слова для поиска (через запятую):")
    await state.set_state(Questionnaire.waiting_for_industry)

@dp.message(Questionnaire.waiting_for_industry)
async def process_industry(message: types.Message, state: FSMContext):
    """Обработка ключевых слов"""
    await state.update_data(industry=message.text.strip())
    await message.answer(
        "✅ <b>Ключевые слова сохранены</b>\n\n"
        "Введите бюджет контрактов (например):\n"
        "• от 100 000 до 1 000 000 руб.\n"
        "• до 500 000 руб.\n"
        "• любой"
    )
    await state.set_state(Questionnaire.waiting_for_amount)

@dp.message(Questionnaire.waiting_for_amount)
async def process_amount(message: types.Message, state: FSMContext):
    """Обработка бюджета"""
    await state.update_data(contract_amount=message.text.strip())
    await message.answer("✅ <b>Бюджет сохранен</b>\n\nВведите регионы работы через запятую:")
    await state.set_state(Questionnaire.waiting_for_regions)

@dp.message(Questionnaire.waiting_for_regions)
async def process_regions(message: types.Message, state: FSMContext):
    """Завершение анкеты"""
    user_data = await state.get_data()
    user_data['regions'] = message.text.strip()
    
    # Сохраняем анкету
    with timing("Сохранение анкеты"):
        questionnaire_id = await db.save_questionnaire(user_data)
    
    if questionnaire_id:
        # Определяем время отправки
        if is_working_hours():
            time_info = "⏱️ <b>Сейчас ищу для вас актуальные тендеры. Подождите не более часа</b>"
        else:
            next_time = get_next_working_time()
            time_info = f"⏱️ <b>Запрос получен в нерабочее время</b>\nВышлю подборку {next_time.strftime('%d.%m.%Y')} с 9:00 до 17:00"
        
        await message.answer(
            f"🎉 <b>Отлично! Анкета сохранена.</b>\n\n"
            f"{time_info}\n\n"
            f"📧 <b>Подборку пришлю:</b>\n"
            f"• На email: {user_data['email']}\n"
            f"• В этот чат Telegram\n\n"
            f"📊 <b>Что будет в подборке:</b>\n"
            f"• Релевантные тендеры по вашим параметрам\n"
            f"• Сроки подачи заявок\n"
            f"• Контакты организаторов\n"
            f"• Рекомендации по участию\n\n"
            f"<i>Следите за сообщениями!</i>",
            reply_markup=get_start_keyboard()
        )
        
        # Уведомляем администратора
        notification = f"""
🆕 <b>НОВАЯ АНКЕТА #{questionnaire_id}</b>

👤 <b>Пользователь:</b> @{user_data['username']}
👨‍💼 <b>ФИО:</b> {user_data['full_name']}
🏢 <b>Компания:</b> {user_data['company_name']}
📞 <b>Телефон:</b> {user_data['phone']}
📧 <b>Email:</b> {user_data['email']}
🎯 <b>Сфера:</b> {user_data['activity_sphere']}
💰 <b>Бюджет:</b> {user_data['contract_amount']}
📍 <b>Регионы:</b> {user_data['regions']}

📅 <b>Время подачи:</b> {datetime.now().strftime('%H:%M %d.%m.%Y')}
{'✅ В рабочее время' if is_working_hours() else '⏰ В нерабочее время'}
        """
        
        # Создаем inline-клавиатуру для быстрой отправки тендера
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="📤 Отправить тендер",
                        callback_data=f"send_tender_{questionnaire_id}"
                    ),
                    InlineKeyboardButton(
                        text="💬 Написать клиенту",
                        callback_data=f"write_{user_data['user_id']}"
                    )
                ]
            ]
        )
        
        await bot.send_message(config.ADMIN_ID, notification, reply_markup=keyboard)
        
        # Запускаем задачу для follow-up
        asyncio.create_task(schedule_follow_up(questionnaire_id, user_data['user_id']))
    else:
        await message.answer(
            "❌ <b>Ошибка сохранения анкеты.</b>\n\n"
            "Пожалуйста, попробуйте позже или свяжитесь с поддержкой.",
            reply_markup=get_start_keyboard()
        )
    
    await state.clear()

# =========== ОБРАБОТКА ОТВЕТОВ НА FOLLOW-UP ===========
@dp.message(F.text.contains("Да, нашел подходящее"))
async def handle_positive_response(message: types.Message):
    """Обработка положительного ответа"""
    with timing("Обработка положительного ответа"):
        # Получаем анкету пользователя
        questionnaire = await db.fetch_one(
            "SELECT id FROM questionnaires WHERE user_id = ? ORDER BY created_at DESC LIMIT 1",
            (message.from_user.id,)
        )
        
        if questionnaire:
            await db.update_follow_up(questionnaire['id'], "Да, нашел подходящее")
            
            await message.answer(
                "🎉 <b>Отлично!</b>\n\n"
                "Рады, что нашли подходящие тендеры!\n\n"
                "🤝 <b>Нужна помощь с подготовкой заявки?</b>\n"
                "Мы можем проконсультировать по:\n"
                "• Подготовке документов\n"
                "• Требованиям организаторов\n"
                "• Стратегии участия\n"
                "• Финансовому обеспечению\n\n"
                "Напишите <b>«Консультация»</b>, и мы свяжемся с вами в течение 15 минут!",
                reply_markup=get_start_keyboard()
            )
            
            # Уведомляем администратора
            await send_notification_to_admin(
                f"✅ <b>Пользователь нашел подходящие тендеры</b>\n\n"
                f"👤 @{message.from_user.username or message.from_user.id}\n"
                f"🆔 ID: {message.from_user.id}\n"
                f"📅 Время: {datetime.now().strftime('%H:%M %d.%m.%Y')}"
            )

@dp.message(F.text.contains("Нет, не нашел"))
async def handle_negative_response(message: types.Message):
    """Обработка отрицательного ответа"""
    questionnaire = await db.fetch_one(
        "SELECT id FROM questionnaires WHERE user_id = ? ORDER BY created_at DESC LIMIT 1",
        (message.from_user.id,)
    )
    
    if questionnaire:
        await db.update_follow_up(questionnaire['id'], "Нет, не нашел")
        
        await message.answer(
            "😕 <b>Жаль, что не нашли подходящее.</b>\n\n"
            "Мы учтем ваши пожелания и будем присылать новые тендеры по вашей сфере.\n\n"
            "📧 <b>Вы также будете получать:</b>\n"
            "• Полезные материалы по тендерам\n"
            "• Новости госзакупок\n"
            "• Советы по участию\n\n"
            "<i>Следующая рассылка через 3 дня.</i>",
            reply_markup=get_start_keyboard()
        )
        
        # Добавляем в группу для рассылки
        await db.execute_query(
            "UPDATE questionnaires SET mailing_group = 1 WHERE user_id = ?",
            (message.from_user.id,)
        )

@dp.message(F.text.contains("Нужна консультация"))
async def handle_consultation_from_followup(message: types.Message):
    """Обработка запроса на консультацию из follow-up"""
    await request_consultation(message)

# =========== АДМИН: ОТПРАВКА ТЕНДЕРА ===========
@dp.callback_query(F.data.startswith("send_tender_"))
async def handle_send_tender(callback: types.CallbackQuery, state: FSMContext):
    """Начало отправки тендера"""
    if callback.from_user.id != config.ADMIN_ID:
        await callback.answer("⛔ У вас нет прав для этого действия")
        return
    
    questionnaire_id = int(callback.data.split("_")[2])
    
    await state.update_data(questionnaire_id=questionnaire_id)
    
    await callback.message.answer(
        f"📤 <b>Отправка тендера для анкеты #{questionnaire_id}</b>\n\n"
        "Отправьте файл с тендерами (PDF, Excel, Word, ZIP):",
        reply_markup=get_cancel_keyboard()
    )
    
    await state.set_state(AdminAction.waiting_for_tender_file)
    await callback.answer()

@dp.message(AdminAction.waiting_for_tender_file, F.document)
async def process_tender_file(message: types.Message, state: FSMContext):
    """Обработка файла с тендерами"""
    user_data = await state.get_data()
    questionnaire_id = user_data['questionnaire_id']
    
    # Получаем данные анкеты
    questionnaire = await db.fetch_one(
        "SELECT user_id, email, full_name, company_name FROM questionnaires WHERE id = ?",
        (questionnaire_id,)
    )
    
    if not questionnaire:
        await message.answer("❌ Анкета не найдена.")
        await state.clear()
        return
    
    file_id = message.document.file_id
    file_name = message.document.file_name
    file_size = message.document.file_size or 0
    
    try:
        # Отправляем файл пользователю
        await bot.send_document(
            questionnaire['user_id'],
            file_id,
            caption=f"📊 <b>Ваша подборка тендеров готова!</b>\n\n"
                    f"Здравствуйте, {questionnaire['full_name']}!\n"
                    f"Мы подобрали для компании <b>«{questionnaire['company_name']}»</b> актуальные тендеры по вашим параметрам.\n\n"
                    f"📧 <b>Копия отправлена на email:</b> {questionnaire['email']}\n\n"
                    f"🔍 <b>Что в подборке:</b>\n"
                    f"• Релевантные тендеры\n"
                    f"• Сроки подачи заявок\n"
                    f"• Контакты организаторов\n"
                    f"• Рекомендации по участию\n\n"
                    f"<i>Через некоторое время мы спросим, удалось ли найти подходящее.</i>"
        )
        
        # Отмечаем отправку в базе
        await db.mark_tender_sent(questionnaire_id, message.from_user.id, file_id)
        
        await message.answer(
            f"✅ <b>Тендер отправлен пользователю</b>\n\n"
            f"👤 Пользователь: {questionnaire['full_name']}\n"
            f"🏢 Компания: {questionnaire['company_name']}\n"
            f"🆔 ID пользователя: {questionnaire['user_id']}\n"
            f"📧 Email: {questionnaire['email']}\n"
            f"📄 Файл: {file_name} ({file_size/1024:.1f} KB)\n\n"
            f"⏱️ <b>Follow-up будет отправлен автоматически.</b>",
            reply_markup=get_admin_keyboard()
        )
        
        # Запускаем follow-up
        asyncio.create_task(schedule_follow_up(questionnaire_id, questionnaire['user_id']))
        
    except Exception as e:
        await message.answer(f"❌ Ошибка отправки файла: {str(e)[:200]}", reply_markup=get_admin_keyboard())
        logger.error(f"Ошибка отправки тендера: {e}")
    
    await state.clear()

@dp.callback_query(F.data.startswith("write_"))
async def write_to_user(callback: types.CallbackQuery):
    """Написать пользователю"""
    if callback.from_user.id != config.ADMIN_ID:
        await callback.answer("⛔ У вас нет прав для этого действия")
        return
    
    user_id = int(callback.data.split("_")[1])
    
    # Здесь можно добавить логику для начала диалога с пользователем
    await callback.message.answer(
        f"💬 <b>Написать пользователю</b>\n\n"
        f"ID пользователя: {user_id}\n\n"
        f"Чтобы написать пользователю, просто отправьте ему сообщение в личные сообщения.\n"
        f"Или используйте команду /msg_{user_id} в этом чате."
    )
    
    await callback.answer()

# =========== РАССЫЛКИ ===========
MAILING_TEMPLATES = [
    {
        "subject": "Почему даже опытные специалисты пропускают выгодные тендеры?",
        "text": """Здравствуйте! 

Почему даже опытные специалисты пропускают выгодные тендеры? 

Часто причина в том, что:
1. Не успевают отслеживать все площадки
2. Не видят тендеры в смежных отраслях
3. Пропускают сжатые сроки подачи заявок

Читайте в нашем материале о том, как автоматизировать поиск тендеров и не упускать выгодные возможности: [ссылка]

А вы сталкивались с такой ситуацией? Поделитесь в ответе — какие сложности испытываете при поиске тендеров?"""
    },
    {
        "subject": "5 главных ошибок при участии в тендерах",
        "text": """Здравствуйте!

Мы проанализировали сотни заявок и выделили 5 главных ошибок, которые допускают компании при участии в тендерах:

1. Неполный пакет документов
2. Ошибки в техническом задании
3. Просроченные сроки подачи
4. Неправильное финансовое обеспечение
5. Отсутствие уникальности предложения

Читайте подробный разбор каждой ошибки и способы их избежать в нашем новом материале: [ссылка]

Что бы вы добавили в этот список?"""
    },
    {
        "subject": "Как увеличить шансы на победу в тендерах на 40%",
        "text": """Здравствуйте!

Знаете ли вы, что правильная подготовка документов увеличивает шансы на победу на 40%?

Мы подготовили чек-лист проверки документов, который включает:
✅ Проверку всех реквизитов
✅ Соответствие техническому заданию
✅ Правильность оформления заявки
✅ Сроки действия документов
✅ Наличие всех необходимых подписей и печатей

Скачайте чек-лист по ссылке: [ссылка]

Пользуетесь ли вы какими-то своими проверочными списками?"""
    },
    {
        "subject": "Нововведения в законодательстве о госзакупках",
        "text": """Здравствуйте!

С 1 января вступили в силу новые правила участия в госзакупках.

Основные изменения:
• Упрощение процедуры для малого бизнеса
• Новые требования к электронным подписям
• Изменения в сроках рассмотрения заявок
• Обновленные правила обеспечения заявок

Мы подготовили краткий обзор изменений с практическими рекомендациями: [ссылка]

Сталкивались ли вы уже с этими изменениями на практике?"""
    }
]

@dp.message(F.text == "📣 Начать рассылку")
async def start_mailing_menu(message: types.Message):
    """Меню рассылок"""
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📢 Шаблон 1", callback_data="mailing_0"),
                InlineKeyboardButton(text="📢 Шаблон 2", callback_data="mailing_1")
            ],
            [
                InlineKeyboardButton(text="📢 Шаблон 3", callback_data="mailing_2"),
                InlineKeyboardButton(text="📢 Шаблон 4", callback_data="mailing_3")
            ],
            [
                InlineKeyboardButton(text="✏️ Своя рассылка", callback_data="custom_mailing"),
                InlineKeyboardButton(text="📊 Статистика", callback_data="mailing_stats")
            ],
            [
                InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_admin")
            ]
        ]
    )
    
    await message.answer(
        "📣 <b>Управление рассылками</b>\n\n"
        "<b>Доступные шаблоны:</b>\n\n"
        "1. Почему даже опытные специалисты пропускают выгодные тендеры?\n"
        "2. 5 главных ошибок при участии в тендерах\n"
        "3. Как увеличить шансы на победу в тендерах на 40%\n"
        "4. Нововведения в законодательстве о госзакупках\n\n"
        "<i>Выберите действие:</i>",
        reply_markup=keyboard
    )

@dp.callback_query(F.data.startswith("mailing_"))
async def send_mailing_template(callback: types.CallbackQuery):
    """Отправка шаблонной рассылки"""
    template_index = int(callback.data.split("_")[1])
    
    if template_index >= len(MAILING_TEMPLATES):
        await callback.answer("❌ Шаблон не найден")
        return
    
    template = MAILING_TEMPLATES[template_index]
    
    # Получаем пользователей для рассылки (те, кто не отписался)
    users = await db.fetch_all(
        "SELECT DISTINCT user_id, username FROM questionnaires WHERE unsubscribe = 0 AND user_id IS NOT NULL"
    )
    
    if not users:
        await callback.message.answer("❌ Нет пользователей для рассылки")
        await callback.answer()
        return
    
    await callback.message.answer(f"🔄 Начинаю рассылку для {len(users)} пользователей...")
    await callback.answer("Рассылка начата")
    
    success_count = 0
    fail_count = 0
    
    for user in users:
        try:
            await bot.send_message(
                user['user_id'],
                template['text']
            )
            success_count += 1
            
            # Небольшая задержка для избежания лимитов
            if success_count % 10 == 0:
                await asyncio.sleep(0.5)
                
        except Exception as e:
            logger.error(f"Ошибка отправки рассылки пользователю {user['user_id']}: {e}")
            fail_count += 1
    
    # Сохраняем результаты
    await db.save_mailing(
        template['text'],
        f"template_{template_index}",
        len(users),
        success_count,
        fail_count
    )
    
    # Обновляем дату последней рассылки для пользователей
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    await db.execute_query(
        "UPDATE questionnaires SET last_mailing_date = ? WHERE unsubscribe = 0",
        (now,)
    )
    
    await callback.message.answer(
        f"✅ <b>Рассылка завершена!</b>\n\n"
        f"📢 Тема: {template['subject']}\n"
        f"👥 Всего получателей: {len(users)}\n"
        f"✅ Успешно отправлено: {success_count}\n"
        f"❌ Не отправлено: {fail_count}\n"
        f"📊 Процент доставки: {(success_count/len(users)*100):.1f}%\n\n"
        f"<i>Ответы пользователей будут приходить в этот чат.</i>"
    )

@dp.callback_query(F.data == "mailing_stats")
async def show_mailing_stats(callback: types.CallbackQuery):
    """Статистика рассылок"""
    # Получаем статистику последних 10 рассылок
    mailings = await db.fetch_all(
        "SELECT * FROM mailings ORDER BY mailing_date DESC LIMIT 10"
    )
    
    if not mailings:
        await callback.message.answer("📭 Нет данных о рассылках")
        await callback.answer()
        return
    
    response = "📊 <b>Статистика рассылок (последние 10)</b>\n\n"
    
    for i, mailing in enumerate(mailings, 1):
        date_str = mailing['mailing_date'][:16] if mailing['mailing_date'] else "Не указана"
        delivery_rate = (mailing['successful_sends'] / mailing['total_users'] * 100) if mailing['total_users'] > 0 else 0
        
        response += f"<b>{i}. {date_str}</b>\n"
        response += f"Тип: {mailing['message_type']}\n"
        response += f"Получателей: {mailing['total_users']}\n"
        response += f"Доставлено: {mailing['successful_sends']}\n"
        response += f"Ошибок: {mailing['failed_sends']}\n"
        response += f"Доставка: {delivery_rate:.1f}%\n"
        response += f"Ответов: {mailing['responses']}\n"
        response += "─" * 20 + "\n\n"
    
    await callback.message.answer(response)
    await callback.answer()

@dp.callback_query(F.data == "custom_mailing")
async def start_custom_mailing(callback: types.CallbackQuery, state: FSMContext):
    """Начать свою рассылку"""
    await callback.message.answer(
        "✏️ <b>Создание своей рассылки</b>\n\n"
        "Введите текст рассылки. Вы можете использовать HTML-разметку:\n"
        "<b>жирный</b>\n"
        "<i>курсив</i>\n"
        "<code>моноширинный</code>\n\n"
        "Пример:\n"
        "<b>Новое предложение!</b>\n"
        "Текст вашей рассылки...",
        reply_markup=get_cancel_keyboard()
    )
    
    await state.set_state(AdminAction.waiting_for_mailing_text)
    await callback.answer()

@dp.message(AdminAction.waiting_for_mailing_text)
async def process_custom_mailing(message: types.Message, state: FSMContext):
    """Обработка текста своей рассылки"""
    mailing_text = message.text
    
    # Получаем пользователей для рассылки
    users = await db.fetch_all(
        "SELECT DISTINCT user_id, username FROM questionnaires WHERE unsubscribe = 0 AND user_id IS NOT NULL"
    )
    
    if not users:
        await message.answer("❌ Нет пользователей для рассылки")
        await state.clear()
        return
    
    await message.answer(f"🔄 Начинаю рассылку для {len(users)} пользователей...")
    
    success_count = 0
    fail_count = 0
    
    for user in users:
        try:
            await bot.send_message(
                user['user_id'],
                mailing_text
            )
            success_count += 1
            
            if success_count % 10 == 0:
                await asyncio.sleep(0.5)
                
        except Exception as e:
            logger.error(f"Ошибка отправки своей рассылки пользователю {user['user_id']}: {e}")
            fail_count += 1
    
    # Сохраняем результаты
    await db.save_mailing(
        mailing_text,
        "custom",
        len(users),
        success_count,
        fail_count
    )
    
    await message.answer(
        f"✅ <b>Своя рассылка завершена!</b>\n\n"
        f"👥 Всего получателей: {len(users)}\n"
        f"✅ Успешно отправлено: {success_count}\n"
        f"❌ Не отправлено: {fail_count}\n"
        f"📊 Процент доставки: {(success_count/len(users)*100):.1f}%",
        reply_markup=get_admin_keyboard()
    )
    
    await state.clear()

# Обработка ответов на рассылки
@dp.message()
async def handle_all_messages(message: types.Message):
    """Обработка всех сообщений (включая ответы на рассылки)"""
    # Пропускаем команды
    if message.text and message.text.startswith('/'):
        return
    
    # Пропускаем сообщения от админа
    if message.from_user.id == config.ADMIN_ID:
        return
    
    # Проверяем, не является ли это ответом на рассылку
    # Ищем последнюю рассылку (за последние 7 дней)
    seven_days_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
    
    last_mailing = await db.fetch_one(
        "SELECT id FROM mailings WHERE mailing_date >= ? ORDER BY mailing_date DESC LIMIT 1",
        (seven_days_ago,)
    )
    
    if last_mailing:
        # Сохраняем ответ
        await db.save_mailing_response(
            last_mailing['id'],
            message.from_user.id,
            message.text
        )
        
        # Передаем ответ администратору
        await send_notification_to_admin(
            f"📨 <b>ОТВЕТ НА РАССЫЛКУ</b>\n\n"
            f"👤 Пользователь: @{message.from_user.username or 'без username'}\n"
            f"🆔 ID: {message.from_user.id}\n"
            f"👨‍💼 Имя: {message.from_user.full_name}\n\n"
            f"💬 <b>Ответ:</b>\n{message.text}"
        )

# =========== АДМИН: ОТЧЕТ ЭФФЕКТИВНОСТИ ===========
@dp.message(F.text == "📈 Отчет эффективности")
async def show_efficiency_report(message: types.Message):
    """Показать отчет эффективности"""
    # Получаем отчет за 14 дней
    report = await db.get_statistics_report(14)
    new_users = await db.get_new_users_count(14)
    
    # Получаем данные за сегодня
    today = datetime.now().strftime("%Y-%m-%d")
    today_stats = await db.fetch_one(
        "SELECT * FROM statistics WHERE date = ?",
        (today,)
    )
    
    # Получаем общее количество анкет
    total_questionnaires = await db.fetch_one(
        "SELECT COUNT(*) as count FROM questionnaires"
    )
    
    if report:
        questionnaires = report['questionnaires_completed'] or 0
        tenders = report['tenders_sent'] or 0
        follow_ups = report['follow_up_responses'] or 0
        consultations = report['consultation_requests'] or 0
        
        response = f"""
📊 <b>ОТЧЕТ ЭФФЕКТИВНОСТИ (14 дней)</b>

👥 <b>Пользователи:</b>
• Новые пользователи: {new_users}
• Всего анкет: {total_questionnaires['count'] if total_questionnaires else 0}
• Заполненных анкет: {questionnaires}

📤 <b>Выгрузки тендеров:</b>
• Отправлено выгрузок: {tenders}
• Ответов на follow-up: {follow_ups}

📞 <b>Консультации:</b>
• Запросов на консультацию: {consultations}

📢 <b>Рассылки:</b>
• Отправлено рассылок: {report['mailings_sent'] or 0}
• Ответов на рассылки: {report['mailing_responses'] or 0}

📈 <b>Конверсии:</b>
• Анкета → Выгрузка: {(tenders/questionnaires*100 if questionnaires > 0 else 0):.1f}%
• Выгрузка → Ответ: {(follow_ups/tenders*100 if tenders > 0 else 0):.1f}%
• Ответ → Консультация: {(consultations/follow_ups*100 if follow_ups > 0 else 0):.1f}%
"""
        
        # Добавляем статистику за сегодня
        if today_stats:
            response += f"\n📅 <b>Сегодня ({today}):</b>\n"
            if today_stats['new_users']:
                response += f"• Новые пользователи: {today_stats['new_users']}\n"
            if today_stats['questionnaires_completed']:
                response += f"• Новые анкеты: {today_stats['questionnaires_completed']}\n"
            if today_stats['tenders_sent']:
                response += f"• Отправлено тендеров: {today_stats['tenders_sent']}\n"
            if today_stats['consultation_requests']:
                response += f"• Запросы консультаций: {today_stats['consultation_requests']}\n"
    else:
        response = "📊 <b>Нет данных для отчета</b>"
    
    await message.answer(response)

# =========== АДМИН: НОВЫЕ АНКЕТЫ ===========
@dp.message(F.text == "📊 Новые анкеты")
async def show_new_questionnaires(message: types.Message):
    """Показать новые анкеты"""
    # Анкеты, где тендер еще не отправлен
    questionnaires = await db.fetch_all(
        "SELECT * FROM questionnaires WHERE tender_sent = 0 ORDER BY created_at DESC LIMIT 10"
    )
    
    if not questionnaires:
        await message.answer("📭 Нет новых анкет, ожидающих обработки.")
        return
    
    response = "🆕 <b>Новые анкеты (последние 10):</b>\n\n"
    
    for i, q in enumerate(questionnaires, 1):
        created_time = q['created_at'][11:16] if q['created_at'] else "??:??"
        response += f"<b>{i}. #{q['id']} - {q['company_name']}</b>\n"
        response += f"👤 {q['full_name']} (@{q['username']})\n"
        response += f"📞 {q['phone']}\n"
        response += f"📧 {q['email']}\n"
        response += f"🎯 {q['activity_sphere'][:30]}...\n"
        response += f"⏰ {created_time}\n"
        
        # Кнопки действий
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="📤 Отправить тендер",
                        callback_data=f"send_tender_{q['id']}"
                    ),
                    InlineKeyboardButton(
                        text="💬 Написать",
                        callback_data=f"write_{q['user_id']}"
                    )
                ]
            ]
        )
        
        if i == 1:
            await message.answer(response, reply_markup=keyboard)
            response = ""  # Сбрасываем для следующего сообщения
        else:
            # Для остальных анкет отправляем отдельными сообщениями
            await message.answer(response, reply_markup=keyboard)
            response = ""
    
    if response:
        await message.answer(response)

# =========== АДМИН: СКАЧАТЬ БАЗУ ===========
@dp.message(F.text == "📋 Скачать базу")
async def download_database(message: types.Message):
    """Скачать базу данных в CSV"""
    try:
        # Экспорт анкет
        questionnaires = await db.fetch_all(
            "SELECT * FROM questionnaires ORDER BY created_at DESC"
        )
        
        if questionnaires:
            output = StringIO()
            writer = csv.writer(output, delimiter=';', quotechar='"', quoting=csv.QUOTE_MINIMAL)
            
            # Заголовки
            writer.writerow([
                'ID', 'User ID', 'Username', 'ФИО', 'Компания', 'ИНН',
                'Контактное лицо', 'Телефон', 'Email', 'Сфера деятельности',
                'Ключевые слова', 'Бюджет', 'Регионы', 'Статус',
                'Тендер отправлен', 'Дата отправки', 'Follow-up ответ',
                'Запрос консультации', 'Отписан', 'Дата создания'
            ])
            
            # Данные
            for q in questionnaires:
                writer.writerow([
                    q['id'], q['user_id'], q['username'] or '', q['full_name'] or '',
                    q['company_name'] or '', q['inn'] or '', q['contact_person'] or '', 
                    q['phone'] or '', q['email'] or '', q['activity_sphere'] or '',
                    q['industry'] or '', q['contract_amount'] or '', q['regions'] or '',
                    q['status'] or '', 'Да' if q['tender_sent'] else 'Нет',
                    q['tender_sent_at'] or '', q['follow_up_response'] or '',
                    'Да' if q['consultation_requested'] else 'Нет',
                    'Да' if q['unsubscribe'] else 'Нет',
                    q['created_at'] or ''
                ])
            
            file = BufferedInputFile(
                output.getvalue().encode('utf-8-sig'),  # utf-8-sig для Excel
                filename=f"tenders_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
            )
            
            await message.answer_document(
                file,
                caption="📋 <b>Экспорт базы данных</b>\n\n"
                        f"Количество записей: {len(questionnaires)}\n"
                        f"Дата экспорта: {datetime.now().strftime('%d.%m.%Y %H:%M')}\n"
                        f"Формат: CSV с разделителем ';'"
            )
        else:
            await message.answer("📭 База данных пуста.")
            
    except Exception as e:
        logger.error(f"Ошибка экспорта базы: {e}")
        await message.answer(f"❌ Ошибка экспорта: {str(e)[:200]}")

# =========== АДМИН: НАСТРОЙКИ ===========
@dp.message(F.text == "⚙️ Настройки")
async def show_settings(message: types.Message):
    """Показать настройки бота"""
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🔄 Создать бэкап", callback_data="create_backup"),
                InlineKeyboardButton(text="📊 Статистика БД", callback_data="db_stats")
            ],
            [
                InlineKeyboardButton(text="🗑️ Очистить кэш", callback_data="clear_cache"),
                InlineKeyboardButton(text="📈 Логи", callback_data="show_logs")
            ],
            [
                InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_admin")
            ]
        ]
    )
    
    # Получаем статистику базы
    db_size = os.path.getsize(config.DB_PATH) if os.path.exists(config.DB_PATH) else 0
    log_size = os.path.getsize(os.path.join(config.LOGS_DIR, 'bot.log')) if os.path.exists(os.path.join(config.LOGS_DIR, 'bot.log')) else 0
    
    settings_text = f"""
⚙️ <b>Настройки бота</b>

<b>Системная информация:</b>
• ID администратора: {config.ADMIN_ID}
• Рабочее время: {config.WORK_START_HOUR}:00 - {config.WORK_END_HOUR}:00
• Дни работы: Пн-Пт

<b>База данных:</b>
• Путь: {config.DB_PATH}
• Размер: {db_size/1024/1024:.2f} MB
• Директория бэкапов: {config.BACKUP_DIR}

<b>Логи:</b>
• Директория: {config.LOGS_DIR}
• Размер логов: {log_size/1024/1024:.2f} MB

<b>Безопасность:</b>
• Лимит сообщений: {config.RATE_LIMIT} сек
• Валидация данных: Включена
    """
    
    await message.answer(settings_text, reply_markup=keyboard)

@dp.callback_query(F.data == "create_backup")
async def handle_create_backup(callback: types.CallbackQuery):
    """Создать бэкап базы данных"""
    await callback.message.answer("🔄 Создаю резервную копию базы данных...")
    
    backup_path = await create_backup()
    
    if backup_path:
        file = FSInputFile(backup_path)
        await callback.message.answer_document(
            file,
            caption=f"✅ <b>Бэкап создан успешно!</b>\n\n"
                    f"Файл: {os.path.basename(backup_path)}\n"
                    f"Время: {datetime.now().strftime('%H:%M:%S')}"
        )
    else:
        await callback.message.answer("❌ Не удалось создать бэкап")
    
    await callback.answer()

@dp.callback_query(F.data == "db_stats")
async def handle_db_stats(callback: types.CallbackQuery):
    """Показать статистику базы данных"""
    try:
        # Получаем различные статистики
        total_questionnaires = await db.fetch_one("SELECT COUNT(*) as count FROM questionnaires")
        total_mailings = await db.fetch_one("SELECT COUNT(*) as count FROM mailings")
        total_users = await db.fetch_one("SELECT COUNT(DISTINCT user_id) as count FROM questionnaires")
        active_users = await db.fetch_one("SELECT COUNT(DISTINCT user_id) as count FROM questionnaires WHERE unsubscribe = 0")
        tenders_sent = await db.fetch_one("SELECT COUNT(*) as count FROM questionnaires WHERE tender_sent = 1")
        
        # Последняя активность
        last_questionnaire = await db.fetch_one("SELECT created_at FROM questionnaires ORDER BY created_at DESC LIMIT 1")
        last_mailing = await db.fetch_one("SELECT mailing_date FROM mailings ORDER BY mailing_date DESC LIMIT 1")
        
        stats_text = f"""
📊 <b>Статистика базы данных</b>

<b>Анкеты:</b>
• Всего анкет: {total_questionnaires['count'] if total_questionnaires else 0}
• Отправлено тендеров: {tenders_sent['count'] if tenders_sent else 0}
• Последняя анкета: {last_questionnaire['created_at'][:16] if last_questionnaire and last_questionnaire['created_at'] else 'Нет данных'}

<b>Пользователи:</b>
• Всего пользователей: {total_users['count'] if total_users else 0}
• Активных пользователей: {active_users['count'] if active_users else 0}
• Отписавшихся: {(total_users['count'] if total_users else 0) - (active_users['count'] if active_users else 0)}

<b>Рассылки:</b>
• Всего рассылок: {total_mailings['count'] if total_mailings else 0}
• Последняя рассылка: {last_mailing['mailing_date'][:16] if last_mailing and last_mailing['mailing_date'] else 'Нет данных'}

<b>Размеры таблиц:</b>
"""
        
        # Получаем размеры таблиц
        tables = await db.fetch_all("SELECT name FROM sqlite_master WHERE type='table'")
        
        for table in tables:
            table_name = table['name']
            count = await db.fetch_one(f"SELECT COUNT(*) as count FROM {table_name}")
            stats_text += f"• {table_name}: {count['count']} записей\n"
        
        await callback.message.answer(stats_text)
        
    except Exception as e:
        await callback.message.answer(f"❌ Ошибка получения статистики: {str(e)[:200]}")
    
    await callback.answer()

@dp.callback_query(F.data == "clear_cache")
async def handle_clear_cache(callback: types.CallbackQuery):
    """Очистить кэш"""
    # Очищаем LRU кэш
    db.get_user_profile.cache_clear()
    
    await callback.message.answer("✅ Кэш успешно очищен")
    await callback.answer()

@dp.callback_query(F.data == "show_logs")
async def handle_show_logs(callback: types.CallbackQuery):
    """Показать последние логи"""
    log_file = os.path.join(config.LOGS_DIR, 'bot.log')
    
    if not os.path.exists(log_file):
        await callback.message.answer("📭 Файл логов не найден")
        await callback.answer()
        return
    
    try:
        # Читаем последние 50 строк логов
        with open(log_file, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        
        if len(lines) > 50:
            log_lines = lines[-50:]
        else:
            log_lines = lines
        
        log_text = "".join(log_lines)
        
        if len(log_text) > 4000:
            log_text = "...\n" + log_text[-4000:]
        
        await callback.message.answer(f"📋 <b>Последние логи (последние {len(log_lines)} строк):</b>\n\n<code>{log_text}</code>")
        
    except Exception as e:
        await callback.message.answer(f"❌ Ошибка чтения логов: {str(e)[:200]}")
    
    await callback.answer()

@dp.callback_query(F.data == "back_to_admin")
async def handle_back_to_admin(callback: types.CallbackQuery):
    """Вернуться в меню админа"""
    await cmd_admin(callback.message)
    await callback.answer()

# =========== АВТОМАТИЧЕСКИЕ РАССЫЛКИ ===========
async def scheduled_mailings():
    """Планировщик автоматических рассылок"""
    while True:
        try:
            now = datetime.now()
            
            # Проверяем, нужно ли отправлять рассылку (вторник и четверг в 11:00)
            if now.weekday() in [1, 3] and now.hour == 11 and now.minute == 0:
                logger.info("🔄 Начинаю автоматическую рассылку...")
                
                # Получаем пользователей для рассылки (кто не отписался)
                users = await db.fetch_all(
                    "SELECT DISTINCT user_id, username FROM questionnaires WHERE unsubscribe = 0 AND user_id IS NOT NULL"
                )
                
                if users:
                    # Выбираем случайный шаблон
                    template = random.choice(MAILING_TEMPLATES)
                    
                    success_count = 0
                    fail_count = 0
                    
                    for user in users:
                        try:
                            await bot.send_message(user['user_id'], template['text'])
                            success_count += 1
                            
                            if success_count % 10 == 0:
                                await asyncio.sleep(0.5)
                                
                        except Exception as e:
                            logger.error(f"Ошибка автоматической рассылки: {e}")
                            fail_count += 1
                    
                    # Сохраняем результаты
                    await db.save_mailing(
                        template['text'],
                        "auto_scheduled",
                        len(users),
                        success_count,
                        fail_count
                    )
                    
                    logger.info(f"✅ Автоматическая рассылка завершена: {success_count}/{len(users)}")
                    
                    # Уведомляем администратора
                    await send_notification_to_admin(
                        f"🤖 <b>АВТОМАТИЧЕСКАЯ РАССЫЛКА</b>\n\n"
                        f"📢 Тема: {template['subject']}\n"
                        f"👥 Получателей: {len(users)}\n"
                        f"✅ Успешно: {success_count}\n"
                        f"❌ Ошибок: {fail_count}\n"
                        f"📅 Дата: {now.strftime('%d.%m.%Y %H:%M')}"
                    )
                else:
                    logger.info("ℹ️ Нет пользователей для автоматической рассылки")
            
            # Ждем 1 минуту перед следующей проверкой
            await asyncio.sleep(60)
            
        except Exception as e:
            logger.error(f"Ошибка в планировщике рассылок: {e}")
            await asyncio.sleep(60)

# =========== АВТОМАТИЧЕСКИЕ FOLLOW-UP ===========
async def check_pending_follow_ups():
    """Проверка pending follow-up сообщений"""
    while True:
        try:
            # Получаем анкеты, для которых нужно отправить follow-up
            pending_follow_ups = await db.get_pending_follow_ups()
            
            for questionnaire in pending_follow_ups:
                tender_sent_at = questionnaire['tender_sent_at']
                if not tender_sent_at:
                    continue
                
                tender_time = datetime.strptime(tender_sent_at, "%Y-%m-%d %H:%M:%S")
                now = datetime.now()
                
                # Проверяем, прошло ли достаточно времени с момента отправки тендера
                time_diff = (now - tender_time).total_seconds()
                
                # Если прошло более 1 часа и это рабочее время
                if time_diff > 3600 and is_working_hours():
                    # Проверяем, не был ли уже отправлен follow-up
                    if not questionnaire['follow_up_sent']:
                        await bot.send_message(
                            questionnaire['user_id'],
                            "📊 Подборка тендеров отправлена. Удалось ли найти что-то подходящее?",
                            reply_markup=get_yes_no_keyboard()
                        )
                        
                        await db.update_follow_up(questionnaire['id'])
                        logger.info(f"✅ Автоматический follow-up отправлен для анкеты #{questionnaire['id']}")
            
            # Проверяем каждые 5 минут
            await asyncio.sleep(300)
            
        except Exception as e:
            logger.error(f"Ошибка в проверке pending follow-ups: {e}")
            await asyncio.sleep(300)

# =========== ЗАПУСК БОТА ===========
async def main():
    """Основная функция запуска бота"""
    logger.info("🚀 Запуск бота Тритики...")
    
    # Проверяем наличие необходимых директорий
    os.makedirs(config.BACKUP_DIR, exist_ok=True)
    os.makedirs(config.LOGS_DIR, exist_ok=True)
    
    # Инициализация базы данных
    logger.info("🔄 Инициализация базы данных...")
    with timing("Инициализация БД"):
        await db.init_db()
    
    # Создаем начальный бэкап
    logger.info("🔄 Создание начального бэкапа...")
    await create_backup()
    
    # Запускаем планировщики в фоне
    logger.info("🔄 Запуск фоновых задач...")
    asyncio.create_task(scheduled_mailings())
    asyncio.create_task(check_pending_follow_ups())
    
    # Запуск бота
    logger.info("🤖 Запуск polling...")
    try:
        await dp.start_polling(bot, skip_updates=True)
    except KeyboardInterrupt:
        logger.info("⏹️ Бот остановлен пользователем")
    except Exception as e:
        logger.error(f"❌ Критическая ошибка при запуске бота: {e}", exc_info=True)
        raise
    finally:
        await bot.session.close()
        logger.info("👋 Бот завершил работу")

if __name__ == "__main__":
    asyncio.run(main())
