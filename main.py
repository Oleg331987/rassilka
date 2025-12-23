import os
import logging
import asyncio
import sys
import json
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
from contextlib import asynccontextmanager

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

config = Config()

# =========== ЛОГИРОВАНИЕ ===========
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(config.LOGS_DIR, 'bot.log'), encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# =========== ИНИЦИАЛИЗАЦИЯ БОТА ===========
from aiogram import Bot, Dispatcher, types, F
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

bot = Bot(
    token=config.BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# =========== БАЗА ДАННЫХ ===========
import aiosqlite
import sqlite3

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
                responses_count INTEGER DEFAULT 0
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
    
    async def save_questionnaire(self, user_data: dict) -> Optional[int]:
        """Сохранение анкеты"""
        try:
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
            
            logger.info(f"✅ Анкета #{questionnaire_id} сохранена")
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
            
            if response.lower() in ['да', 'yes', 'удалось']:
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
        '''
        return await self.fetch_all(query)
    
    async def get_users_for_mailing(self, group: int = 0):
        """Получение пользователей для рассылки"""
        query = '''
        SELECT DISTINCT user_id, username 
        FROM questionnaires 
        WHERE user_id IS NOT NULL 
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

db = Database(config.DB_PATH)

# =========== КЛАВИАТУРЫ ===========
def get_start_keyboard():
    """Клавиатура при старте"""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📝 Заполнить анкету онлайн")],
            [KeyboardButton(text="📥 Скачать анкету")],
            [KeyboardButton(text="❓ Как это работает?")]
        ],
        resize_keyboard=True
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
            [KeyboardButton(text="📋 Скачать базу"), KeyboardButton(text="⚙️ Настройки")]
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

# =========== ПОМОЩНИКИ ===========
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
        return next_day.replace(hour=config.WORK_START_HOUR, minute=0, second=0)
    
    # Если сейчас не рабочий день
    days_to_add = 1
    while (now.weekday() + days_to_add) % 7 not in config.WORK_DAYS:
        days_to_add += 1
    
    next_day = now + timedelta(days=days_to_add)
    return next_day.replace(hour=config.WORK_START_HOUR, minute=0, second=0)

async def send_notification_to_admin(message_text: str):
    """Отправка уведомления администратору"""
    try:
        await bot.send_message(config.ADMIN_ID, message_text)
    except Exception as e:
        logger.error(f"❌ Ошибка отправки уведомления админу: {e}")

async def schedule_follow_up(questionnaire_id: int, user_id: int):
    """Планирование follow-up сообщения"""
    try:
        # Ждем 1 час
        await asyncio.sleep(3600)
        
        # Проверяем, был ли отправлен тендер
        questionnaire = await db.fetch_one(
            "SELECT tender_sent FROM questionnaires WHERE id = ?",
            (questionnaire_id,)
        )
        
        if questionnaire and questionnaire['tender_sent']:
            await bot.send_message(
                user_id,
                "📊 Подборка тендеров отправлена. Удалось ли найти что-то подходящее?",
                reply_markup=get_yes_no_keyboard()
            )
            
            await db.update_follow_up(questionnaire_id)
            
    except Exception as e:
        logger.error(f"❌ Ошибка в schedule_follow_up: {e}")

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
    # Создаем текстовый файл с анкетой
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
    if not inn.isdigit() or len(inn) not in (10, 12):
        await message.answer("❌ Неверный ИНН. Введите 10 или 12 цифр:")
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
    await state.update_data(phone=message.text.strip())
    await message.answer("✅ <b>Телефон сохранен</b>\n\nВведите email для отправки тендеров:")
    await state.set_state(Questionnaire.waiting_for_email)

@dp.message(Questionnaire.waiting_for_email)
async def process_email(message: types.Message, state: FSMContext):
    """Обработка email"""
    email = message.text.strip()
    if '@' not in email or '.' not in email:
        await message.answer("❌ Неверный email. Введите снова:")
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
    await message.answer("✅ <b>Ключевые слова сохранены</b>\n\nВведите бюджет контрактов (например: от 100 000 до 1 000 000 руб.):")
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
            f"<i>Следите за сообщениями!</i>"
        )
        
        # Уведомляем администратора
        notification = f"""
🆕 <b>НОВАЯ АНКЕТА #{questionnaire_id}</b>

👤 <b>Пользователь:</b> @{user_data['username']}
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
        
        # Запускаем задачу для follow-up через 1 час
        asyncio.create_task(schedule_follow_up(questionnaire_id, user_data['user_id']))
    else:
        await message.answer("❌ Ошибка сохранения анкеты. Пожалуйста, попробуйте позже.")
    
    await state.clear()

# =========== ОБРАБОТКА ОТВЕТОВ НА FOLLOW-UP ===========
@dp.message(F.text.contains("Да, нашел подходящее"))
async def handle_positive_response(message: types.Message):
    """Обработка положительного ответа"""
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
            "Напишите <b>«Консультация»</b>, и мы свяжемся с вами в течение 15 минут!"
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
            "<i>Следующая рассылка через 3 дня.</i>"
        )
        
        # Добавляем в группу для рассылки
        await db.execute_query(
            "UPDATE questionnaires SET mailing_group = 1 WHERE user_id = ?",
            (message.from_user.id,)
        )

@dp.message(F.text.contains("Консультация") | F.text.contains("консультация"))
async def handle_consultation_request(message: types.Message):
    """Обработка запроса на консультацию"""
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
        f"📅 Время: {datetime.now().strftime('%H:%M %d.%m.%Y')}\n\n"
        f"💬 Сообщение: {message.text}"
    )
    
    # Обновляем статистику
    await db.update_statistics('consultation_requests')

# =========== АДМИН: ОТПРАВКА ТЕНДЕРА ===========
@dp.callback_query(F.data.startswith("send_tender_"))
async def handle_send_tender(callback: types.CallbackQuery, state: FSMContext):
    """Начало отправки тендера"""
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
        "SELECT user_id, email FROM questionnaires WHERE id = ?",
        (questionnaire_id,)
    )
    
    if not questionnaire:
        await message.answer("❌ Анкета не найдена.")
        await state.clear()
        return
    
    file_id = message.document.file_id
    file_name = message.document.file_name
    
    try:
        # Отправляем файл пользователю
        await bot.send_document(
            questionnaire['user_id'],
            file_id,
            caption="📊 <b>Ваша подборка тендеров готова!</b>\n\n"
                    "Мы подобрали для вас актуальные тендеры по вашим параметрам.\n\n"
                    "📧 <b>Копия отправлена на email:</b> " + questionnaire['email'] + "\n\n"
                    "🔍 <b>Что в подборке:</b>\n"
                    "• Релевантные тендеры\n"
                    "• Сроки подачи заявок\n"
                    "• Контакты организаторов\n"
                    "• Рекомендации по участию\n\n"
                    "<i>Через 1 час спросим, удалось ли найти подходящее.</i>"
        )
        
        # Отмечаем отправку в базе
        await db.mark_tender_sent(questionnaire_id, message.from_user.id, file_id)
        
        await message.answer(
            f"✅ <b>Тендер отправлен пользователю</b>\n\n"
            f"👤 ID пользователя: {questionnaire['user_id']}\n"
            f"📧 Email: {questionnaire['email']}\n"
            f"📄 Файл: {file_name}\n\n"
            f"⏱️ <b>Через 1 час пользователю будет отправлен follow-up вопрос.</b>"
        )
        
        # Запускаем follow-up через 1 час
        asyncio.create_task(schedule_follow_up(questionnaire_id, questionnaire['user_id']))
        
    except Exception as e:
        await message.answer(f"❌ Ошибка отправки файла: {e}")
    
    await state.clear()

# =========== АДМИН: РАССЫЛКИ ===========
# Список тем для рассылок
MAILING_TEMPLATES = [
    {
        "subject": "Почему даже опытные специалисты пропускают выгодные тендеры?",
        "text": "Здравствуйте! Почему даже опытные специалисты пропускают выгодные тендеры? Читайте в нашем материале: [ссылка/текст].\n\nА вы сталкивались с такой ситуацией? Поделитесь в ответе — какие сложности испытываете при поиске тендеров?"
    },
    {
        "subject": "5 главных ошибок при участии в тендерах",
        "text": "Здравствуйте! Мы проанализировали сотни заявок и выделили 5 главных ошибок, которые допускают компании при участии в тендерах.\n\nЧитайте в нашем новом материале: [ссылка]\n\nЧто бы вы добавили в этот список?"
    },
    {
        "subject": "Как увеличить шансы на победу в тендерах на 40%",
        "text": "Здравствуйте! Знаете ли вы, что правильная подготовка документов увеличивает шансы на победу на 40%?\n\nМы подготовили чек-лист проверки документов: [ссылка]\n\nПользуетесь ли вы какими-то своими проверочными списками?"
    },
    {
        "subject": "Нововведения в законодательстве о госзакупках",
        "text": "Здравствуйте! С 1 января вступили в силу новые правила участия в госзакупках.\n\nМы подготовили краткий обзор изменений: [ссылка]\n\nСталкивались ли вы уже с этими изменениями на практике?"
    }
]

@dp.message(F.text == "📣 Начать рассылку")
async def start_mailing_menu(message: types.Message):
    """Меню рассылок"""
    if message.from_user.id != config.ADMIN_ID:
        return
    
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📢 Рассылка 1", callback_data="mailing_0"),
                InlineKeyboardButton(text="📢 Рассылка 2", callback_data="mailing_1")
            ],
            [
                InlineKeyboardButton(text="📢 Рассылка 3", callback_data="mailing_2"),
                InlineKeyboardButton(text="📢 Рассылка 4", callback_data="mailing_3")
            ],
            [
                InlineKeyboardButton(text="✏️ Своя рассылка", callback_data="custom_mailing"),
                InlineKeyboardButton(text="📊 Статистика рассылок", callback_data="mailing_stats")
            ]
        ]
    )
    
    await message.answer(
        "📣 <b>Управление рассылками</b>\n\n"
        "<b>Шаблоны рассылок (2 раза в неделю):</b>\n\n"
        "1. Почему даже опытные специалисты пропускают выгодные тендеры?\n"
        "2. 5 главных ошибок при участии в тендерах\n"
        "3. Как увеличить шансы на победу в тендерах на 40%\n"
        "4. Нововведения в законодательстве о госзакупках\n\n"
        "<i>Выберите рассылку для отправки:</i>",
        reply_markup=keyboard
    )

@dp.callback_query(F.data.startswith("mailing_"))
async def send_mailing_template(callback: types.CallbackQuery):
    """Отправка шаблонной рассылки"""
    if callback.from_user.id != config.ADMIN_ID:
        return
    
    template_index = int(callback.data.split("_")[1])
    
    if template_index >= len(MAILING_TEMPLATES):
        await callback.answer("❌ Шаблон не найден")
        return
    
    template = MAILING_TEMPLATES[template_index]
    
    # Получаем пользователей для рассылки (те, кто не ответил "да" на follow-up)
    users = await db.get_users_for_mailing(group=1)
    
    if not users:
        await callback.answer("❌ Нет пользователей для рассылки")
        return
    
    await callback.message.answer(f"🔄 Начинаю рассылку для {len(users)} пользователей...")
    
    success_count = 0
    fail_count = 0
    
    for user in users:
        try:
            await bot.send_message(
                user['user_id'],
                template['text']
            )
            success_count += 1
            await asyncio.sleep(0.1)  # Задержка для избежания лимитов
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
    
    await callback.message.answer(
        f"✅ <b>Рассылка завершена!</b>\n\n"
        f"📢 Тема: {template['subject']}\n"
        f"👥 Получателей: {len(users)}\n"
        f"✅ Успешно: {success_count}\n"
        f"❌ Ошибок: {fail_count}\n\n"
        f"<i>Ответы пользователей будут приходить в этот чат.</i>"
    )
    
    await callback.answer()

# Обработка ответов на рассылки
@dp.message()
async def handle_mailing_response(message: types.Message):
    """Обработка ответов на рассылки"""
    # Проверяем, не является ли сообщение командой
    if message.text.startswith('/'):
        return
    
    # Проверяем, админ ли это
    if message.from_user.id == config.ADMIN_ID:
        return
    
    # Получаем последнюю рассылку
    last_mailing = await db.fetch_one(
        "SELECT id FROM mailings ORDER BY mailing_date DESC LIMIT 1"
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
            f"👤 Пользователь: @{message.from_user.username or message.from_user.id}\n"
            f"🆔 ID: {message.from_user.id}\n\n"
            f"💬 Ответ:\n{message.text}"
        )

# =========== АДМИН: ОТЧЕТ ЭФФЕКТИВНОСТИ ===========
@dp.message(F.text == "📈 Отчет эффективности")
async def show_efficiency_report(message: types.Message):
    """Показать отчет эффективности"""
    if message.from_user.id != config.ADMIN_ID:
        return
    
    # Получаем отчет за 14 дней
    report = await db.get_statistics_report(14)
    new_users = await db.get_new_users_count(14)
    
    if report:
        response = f"""
📊 <b>ОТЧЕТ ЭФФЕКТИВНОСТИ (14 дней)</b>

👥 <b>Пользователи:</b>
• Новые пользователи: {new_users}
• Заполненных анкет: {report['questionnaires_completed'] or 0}

📤 <b>Выгрузки тендеров:</b>
• Отправлено выгрузок: {report['tenders_sent'] or 0}
• Ответов на follow-up: {report['follow_up_responses'] or 0}

📞 <b>Консультации:</b>
• Запросов на консультацию: {report['consultation_requests'] or 0}

📢 <b>Рассылки:</b>
• Отправлено рассылок: {report['mailings_sent'] or 0}
• Ответов на рассылки: {report['mailing_responses'] or 0}

📈 <b>Конверсии:</b>
• Анкета → Выгрузка: {((report['tenders_sent'] or 0) / (report['questionnaires_completed'] or 1) * 100):.1f}%
• Выгрузка → Ответ: {((report['follow_up_responses'] or 0) / (report['tenders_sent'] or 1) * 100):.1f}%
• Ответ → Консультация: {((report['consultation_requests'] or 0) / (report['follow_up_responses'] or 1) * 100):.1f}%
"""
    else:
        response = "📊 <b>Нет данных для отчета</b>"
    
    await message.answer(response)

# =========== АДМИН: НОВЫЕ АНКЕТЫ ===========
@dp.message(F.text == "📊 Новые анкеты")
async def show_new_questionnaires(message: types.Message):
    """Показать новые анкеты"""
    if message.from_user.id != config.ADMIN_ID:
        return
    
    questionnaires = await db.fetch_all(
        "SELECT * FROM questionnaires WHERE tender_sent = 0 ORDER BY created_at DESC LIMIT 10"
    )
    
    if not questionnaires:
        await message.answer("📭 Нет новых анкет.")
        return
    
    response = "🆕 <b>Новые анкеты (последние 10):</b>\n\n"
    
    for q in questionnaires:
        response += f"""
<b>#{q['id']}</b> - {q['company_name']}
👤 @{q['username']} | 📞 {q['phone']}
📧 {q['email']}
🎯 {q['activity_sphere'][:30]}...
📅 {q['created_at'][:16]}

"""
    
    await message.answer(response)

# =========== АДМИН: СКАЧАТЬ БАЗУ ===========
import csv
from io import StringIO

@dp.message(F.text == "📋 Скачать базу")
async def download_database(message: types.Message):
    """Скачать базу данных в CSV"""
    if message.from_user.id != config.ADMIN_ID:
        return
    
    try:
        # Экспорт анкет
        questionnaires = await db.fetch_all(
            "SELECT * FROM questionnaires ORDER BY created_at DESC"
        )
        
        if questionnaires:
            output = StringIO()
            writer = csv.writer(output)
            
            # Заголовки
            writer.writerow([
                'ID', 'User ID', 'Username', 'ФИО', 'Компания', 'ИНН',
                'Контактное лицо', 'Телефон', 'Email', 'Сфера деятельности',
                'Ключевые слова', 'Бюджет', 'Регионы', 'Статус',
                'Тендер отправлен', 'Дата отправки', 'Follow-up ответ',
                'Запрос консультации', 'Дата создания'
            ])
            
            # Данные
            for q in questionnaires:
                writer.writerow([
                    q['id'], q['user_id'], q['username'], q['full_name'],
                    q['company_name'], q['inn'], q['contact_person'], q['phone'],
                    q['email'], q['activity_sphere'], q['industry'],
                    q['contract_amount'], q['regions'], q['status'],
                    'Да' if q['tender_sent'] else 'Нет',
                    q['tender_sent_at'] or '',
                    q['follow_up_response'] or '',
                    'Да' if q['consultation_requested'] else 'Нет',
                    q['created_at']
                ])
            
            file = BufferedInputFile(
                output.getvalue().encode('utf-8'),
                filename=f"database_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
            )
            
            await message.answer_document(
                file,
                caption="📋 <b>Экспорт базы данных</b>\n\n"
                        f"Количество записей: {len(questionnaires)}\n"
                        f"Дата экспорта: {datetime.now().strftime('%d.%m.%Y %H:%M')}"
            )
        else:
            await message.answer("📭 База данных пуста.")
            
    except Exception as e:
        logger.error(f"Ошибка экспорта базы: {e}")
        await message.answer(f"❌ Ошибка экспорта: {e}")

# =========== АВТОМАТИЧЕСКИЕ РАССЫЛКИ ===========
async def scheduled_mailings():
    """Планировщик автоматических рассылок"""
    while True:
        try:
            now = datetime.now()
            
            # Проверяем, нужна ли рассылка (например, вторник и четверг в 10:00)
            if now.weekday() in [1, 3] and now.hour == 10 and now.minute == 0:
                logger.info("🔄 Начинаю автоматическую рассылку...")
                
                # Получаем пользователей для рассылки
                users = await db.get_users_for_mailing(group=1)
                
                if users:
                    # Выбираем случайный шаблон
                    import random
                    template = random.choice(MAILING_TEMPLATES)
                    
                    success_count = 0
                    fail_count = 0
                    
                    for user in users:
                        try:
                            await bot.send_message(user['user_id'], template['text'])
                            success_count += 1
                            await asyncio.sleep(0.1)
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
            
            # Ждем 1 минуту перед следующей проверкой
            await asyncio.sleep(60)
            
        except Exception as e:
            logger.error(f"Ошибка в планировщике рассылок: {e}")
            await asyncio.sleep(60)

# =========== ЗАПУСК БОТА ===========
async def main():
    """Основная функция"""
    logger.info("🚀 Запуск бота ТендерПоиск...")
    
    # Инициализация базы данных
    await db.init_db()
    
    # Запускаем планировщик рассылок в фоне
    asyncio.create_task(scheduled_mailings())
    
    # Запуск бота
    try:
        await dp.start_polling(bot, skip_updates=True)
    except Exception as e:
        logger.error(f"❌ Ошибка запуска бота: {e}")
        raise

if __name__ == "__main__":
    # Создаем необходимые директории
    os.makedirs(config.BACKUP_DIR, exist_ok=True)
    os.makedirs(config.LOGS_DIR, exist_ok=True)
    
    # Запускаем бота
    asyncio.run(main())
