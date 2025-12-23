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
    ReplyKeyboardRemove, BufferedInputFile
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
                created_at TEXT,
                admin_comment TEXT,
                feedback_given BOOLEAN DEFAULT 0,
                feedback_date TEXT,
                feedback_text TEXT,
                updated_at TEXT
            )
            ''')
            
            # Таблица сообщений
            await conn.execute('''
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                from_id INTEGER,
                to_id INTEGER,
                message_text TEXT,
                created_at TEXT
            )
            ''')
            
            # Таблица рассылок
            await conn.execute('''
            CREATE TABLE IF NOT EXISTS mailings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mailing_date TEXT,
                message_text TEXT,
                total_users INTEGER,
                successful_sends INTEGER,
                failed_sends INTEGER
            )
            ''')
            
            # Создаем индексы
            await conn.execute('CREATE INDEX IF NOT EXISTS idx_user_id ON questionnaires (user_id)')
            await conn.execute('CREATE INDEX IF NOT EXISTS idx_status ON questionnaires (status)')
            await conn.execute('CREATE INDEX IF NOT EXISTS idx_created_at ON questionnaires (created_at)')
            
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
        """Сохранение анкеты в базу данных"""
        try:
            query = '''
            INSERT INTO questionnaires 
            (user_id, username, full_name, company_name, inn, contact_person, phone, email, 
             activity_sphere, industry, contract_amount, regions, created_at, updated_at)
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
            
            logger.info(f"✅ Анкета #{questionnaire_id} сохранена")
            return questionnaire_id
            
        except Exception as e:
            logger.error(f"❌ Ошибка сохранения анкеты: {e}")
            return None
    
    async def get_questionnaires(self, status: Optional[str] = None, page: int = 1, per_page: int = 10):
        """Получение анкет с пагинацией"""
        try:
            offset = (page - 1) * per_page
            
            if status:
                query = "SELECT * FROM questionnaires WHERE status = ? ORDER BY created_at DESC LIMIT ? OFFSET ?"
                params = (status, per_page, offset)
            else:
                query = "SELECT * FROM questionnaires ORDER BY created_at DESC LIMIT ? OFFSET ?"
                params = (per_page, offset)
            
            questionnaires = await self.fetch_all(query, params)
            return questionnaires
            
        except Exception as e:
            logger.error(f"❌ Ошибка получения анкет: {e}")
            return []
    
    async def get_user_questionnaire(self, user_id: int):
        """Получение анкеты пользователя"""
        query = "SELECT * FROM questionnaires WHERE user_id = ? ORDER BY created_at DESC LIMIT 1"
        return await self.fetch_one(query, (user_id,))
    
    async def get_all_users(self):
        """Получение всех пользователей"""
        query = "SELECT DISTINCT user_id FROM questionnaires WHERE user_id IS NOT NULL"
        rows = await self.fetch_all(query)
        return [row['user_id'] for row in rows]
    
    async def update_questionnaire_status(self, questionnaire_id: int, status: str, comment: str = None):
        """Обновление статуса анкеты"""
        try:
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            
            if comment:
                query = "UPDATE questionnaires SET status = ?, admin_comment = ?, updated_at = ? WHERE id = ?"
                params = (status, comment, now, questionnaire_id)
            else:
                query = "UPDATE questionnaires SET status = ?, updated_at = ? WHERE id = ?"
                params = (status, now, questionnaire_id)
            
            await self.execute_query(query, params)
            logger.info(f"✅ Статус анкеты #{questionnaire_id} обновлен")
            return True
        except Exception as e:
            logger.error(f"❌ Ошибка обновления статуса: {e}")
            return False
    
    async def save_message(self, from_id: int, to_id: int, message_text: str):
        """Сохранение сообщения"""
        query = "INSERT INTO messages (from_id, to_id, message_text, created_at) VALUES (?, ?, ?, ?)"
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        await self.execute_query(query, (from_id, to_id, message_text, now))
    
    async def save_mailing(self, message_text: str, total_users: int, successful: int, failed: int):
        """Сохранение информации о рассылке"""
        query = '''
        INSERT INTO mailings (mailing_date, message_text, total_users, successful_sends, failed_sends)
        VALUES (?, ?, ?, ?, ?)
        '''
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        await self.execute_query(query, (now, message_text, total_users, successful, failed))

db = Database(config.DB_PATH)

# =========== КЛАВИАТУРЫ ===========
def get_main_keyboard():
    """Главное меню для пользователей"""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📝 Заполнить анкету")],
            [KeyboardButton(text="📋 Моя анкета"), KeyboardButton(text="📨 Написать менеджеру")],
            [KeyboardButton(text="💬 Оставить отзыв"), KeyboardButton(text="📊 Статус заявок")],
            [KeyboardButton(text="ℹ️ О компании"), KeyboardButton(text="❓ Помощь")]
        ],
        resize_keyboard=True
    )

def get_admin_keyboard():
    """Клавиатура администратора"""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📊 Все заявки"), KeyboardButton(text="🆕 Новые заявки")],
            [KeyboardButton(text="✅ Обработанные"), KeyboardButton(text="📁 Архив")],
            [KeyboardButton(text="📤 Рассылка"), KeyboardButton(text="📈 Статистика")],
            [KeyboardButton(text="👥 Пользователи"), KeyboardButton(text="🔧 Управление")],
            [KeyboardButton(text="⬅️ В меню")]
        ],
        resize_keyboard=True
    )

def get_cancel_keyboard():
    """Клавиатура отмены"""
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="❌ Отменить")]],
        resize_keyboard=True
    )

def get_questionnaire_detail_keyboard(questionnaire_id: int, page: int = 1, status: str = None):
    """Клавиатура для детального просмотра анкеты"""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Обработано", callback_data=f"status_{questionnaire_id}_processed"),
                InlineKeyboardButton(text="📁 Архив", callback_data=f"status_{questionnaire_id}_archived")
            ],
            [
                InlineKeyboardButton(text="💬 Комментарий", callback_data=f"comment_{questionnaire_id}"),
                InlineKeyboardButton(text="📨 Написать", callback_data=f"write_{questionnaire_id}")
            ],
            [
                InlineKeyboardButton(text="⬅️ Назад", callback_data=f"back_{page}_{status}")
            ]
        ]
    )

def get_pagination_keyboard(page: int, total_pages: int, status: str = None):
    """Клавиатура пагинации"""
    buttons = []
    
    if page > 1:
        callback_data = f"page_{page-1}_{status}" if status else f"page_{page-1}"
        buttons.append(InlineKeyboardButton(text="⬅️", callback_data=callback_data))
    
    buttons.append(InlineKeyboardButton(text=f"{page}/{total_pages}", callback_data="current"))
    
    if page < total_pages:
        callback_data = f"page_{page+1}_{status}" if status else f"page_{page+1}"
        buttons.append(InlineKeyboardButton(text="➡️", callback_data=callback_data))
    
    return InlineKeyboardMarkup(inline_keyboard=[buttons])

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
    waiting_for_mailing_text = State()
    waiting_for_comment = State()
    waiting_for_message_to_user = State()

class UserMessageToAdmin(StatesGroup):
    waiting_for_message_text = State()

# =========== ПОМОЩНИКИ ===========
async def send_notification_to_admin(message_text: str):
    """Отправка уведомления администратору"""
    try:
        await bot.send_message(config.ADMIN_ID, message_text)
    except Exception as e:
        logger.error(f"❌ Ошибка отправки уведомления админу: {e}")

def format_questionnaire(questionnaire) -> str:
    """Форматирование анкеты"""
    status_icons = {'new': '🆕', 'processed': '✅', 'archived': '📁'}
    status_icon = status_icons.get(questionnaire['status'], '📋')
    
    return f"""
{status_icon} <b>Анкета #{questionnaire['id']}</b>

<b>👤 Данные клиента:</b>
• ID: {questionnaire['user_id']}
• Username: @{questionnaire['username']}
• ФИО: {questionnaire['full_name']}
• Компания: {questionnaire['company_name']}
• ИНН: {questionnaire['inn']}
• Контакт: {questionnaire['contact_person']}
• Телефон: {questionnaire['phone']}
• Email: {questionnaire['email']}

<b>📊 Параметры поиска:</b>
• Сфера: {questionnaire['activity_sphere']}
• Ключевые слова: {questionnaire['industry']}
• Бюджет: {questionnaire['contract_amount']}
• Регионы: {questionnaire['regions']}

<b>📈 Статус:</b> {questionnaire['status']} {status_icon}
<b>📅 Создана:</b> {questionnaire['created_at'][:16]}
"""

# =========== ОБРАБОТЧИКИ КОМАНД ===========
@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    """Обработка команды /start"""
    await state.clear()
    
    if message.from_user.id == config.ADMIN_ID:
        await message.answer(
            "👑 <b>Панель администратора</b>\n\n"
            "Добро пожаловать в админ-панель!",
            reply_markup=get_admin_keyboard()
        )
    else:
        await message.answer(
            "🏢 <b>Добро пожаловать в бот ООО 'Тритика'!</b>\n\n"
            "Мы помогаем находить выгодные тендеры для вашего бизнеса.",
            reply_markup=get_main_keyboard()
        )

@dp.message(F.text == "⬅️ В меню")
async def back_to_menu(message: types.Message, state: FSMContext):
    """Возврат в меню"""
    await cmd_start(message, state)

@dp.message(F.text == "❌ Отменить")
async def cancel_action(message: types.Message, state: FSMContext):
    """Отмена действия"""
    await state.clear()
    
    if message.from_user.id == config.ADMIN_ID:
        await message.answer("❌ Действие отменено.", reply_markup=get_admin_keyboard())
    else:
        await message.answer("❌ Действие отменено.", reply_markup=get_main_keyboard())

# =========== ЗАПОЛНЕНИЕ АНКЕТЫ ===========
@dp.message(F.text == "📝 Заполнить анкету")
async def start_questionnaire(message: types.Message, state: FSMContext):
    """Начало заполнения анкеты"""
    if message.from_user.id == config.ADMIN_ID:
        await message.answer("Вы администратор.", reply_markup=get_admin_keyboard())
        return
    
    await message.answer(
        "📋 <b>Начинаем заполнение анкеты!</b>\n\n"
        "Введите ваше ФИО полностью:",
        reply_markup=get_cancel_keyboard()
    )
    await state.set_state(Questionnaire.waiting_for_name)

@dp.message(Questionnaire.waiting_for_name)
async def process_name(message: types.Message, state: FSMContext):
    """Обработка ФИО"""
    if message.text == "❌ Отменить":
        await cancel_action(message, state)
        return
    
    await state.update_data(
        full_name=message.text.strip(),
        user_id=message.from_user.id,
        username=message.from_user.username or "Не указан"
    )
    await message.answer("✅ <b>ФИО сохранено</b>\n\nВведите название компании:")
    await state.set_state(Questionnaire.waiting_for_company)

@dp.message(Questionnaire.waiting_for_company)
async def process_company(message: types.Message, state: FSMContext):
    """Обработка названия компании"""
    if message.text == "❌ Отменить":
        await cancel_action(message, state)
        return
    
    await state.update_data(company_name=message.text.strip())
    await message.answer("✅ <b>Компания сохранена</b>\n\nВведите ИНН:")
    await state.set_state(Questionnaire.waiting_for_inn)

@dp.message(Questionnaire.waiting_for_inn)
async def process_inn(message: types.Message, state: FSMContext):
    """Обработка ИНН"""
    if message.text == "❌ Отменить":
        await cancel_action(message, state)
        return
    
    inn = message.text.strip()
    if not inn.isdigit() or len(inn) not in (10, 12):
        await message.answer("❌ Неверный ИНН. Введите 10 или 12 цифр:")
        return
    
    await state.update_data(inn=inn)
    await message.answer("✅ <b>ИНН сохранен</b>\n\nВведите контактное лицо:")
    await state.set_state(Questionnaire.waiting_for_contact)

@dp.message(Questionnaire.waiting_for_contact)
async def process_contact(message: types.Message, state: FSMContext):
    """Обработка контактного лица"""
    if message.text == "❌ Отменить":
        await cancel_action(message, state)
        return
    
    await state.update_data(contact_person=message.text.strip())
    await message.answer("✅ <b>Контакт сохранен</b>\n\nВведите телефон:")
    await state.set_state(Questionnaire.waiting_for_phone)

@dp.message(Questionnaire.waiting_for_phone)
async def process_phone(message: types.Message, state: FSMContext):
    """Обработка телефона"""
    if message.text == "❌ Отменить":
        await cancel_action(message, state)
        return
    
    await state.update_data(phone=message.text.strip())
    await message.answer("✅ <b>Телефон сохранен</b>\n\nВведите email:")
    await state.set_state(Questionnaire.waiting_for_email)

@dp.message(Questionnaire.waiting_for_email)
async def process_email(message: types.Message, state: FSMContext):
    """Обработка email"""
    if message.text == "❌ Отменить":
        await cancel_action(message, state)
        return
    
    email = message.text.strip()
    if '@' not in email or '.' not in email:
        await message.answer("❌ Неверный email. Введите снова:")
        return
    
    await state.update_data(email=email)
    await message.answer("✅ <b>Email сохранен</b>\n\nВведите сферу деятельности:")
    await state.set_state(Questionnaire.waiting_for_activity)

@dp.message(Questionnaire.waiting_for_activity)
async def process_activity(message: types.Message, state: FSMContext):
    """Обработка сферы деятельности"""
    if message.text == "❌ Отменить":
        await cancel_action(message, state)
        return
    
    await state.update_data(activity_sphere=message.text.strip())
    await message.answer("✅ <b>Сфера сохранена</b>\n\nВведите ключевые слова:")
    await state.set_state(Questionnaire.waiting_for_industry)

@dp.message(Questionnaire.waiting_for_industry)
async def process_industry(message: types.Message, state: FSMContext):
    """Обработка ключевых слов"""
    if message.text == "❌ Отменить":
        await cancel_action(message, state)
        return
    
    await state.update_data(industry=message.text.strip())
    await message.answer("✅ <b>Ключевые слова сохранены</b>\n\nВведите бюджет контрактов:")
    await state.set_state(Questionnaire.waiting_for_amount)

@dp.message(Questionnaire.waiting_for_amount)
async def process_amount(message: types.Message, state: FSMContext):
    """Обработка бюджета"""
    if message.text == "❌ Отменить":
        await cancel_action(message, state)
        return
    
    await state.update_data(contract_amount=message.text.strip())
    await message.answer("✅ <b>Бюджет сохранен</b>\n\nВведите регионы работы:")
    await state.set_state(Questionnaire.waiting_for_regions)

@dp.message(Questionnaire.waiting_for_regions)
async def process_regions(message: types.Message, state: FSMContext):
    """Завершение анкеты"""
    if message.text == "❌ Отменить":
        await cancel_action(message, state)
        return
    
    user_data = await state.get_data()
    user_data['regions'] = message.text.strip()
    
    # Сохраняем анкету
    questionnaire_id = await db.save_questionnaire(user_data)
    
    if questionnaire_id:
        await message.answer(
            "✅ <b>Анкета сохранена!</b>\n\n"
            "Мы свяжемся с вами в ближайшее время.",
            reply_markup=get_main_keyboard()
        )
        
        # Уведомляем администратора
        notification = f"""
🆕 <b>НОВАЯ АНКЕТА #{questionnaire_id}</b>

👤 Пользователь: @{user_data['username']}
🏢 Компания: {user_data['company_name']}
📞 Телефон: {user_data['phone']}
📅 Время: {datetime.now().strftime('%H:%M %d.%m.%Y')}
        """
        await send_notification_to_admin(notification)
    else:
        await message.answer("❌ Ошибка сохранения анкеты.", reply_markup=get_main_keyboard())
    
    await state.clear()

# =========== ПОЛЬЗОВАТЕЛЬ: МОЯ АНКЕТА ===========
@dp.message(F.text == "📋 Моя анкета")
async def my_questionnaire(message: types.Message):
    """Просмотр своей анкеты"""
    if message.from_user.id == config.ADMIN_ID:
        await message.answer("Вы администратор.", reply_markup=get_admin_keyboard())
        return
    
    questionnaire = await db.get_user_questionnaire(message.from_user.id)
    
    if not questionnaire:
        await message.answer(
            "📭 У вас нет заполненной анкеты.",
            reply_markup=get_main_keyboard()
        )
        return
    
    response = format_questionnaire(questionnaire)
    await message.answer(response, reply_markup=get_main_keyboard())

# =========== ПОЛЬЗОВАТЕЛЬ: НАПИСАТЬ МЕНЕДЖЕРУ ===========
@dp.message(F.text == "📨 Написать менеджеру")
async def write_to_manager(message: types.Message, state: FSMContext):
    """Написать менеджеру"""
    if message.from_user.id == config.ADMIN_ID:
        await message.answer("Вы администратор.", reply_markup=get_admin_keyboard())
        return
    
    await message.answer(
        "📨 <b>Написать менеджеру</b>\n\n"
        "Введите ваше сообщение:",
        reply_markup=get_cancel_keyboard()
    )
    await state.set_state(UserMessageToAdmin.waiting_for_message_text)

@dp.message(UserMessageToAdmin.waiting_for_message_text)
async def send_to_manager(message: types.Message, state: FSMContext):
    """Отправка сообщения менеджеру"""
    if message.text == "❌ Отменить":
        await cancel_action(message, state)
        return
    
    # Сохраняем сообщение
    await db.save_message(message.from_user.id, config.ADMIN_ID, message.text)
    
    # Отправляем админу
    notification = f"""
📨 <b>Сообщение от пользователя</b>

👤 Пользователь: @{message.from_user.username or message.from_user.id}
🆔 ID: {message.from_user.id}

💬 Сообщение:
{message.text}
    """
    await send_notification_to_admin(notification)
    
    await message.answer("✅ Сообщение отправлено.", reply_markup=get_main_keyboard())
    await state.clear()

# =========== АДМИН: ПРОСМОТР ЗАЯВОК ===========
@dp.message(F.text == "📊 Все заявки")
async def show_all_requests(message: types.Message):
    """Показать все заявки"""
    if message.from_user.id != config.ADMIN_ID:
        return
    
    questionnaires = await db.get_questionnaires(page=1)
    await show_questionnaires_page(message, questionnaires, 1, None)

@dp.message(F.text == "🆕 Новые заявки")
async def show_new_requests(message: types.Message):
    """Показать новые заявки"""
    if message.from_user.id != config.ADMIN_ID:
        return
    
    questionnaires = await db.get_questionnaires(status='new', page=1)
    await show_questionnaires_page(message, questionnaires, 1, 'new')

@dp.message(F.text == "✅ Обработанные")
async def show_processed_requests(message: types.Message):
    """Показать обработанные заявки"""
    if message.from_user.id != config.ADMIN_ID:
        return
    
    questionnaires = await db.get_questionnaires(status='processed', page=1)
    await show_questionnaires_page(message, questionnaires, 1, 'processed')

@dp.message(F.text == "📁 Архив")
async def show_archived_requests(message: types.Message):
    """Показать архивные заявки"""
    if message.from_user.id != config.ADMIN_ID:
        return
    
    questionnaires = await db.get_questionnaires(status='archived', page=1)
    await show_questionnaires_page(message, questionnaires, 1, 'archived')

async def show_questionnaires_page(message: types.Message, questionnaires, page: int, status: str):
    """Показать страницу с анкетами"""
    if not questionnaires:
        await message.answer("📭 Заявок нет.", reply_markup=get_admin_keyboard())
        return
    
    response = f"📊 <b>Страница {page}</b>\n\n"
    
    for q in questionnaires[:5]:
        status_icon = {'new': '🆕', 'processed': '✅', 'archived': '📁'}.get(q['status'], '📋')
        response += f"{status_icon} <b>#{q['id']}</b> - {q['company_name']}\n"
        response += f"👤 @{q['username']} | 📞 {q['phone']}\n"
        response += f"📅 {q['created_at'][:10]}\n"
        response += "─" * 30 + "\n"
    
    keyboard = get_pagination_keyboard(page, page + 1, status)
    await message.answer(response, reply_markup=keyboard)

# =========== АДМИН: ПАГИНАЦИЯ ===========
@dp.callback_query(F.data.startswith("page_"))
async def handle_pagination(callback: types.CallbackQuery):
    """Обработка пагинации"""
    if callback.from_user.id != config.ADMIN_ID:
        return
    
    parts = callback.data.split("_")
    page = int(parts[1])
    status = parts[2] if len(parts) > 2 else None
    
    questionnaires = await db.get_questionnaires(status=status, page=page)
    await show_questionnaires_page(callback.message, questionnaires, page, status)
    await callback.answer()

# =========== АДМИН: УПРАВЛЕНИЕ АНКЕТОЙ ===========
@dp.callback_query(F.data.startswith("status_"))
async def handle_status_change(callback: types.CallbackQuery):
    """Изменение статуса анкеты"""
    if callback.from_user.id != config.ADMIN_ID:
        return
    
    parts = callback.data.split("_")
    questionnaire_id = int(parts[1])
    new_status = parts[2]
    
    success = await db.update_questionnaire_status(questionnaire_id, new_status)
    
    if success:
        await callback.answer(f"✅ Статус изменен на {new_status}")
        
        # Получаем обновленную анкету
        questionnaire = await db.fetch_one(
            "SELECT * FROM questionnaires WHERE id = ?", 
            (questionnaire_id,)
        )
        
        if questionnaire:
            response = format_questionnaire(questionnaire)
            keyboard = get_questionnaire_detail_keyboard(questionnaire_id)
            await callback.message.edit_text(response, reply_markup=keyboard)
    else:
        await callback.answer("❌ Ошибка изменения статуса")

@dp.callback_query(F.data.startswith("comment_"))
async def handle_comment(callback: types.CallbackQuery, state: FSMContext):
    """Добавление комментария"""
    if callback.from_user.id != config.ADMIN_ID:
        return
    
    questionnaire_id = int(callback.data.split("_")[1])
    await state.update_data(comment_questionnaire_id=questionnaire_id)
    
    await callback.message.answer(
        f"💬 <b>Комментарий к анкете #{questionnaire_id}</b>\n\n"
        "Введите комментарий:",
        reply_markup=get_cancel_keyboard()
    )
    await state.set_state(AdminAction.waiting_for_comment)
    await callback.answer()

@dp.message(AdminAction.waiting_for_comment)
async def process_comment(message: types.Message, state: FSMContext):
    """Обработка комментария"""
    if message.text == "❌ Отменить":
        await cancel_action(message, state)
        return
    
    user_data = await state.get_data()
    questionnaire_id = user_data.get('comment_questionnaire_id')
    
    success = await db.update_questionnaire_status(questionnaire_id, 'processed', message.text)
    
    if success:
        await message.answer("✅ Комментарий добавлен.", reply_markup=get_admin_keyboard())
    else:
        await message.answer("❌ Ошибка.", reply_markup=get_admin_keyboard())
    
    await state.clear()

# =========== АДМИН: РАССЫЛКА ===========
@dp.message(F.text == "📤 Рассылка")
async def start_mailing(message: types.Message, state: FSMContext):
    """Начало рассылки"""
    if message.from_user.id != config.ADMIN_ID:
        return
    
    users = await db.get_all_users()
    
    if not users:
        await message.answer("❌ Нет пользователей для рассылки.", reply_markup=get_admin_keyboard())
        return
    
    await message.answer(
        f"📤 <b>Рассылка</b>\n\n"
        f"Получателей: {len(users)}\n\n"
        "Введите текст рассылки:",
        reply_markup=get_cancel_keyboard()
    )
    await state.set_state(AdminAction.waiting_for_mailing_text)

@dp.message(AdminAction.waiting_for_mailing_text)
async def process_mailing(message: types.Message, state: FSMContext):
    """Обработка рассылки"""
    if message.text == "❌ Отменить":
        await cancel_action(message, state)
        return
    
    users = await db.get_all_users()
    mailing_text = message.text
    
    await message.answer(f"🔄 Начинаю рассылку для {len(users)} пользователей...")
    
    success_count = 0
    fail_count = 0
    
    for user_id in users:
        try:
            await bot.send_message(user_id, mailing_text)
            success_count += 1
            await asyncio.sleep(0.05)  # Задержка для избежания лимитов
        except Exception as e:
            logger.error(f"Ошибка отправки пользователю {user_id}: {e}")
            fail_count += 1
    
    # Сохраняем результаты
    await db.save_mailing(mailing_text, len(users), success_count, fail_count)
    
    await message.answer(
        f"✅ <b>Рассылка завершена!</b>\n\n"
        f"Всего: {len(users)}\n"
        f"✅ Успешно: {success_count}\n"
        f"❌ Ошибок: {fail_count}",
        reply_markup=get_admin_keyboard()
    )
    
    await state.clear()

# =========== АДМИН: СТАТИСТИКА ===========
@dp.message(F.text == "📈 Статистика")
async def show_statistics(message: types.Message):
    """Показать статистику"""
    if message.from_user.id != config.ADMIN_ID:
        return
    
    try:
        # Получаем статистику
        total = await db.fetch_one("SELECT COUNT(*) as count FROM questionnaires")
        new = await db.fetch_one("SELECT COUNT(*) as count FROM questionnaires WHERE status = 'new'")
        processed = await db.fetch_one("SELECT COUNT(*) as count FROM questionnaires WHERE status = 'processed'")
        archived = await db.fetch_one("SELECT COUNT(*) as count FROM questionnaires WHERE status = 'archived'")
        users = await db.fetch_one("SELECT COUNT(DISTINCT user_id) as count FROM questionnaires")
        
        response = f"""
📈 <b>Статистика</b>

<b>📊 Анкеты:</b>
• Всего: {total['count']}
• Новые: {new['count']}
• Обработанные: {processed['count']}
• Архивные: {archived['count']}

<b>👥 Пользователи:</b>
• Уникальных: {users['count']}

<b>📅 За последние 24 часа:</b>
• Новых анкет: 0
        """
        
        # Статистика за последние 24 часа
        day_ago = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
        daily = await db.fetch_one(
            "SELECT COUNT(*) as count FROM questionnaires WHERE created_at >= ?",
            (day_ago,)
        )
        
        response = response.replace("• Новых анкет: 0", f"• Новых анкет: {daily['count']}")
        
        await message.answer(response, reply_markup=get_admin_keyboard())
        
    except Exception as e:
        logger.error(f"Ошибка получения статистики: {e}")
        await message.answer("❌ Ошибка получения статистики.", reply_markup=get_admin_keyboard())

# =========== ОСТАЛЬНЫЕ ФУНКЦИИ ===========
@dp.message(F.text == "ℹ️ О компании")
async def about_company(message: types.Message):
    """Информация о компании"""
    response = """
🏢 <b>ООО «Тритика»</b>

<b>Наша миссия:</b>
Мы помогаем бизнесу находить выгодные тендеры.

<b>Контакты:</b>
📞 Телефон: +7 (904) 653-69-87
📧 Email: info@tritika.ru

<b>Рабочее время:</b>
Пн-Пт: 9:00 - 18:00
"""
    await message.answer(response, reply_markup=get_main_keyboard())

@dp.message(F.text == "❓ Помощь")
async def show_help(message: types.Message):
    """Помощь"""
    response = """
🤝 <b>Помощь</b>

<b>Основные функции:</b>
• 📝 Заполнить анкету - создать новую заявку
• 📋 Моя анкета - просмотреть текущую анкету
• 📨 Написать менеджеру - задать вопрос
• 💬 Оставить отзыв - оставить отзыв о работе

<b>Контакты поддержки:</b>
📞 +7 (904) 653-69-87
📧 info@tritika.ru
"""
    await message.answer(response, reply_markup=get_main_keyboard())

@dp.message(F.text == "📊 Статус заявок")
async def show_requests_status(message: types.Message):
    """Статус заявок"""
    if message.from_user.id == config.ADMIN_ID:
        await message.answer("Используйте админ-меню.", reply_markup=get_admin_keyboard())
        return
    
    questionnaires = await db.fetch_all(
        "SELECT id, status, created_at FROM questionnaires WHERE user_id = ? ORDER BY created_at DESC",
        (message.from_user.id,)
    )
    
    if not questionnaires:
        await message.answer("📭 У вас нет заявок.", reply_markup=get_main_keyboard())
        return
    
    response = "📊 <b>Ваши заявки:</b>\n\n"
    
    for q in questionnaires[:5]:
        status_icon = {'new': '🆕', 'processed': '✅', 'archived': '📁'}.get(q['status'], '📋')
        response += f"{status_icon} <b>#{q['id']}</b> - {q['status']}\n"
        response += f"📅 {q['created_at'][:10]}\n"
        response += "─" * 30 + "\n"
    
    await message.answer(response, reply_markup=get_main_keyboard())

# =========== ЗАПУСК БОТА ===========
async def main():
    """Основная функция"""
    logger.info("🚀 Запуск бота ТРИТИКА...")
    
    # Инициализация базы данных
    await db.init_db()
    
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
