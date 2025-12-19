import os
import sqlite3
import logging
import asyncio
import shutil
import sys
import threading
import time
import json
import csv
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
from contextlib import asynccontextmanager
from io import StringIO

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, StateFilter, BaseFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    ReplyKeyboardMarkup, 
    KeyboardButton, 
    InlineKeyboardMarkup, 
    InlineKeyboardButton,
    ReplyKeyboardRemove,
    BufferedInputFile,
    FSInputFile
)
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
import http.server
import socketserver
from http.server import BaseHTTPRequestHandler, HTTPServer

# =========== КОНФИГУРАЦИЯ ===========
class Config:
    def __init__(self):
        self.BOT_TOKEN = os.getenv("BOT_TOKEN")
        self.ADMIN_ID = os.getenv("ADMIN_ID")
        
        if not self.BOT_TOKEN:
            logger.error("❌ BOT_TOKEN не установлен! Добавьте в Secrets.")
            sys.exit(1)
            
        if not self.ADMIN_ID:
            logger.error("❌ ADMIN_ID не установлен! Добавьте в Secrets.")
            sys.exit(1)
            
        self.ADMIN_ID = int(self.ADMIN_ID)
        
        # Настройки базы данных
        self.DB_PATH = os.getenv("DB_PATH", "tenders.db")
        self.BACKUP_DIR = os.getenv("BACKUP_DIR", "backups")
        
        # Создаем директорию для бэкапов
        os.makedirs(self.BACKUP_DIR, exist_ok=True)

config = Config()

# =========== ЛОГИРОВАНИЕ ===========
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('bot.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# =========== ИНИЦИАЛИЗАЦИЯ БОТА ===========
bot = Bot(
    token=config.BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# =========== ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ ===========
mailing_data = {
    'active': False,
    'message_text': '',
    'sent_count': 0,
    'error_count': 0,
    'start_time': None,
    'total_users': 0
}

user_sessions = {}  # Храним последнюю активную клавиатуру для каждого пользователя

# =========== БАЗА ДАННЫХ ===========
class Database:
    def __init__(self, db_path: str = "tenders.db"):
        self.db_path = db_path
        self.init_db()
        
    def get_connection(self):
        """Создает новое подключение к базе данных"""
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row  # Для доступа к колонкам по имени
        return conn
    
    def init_db(self):
        """Инициализация базы данных"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        # Таблица анкет
        cursor.execute('''
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
            last_mailing_date TEXT,
            updated_at TEXT
        )
        ''')
        
        # Таблица сообщений
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            from_id INTEGER,
            to_id INTEGER,
            message_text TEXT,
            created_at TEXT
        )
        ''')
        
        # Таблица отправленных файлов
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS sent_files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            questionnaire_id INTEGER,
            file_name TEXT,
            sent_by INTEGER,
            sent_at TEXT
        )
        ''')
        
        # Таблица истории рассылок
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS mailings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mailing_date TEXT,
            message_text TEXT,
            total_users INTEGER,
            successful_sends INTEGER,
            failed_sends INTEGER
        )
        ''')
        
        # Таблица логов действий админа
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS admin_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            admin_id INTEGER,
            action TEXT,
            details TEXT,
            created_at TEXT
        )
        ''')
        
        # Создаем индексы для оптимизации
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_user_id ON questionnaires (user_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_status ON questionnaires (status)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_created_at ON questionnaires (created_at)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_from_id ON messages (from_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_to_id ON messages (to_id)')
        
        conn.commit()
        conn.close()
        logger.info("✅ База данных инициализирована")
    
    def backup_db(self):
        """Создание резервной копии базы данных"""
        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_path = os.path.join(config.BACKUP_DIR, f"backup_{timestamp}.db")
            shutil.copy2(self.db_path, backup_path)
            logger.info(f"✅ Создан бэкап базы: {backup_path}")
            return backup_path
        except Exception as e:
            logger.error(f"❌ Ошибка создания бэкапа: {e}")
            return None
    
    def add_admin_log(self, admin_id: int, action: str, details: str = ""):
        """Добавление лога действий админа"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO admin_logs (admin_id, action, details, created_at) VALUES (?, ?, ?, ?)",
                (admin_id, action, details, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            )
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"Ошибка записи лога: {e}")

db = Database(config.DB_PATH)

# =========== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ===========
def get_keyboard_for_user(user_id: int):
    """Возвращает клавиатуру в зависимости от пользователя"""
    if user_id == config.ADMIN_ID:
        return get_admin_keyboard()
    else:
        return get_main_keyboard()

def update_user_session(user_id: int, keyboard_type: str = "main"):
    """Обновляет сессию пользователя"""
    user_sessions[user_id] = keyboard_type

def get_user_keyboard(user_id: int):
    """Получает клавиатуру из сессии пользователя"""
    return user_sessions.get(user_id, "main")

def save_questionnaire_to_db(user_data):
    """Сохраняем анкету в базу данных"""
    try:
        conn = db.get_connection()
        cursor = conn.cursor()
        
        # Проверяем, существует ли уже анкета от этого пользователя
        cursor.execute('''
            SELECT id FROM questionnaires 
            WHERE user_id = ? AND status != 'archived' 
            ORDER BY created_at DESC LIMIT 1
        ''', (user_data['user_id'],))
        existing = cursor.fetchone()
        
        if existing:
            # Обновляем существующую анкету
            cursor.execute('''
                UPDATE questionnaires 
                SET full_name = ?, company_name = ?, inn = ?, contact_person = ?,
                    phone = ?, email = ?, activity_sphere = ?, industry = ?,
                    contract_amount = ?, regions = ?, status = 'new',
                    updated_at = ?, username = ?
                WHERE id = ?
            ''', (
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
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                user_data['username'],
                existing['id']
            ))
            questionnaire_id = existing['id']
        else:
            # Создаем новую анкету
            cursor.execute('''
            INSERT INTO questionnaires 
            (user_id, username, full_name, company_name, inn, contact_person, phone, email, 
             activity_sphere, industry, contract_amount, regions, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
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
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            ))
            questionnaire_id = cursor.lastrowid
        
        conn.commit()
        conn.close()
        
        logger.info(f"✅ Анкета #{questionnaire_id} сохранена для пользователя {user_data['user_id']}")
        return questionnaire_id
    except Exception as e:
        logger.error(f"❌ Ошибка сохранения в БД: {e}", exc_info=True)
        return None

def get_questionnaires(status=None, page=1, per_page=10):
    """Получаем заявки из базы с пагинацией"""
    try:
        conn = db.get_connection()
        cursor = conn.cursor()
        
        if status:
            cursor.execute("SELECT COUNT(*) FROM questionnaires WHERE status = ?", (status,))
            total = cursor.fetchone()[0]
            cursor.execute(
                "SELECT * FROM questionnaires WHERE status = ? ORDER BY created_at DESC LIMIT ? OFFSET ?", 
                (status, per_page, (page-1)*per_page)
            )
        else:
            cursor.execute("SELECT COUNT(*) FROM questionnaires")
            total = cursor.fetchone()[0]
            cursor.execute(
                "SELECT * FROM questionnaires ORDER BY created_at DESC LIMIT ? OFFSET ?", 
                (per_page, (page-1)*per_page)
            )
        
        questionnaires = cursor.fetchall()
        conn.close()
        
        total_pages = (total + per_page - 1) // per_page
        return questionnaires, total, total_pages
    except Exception as e:
        logger.error(f"❌ Ошибка получения заявок: {e}")
        return [], 0, 0

def get_questionnaire_by_user_id(user_id):
    """Получаем последнюю анкету пользователя"""
    try:
        conn = db.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM questionnaires WHERE user_id = ? ORDER BY created_at DESC LIMIT 1", 
            (user_id,)
        )
        questionnaire = cursor.fetchone()
        conn.close()
        return questionnaire
    except Exception as e:
        logger.error(f"❌ Ошибка получения анкеты: {e}")
        return None

def get_all_users():
    """Получаем всех пользователей для рассылки"""
    try:
        conn = db.get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT DISTINCT user_id FROM questionnaires WHERE user_id IS NOT NULL")
        users = [row[0] for row in cursor.fetchall()]
        conn.close()
        return users
    except Exception as e:
        logger.error(f"❌ Ошибка получения пользователей: {e}")
        return []

def get_statistics():
    """Получаем статистику"""
    try:
        conn = db.get_connection()
        cursor = conn.cursor()
        
        # Общая статистика
        cursor.execute("SELECT COUNT(*) as total FROM questionnaires")
        total_questionnaires = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) as new FROM questionnaires WHERE status = 'new'")
        new_questionnaires = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) as processed FROM questionnaires WHERE status = 'processed'")
        processed_questionnaires = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(DISTINCT user_id) as users FROM questionnaires")
        total_users = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) as feedback FROM questionnaires WHERE feedback_given = 1")
        feedback_count = cursor.fetchone()[0]
        
        # Статистика за последние 7 дней
        week_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
        cursor.execute("SELECT COUNT(*) as last_week FROM questionnaires WHERE created_at >= ?", (week_ago,))
        last_week = cursor.fetchone()[0]
        
        conn.close()
        
        return {
            'total_questionnaires': total_questionnaires,
            'new_questionnaires': new_questionnaires,
            'processed_questionnaires': processed_questionnaires,
            'total_users': total_users,
            'feedback_count': feedback_count,
            'last_week': last_week
        }
    except Exception as e:
        logger.error(f"Ошибка получения статистики: {e}")
        return {}

def update_questionnaire_status(questionnaire_id: int, status: str, admin_comment: str = None):
    """Обновление статуса анкеты"""
    try:
        conn = db.get_connection()
        cursor = conn.cursor()
        
        if admin_comment:
            cursor.execute(
                "UPDATE questionnaires SET status = ?, admin_comment = ?, updated_at = ? WHERE id = ?",
                (status, admin_comment, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), questionnaire_id)
            )
        else:
            cursor.execute(
                "UPDATE questionnaires SET status = ?, updated_at = ? WHERE id = ?",
                (status, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), questionnaire_id)
            )
        
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logger.error(f"Ошибка обновления статуса: {e}")
        return False

def save_message(from_id: int, to_id: int, message_text: str):
    """Сохранение сообщения в базу"""
    try:
        conn = db.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO messages (from_id, to_id, message_text, created_at) VALUES (?, ?, ?, ?)",
            (from_id, to_id, message_text, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        )
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logger.error(f"Ошибка сохранения сообщения: {e}")
        return False

# =========== КЛАВИАТУРЫ ===========
def get_main_keyboard():
    """Главное меню для пользователей"""
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📝 Заполнить анкету")],
            [KeyboardButton(text="📨 Написать менеджеру")],
            [KeyboardButton(text="💬 Оставить отзыв")],
            [KeyboardButton(text="ℹ️ О компании")],
        ],
        resize_keyboard=True,
        input_field_placeholder="Выберите действие"
    )
    return keyboard

def get_admin_keyboard():
    """Клавиатура администратора"""
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📊 Все заявки"), KeyboardButton(text="🆕 Новые заявки")],
            [KeyboardButton(text="📁 Выгрузить тендеры"), KeyboardButton(text="💬 Написать клиенту")],
            [KeyboardButton(text="📤 Сделать рассылку"), KeyboardButton(text="📋 Статистика")],
            [KeyboardButton(text="🔧 Управление"), KeyboardButton(text="🏠 Главное меню")]
        ],
        resize_keyboard=True
    )
    return keyboard

def get_management_keyboard():
    """Клавиатура управления"""
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="💾 Создать бэкап"), KeyboardButton(text="📋 Логи")],
            [KeyboardButton(text="🔄 Обновить БД"), KeyboardButton(text="📤 Экспорт данных")],
            [KeyboardButton(text="⬅️ Назад в админ-меню")]
        ],
        resize_keyboard=True
    )
    return keyboard

def get_cancel_keyboard():
    keyboard = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="❌ Отменить")]],
        resize_keyboard=True
    )
    return keyboard

def get_pagination_keyboard(page: int, total_pages: int, status: str = None):
    """Клавиатура для пагинации"""
    buttons = []
    if page > 1:
        callback_data = f"page_{page-1}_{status}" if status else f"page_{page-1}"
        buttons.append(InlineKeyboardButton(text="⬅️ Назад", callback_data=callback_data))
    if page < total_pages:
        callback_data = f"page_{page+1}_{status}" if status else f"page_{page+1}"
        buttons.append(InlineKeyboardButton(text="Вперёд ➡️", callback_data=callback_data))
    
    return InlineKeyboardMarkup(inline_keyboard=[buttons]) if buttons else None

def get_questionnaire_actions_keyboard(questionnaire_id: int):
    """Клавиатура действий с анкетой"""
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Обработать", callback_data=f"process_{questionnaire_id}"),
                InlineKeyboardButton(text="📝 Комментарий", callback_data=f"comment_{questionnaire_id}")
            ],
            [
                InlineKeyboardButton(text="🗑️ В архив", callback_data=f"archive_{questionnaire_id}"),
                InlineKeyboardButton(text="📨 Написать", callback_data=f"write_{questionnaire_id}")
            ]
        ]
    )
    return keyboard

def get_yes_no_keyboard():
    """Клавиатура Да/Нет для отзывов"""
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="✅ Да, все отлично"), KeyboardButton(text="❌ Есть замечания")],
            [KeyboardButton(text="❌ Отменить")]
        ],
        resize_keyboard=True
    )
    return keyboard

def get_mailing_confirmation_keyboard():
    """Клавиатура подтверждения рассылки"""
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Начать рассылку", callback_data="start_mailing"),
                InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_mailing")
            ]
        ]
    )
    return keyboard

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
    waiting_for_user_id_for_file = State()
    waiting_for_file = State()
    waiting_for_user_id_for_message = State()
    waiting_for_message_to_user = State()
    waiting_for_comment = State()
    waiting_for_questionnaire_id_for_comment = State()

class UserFeedback(StatesGroup):
    waiting_for_feedback = State()
    waiting_for_feedback_text = State()

class UserMessageToAdmin(StatesGroup):
    waiting_for_message_text = State()

# =========== ОБРАБОТЧИКИ КОМАНД ===========
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    """Обработка команды /start"""
    user_id = message.from_user.id
    
    if user_id == config.ADMIN_ID:
        update_user_session(user_id, "admin")
        await message.answer(
            "👑 <b>Панель администратора</b>\n\n"
            "Добро пожаловать в админ-панель!\n\n"
            "<b>Доступные функции:</b>\n"
            "• 📊 Все заявки - просмотр всех анкет\n"
            "• 🆕 Новые заявки - только новые заявки\n"
            "• 📁 Выгрузить тендеры - отправить выгрузку клиенту\n"
            "• 💬 Написать клиенту - отправить сообщение клиенту\n"
            "• 📤 Сделать рассылку - массовая рассылка клиентам\n"
            "• 📋 Статистика - подробная статистика работы\n"
            "• 🔧 Управление - дополнительные инструменты\n\n"
            "Используйте кнопки ниже:",
            reply_markup=get_admin_keyboard()
        )
    else:
        update_user_session(user_id, "main")
        await message.answer(
            "🏢 <b>Добро пожаловать в бот ООО 'Тритика'!</b>\n\n"
            "Мы помогаем находить выгодные тендеры для вашего бизнеса.\n\n"
            "<b>Наши услуги:</b>\n"
            "• Поиск тендеров по вашим параметрам\n"
            "• Персональная выгрузка в течение часа\n"
            "• Консультации по участию\n"
            "• Сопровождение сделок\n\n"
            "Нажмите <b>'📝 Заполнить анкету'</b> чтобы начать!",
            reply_markup=get_main_keyboard()
        )

@dp.message(F.text == "🏠 Главное меню")
async def main_menu(message: types.Message):
    """Главное меню"""
    user_id = message.from_user.id
    keyboard = get_keyboard_for_user(user_id)
    await message.answer("Главное меню:", reply_markup=keyboard)
    update_user_session(user_id, "admin" if user_id == config.ADMIN_ID else "main")

@dp.message(F.text == "⬅️ Назад в админ-меню")
async def back_to_admin_menu(message: types.Message):
    """Возврат в админ-меню"""
    if message.from_user.id != config.ADMIN_ID:
        return
    update_user_session(message.from_user.id, "admin")
    await message.answer("Админ-меню:", reply_markup=get_admin_keyboard())

@dp.message(F.text == "🔧 Управление")
async def management_menu(message: types.Message):
    """Меню управления"""
    if message.from_user.id != config.ADMIN_ID:
        return
    update_user_session(message.from_user.id, "management")
    await message.answer(
        "🔧 <b>Управление системой</b>\n\n"
        "Выберите действие:",
        reply_markup=get_management_keyboard()
    )

# =========== ЗАПОЛНЕНИЕ АНКЕТЫ ===========
@dp.message(F.text == "📝 Заполнить анкету")
async def start_questionnaire(message: types.Message, state: FSMContext):
    """Начало заполнения анкеты"""
    if message.from_user.id == config.ADMIN_ID:
        await message.answer("Вы администратор, вам не нужно заполнять анкету.", reply_markup=get_admin_keyboard())
        return
    
    current_state = await state.get_state()
    if current_state:
        await message.answer("Вы уже заполняете анкету. Продолжайте или нажмите ❌ Отменить.", reply_markup=get_cancel_keyboard())
        return
    
    await message.answer(
        "📋 <b>Начинаем заполнение анкеты!</b>\n\n"
        "Заполнение займет 2-3 минуты.\n\n"
        "<b>Введите ваше ФИО полностью:</b>\n"
        "<i>Пример: Иванов Иван Иванович</i>",
        reply_markup=get_cancel_keyboard()
    )
    await state.set_state(Questionnaire.waiting_for_name)

@dp.message(Questionnaire.waiting_for_name)
async def process_name(message: types.Message, state: FSMContext):
    """Обработка ФИО"""
    if message.text == "❌ Отменить":
        await cancel_action(message, state)
        return
    
    name = message.text.strip()
    if len(name) < 2:
        await message.answer("❌ ФИО должно содержать минимум 2 символа. Введите снова:")
        return
    
    await state.update_data(full_name=name, user_id=message.from_user.id, username=message.from_user.username or "Не указан")
    await message.answer(
        "✅ <b>ФИО сохранено</b>\n\n"
        "<b>Введите полное название вашей компании:</b>\n"
        "<i>Пример: ООО 'Ромашка'</i>",
        reply_markup=get_cancel_keyboard()
    )
    await state.set_state(Questionnaire.waiting_for_company)

@dp.message(Questionnaire.waiting_for_company)
async def process_company(message: types.Message, state: FSMContext):
    """Обработка названия компании"""
    if message.text == "❌ Отменить":
        await cancel_action(message, state)
        return
    
    company = message.text.strip()
    if len(company) < 2:
        await message.answer("❌ Название компании должно содержать минимум 2 символа. Введите снова:")
        return
    
    await state.update_data(company_name=company)
    await message.answer(
        "✅ <b>Название компании сохранено</b>\n\n"
        "<b>Введите ИНН компании:</b>\n"
        "<i>10 или 12 цифр без пробелов</i>\n"
        "<i>Пример: 1234567890</i>",
        reply_markup=get_cancel_keyboard()
    )
    await state.set_state(Questionnaire.waiting_for_inn)

@dp.message(Questionnaire.waiting_for_inn)
async def process_inn(message: types.Message, state: FSMContext):
    """Обработка ИНН"""
    if message.text == "❌ Отменить":
        await cancel_action(message, state)
        return
    
    inn = message.text.strip().replace(' ', '')
    if len(inn) not in (10, 12) or not inn.isdigit():
        await message.answer("❌ Неверный ИНН. ИНН должен содержать 10 или 12 цифр. Введите снова:")
        return
    
    await state.update_data(inn=inn)
    await message.answer(
        "✅ <b>ИНН сохранен</b>\n\n"
        "<b>Введите контактное лицо для связи:</b>\n"
        "<i>Кто будет общаться по тендерам (ФИО или должность)</i>\n"
        "<i>Пример: Петров Петр Петрович или Менеджер по закупкам</i>",
        reply_markup=get_cancel_keyboard()
    )
    await state.set_state(Questionnaire.waiting_for_contact)

@dp.message(Questionnaire.waiting_for_contact)
async def process_contact(message: types.Message, state: FSMContext):
    """Обработка контактного лица"""
    if message.text == "❌ Отменить":
        await cancel_action(message, state)
        return
    
    contact = message.text.strip()
    if len(contact) < 2:
        await message.answer("❌ Контактное лицо должно содержать минимум 2 символа. Введите снова:")
        return
    
    await state.update_data(contact_person=contact)
    await message.answer(
        "✅ <b>Контактное лицо сохранено</b>\n\n"
        "<b>Введите телефон для связи:</b>\n"
        "<i>Пример: +7 999 123-45-67 или 8-999-123-45-67</i>",
        reply_markup=get_cancel_keyboard()
    )
    await state.set_state(Questionnaire.waiting_for_phone)

@dp.message(Questionnaire.waiting_for_phone)
async def process_phone(message: types.Message, state: FSMContext):
    """Обработка телефона"""
    if message.text == "❌ Отменить":
        await cancel_action(message, state)
        return
    
    phone = message.text.strip()
    if len(phone) < 10:
        await message.answer("❌ Телефон должен содержать минимум 10 символов. Введите снова:")
        return
    
    await state.update_data(phone=phone)
    await message.answer(
        "✅ <b>Телефон сохранен</b>\n\n"
        "<b>Введите email:</b>\n"
        "<i>На этот адрес придет выгрузка тендеров</i>\n"
        "<i>Пример: example@company.ru</i>",
        reply_markup=get_cancel_keyboard()
    )
    await state.set_state(Questionnaire.waiting_for_email)

@dp.message(Questionnaire.waiting_for_email)
async def process_email(message: types.Message, state: FSMContext):
    """Обработка email"""
    if message.text == "❌ Отменить":
        await cancel_action(message, state)
        return
    
    email = message.text.strip().lower()
    if '@' not in email or '.' not in email or len(email) < 5:
        await message.answer("❌ Введите корректный email адрес. Пример: example@company.ru")
        return
    
    await state.update_data(email=email)
    await message.answer(
        "✅ <b>Email сохранен</b>\n\n"
        "<b>Введите сферу деятельности компании:</b>\n"
        "<i>Пример: Строительство, ОКВЭД 41.20</i>",
        reply_markup=get_cancel_keyboard()
    )
    await state.set_state(Questionnaire.waiting_for_activity)

@dp.message(Questionnaire.waiting_for_activity)
async def process_activity(message: types.Message, state: FSMContext):
    """Обработка сферы деятельности"""
    if message.text == "❌ Отменить":
        await cancel_action(message, state)
        return
    
    activity = message.text.strip()
    if len(activity) < 2:
        await message.answer("❌ Сфера деятельности должна содержать минимум 2 символа. Введите снова:")
        return
    
    await state.update_data(activity_sphere=activity)
    await message.answer(
        "✅ <b>Сфера деятельности сохранена</b>\n\n"
        "<b>Введите ключевые слова для поиска тендеров:</b>\n"
        "<i>Чем занимается ваша компания (через запятую)</i>\n"
        "<i>Пример: строительство, ремонт, отделка, монтаж</i>",
        reply_markup=get_cancel_keyboard()
    )
    await state.set_state(Questionnaire.waiting_for_industry)

@dp.message(Questionnaire.waiting_for_industry)
async def process_industry(message: types.Message, state: FSMContext):
    """Обработка ключевых слов"""
    if message.text == "❌ Отменить":
        await cancel_action(message, state)
        return
    
    industry = message.text.strip()
    if len(industry) < 2:
        await message.answer("❌ Ключевые слова должны содержать минимум 2 символа. Введите снова:")
        return
    
    await state.update_data(industry=industry)
    await message.answer(
        "✅ <b>Ключевые слова сохранены</b>\n\n"
        "<b>Введите желаемый бюджет контрактов:</b>\n"
        "<i>Пример: от 100 000 до 500 000 рублей</i>",
        reply_markup=get_cancel_keyboard()
    )
    await state.set_state(Questionnaire.waiting_for_amount)

@dp.message(Questionnaire.waiting_for_amount)
async def process_amount(message: types.Message, state: FSMContext):
    """Обработка бюджета"""
    if message.text == "❌ Отменить":
        await cancel_action(message, state)
        return
    
    amount = message.text.strip()
    if len(amount) < 2:
        await message.answer("❌ Бюджет должен содержать минимум 2 символа. Введите снова:")
        return
    
    await state.update_data(contract_amount=amount)
    await message.answer(
        "✅ <b>Бюджет сохранен</b>\n\n"
        "<b>Введите регионы работы через запятую:</b>\n"
        "<i>В каких регионах готовы работать</i>\n"
        "<i>Пример: Москва, Московская область, Владимир</i>",
        reply_markup=get_cancel_keyboard()
    )
    await state.set_state(Questionnaire.waiting_for_regions)

@dp.message(Questionnaire.waiting_for_regions)
async def process_regions(message: types.Message, state: FSMContext):
    """Завершение заполнения анкеты"""
    if message.text == "❌ Отменить":
        await cancel_action(message, state)
        return
    
    regions = message.text.strip()
    if len(regions) < 2:
        await message.answer("❌ Регионы должны содержать минимум 2 символа. Введите снова:")
        return
    
    user_data = await state.get_data()
    user_data['regions'] = regions
    
    questionnaire_id = save_questionnaire_to_db(user_data)
    
    if questionnaire_id:
        update_user_session(message.from_user.id, "main")
        await message.answer(
            "✅ <b>Запрос получен!</b>\n\n"
            "Благодарим вас за обращение в наш сервис. Мы уже начали поиск тендеров по вашим параметрам.\n\n"
            "Обработка запроса и формирование персональной подборки займет не более 1-го часа.\n"
            "Как только выгрузка будет готова, мы пришлем ее в этот чат.\n\n"
            "<b>Следите за обновлениями!</b>\n"
            "—\n"
            "Всегда на связи, команда ТРИТИКА.\n"
            "Телефон: +7 (904) 653-69-87\n"
            "Сайт: https://tritika.ru/\n"
            "E-mail: info@tritika.ru",
            reply_markup=get_main_keyboard()
        )
        
        admin_message = f"""
🆕 <b>НОВАЯ АНКЕТА #{questionnaire_id}</b>

<b>👤 Данные клиента:</b>
• ID пользователя: {user_data['user_id']}
• Username: @{user_data['username']}
• ФИО: {user_data['full_name']}
• Компания: {user_data['company_name']}
• ИНН: {user_data['inn']}
• Контакт: {user_data['contact_person']}
• Телефон: {user_data['phone']}
• Email: {user_data['email']}

<b>📊 Параметры поиска:</b>
• Сфера: {user_data['activity_sphere']}
• Ключевые слова: {user_data['industry']}
• Бюджет: {user_data['contract_amount']}
• Регионы: {user_data['regions']}

<b>⏰ Время подачи:</b> {datetime.now().strftime('%d.%m.%Y %H:%M')}
"""
        
        try:
            await bot.send_message(config.ADMIN_ID, admin_message)
            logger.info(f"✅ Анкета #{questionnaire_id} отправлена админу")
        except Exception as e:
            logger.error(f"❌ Ошибка отправки админу: {e}")
    else:
        await message.answer(
            "❌ Произошла ошибка при сохранении анкеты. Пожалуйста, попробуйте позже.",
            reply_markup=get_main_keyboard()
        )
    
    await state.clear()

# =========== АДМИН: ПРОСМОТР ЗАЯВОК ===========
@dp.message(F.text == "📊 Все заявки")
async def admin_all_requests(message: types.Message):
    """Показываем все заявки админу"""
    if message.from_user.id != config.ADMIN_ID:
        return
    
    questionnaires, total, total_pages = get_questionnaires(page=1)
    
    if not questionnaires:
        await message.answer("📭 Заявок пока нет.", reply_markup=get_admin_keyboard())
        return
    
    response = f"📊 <b>Все заявки (страница 1/{total_pages}):</b>\n\n"
    
    for q in questionnaires[:5]:
        status_icon = "🆕" if q['status'] == "new" else "✅" if q['status'] == "processed" else "📁"
        feedback_icon = "💬" if q['feedback_given'] else "💭"
        response += f"""
<b>#{q['id']}</b> - {q['company_name']} ({q['inn']})
👤 ID: {q['user_id']} | @{q['username']}
📞 Телефон: {q['phone']}
📧 Email: {q['email']}
📅 {q['created_at'][:10]}
{status_icon} Статус: {q['status']} | {feedback_icon} Отзыв: {'Да' if q['feedback_given'] else 'Нет'}
──────────────────────
"""
    
    if len(questionnaires) > 5:
        response += f"\n... и еще {len(questionnaires) - 5} заявок"
    
    keyboard = get_pagination_keyboard(1, total_pages)
    if keyboard:
        await message.answer(response, reply_markup=keyboard)
    else:
        await message.answer(response, reply_markup=get_admin_keyboard())

@dp.message(F.text == "🆕 Новые заявки")
async def admin_new_requests(message: types.Message):
    """Показываем новые заявки админу"""
    if message.from_user.id != config.ADMIN_ID:
        return
    
    questionnaires, total, total_pages = get_questionnaires(status='new', page=1)
    
    if not questionnaires:
        await message.answer("✅ Новых заявок нет.", reply_markup=get_admin_keyboard())
        return
    
    response = f"🆕 <b>Новые заявки (страница 1/{total_pages}):</b>\n\n"
    
    for q in questionnaires[:5]:
        response += f"""
<b>#{q['id']}</b> - {q['company_name']} ({q['inn']})
👤 ID: {q['user_id']} | @{q['username']}
📞 Телефон: {q['phone']}
📧 Email: {q['email']}
📅 {q['created_at'][:10]}
──────────────────────
"""
    
    if len(questionnaires) > 5:
        response += f"\n... и еще {len(questionnaires) - 5} заявок"
    
    keyboard = get_pagination_keyboard(1, total_pages, 'new')
    if keyboard:
        await message.answer(response, reply_markup=keyboard)
    else:
        await message.answer(response, reply_markup=get_admin_keyboard())

@dp.callback_query(F.data.startswith("page_"))
async def handle_pagination(callback: types.CallbackQuery):
    """Обработка пагинации"""
    if callback.from_user.id != config.ADMIN_ID:
        return
    
    try:
        parts = callback.data.split("_")
        page = int(parts[1])
        status = parts[2] if len(parts) > 2 else None
        
        questionnaires, total, total_pages = get_questionnaires(status=status, page=page)
        
        if not questionnaires:
            await callback.answer("Нет заявок на этой странице")
            return
        
        if status == 'new':
            title = "Новые заявки"
        else:
            title = "Все заявки"
        
        response = f"{title} (страница {page}/{total_pages}):</b>\n\n"
        
        for q in questionnaires:
            status_icon = "🆕" if q['status'] == "new" else "✅" if q['status'] == "processed" else "📁"
            feedback_icon = "💬" if q['feedback_given'] else "💭"
            response += f"""
<b>#{q['id']}</b> - {q['company_name']} ({q['inn']})
👤 ID: {q['user_id']} | @{q['username']}
📞 Телефон: {q['phone']}
📧 Email: {q['email']}
📅 {q['created_at'][:10]}
{status_icon} Статус: {q['status']} | {feedback_icon} Отзыв: {'Да' if q['feedback_given'] else 'Нет'}
──────────────────────
"""
        
        keyboard = get_pagination_keyboard(page, total_pages, status)
        await callback.message.edit_text(response, reply_markup=keyboard)
        await callback.answer()
        
    except Exception as e:
        logger.error(f"Ошибка пагинации: {e}")
        await callback.answer("Ошибка пагинации")

# =========== АДМИН: УПРАВЛЕНИЕ ===========
@dp.message(F.text == "💾 Создать бэкап")
async def create_backup(message: types.Message):
    """Создание бэкапа базы данных"""
    if message.from_user.id != config.ADMIN_ID:
        return
    
    await message.answer("🔄 Создаю резервную копию базы данных...")
    backup_path = db.backup_db()
    
    if backup_path:
        try:
            with open(backup_path, 'rb') as f:
                await message.answer_document(
                    BufferedInputFile(f.read(), filename=os.path.basename(backup_path)),
                    caption=f"✅ Бэкап создан: {os.path.basename(backup_path)}"
                )
        except Exception as e:
            await message.answer(f"✅ Бэкап создан, но отправить не удалось: {e}")
    else:
        await message.answer("❌ Не удалось создать бэкап")

@dp.message(F.text == "📋 Логи")
async def send_logs(message: types.Message):
    """Отправка логов"""
    if message.from_user.id != config.ADMIN_ID:
        return
    
    try:
        with open('bot.log', 'rb') as f:
            await message.answer_document(
                BufferedInputFile(f.read(), filename='bot.log'),
                caption="📋 Логи бота"
            )
    except FileNotFoundError:
        await message.answer("Файл логов не найден")
    except Exception as e:
        await message.answer(f"❌ Ошибка отправки логов: {e}")

@dp.message(F.text == "🔄 Обновить БД")
async def update_database(message: types.Message):
    """Обновление структуры базы данных"""
    if message.from_user.id != config.ADMIN_ID:
        return
    
    try:
        db.init_db()
        await message.answer("✅ Структура базы данных обновлена")
    except Exception as e:
        await message.answer(f"❌ Ошибка обновления БД: {e}")

@dp.message(F.text == "📤 Экспорт данных")
async def export_data(message: types.Message):
    """Экспорт данных в CSV"""
    if message.from_user.id != config.ADMIN_ID:
        return
    
    try:
        conn = db.get_connection()
        cursor = conn.cursor()
        
        # Экспорт анкет
        cursor.execute("SELECT * FROM questionnaires")
        questionnaires = cursor.fetchall()
        
        if questionnaires:
            import csv
            from io import StringIO
            
            output = StringIO()
            writer = csv.writer(output)
            
            # Заголовки
            writer.writerow(['ID', 'User ID', 'Username', 'ФИО', 'Компания', 'ИНН', 
                           'Контактное лицо', 'Телефон', 'Email', 'Сфера деятельности',
                           'Ключевые слова', 'Бюджет', 'Регионы', 'Статус', 'Дата создания'])
            
            # Данные
            for q in questionnaires:
                writer.writerow([
                    q['id'], q['user_id'], q['username'], q['full_name'],
                    q['company_name'], q['inn'], q['contact_person'], q['phone'],
                    q['email'], q['activity_sphere'], q['industry'],
                    q['contract_amount'], q['regions'], q['status'], q['created_at']
                ])
            
            await message.answer_document(
                BufferedInputFile(output.getvalue().encode(), filename='questionnaires.csv'),
                caption="📊 Экспорт анкет"
            )
        else:
            await message.answer("Нет данных для экспорта")
        
        conn.close()
        
    except Exception as e:
        await message.answer(f"❌ Ошибка экспорта: {e}")

# =========== АДМИН: ВЫГРУЗКА ТЕНДЕРОВ ===========
@dp.message(F.text == "📁 Выгрузить тендеры")
async def send_tenders_start(message: types.Message, state: FSMContext):
    """Начало процесса отправки файла клиенту"""
    if message.from_user.id != config.ADMIN_ID:
        return
    
    await message.answer(
        "📁 <b>Отправка файла клиенту</b>\n\n"
        "Введите ID пользователя, которому нужно отправить файл:",
        reply_markup=get_cancel_keyboard()
    )
    await state.set_state(AdminAction.waiting_for_user_id_for_file)

@dp.message(AdminAction.waiting_for_user_id_for_file)
async def get_user_id_for_file(message: types.Message, state: FSMContext):
    """Получение ID пользователя для отправки файла"""
    if message.text == "❌ Отменить":
        await cancel_action(message, state)
        return
    
    try:
        user_id = int(message.text)
        await state.update_data(user_id_for_file=user_id)
        await message.answer(
            f"✅ Пользователь: {user_id}\n\n"
            "Теперь отправьте файл (PDF, Excel, Word, TXT):",
            reply_markup=get_cancel_keyboard()
        )
        await state.set_state(AdminAction.waiting_for_file)
    except ValueError:
        await message.answer("❌ Неверный ID. Введите числовой ID пользователя:")

@dp.message(AdminAction.waiting_for_file, F.document | F.photo)
async def handle_file_for_user(message: types.Message, state: FSMContext):
    """Обработка отправки файла пользователю"""
    user_data = await state.get_data()
    user_id = user_data.get('user_id_for_file')
    
    try:
        if message.document:
            file_id = message.document.file_id
            file_name = message.document.file_name
        elif message.photo:
            file_id = message.photo[-1].file_id
            file_name = f"photo_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
        else:
            await message.answer("❌ Пожалуйста, отправьте файл")
            return
        
        # Отправляем файл пользователю
        if message.document:
            await bot.send_document(user_id, file_id, caption="📁 Ваша выгрузка тендеров готова!")
        elif message.photo:
            await bot.send_photo(user_id, file_id, caption="📁 Ваша выгрузка тендеров готова!")
        
        # Сохраняем информацию о отправке
        conn = db.get_connection()
        cursor = conn.cursor()
        questionnaire = get_questionnaire_by_user_id(user_id)
        
        if questionnaire:
            cursor.execute(
                "INSERT INTO sent_files (questionnaire_id, file_name, sent_by, sent_at) VALUES (?, ?, ?, ?)",
                (questionnaire['id'], file_name, message.from_user.id, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            )
            
            # Обновляем статус анкеты
            cursor.execute(
                "UPDATE questionnaires SET status = 'processed', updated_at = ? WHERE id = ?",
                (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), questionnaire['id'])
            )
            conn.commit()
        
        conn.close()
        
        await message.answer(
            f"✅ Файл успешно отправлен пользователю {user_id}",
            reply_markup=get_admin_keyboard()
        )
        
        db.add_admin_log(message.from_user.id, "send_file", f"Отправлен файл пользователю {user_id}")
        
    except Exception as e:
        await message.answer(f"❌ Ошибка отправки файла: {e}")
    
    await state.clear()

# =========== АДМИН: НАПИСАТЬ КЛИЕНТУ ===========
@dp.message(F.text == "💬 Написать клиенту")
async def write_to_client_start(message: types.Message, state: FSMContext):
    """Начало процесса отправки сообщения клиенту"""
    if message.from_user.id != config.ADMIN_ID:
        return
    
    await message.answer(
        "💬 <b>Отправка сообщения клиенту</b>\n\n"
        "Введите ID пользователя, которому нужно отправить сообщение:",
        reply_markup=get_cancel_keyboard()
    )
    await state.set_state(AdminAction.waiting_for_user_id_for_message)

@dp.message(AdminAction.waiting_for_user_id_for_message)
async def get_user_id_for_message(message: types.Message, state: FSMContext):
    """Получение ID пользователя для отправки сообщения"""
    if message.text == "❌ Отменить":
        await cancel_action(message, state)
        return
    
    try:
        user_id = int(message.text)
        await state.update_data(user_id_for_message=user_id)
        await message.answer(
            f"✅ Пользователь: {user_id}\n\n"
            "Введите текст сообщения:",
            reply_markup=get_cancel_keyboard()
        )
        await state.set_state(AdminAction.waiting_for_message_to_user)
    except ValueError:
        await message.answer("❌ Неверный ID. Введите числовой ID пользователя:")

@dp.message(AdminAction.waiting_for_message_to_user)
async def send_message_to_user(message: types.Message, state: FSMContext):
    """Отправка сообщения пользователю"""
    if message.text == "❌ Отменить":
        await cancel_action(message, state)
        return
    
    user_data = await state.get_data()
    user_id = user_data.get('user_id_for_message')
    message_text = message.text
    
    try:
        await bot.send_message(user_id, f"📨 Сообщение от менеджера:\n\n{message_text}")
        
        # Сохраняем сообщение в базе
        save_message(message.from_user.id, user_id, message_text)
        
        await message.answer(
            f"✅ Сообщение отправлено пользователю {user_id}",
            reply_markup=get_admin_keyboard()
        )
        
        db.add_admin_log(message.from_user.id, "send_message", f"Отправлено сообщение пользователю {user_id}")
        
    except Exception as e:
        await message.answer(f"❌ Ошибка отправки сообщения: {e}")
    
    await state.clear()

# =========== АДМИН: РАССЫЛКА ===========
@dp.message(F.text == "📤 Сделать рассылку")
async def start_mailing(message: types.Message, state: FSMContext):
    """Начало процесса рассылки"""
    if message.from_user.id != config.ADMIN_ID:
        return
    
    users = get_all_users()
    if not users:
        await message.answer("❌ Нет пользователей для рассылки", reply_markup=get_admin_keyboard())
        return
    
    await message.answer(
        f"📤 <b>Массовая рассылка</b>\n\n"
        f"Количество получателей: {len(users)}\n\n"
        "Введите текст рассылки:",
        reply_markup=get_cancel_keyboard()
    )
    await state.set_state(AdminAction.waiting_for_mailing_text)

@dp.message(AdminAction.waiting_for_mailing_text)
async def get_mailing_text(message: types.Message, state: FSMContext):
    """Получение текста для рассылки"""
    if message.text == "❌ Отменить":
        await cancel_action(message, state)
        return
    
    mailing_text = message.text
    users = get_all_users()
    
    global mailing_data
    mailing_data['message_text'] = mailing_text
    mailing_data['total_users'] = len(users)
    mailing_data['sent_count'] = 0
    mailing_data['error_count'] = 0
    
    await message.answer(
        f"📤 <b>Подтверждение рассылки</b>\n\n"
        f"Текст: {mailing_text}\n\n"
        f"Количество получателей: {len(users)}\n\n"
        "Начать рассылку?",
        reply_markup=get_mailing_confirmation_keyboard()
    )
    await state.clear()

@dp.callback_query(F.data == "start_mailing")
async def confirm_mailing(callback: types.CallbackQuery):
    """Подтверждение и начало рассылки"""
    if callback.from_user.id != config.ADMIN_ID:
        return
    
    await callback.message.edit_text("🔄 Начинаю рассылку...")
    
    users = get_all_users()
    total = len(users)
    global mailing_data
    
    mailing_data['active'] = True
    mailing_data['start_time'] = datetime.now()
    
    success_count = 0
    error_count = 0
    
    for i, user_id in enumerate(users, 1):
        try:
            await bot.send_message(user_id, mailing_data['message_text'])
            success_count += 1
            
            # Обновляем дату последней рассылки в анкете
            conn = db.get_connection()
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE questionnaires SET last_mailing_date = ? WHERE user_id = ?",
                (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), user_id)
            )
            conn.commit()
            conn.close()
            
            # Обновляем прогресс каждые 10 отправок
            if i % 10 == 0 or i == total:
                progress = int((i / total) * 100)
                await callback.message.edit_text(
                    f"📤 Рассылка в процессе...\n\n"
                    f"Прогресс: {i}/{total} ({progress}%)\n"
                    f"✅ Успешно: {success_count}\n"
                    f"❌ Ошибок: {error_count}"
                )
            
            await asyncio.sleep(0.1)  # Задержка чтобы не превысить лимиты Telegram
        
        except Exception as e:
            error_count += 1
            logger.error(f"Ошибка отправки пользователю {user_id}: {e}")
    
    # Сохраняем статистику рассылки
    conn = db.get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO mailings (mailing_date, message_text, total_users, successful_sends, failed_sends) VALUES (?, ?, ?, ?, ?)",
        (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), mailing_data['message_text'], total, success_count, error_count)
    )
    conn.commit()
    conn.close()
    
    mailing_data['active'] = False
    
    await callback.message.edit_text(
        f"✅ <b>Рассылка завершена!</b>\n\n"
        f"Всего получателей: {total}\n"
        f"✅ Успешно отправлено: {success_count}\n"
        f"❌ Ошибок: {error_count}\n"
        f"⏱️ Время выполнения: {datetime.now() - mailing_data['start_time']}"
    )
    
    db.add_admin_log(callback.from_user.id, "mailing", f"Рассылка: {success_count}/{total} успешно")

@dp.callback_query(F.data == "cancel_mailing")
async def cancel_mailing(callback: types.CallbackQuery):
    """Отмена рассылки"""
    await callback.message.edit_text("❌ Рассылка отменена")
    await callback.answer()

# =========== АДМИН: СТАТИСТИКА ===========
@dp.message(F.text == "📋 Статистика")
async def show_statistics(message: types.Message):
    """Показ статистики"""
    if message.from_user.id != config.ADMIN_ID:
        return
    
    stats = get_statistics()
    
    response = f"""
📊 <b>Статистика системы</b>

<b>📈 Общая статистика:</b>
• Всего анкет: {stats.get('total_questionnaires', 0)}
• Новые анкеты: {stats.get('new_questionnaires', 0)}
• Обработанные анкеты: {stats.get('processed_questionnaires', 0)}

<b>👥 Пользователи:</b>
• Уникальных пользователей: {stats.get('total_users', 0)}
• Оставили отзыв: {stats.get('feedback_count', 0)}

<b>📅 Активность за неделю:</b>
• Новых анкет: {stats.get('last_week', 0)}

<b>💾 Система:</b>
• Бэкапов: {len(os.listdir(config.BACKUP_DIR)) if os.path.exists(config.BACKUP_DIR) else 0}
• Активных сессий: {len(user_sessions)}
"""
    
    await message.answer(response, reply_markup=get_admin_keyboard())

# =========== ПОЛЬЗОВАТЕЛЬ: НАПИСАТЬ МЕНЕДЖЕРУ ===========
@dp.message(F.text == "📨 Написать менеджеру")
async def write_to_manager_start(message: types.Message, state: FSMContext):
    """Начало отправки сообщения менеджеру"""
    if message.from_user.id == config.ADMIN_ID:
        await message.answer("Вы администратор, используйте админ-меню.", reply_markup=get_admin_keyboard())
        return
    
    await message.answer(
        "📨 <b>Написать менеджеру</b>\n\n"
        "Введите ваше сообщение. Менеджер ответит вам в ближайшее время:",
        reply_markup=get_cancel_keyboard()
    )
    await state.set_state(UserMessageToAdmin.waiting_for_message_text)

@dp.message(UserMessageToAdmin.waiting_for_message_text)
async def send_message_to_manager(message: types.Message, state: FSMContext):
    """Отправка сообщения менеджеру"""
    if message.text == "❌ Отменить":
        await cancel_action(message, state)
        return
    
    user_id = message.from_user.id
    message_text = message.text
    
    # Сохраняем сообщение в базе
    save_message(user_id, config.ADMIN_ID, message_text)
    
    # Отправляем админу
    try:
        user_info = f"@{message.from_user.username}" if message.from_user.username else f"ID: {user_id}"
        await bot.send_message(
            config.ADMIN_ID,
            f"📨 <b>Сообщение от пользователя</b>\n\n"
            f"👤 Пользователь: {user_info}\n"
            f"🆔 ID: {user_id}\n\n"
            f"💬 Сообщение:\n{message_text}"
        )
        
        await message.answer(
            "✅ Ваше сообщение отправлено менеджеру. Ответ поступит в этот чат.",
            reply_markup=get_main_keyboard()
        )
        
    except Exception as e:
        await message.answer(
            "❌ Ошибка отправки сообщения. Попробуйте позже.",
            reply_markup=get_main_keyboard()
        )
    
    await state.clear()

# =========== ПОЛЬЗОВАТЕЛЬ: ОСТАВИТЬ ОТЗЫВ ===========
@dp.message(F.text == "💬 Оставить отзыв")
async def start_feedback(message: types.Message, state: FSMContext):
    """Начало процесса оставления отзыва"""
    if message.from_user.id == config.ADMIN_ID:
        await message.answer("Вы администратор, используйте админ-меню.", reply_markup=get_admin_keyboard())
        return
    
    # Проверяем, есть ли анкета у пользователя
    questionnaire = get_questionnaire_by_user_id(message.from_user.id)
    if not questionnaire:
        await message.answer(
            "❌ Для оставления отзыва сначала заполните анкету.",
            reply_markup=get_main_keyboard()
        )
        return
    
    # Проверяем, оставлял ли уже отзыв
    if questionnaire['feedback_given']:
        await message.answer(
            "✅ Вы уже оставляли отзыв. Спасибо!",
            reply_markup=get_main_keyboard()
        )
        return
    
    await message.answer(
        "💬 <b>Оставить отзыв</b>\n\n"
        "Вы довольны нашей работой?",
        reply_markup=get_yes_no_keyboard()
    )
    await state.set_state(UserFeedback.waiting_for_feedback)

@dp.message(UserFeedback.waiting_for_feedback)
async def process_feedback_choice(message: types.Message, state: FSMContext):
    """Обработка выбора отзыва"""
    if message.text == "❌ Отменить":
        await cancel_action(message, state)
        return
    
    if message.text not in ["✅ Да, все отлично", "❌ Есть замечания"]:
        await message.answer("Пожалуйста, используйте кнопки:", reply_markup=get_yes_no_keyboard())
        return
    
    feedback_type = "positive" if message.text == "✅ Да, все отлично" else "negative"
    
    await state.update_data(feedback_type=feedback_type)
    
    if feedback_type == "positive":
        feedback_text = "Всё отлично, спасибо!"
        await finish_feedback(message, state, feedback_text)
    else:
        await message.answer(
            "📝 Пожалуйста, напишите ваши замечания или предложения:",
            reply_markup=get_cancel_keyboard()
        )
        await state.set_state(UserFeedback.waiting_for_feedback_text)

@dp.message(UserFeedback.waiting_for_feedback_text)
async def process_feedback_text(message: types.Message, state: FSMContext):
    """Обработка текста отзыва"""
    if message.text == "❌ Отменить":
        await cancel_action(message, state)
        return
    
    feedback_text = message.text
    await finish_feedback(message, state, feedback_text)

async def finish_feedback(message: types.Message, state: FSMContext, feedback_text: str):
    """Завершение процесса отзыва"""
    user_data = await state.get_data()
    feedback_type = user_data.get('feedback_type', 'positive')
    
    # Сохраняем отзыв в базе
    try:
        conn = db.get_connection()
        cursor = conn.cursor()
        
        questionnaire = get_questionnaire_by_user_id(message.from_user.id)
        if questionnaire:
            cursor.execute(
                "UPDATE questionnaires SET feedback_given = 1, feedback_date = ?, feedback_text = ? WHERE id = ?",
                (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), feedback_text, questionnaire['id'])
            )
            conn.commit()
            
            # Отправляем админу уведомление
            await bot.send_message(
                config.ADMIN_ID,
                f"💬 <b>Новый отзыв</b>\n\n"
                f"👤 Пользователь: @{questionnaire['username']} (ID: {questionnaire['user_id']})\n"
                f"🏢 Компания: {questionnaire['company_name']}\n"
                f"📊 Тип: {'✅ Положительный' if feedback_type == 'positive' else '❌ Отрицательный'}\n"
                f"📝 Текст: {feedback_text}"
            )
        
        conn.close()
        
        if feedback_type == "positive":
            await message.answer(
                "✅ Спасибо за ваш отзыв! Мы рады, что вы довольны нашей работой.",
                reply_markup=get_main_keyboard()
            )
        else:
            await message.answer(
                "✅ Спасибо за ваш отзыв! Мы учтем ваши замечания и постараемся стать лучше.",
                reply_markup=get_main_keyboard()
            )
        
    except Exception as e:
        await message.answer(
            "❌ Ошибка сохранения отзыва. Попробуйте позже.",
            reply_markup=get_main_keyboard()
        )
    
    await state.clear()

# =========== ПОЛЬЗОВАТЕЛЬ: О КОМПАНИИ ===========
@dp.message(F.text == "ℹ️ О компании")
async def about_company(message: types.Message):
    """Информация о компании"""
    response = """
🏢 <b>ООО «Тритика»</b>

<b>Наша миссия:</b>
Мы помогаем бизнесу находить выгодные тендеры и эффективно участвовать в закупках.

<b>Наши услуги:</b>
• 🔍 Поиск тендеров по вашим параметрам
• 📊 Персональная выгрузка тендеров
• 💼 Консультации по участию в закупках
• 📑 Подготовка документов для участия
• 🤝 Сопровождение сделок

<b>Преимущества:</b>
• 🚀 Выгрузка тендеров в течение 1 часа
• 🎯 Только релевантные предложения
• 💰 Экономия времени на поиске
• 📈 Увеличение шансов на победу
• 👨‍💼 Персональный менеджер

<b>Контакты:</b>
📞 Телефон: +7 (904) 653-69-87
🌐 Сайт: https://tritika.ru/
📧 Email: info@tritika.ru

<b>Рабочее время:</b>
Пн-Пт: 9:00 - 18:00
Сб-Вс: выходной

<b>Как начать работу:</b>
1. Нажмите «📝 Заполнить анкету»
2. Укажите параметры поиска
3. Получите персональную выгрузку тендеров в течение часа!
"""
    
    await message.answer(response, reply_markup=get_main_keyboard())

# =========== ОБРАБОТКА СООБЩЕНИЙ ОТ АДМИНА ПОЛЬЗОВАТЕЛЯМ ===========
@dp.message()
async def handle_admin_reply_to_user(message: types.Message, state: FSMContext):
    """Обработка ответов админа пользователям"""
    if message.from_user.id == config.ADMIN_ID and message.reply_to_message:
        # Проверяем, что это ответ на сообщение от пользователя
        try:
            # Ищем ID пользователя в тексте сообщения
            reply_text = message.reply_to_message.text
            if "ID:" in reply_text:
                lines = reply_text.split('\n')
                user_id_line = [line for line in lines if 'ID:' in line][0]
                user_id = int(user_id_line.split('ID:')[1].strip())
                
                # Отправляем сообщение пользователю
                await bot.send_message(user_id, f"📨 Ответ менеджера:\n\n{message.text}")
                
                # Сохраняем в базе
                save_message(config.ADMIN_ID, user_id, message.text)
                
                await message.answer("✅ Ответ отправлен пользователю")
                
                db.add_admin_log(message.from_user.id, "reply_to_user", f"Ответ пользователю {user_id}")
            else:
                await message.answer("❌ Не удалось определить пользователя")
        except Exception as e:
            logger.error(f"Ошибка отправки ответа пользователю: {e}")
            await message.answer(f"❌ Ошибка: {e}")

# =========== ОТМЕНА ===========
@dp.message(F.text == "❌ Отменить")
async def cancel_action(message: types.Message, state: FSMContext):
    """Отмена текущего действия"""
    current_state = await state.get_state()
    if current_state is None:
        keyboard = get_keyboard_for_user(message.from_user.id)
        await message.answer("Главное меню:", reply_markup=keyboard)
        return
    
    await message.answer(
        "❌ Действие отменено.",
        reply_markup=get_keyboard_for_user(message.from_user.id)
    )
    await state.clear()

# =========== HTTP СЕРВЕР ДЛЯ HEALTHCHECK ===========
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/' or self.path == '/health':
            self.send_response(200)
            self.send_header('Content-type', 'text/plain')
            self.end_headers()
            
            # Проверяем доступность базы данных
            try:
                conn = db.get_connection()
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) FROM questionnaires")
                conn.close()
                status = "OK"
            except Exception as e:
                status = f"DB ERROR: {str(e)}"
            
            self.wfile.write(f'Bot Status: {status}\n'.encode())
            self.wfile.write(f'Database: {config.DB_PATH}\n'.encode())
            self.wfile.write(f'Backups: {len(os.listdir(config.BACKUP_DIR)) if os.path.exists(config.BACKUP_DIR) else 0}\n'.encode())
            self.wfile.write(f'Active users: {len(user_sessions)}\n'.encode())
            self.wfile.write(f'Mailing active: {mailing_data["active"]}\n'.encode())
        else:
            self.send_response(404)
            self.end_headers()
    
    def log_message(self, format, *args):
        pass

def run_healthcheck_server():
    """Запуск HTTP сервера для healthcheck"""
    port = int(os.environ.get('PORT', 8080))
    server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
    logger.info(f"✅ Healthcheck сервер запущен на порту {port}")
    server.serve_forever()

# =========== ЗАПУСК БОТА ===========
async def main():
    """Основная функция запуска"""
    logger.info("🚀 Запуск бота ТРИТИКА...")
    
    # Создаем бэкап при старте
    db.backup_db()
    
    # Запускаем HTTP сервер в отдельном потоке
    http_thread = threading.Thread(target=run_healthcheck_server, daemon=True)
    http_thread.start()
    logger.info("✅ Healthcheck сервер запущен")
    
    # Запускаем бота
    try:
        bot_info = await bot.get_me()
        logger.info(f"✅ Бот запущен: @{bot_info.username}")
        logger.info(f"✅ Администратор: {config.ADMIN_ID}")
        logger.info(f"✅ База данных: {config.DB_PATH}")
        
        # Планировщик ежедневных бэкапов
        async def daily_backup():
            while True:
                await asyncio.sleep(24 * 60 * 60)  # 24 часа
                logger.info("🔄 Создание ежедневного бэкапа...")
                db.backup_db()
        
        # Запускаем задачу бэкапа в фоне
        asyncio.create_task(daily_backup())
        
        # Запускаем polling
        await dp.start_polling(bot, skip_updates=True)
        
    except Exception as e:
        logger.error(f"❌ Ошибка запуска бота: {e}", exc_info=True)
        raise

# =========== ЗАПУСК ПРИЛОЖЕНИЯ ===========
if __name__ == "__main__":
    # Создаем необходимые директории
    os.makedirs(config.BACKUP_DIR, exist_ok=True)
    
    # Запускаем основную функцию
    asyncio.run(main())
