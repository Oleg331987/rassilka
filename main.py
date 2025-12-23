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
from typing import Optional, List, Dict, Any, Tuple
from contextlib import asynccontextmanager
from io import StringIO, BytesIO
import pandas as pd

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
    FSInputFile,
    CallbackQuery
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

# Храним последнее меню для каждого пользователя
user_menus = {}
# Храним данные анкет в процессе заполнения
questionnaire_data = {}

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
            file_id TEXT,
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
            failed_sends INTEGER,
            duration_seconds REAL
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
        
        # Таблица сессий пользователей
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS user_sessions (
            user_id INTEGER PRIMARY KEY,
            last_activity TEXT,
            menu_state TEXT,
            created_at TEXT
        )
        ''')
        
        # Создаем индексы для оптимизации
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_user_id ON questionnaires (user_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_status ON questionnaires (status)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_created_at ON questionnaires (created_at)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_from_id ON messages (from_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_to_id ON messages (to_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_mailing_date ON mailings (mailing_date)')
        
        conn.commit()
        conn.close()
        logger.info("✅ База данных инициализирована")
    
    def backup_db(self):
        """Создание резервной копии базы данных"""
        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_path = os.path.join(config.BACKUP_DIR, f"backup_{timestamp}.db")
            shutil.copy2(self.db_path, backup_path)
            
            # Сохраняем также в формате SQL
            sql_backup_path = os.path.join(config.BACKUP_DIR, f"backup_{timestamp}.sql")
            conn = self.get_connection()
            with open(sql_backup_path, 'w', encoding='utf-8') as f:
                for line in conn.iterdump():
                    f.write('%s\n' % line)
            conn.close()
            
            logger.info(f"✅ Создан бэкап базы: {backup_path}")
            return backup_path, sql_backup_path
        except Exception as e:
            logger.error(f"❌ Ошибка создания бэкапа: {e}")
            return None, None
    
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
    
    def update_user_session(self, user_id: int, menu_state: str = None):
        """Обновление сессии пользователя"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            
            cursor.execute('''
                INSERT OR REPLACE INTO user_sessions (user_id, last_activity, menu_state, created_at)
                VALUES (?, ?, ?, COALESCE((SELECT created_at FROM user_sessions WHERE user_id = ?), ?))
            ''', (user_id, now, menu_state, user_id, now))
            
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"Ошибка обновления сессии: {e}")

db = Database(config.DB_PATH)

# =========== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ===========
def update_user_menu(user_id: int, menu_name: str):
    """Обновляет текущее меню пользователя"""
    user_menus[user_id] = menu_name
    db.update_user_session(user_id, menu_name)

def get_current_user_menu(user_id: int) -> str:
    """Получает текущее меню пользователя"""
    return user_menus.get(user_id, "main")

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
        
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
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
                now,
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
                now,
                now
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
        
        # Статистика по рассылкам
        cursor.execute("SELECT COUNT(*) as total_mailings FROM mailings")
        total_mailings = cursor.fetchone()[0]
        
        cursor.execute("SELECT SUM(successful_sends) as total_sent FROM mailings")
        total_sent = cursor.fetchone()[0] or 0
        
        # Активные пользователи за последние 30 дней
        month_ago = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
        cursor.execute("SELECT COUNT(DISTINCT user_id) as active_users FROM questionnaires WHERE created_at >= ?", (month_ago,))
        active_users = cursor.fetchone()[0]
        
        conn.close()
        
        return {
            'total_questionnaires': total_questionnaires,
            'new_questionnaires': new_questionnaires,
            'processed_questionnaires': processed_questionnaires,
            'total_users': total_users,
            'feedback_count': feedback_count,
            'last_week': last_week,
            'total_mailings': total_mailings,
            'total_sent': total_sent,
            'active_users': active_users
        }
    except Exception as e:
        logger.error(f"Ошибка получения статистики: {e}")
        return {}

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

def get_users_with_questionnaires(page=1, per_page=20):
    """Получение списка пользователей с анкетами"""
    try:
        conn = db.get_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT DISTINCT 
                q.user_id,
                q.username,
                COUNT(q.id) as questionnaire_count,
                MAX(q.created_at) as last_activity,
                SUM(CASE WHEN q.status = 'new' THEN 1 ELSE 0 END) as new_count,
                SUM(CASE WHEN q.feedback_given = 1 THEN 1 ELSE 0 END) as feedback_count
            FROM questionnaires q
            WHERE q.user_id IS NOT NULL
            GROUP BY q.user_id, q.username
            ORDER BY last_activity DESC
            LIMIT ? OFFSET ?
        ''', (per_page, (page-1)*per_page))
        
        users = cursor.fetchall()
        
        # Общее количество пользователей
        cursor.execute('''
            SELECT COUNT(DISTINCT user_id) 
            FROM questionnaires 
            WHERE user_id IS NOT NULL
        ''')
        total = cursor.fetchone()[0]
        
        conn.close()
        
        total_pages = (total + per_page - 1) // per_page
        return users, total, total_pages
    except Exception as e:
        logger.error(f"Ошибка получения пользователей: {e}")
        return [], 0, 0

def update_questionnaire_status(questionnaire_id: int, status: str, admin_comment: str = None):
    """Обновление статуса анкеты"""
    try:
        conn = db.get_connection()
        cursor = conn.cursor()
        
        update_query = """
            UPDATE questionnaires 
            SET status = ?, updated_at = ?
        """
        params = [status, datetime.now().strftime("%Y-%m-%d %H:%M:%S")]
        
        if admin_comment:
            update_query += ", admin_comment = ?"
            params.append(admin_comment)
        
        update_query += " WHERE id = ?"
        params.append(questionnaire_id)
        
        cursor.execute(update_query, params)
        conn.commit()
        conn.close()
        
        logger.info(f"Статус анкеты #{questionnaire_id} обновлен на '{status}'")
        return True
    except Exception as e:
        logger.error(f"Ошибка обновления статуса: {e}")
        return False

def get_user_statistics(user_id: int):
    """Получение статистики пользователя"""
    try:
        conn = db.get_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT 
                COUNT(*) as total_questionnaires,
                SUM(CASE WHEN status = 'new' THEN 1 ELSE 0 END) as new_count,
                SUM(CASE WHEN status = 'processed' THEN 1 ELSE 0 END) as processed_count,
                SUM(CASE WHEN status = 'archived' THEN 1 ELSE 0 END) as archived_count,
                SUM(CASE WHEN feedback_given = 1 THEN 1 ELSE 0 END) as feedback_count,
                MAX(created_at) as last_created,
                MAX(updated_at) as last_updated
            FROM questionnaires 
            WHERE user_id = ?
        ''', (user_id,))
        
        stats = cursor.fetchone()
        conn.close()
        
        return dict(stats) if stats else None
    except Exception as e:
        logger.error(f"Ошибка получения статистики пользователя: {e}")
        return None

def get_questionnaire_by_id(questionnaire_id: int):
    """Получение анкеты по ID"""
    try:
        conn = db.get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM questionnaires WHERE id = ?", (questionnaire_id,))
        questionnaire = cursor.fetchone()
        conn.close()
        return questionnaire
    except Exception as e:
        logger.error(f"Ошибка получения анкеты по ID: {e}")
        return None

def get_active_sessions():
    """Получение активных сессий"""
    try:
        conn = db.get_connection()
        cursor = conn.cursor()
        
        # Сессии за последние 24 часа
        day_ago = (datetime.now() - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
        cursor.execute(
            "SELECT COUNT(*) as active_sessions FROM user_sessions WHERE last_activity >= ?",
            (day_ago,)
        )
        active = cursor.fetchone()[0]
        
        # Всего сессий
        cursor.execute("SELECT COUNT(*) as total_sessions FROM user_sessions")
        total = cursor.fetchone()[0]
        
        conn.close()
        return active, total
    except Exception as e:
        logger.error(f"Ошибка получения сессий: {e}")
        return 0, 0

# =========== КЛАВИАТУРЫ ===========
def get_main_keyboard():
    """Главное меню для пользователей"""
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📝 Заполнить анкету")],
            [KeyboardButton(text="📋 Моя анкета"), KeyboardButton(text="📨 Написать менеджеру")],
            [KeyboardButton(text="💬 Оставить отзыв"), KeyboardButton(text="📊 Статус заявок")],
            [KeyboardButton(text="ℹ️ О компании"), KeyboardButton(text="❓ Помощь")],
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
            [KeyboardButton(text="✅ Обработанные"), KeyboardButton(text="📁 Архив")],
            [KeyboardButton(text="📁 Отправить файл"), KeyboardButton(text="💬 Написать клиенту")],
            [KeyboardButton(text="📤 Рассылка"), KeyboardButton(text="📈 Статистика")],
            [KeyboardButton(text="👥 Пользователи"), KeyboardButton(text="🔧 Управление")],
            [KeyboardButton(text="⬅️ В меню")]
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
            [KeyboardButton(text="📊 Системный отчет"), KeyboardButton(text="🗑️ Очистка БД")],
            [KeyboardButton(text="⬅️ Назад")]
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

def get_questionnaire_detail_keyboard(questionnaire_id: int, current_page: int = 1, status: str = None):
    """Клавиатура для детального просмотра анкеты"""
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Обработано", callback_data=f"status_{questionnaire_id}_processed_{current_page}_{status}"),
                InlineKeyboardButton(text="📁 Архив", callback_data=f"status_{questionnaire_id}_archived_{current_page}_{status}")
            ],
            [
                InlineKeyboardButton(text="💬 Комментарий", callback_data=f"comment_{questionnaire_id}_{current_page}_{status}"),
                InlineKeyboardButton(text="📨 Написать", callback_data=f"write_{questionnaire_id}")
            ],
            [
                InlineKeyboardButton(text="🔄 Статус", callback_data=f"check_status_{questionnaire_id}"),
                InlineKeyboardButton(text="📝 Редактировать", callback_data=f"edit_{questionnaire_id}")
            ],
            [
                InlineKeyboardButton(text="⬅️ Назад", callback_data=f"back_to_list_{current_page}_{status}")
            ]
        ]
    )
    return keyboard

def get_users_list_keyboard(users, page: int = 1, per_page: int = 10):
    """Клавиатура для списка пользователей"""
    keyboard_buttons = []
    
    start_idx = (page - 1) * per_page
    end_idx = start_idx + per_page
    page_users = users[start_idx:end_idx]
    
    for user in page_users:
        username = user.get('username', f'ID: {user.get("user_id")}')
        if len(username) > 20:
            username = username[:17] + "..."
        
        keyboard_buttons.append([
            InlineKeyboardButton(
                text=f"👤 {username}",
                callback_data=f"user_{user.get('user_id')}"
            )
        ])
    
    # Кнопки пагинации
    pagination_buttons = []
    total_pages = (len(users) + per_page - 1) // per_page
    
    if page > 1:
        pagination_buttons.append(
            InlineKeyboardButton(text="⬅️", callback_data=f"users_page_{page-1}")
        )
    
    pagination_buttons.append(
        InlineKeyboardButton(text=f"{page}/{total_pages}", callback_data="current_page")
    )
    
    if page < total_pages:
        pagination_buttons.append(
            InlineKeyboardButton(text="➡️", callback_data=f"users_page_{page+1}")
        )
    
    if pagination_buttons:
        keyboard_buttons.append(pagination_buttons)
    
    keyboard_buttons.append([
        InlineKeyboardButton(text="📤 Рассылка", callback_data="mailing_to_all"),
        InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_admin")
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)

def get_quick_actions_keyboard():
    """Клавиатура быстрых действий для админа"""
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🔄 Обновить"), KeyboardButton(text="📊 Статистика")],
            [KeyboardButton(text="📨 Ответить"), KeyboardButton(text="✅ Обработать")],
            [KeyboardButton(text="📤 Рассылка"), KeyboardButton(text="🔙 Назад")]
        ],
        resize_keyboard=True,
        one_time_keyboard=True
    )
    return keyboard

def get_statistics_keyboard():
    """Клавиатура для статистики"""
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📊 Ежедневный отчет", callback_data="daily_report"),
                InlineKeyboardButton(text="📈 Графики", callback_data="charts")
            ],
            [
                InlineKeyboardButton(text="📤 Экспорт CSV", callback_data="export_stats"),
                InlineKeyboardButton(text="🔄 Обновить", callback_data="refresh_stats")
            ],
            [
                InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_admin")
            ]
        ]
    )
    return keyboard

def get_user_detail_keyboard(user_id: int):
    """Клавиатура для детальной информации о пользователе"""
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="💬 Написать", callback_data=f"write_user_{user_id}"),
                InlineKeyboardButton(text="📊 Все анкеты", callback_data=f"all_quests_{user_id}")
            ],
            [
                InlineKeyboardButton(text="📤 Сделать рассылку", callback_data=f"mailing_user_{user_id}"),
                InlineKeyboardButton(text="📋 Статистика", callback_data=f"stats_user_{user_id}")
            ],
            [
                InlineKeyboardButton(text="⬅️ Назад к списку", callback_data="users_page_1")
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

class UserFeedback(StatesGroup):
    waiting_for_feedback = State()
    waiting_for_feedback_text = State()

class UserMessageToAdmin(StatesGroup):
    waiting_for_message_text = State()

# =========== ФУНКЦИИ ФОРМАТИРОВАНИЯ ===========
def format_questionnaire_detail(questionnaire) -> str:
    """Форматирование детальной информации об анкете"""
    status_icons = {
        'new': '🆕',
        'processed': '✅',
        'archived': '📁'
    }
    
    status_icon = status_icons.get(questionnaire['status'], '📋')
    
    response = f"""
{status_icon} <b>Анкета #{questionnaire['id']}</b>

<b>👤 Данные клиента:</b>
• ID пользователя: {questionnaire['user_id']}
• Username: @{questionnaire['username']}
• ФИО: {questionnaire['full_name']}
• Компания: {questionnaire['company_name']}
• ИНН: {questionnaire['inn']}
• Контактное лицо: {questionnaire['contact_person']}
• Телефон: {questionnaire['phone']}
• Email: {questionnaire['email']}

<b>📊 Параметры поиска:</b>
• Сфера деятельности: {questionnaire['activity_sphere']}
• Ключевые слова: {questionnaire['industry']}
• Бюджет контрактов: {questionnaire['contract_amount']}
• Регионы работы: {questionnaire['regions']}

<b>📈 Статус:</b> {questionnaire['status']} {status_icon}
<b>⭐ Отзыв:</b> {'Да' if questionnaire['feedback_given'] else 'Нет'}
<b>📅 Дата создания:</b> {questionnaire['created_at'][:16]}
<b>🔄 Дата обновления:</b> {questionnaire['updated_at'][:16] if questionnaire['updated_at'] else 'Нет'}
"""
    
    if questionnaire['admin_comment']:
        response += f"\n<b>💬 Комментарий админа:</b>\n{questionnaire['admin_comment']}\n"
    
    if questionnaire['feedback_given'] and questionnaire['feedback_text']:
        feedback_type = "✅ Положительный" if "отлично" in questionnaire['feedback_text'].lower() else "📝 С замечаниями"
        response += f"\n<b>📝 Отзыв клиента ({feedback_type}):</b>\n"
        response += f"{questionnaire['feedback_text'][:200]}...\n"
        response += f"📅 Дата отзыва: {questionnaire['feedback_date'][:16] if questionnaire['feedback_date'] else 'Нет'}"
    
    return response

def format_user_detail(user_id: int, username: str, stats: dict, questionnaires: list) -> str:
    """Форматирование детальной информации о пользователе"""
    response = f"""
<b>👤 Информация о пользователе</b>

<b>Основные данные:</b>
• ID: {user_id}
• Username: @{username}
• Первая активность: {stats['first_activity'][:16] if stats['first_activity'] else 'Нет'}
• Последняя активность: {stats['last_activity'][:16] if stats['last_activity'] else 'Нет'}

<b>📊 Статистика:</b>
• Всего анкет: {stats['total'] or 0}
• Новые: {stats['new'] or 0}
• Обработанные: {stats['processed'] or 0}
• В архиве: {stats['archived'] or 0}
• Отзывов: {stats['feedback'] or 0}

<b>📝 Последние анкеты:</b>
"""
    
    if questionnaires:
        for q in questionnaires[:3]:  # Показываем 3 последние
            status_icon = "🆕" if q['status'] == 'new' else "✅" if q['status'] == 'processed' else "📁"
            response += f"\n#{q['id']} - {q['company_name']} {status_icon}"
            response += f"\n📅 {q['created_at'][:10]} | 📞 {q['phone']}"
            response += "\n─" * 20
    else:
        response += "\nУ пользователя нет анкет"
    
    return response

def format_statistics_detailed(stats: dict, daily_stats: list, hour_stats: list, top_users: list) -> str:
    """Форматирование детальной статистики"""
    response = f"""
📈 <b>Детальная статистика</b>

<b>📊 Общая статистика:</b>
• Всего анкет: {stats['total_questionnaires']}
• Новые: {stats['new_questionnaires']}
• Обработанные: {stats['processed_questionnaires']}
• Уникальных пользователей: {stats['total_users']}
• Отзывов: {stats['feedback_count']}
• За последние 7 дней: {stats['last_week']}

<b>📤 Рассылки:</b>
• Всего рассылок: {stats['total_mailings']}
• Отправлено сообщений: {stats['total_sent']}
• Активных пользователей (30 дней): {stats['active_users']}

<b>📅 Статистика по дням (7 дней):</b>
"""
    
    for day in daily_stats[:5]:  # Показываем последние 5 дней
        response += f"• {day['date']}: {day['count']} ({day['new']} новых, {day['processed']} обработано)\n"
    
    response += "\n<b>⏰ Активное время (последние 30 дней):</b>\n"
    active_hours = []
    for hour in hour_stats:
        if hour['count'] > 0:
            active_hours.append(f"{hour['hour']}:00 - {hour['count']}")
    
    if active_hours:
        response += ", ".join(active_hours[:10]) + "..."
    else:
        response += "Нет данных"
    
    response += "\n\n<b>👥 Топ активных пользователей:</b>\n"
    for user in top_users:
        response += f"• @{user['username'] or user['user_id']}: {user['quest_count']} анкет\n"
    
    # Системная информация
    active_sessions, total_sessions = get_active_sessions()
    response += f"""
    
<b>💾 Системная информация:</b>
• База данных: {config.DB_PATH}
• Бэкапов: {len(os.listdir(config.BACKUP_DIR)) if os.path.exists(config.BACKUP_DIR) else 0}
• Активных сессий: {active_sessions} из {total_sessions}
• Рассылка активна: {'Да' if mailing_data['active'] else 'Нет'}
"""
    
    return response

# =========== ОБРАБОТЧИКИ КОМАНД ===========
@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    """Обработка команды /start"""
    user_id = message.from_user.id
    await state.clear()
    
    if user_id == config.ADMIN_ID:
        update_user_menu(user_id, "admin")
        await message.answer(
            "👑 <b>Панель администратора ТРИТИКА</b>\n\n"
            "Добро пожаловать в расширенную админ-панель!\n\n"
            "<b>📊 Основные функции:</b>\n"
            "• Просмотр и управление заявками\n"
            "• Работа с пользователями\n"
            "• Массовые рассылки\n"
            "• Детальная статистика\n\n"
            "<b>⚡ Быстрые действия:</b>\n"
            "• 🔄 Обновить - обновить текущий раздел\n"
            "• 📊 Статистика - подробная аналитика\n"
            "• 📨 Ответить - быстрый ответ на сообщение\n\n"
            "Используйте кнопки ниже:",
            reply_markup=get_admin_keyboard()
        )
    else:
        update_user_menu(user_id, "main")
        await message.answer(
            "🏢 <b>Добро пожаловать в бот ООО 'Тритика'!</b>\n\n"
            "Мы помогаем находить выгодные тендеры для вашего бизнеса.\n\n"
            "<b>🚀 Новые возможности:</b>\n"
            "• 📋 Моя анкета - просмотр текущей анкеты\n"
            "• 📊 Статус заявок - отслеживание всех ваших заявок\n"
            "• ❓ Помощь - ответы на частые вопросы\n\n"
            "Нажмите <b>'📝 Заполнить анкету'</b> чтобы начать!",
            reply_markup=get_main_keyboard()
        )

@dp.message(Command("menu"))
async def cmd_menu(message: types.Message, state: FSMContext):
    """Команда для возврата в меню"""
    await cmd_start(message, state)

@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    """Команда помощи"""
    await help_command(message)

@dp.message(Command("stats"))
async def cmd_stats(message: types.Message):
    """Команда статистики для админа"""
    if message.from_user.id == config.ADMIN_ID:
        await admin_statistics_detailed(message)

# =========== ОБРАБОТКА КНОПКИ НАЗАД ===========
@dp.message(F.text == "⬅️ В меню")
async def back_to_main_menu(message: types.Message, state: FSMContext):
    """Возврат в главное меню"""
    await cmd_start(message, state)

@dp.message(F.text == "⬅️ Назад")
async def go_back(message: types.Message, state: FSMContext):
    """Обработка кнопки Назад"""
    user_id = message.from_user.id
    current_state = await state.get_state()
    
    # Если есть активное состояние - отменяем его
    if current_state:
        await cancel_action(message, state)
        return
    
    # Определяем предыдущее меню
    if user_id == config.ADMIN_ID:
        current_menu = get_current_user_menu(user_id)
        
        if current_menu == "management":
            update_user_menu(user_id, "admin")
            await message.answer(
                "👑 <b>Админ-меню</b>\n\n"
                "Выберите действие:",
                reply_markup=get_admin_keyboard()
            )
        elif current_menu == "admin":
            await cmd_start(message, state)
        else:
            update_user_menu(user_id, "admin")
            await message.answer(
                "👑 <b>Админ-меню</b>\n\n"
                "Выберите действие:",
                reply_markup=get_admin_keyboard()
            )
    else:
        update_user_menu(user_id, "main")
        await message.answer(
            "🏠 <b>Главное меню</b>\n\n"
            "Выберите действие:",
            reply_markup=get_main_keyboard()
        )

# =========== ПОЛЬЗОВАТЕЛЬ: ГЛАВНОЕ МЕНЮ ===========
@dp.message(F.text == "ℹ️ О компании")
async def about_company(message: types.Message):
    """Информация о компании"""
    update_user_menu(message.from_user.id, "about")
    
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
"""
    
    await message.answer(response, reply_markup=get_main_keyboard())

@dp.message(F.text == "❓ Помощь")
async def help_command(message: types.Message):
    """Команда помощи"""
    response = """
🤝 <b>Помощь по боту</b>

<b>Основные функции:</b>
• 📝 <b>Заполнить анкету</b> - создать новую заявку на поиск тендеров
• 📋 <b>Моя анкета</b> - просмотреть текущую анкету и ее статус
• 📊 <b>Статус заявок</b> - просмотреть все ваши заявки и их статусы
• 📨 <b>Написать менеджеру</b> - задать вопрос менеджеру
• 💬 <b>Оставить отзыв</b> - оставить отзыв о нашей работе

<b>Частые вопросы:</b>
<b>Q:</b> Сколько времени занимает обработка заявки?
<b>A:</b> Обычно в течение 1 часа. В пиковые периоды - до 24 часов.

<b>Q:</b> Как часто можно отправлять заявки?
<b>A:</b> Вы можете отправлять неограниченное количество заявок.

<b>Q:</b> Как связаться с менеджером?
<b>A:</b> Используйте кнопку "📨 Написать менеджеру" или напишите на info@tritika.ru

<b>Контакты поддержки:</b>
📞 Телефон: +7 (904) 653-69-87
📧 Email: info@tritika.ru
🌐 Сайт: https://tritika.ru/

<b>Рабочее время:</b>
Пн-Пт: 9:00-18:00 (МСК)
"""
    
    await message.answer(response, reply_markup=get_main_keyboard())

@dp.message(F.text == "🔄 Обновить")
async def refresh_data(message: types.Message):
    """Обновление данных"""
    user_id = message.from_user.id
    
    if user_id == config.ADMIN_ID:
        current_menu = get_current_user_menu(user_id)
        
        if current_menu == "all_requests":
            await admin_all_requests(message)
        elif current_menu == "new_requests":
            await admin_new_requests(message)
        elif current_menu == "processed_requests":
            await processed_requests(message)
        elif current_menu == "archived_requests":
            await archived_requests(message)
        elif current_menu == "statistics":
            await admin_statistics_detailed(message)
        elif current_menu == "users_list":
            await admin_users_list(message)
        else:
            await message.answer("Данные обновлены.", reply_markup=get_admin_keyboard())
    else:
        # Для обычных пользователей
        current_menu = get_current_user_menu(user_id)
        
        if current_menu == "my_questionnaire":
            await my_questionnaire(message)
        elif current_menu == "my_requests":
            await my_requests_status(message)
        else:
            await message.answer("✅ Данные обновлены", reply_markup=get_main_keyboard())

# =========== ЗАПОЛНЕНИЕ АНКЕТЫ ===========
@dp.message(F.text == "📝 Заполнить анкету")
async def start_questionnaire(message: types.Message, state: FSMContext):
    """Начало заполнения анкеты"""
    if message.from_user.id == config.ADMIN_ID:
        await message.answer("Вы администратор, вам не нужно заполнять анкету.", reply_markup=get_admin_keyboard())
        return
    
    # Проверяем, заполняет ли пользователь уже анкету
    current_data = await state.get_data()
    if current_data.get('questionnaire_started'):
        # Продолжаем с того места, где остановились
        current_state = await state.get_state()
        state_map = {
            "Questionnaire:waiting_for_name": "ФИО",
            "Questionnaire:waiting_for_company": "Название компании",
            "Questionnaire:waiting_for_inn": "ИНН",
            "Questionnaire:waiting_for_contact": "Контактное лицо",
            "Questionnaire:waiting_for_phone": "Телефон",
            "Questionnaire:waiting_for_email": "Email",
            "Questionnaire:waiting_for_activity": "Сфера деятельности",
            "Questionnaire:waiting_for_industry": "Ключевые слова",
            "Questionnaire:waiting_for_amount": "Бюджет",
            "Questionnaire:waiting_for_regions": "Регионы"
        }
        
        current_field = state_map.get(str(current_state), "начало")
        await message.answer(
            f"📝 <b>Вы уже заполняете анкету!</b>\n\n"
            f"Текущий шаг: <b>{current_field}</b>\n"
            f"Продолжайте заполнение или нажмите ❌ Отменить.",
            reply_markup=get_cancel_keyboard()
        )
        return
    
    # Начинаем новую анкету
    await state.update_data(questionnaire_started=True)
    update_user_menu(message.from_user.id, "questionnaire")
    
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
        update_user_menu(message.from_user.id, "main")
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

# =========== ПОЛЬЗОВАТЕЛЬ: МОЯ АНКЕТА ===========
@dp.message(F.text == "📋 Моя анкета")
async def my_questionnaire(message: types.Message):
    """Просмотр своей анкеты"""
    user_id = message.from_user.id
    
    if user_id == config.ADMIN_ID:
        await message.answer("Вы администратор, используйте админ-меню.", reply_markup=get_admin_keyboard())
        return
    
    questionnaire = get_questionnaire_by_user_id(user_id)
    
    if not questionnaire:
        await message.answer(
            "📭 У вас еще нет заполненной анкеты.\n\n"
            "Нажмите <b>'📝 Заполнить анкету'</b> чтобы создать первую заявку!",
            reply_markup=get_main_keyboard()
        )
        return
    
    status_icons = {
        'new': '🆕',
        'processed': '✅',
        'archived': '📁'
    }
    
    status_icon = status_icons.get(questionnaire['status'], '📋')
    
    response = f"""
{status_icon} <b>Моя анкета #{questionnaire['id']}</b>

<b>📋 Основная информация:</b>
• Статус: {questionnaire['status']} {status_icon}
• Дата создания: {questionnaire['created_at'][:16]}
• Дата обновления: {questionnaire['updated_at'][:16] if questionnaire['updated_at'] else 'Нет'}

<b>👤 Мои данные:</b>
• ФИО: {questionnaire['full_name']}
• Компания: {questionnaire['company_name']}
• ИНН: {questionnaire['inn']}
• Контактное лицо: {questionnaire['contact_person']}
• Телефон: {questionnaire['phone']}
• Email: {questionnaire['email']}

<b>🎯 Параметры поиска:</b>
• Сфера деятельности: {questionnaire['activity_sphere']}
• Ключевые слова: {questionnaire['industry']}
• Бюджет контрактов: {questionnaire['contract_amount']}
• Регионы работы: {questionnaire['regions']}

"""
    
    if questionnaire['admin_comment']:
        response += f"<b>💬 Комментарий менеджера:</b>\n{questionnaire['admin_comment']}\n\n"
    
    if questionnaire['feedback_given']:
        feedback_type = "✅ Положительный" if "отлично" in questionnaire['feedback_text'].lower() else "📝 С замечаниями"
        response += f"<b>⭐ Ваш отзыв:</b> {feedback_type}\n"
        if len(questionnaire['feedback_text']) > 50:
            response += f"{questionnaire['feedback_text'][:50]}...\n"
    
    # Получаем статистику пользователя
    stats = get_user_statistics(user_id)
    if stats:
        response += f"\n<b>📊 Ваша статистика:</b>\n"
        response += f"• Всего заявок: {stats['total_questionnaires']}\n"
        response += f"• Новые: {stats['new_count']}\n"
        response += f"• Обработанные: {stats['processed_count']}\n"
        response += f"• Отзывы: {stats['feedback_count']}\n"
    
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📝 Заполнить новую"), KeyboardButton(text="📨 Написать менеджеру")],
            [KeyboardButton(text="🔄 Обновить"), KeyboardButton(text="📊 Статус заявок")],
            [KeyboardButton(text="⬅️ Назад")]
        ],
        resize_keyboard=True
    )
    
    await message.answer(response, reply_markup=keyboard)

@dp.message(F.text == "📊 Статус заявок")
async def my_requests_status(message: types.Message):
    """Просмотр статуса всех заявок пользователя"""
    user_id = message.from_user.id
    
    if user_id == config.ADMIN_ID:
        await message.answer("Вы администратор, используйте админ-меню.", reply_markup=get_admin_keyboard())
        return
    
    try:
        conn = db.get_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT id, status, created_at, updated_at, admin_comment
            FROM questionnaires 
            WHERE user_id = ? 
            ORDER BY created_at DESC
            LIMIT 10
        ''', (user_id,))
        
        questionnaires = cursor.fetchall()
        conn.close()
        
        if not questionnaires:
            await message.answer(
                "📭 У вас еще нет заявок.\n\n"
                "Нажмите <b>'📝 Заполнить анкету'</b> чтобы создать первую заявку!",
                reply_markup=get_main_keyboard()
            )
            return
        
        response = "📊 <b>Статус ваших заявок:</b>\n\n"
        
        status_translation = {
            'new': '🆕 Новая',
            'processed': '✅ Обработана',
            'archived': '📁 В архиве'
        }
        
        for q in questionnaires:
            status = status_translation.get(q['status'], q['status'])
            response += f"<b>#{q['id']}</b> - {status}\n"
            response += f"📅 Создана: {q['created_at'][:16]}\n"
            
            if q['updated_at'] and q['updated_at'] != q['created_at']:
                response += f"🔄 Обновлена: {q['updated_at'][:16]}\n"
            
            if q['admin_comment']:
                response += f"💬 Комментарий: {q['admin_comment'][:50]}...\n"
            
            response += "─" * 30 + "\n"
        
        response += f"\n<b>Всего заявок:</b> {len(questionnaires)}"
        
        await message.answer(response, reply_markup=get_main_keyboard())
        
    except Exception as e:
        await message.answer("❌ Ошибка при получении статуса заявок.")

# =========== ПОЛЬЗОВАТЕЛЬ: НАПИСАТЬ МЕНЕДЖЕРУ ===========
@dp.message(F.text == "📨 Написать менеджеру")
async def write_to_manager_start(message: types.Message, state: FSMContext):
    """Начало отправки сообщения менеджеру"""
    if message.from_user.id == config.ADMIN_ID:
        await message.answer("Вы администратор, используйте админ-меню.", reply_markup=get_admin_keyboard())
        return
    
    update_user_menu(message.from_user.id, "write_to_manager")
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
        
        update_user_menu(user_id, "main")
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
    
    update_user_menu(message.from_user.id, "feedback")
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
        
        update_user_menu(message.from_user.id, "main")
        
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

# =========== АДМИН: ПРОСМОТР ЗАЯВОК ===========
@dp.message(F.text == "📊 Все заявки")
async def admin_all_requests(message: types.Message):
    """Показываем все заявки админу"""
    if message.from_user.id != config.ADMIN_ID:
        return
    
    update_user_menu(message.from_user.id, "all_requests")
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
    
    update_user_menu(message.from_user.id, "new_requests")
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

@dp.message(F.text == "✅ Обработанные")
async def processed_requests(message: types.Message):
    """Просмотр обработанных заявок"""
    if message.from_user.id != config.ADMIN_ID:
        return
    
    update_user_menu(message.from_user.id, "processed_requests")
    questionnaires, total, total_pages = get_questionnaires(status='processed', page=1)
    
    if not questionnaires:
        await message.answer("✅ Обработанных заявок пока нет.", reply_markup=get_admin_keyboard())
        return
    
    response = f"✅ <b>Обработанные заявки (страница 1/{total_pages}):</b>\n\n"
    
    for q in questionnaires[:5]:
        response += f"""
<b>#{q['id']}</b> - {q['company_name']} ({q['inn']})
👤 ID: {q['user_id']} | @{q['username']}
📞 Телефон: {q['phone']}
📧 Email: {q['email']}
📅 {q['created_at'][:10]}
🔄 Обновлено: {q['updated_at'][:10] if q['updated_at'] else 'Нет'}
──────────────────────
"""
    
    if len(questionnaires) > 5:
        response += f"\n... и еще {len(questionnaires) - 5} заявок"
    
    keyboard = get_pagination_keyboard(1, total_pages, 'processed')
    if keyboard:
        await message.answer(response, reply_markup=keyboard)
    else:
        await message.answer(response, reply_markup=get_admin_keyboard())

@dp.message(F.text == "📁 Архив")
async def archived_requests(message: types.Message):
    """Просмотр архивных заявок"""
    if message.from_user.id != config.ADMIN_ID:
        return
    
    update_user_menu(message.from_user.id, "archived_requests")
    questionnaires, total, total_pages = get_questionnaires(status='archived', page=1)
    
    if not questionnaires:
        await message.answer("📁 Архив пуст.", reply_markup=get_admin_keyboard())
        return
    
    response = f"📁 <b>Архивные заявки (страница 1/{total_pages}):</b>\n\n"
    
    for q in questionnaires[:5]:
        response += f"""
<b>#{q['id']}</b> - {q['company_name']} ({q['inn']})
👤 ID: {q['user_id']} | @{q['username']}
📞 Телефон: {q['phone']}
📧 Email: {q['email']}
📅 Создана: {q['created_at'][:10]}
🔄 Архив: {q['updated_at'][:10] if q['updated_at'] else 'Нет'}
──────────────────────
"""
    
    if len(questionnaires) > 5:
        response += f"\n... и еще {len(questionnaires) - 5} заявок"
    
    keyboard = get_pagination_keyboard(1, total_pages, 'archived')
    if keyboard:
        await message.answer(response, reply_markup=keyboard)
    else:
        await message.answer(response, reply_markup=get_admin_keyboard())

# =========== АДМИН: ПАГИНАЦИЯ ===========
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
        
        status_titles = {
            'new': "🆕 Новые заявки",
            'processed': "✅ Обработанные заявки",
            'archived': "📁 Архивные заявки",
            None: "📊 Все заявки"
        }
        
        title = status_titles.get(status, "📊 Заявки")
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
        
        # Добавляем кнопки для детального просмотра
        inline_buttons = []
        for q in questionnaires[:3]:  # Показываем кнопки для первых 3 анкет
            inline_buttons.append([
                InlineKeyboardButton(
                    text=f"🔍 #{q['id']} - {q['company_name'][:15]}...",
                    callback_data=f"quest_detail_{q['id']}_{page}_{status}"
                )
            ])
        
        if inline_buttons:
            # Объединяем клавиатуры
            if keyboard:
                keyboard.inline_keyboard.extend(inline_buttons)
            else:
                keyboard = InlineKeyboardMarkup(inline_keyboard=inline_buttons)
        
        await callback.message.edit_text(response, reply_markup=keyboard)
        await callback.answer()
        
    except Exception as e:
        logger.error(f"Ошибка пагинации: {e}")
        await callback.answer("Ошибка пагинации")

@dp.callback_query(F.data.startswith("quest_detail_"))
async def handle_questionnaire_detail(callback: types.CallbackQuery):
    """Просмотр деталей анкеты"""
    if callback.from_user.id != config.ADMIN_ID:
        return
    
    try:
        parts = callback.data.split("_")
        quest_id = int(parts[2])
        page = int(parts[3]) if len(parts) > 3 else 1
        status = parts[4] if len(parts) > 4 else None
        
        questionnaire = get_questionnaire_by_id(quest_id)
        
        if not questionnaire:
            await callback.answer("Анкета не найдена")
            return
        
        response = format_questionnaire_detail(questionnaire)
        keyboard = get_questionnaire_detail_keyboard(quest_id, page, status)
        
        await callback.message.edit_text(response, reply_markup=keyboard)
        await callback.answer()
        
    except Exception as e:
        logger.error(f"Ошибка просмотра деталей анкеты: {e}")
        await callback.answer("❌ Ошибка")

@dp.callback_query(F.data.startswith("status_"))
async def handle_status_change(callback: types.CallbackQuery):
    """Изменение статуса анкеты"""
    if callback.from_user.id != config.ADMIN_ID:
        return
    
    try:
        parts = callback.data.split("_")
        quest_id = int(parts[1])
        new_status = parts[2]
        page = int(parts[3]) if len(parts) > 3 else 1
        status_filter = parts[4] if len(parts) > 4 else None
        
        status_names = {
            'processed': '✅ Обработано',
            'archived': '📁 В архив',
            'new': '🆕 Новая'
        }
        
        success = update_questionnaire_status(quest_id, new_status)
        
        if success:
            # Обновляем сообщение с деталями анкеты
            questionnaire = get_questionnaire_by_id(quest_id)
            
            if questionnaire:
                response = format_questionnaire_detail(questionnaire)
                keyboard = get_questionnaire_detail_keyboard(quest_id, page, status_filter)
                await callback.message.edit_text(response, reply_markup=keyboard)
            
            await callback.answer(f"✅ Статус изменен на: {status_names.get(new_status, new_status)}")
            
            # Логируем действие
            db.add_admin_log(
                callback.from_user.id, 
                "change_status", 
                f"Анкета #{quest_id} -> {new_status}"
            )
        else:
            await callback.answer("❌ Ошибка изменения статуса")
            
    except Exception as e:
        logger.error(f"Ошибка изменения статуса: {e}")
        await callback.answer("❌ Ошибка")

@dp.callback_query(F.data.startswith("back_to_list_"))
async def back_to_list(callback: types.CallbackQuery):
    """Возврат к списку"""
    if callback.from_user.id != config.ADMIN_ID:
        return
    
    try:
        parts = callback.data.split("_")
        page = int(parts[3])
        status = parts[4] if len(parts) > 4 else None
        
        # Используем существующий обработчик пагинации
        callback_data = f"page_{page}_{status}" if status else f"page_{page}"
        
        questionnaires, total, total_pages = get_questionnaires(status=status, page=page)
        
        if not questionnaires:
            await callback.answer("Нет заявок на этой странице")
            return
        
        status_titles = {
            'new': "🆕 Новые заявки",
            'processed': "✅ Обработанные заявки",
            'archived': "📁 Архивные заявки",
            None: "📊 Все заявки"
        }
        
        title = status_titles.get(status, "📊 Заявки")
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
        logger.error(f"Ошибка возврата к списку: {e}")
        await callback.answer("❌ Ошибка")

# =========== АДМИН: КОММЕНТАРИИ ===========
@dp.callback_query(F.data.startswith("comment_"))
async def handle_comment_request(callback: types.CallbackQuery, state: FSMContext):
    """Запрос на добавление комментария"""
    if callback.from_user.id != config.ADMIN_ID:
        return
    
    try:
        parts = callback.data.split("_")
        quest_id = int(parts[1])
        page = int(parts[2]) if len(parts) > 2 else 1
        status = parts[3] if len(parts) > 3 else None
        
        await state.update_data(
            comment_quest_id=quest_id,
            comment_page=page,
            comment_status=status
        )
        
        await callback.message.answer(
            f"💬 <b>Добавление комментария к анкете #{quest_id}</b>\n\n"
            "Введите комментарий:",
            reply_markup=get_cancel_keyboard()
        )
        
        await state.set_state(AdminAction.waiting_for_comment)
        await callback.answer()
        
    except Exception as e:
        logger.error(f"Ошибка запроса комментария: {e}")
        await callback.answer("❌ Ошибка")

@dp.message(AdminAction.waiting_for_comment)
async def process_comment(message: types.Message, state: FSMContext):
    """Обработка комментария"""
    if message.text == "❌ Отменить":
        await cancel_action(message, state)
        return
    
    user_data = await state.get_data()
    quest_id = user_data.get('comment_quest_id')
    page = user_data.get('comment_page', 1)
    status = user_data.get('comment_status')
    
    comment = message.text
    
    # Обновляем анкету с комментарием
    success = update_questionnaire_status(quest_id, 'processed', comment)
    
    if success:
        # Возвращаемся к детальному просмотру
        questionnaire = get_questionnaire_by_id(quest_id)
        
        if questionnaire:
            response = format_questionnaire_detail(questionnaire)
            keyboard = get_questionnaire_detail_keyboard(quest_id, page, status)
            await message.answer("✅ Комментарий добавлен!")
            await message.answer(response, reply_markup=keyboard)
            
            db.add_admin_log(
                message.from_user.id,
                "add_comment",
                f"Комментарий к анкете #{quest_id}"
            )
    else:
        await message.answer("❌ Ошибка добавления комментария")
    
    await state.clear()

# =========== АДМИН: ПОЛЬЗОВАТЕЛИ ===========
@dp.message(F.text == "👥 Пользователи")
async def admin_users_list(message: types.Message):
    """Список пользователей для админа"""
    if message.from_user.id != config.ADMIN_ID:
        return
    
    users, total, total_pages = get_users_with_questionnaires(page=1)
    
    if not users:
        await message.answer("👥 Пользователей пока нет.", reply_markup=get_admin_keyboard())
        return
    
    response = f"👥 <b>Пользователи (всего: {total})</b>\n\n"
    
    for user in users[:5]:  # Показываем первые 5
        response += f"""
<b>👤 @{user['username'] or f'ID: {user['user_id']}'}</b>
🆔 ID: {user['user_id']}
📊 Анкет: {user['questionnaire_count']} ({user['new_count']} новых)
⭐ Отзывов: {user['feedback_count']}
📅 Активность: {user['last_activity'][:10]}
──────────────────────
"""
    
    if len(users) > 5:
        response += f"\n... и еще {len(users) - 5} пользователей"
    
    keyboard = get_users_list_keyboard(users, page=1)
    await message.answer(response, reply_markup=keyboard)

@dp.callback_query(F.data.startswith("user_"))
async def handle_user_detail(callback: types.CallbackQuery):
    """Детальная информация о пользователе"""
    if callback.from_user.id != config.ADMIN_ID:
        return
    
    try:
        user_id = int(callback.data.split("_")[1])
        
        conn = db.get_connection()
        cursor = conn.cursor()
        
        # Получаем все анкеты пользователя
        cursor.execute('''
            SELECT * FROM questionnaires 
            WHERE user_id = ? 
            ORDER BY created_at DESC
            LIMIT 10
        ''', (user_id,))
        
        questionnaires = cursor.fetchall()
        
        # Получаем статистику
        cursor.execute('''
            SELECT 
                COUNT(*) as total,
                SUM(CASE WHEN status = 'new' THEN 1 ELSE 0 END) as new,
                SUM(CASE WHEN status = 'processed' THEN 1 ELSE 0 END) as processed,
                SUM(CASE WHEN status = 'archived' THEN 1 ELSE 0 END) as archived,
                SUM(CASE WHEN feedback_given = 1 THEN 1 ELSE 0 END) as feedback,
                MIN(created_at) as first_activity,
                MAX(created_at) as last_activity
            FROM questionnaires 
            WHERE user_id = ?
        ''', (user_id,))
        
        stats = cursor.fetchone()
        
        # Получаем username из первой анкеты
        username = questionnaires[0]['username'] if questionnaires else "Неизвестно"
        
        conn.close()
        
        response = format_user_detail(user_id, username, dict(stats) if stats else {}, questionnaires)
        keyboard = get_user_detail_keyboard(user_id)
        
        await callback.message.edit_text(response, reply_markup=keyboard)
        await callback.answer()
        
    except Exception as e:
        logger.error(f"Ошибка получения данных пользователя: {e}")
        await callback.answer("❌ Ошибка")

@dp.callback_query(F.data.startswith("users_page_"))
async def handle_users_pagination(callback: types.CallbackQuery):
    """Пагинация пользователей"""
    if callback.from_user.id != config.ADMIN_ID:
        return
    
    try:
        page = int(callback.data.split("_")[2])
        users, total, total_pages = get_users_with_questionnaires(page=page)
        
        if not users:
            await callback.answer("Нет пользователей на этой странице")
            return
        
        response = f"👥 <b>Пользователи (страница {page}/{total_pages})</b>\n\n"
        
        for user in users[:5]:
            response += f"""
<b>👤 @{user['username'] or f'ID: {user['user_id']}'}</b>
🆔 ID: {user['user_id']}
📊 Анкет: {user['questionnaire_count']} ({user['new_count']} новых)
⭐ Отзывов: {user['feedback_count']}
📅 Активность: {user['last_activity'][:10]}
──────────────────────
"""
        
        if len(users) > 5:
            response += f"\n... и еще {len(users) - 5} пользователей"
        
        keyboard = get_users_list_keyboard(users, page=page)
        await callback.message.edit_text(response, reply_markup=keyboard)
        await callback.answer()
        
    except Exception as e:
        logger.error(f"Ошибка пагинации пользователей: {e}")
        await callback.answer("❌ Ошибка")

# =========== АДМИН: СТАТИСТИКА ===========
@dp.message(F.text == "📈 Статистика")
async def admin_statistics_detailed(message: types.Message):
    """Детальная статистика для админа"""
    if message.from_user.id != config.ADMIN_ID:
        return
    
    stats = get_statistics()
    
    # Дополнительная статистика
    try:
        conn = db.get_connection()
        cursor = conn.cursor()
        
        # Статистика по дням за последние 7 дней
        cursor.execute('''
            SELECT 
                DATE(created_at) as date,
                COUNT(*) as count,
                SUM(CASE WHEN status = 'new' THEN 1 ELSE 0 END) as new,
                SUM(CASE WHEN status = 'processed' THEN 1 ELSE 0 END) as processed
            FROM questionnaires 
            WHERE created_at >= date('now', '-7 days')
            GROUP BY DATE(created_at)
            ORDER BY date DESC
        ''')
        
        daily_stats = cursor.fetchall()
        
        # Статистика по времени суток
        cursor.execute('''
            SELECT 
                strftime('%H', created_at) as hour,
                COUNT(*) as count
            FROM questionnaires
            WHERE created_at >= date('now', '-30 days')
            GROUP BY strftime('%H', created_at)
            ORDER BY hour
        ''')
        
        hour_stats = cursor.fetchall()
        
        # Топ активных пользователей
        cursor.execute('''
            SELECT 
                user_id,
                username,
                COUNT(*) as quest_count
            FROM questionnaires
            WHERE user_id IS NOT NULL
            GROUP BY user_id
            ORDER BY quest_count DESC
            LIMIT 5
        ''')
        
        top_users = cursor.fetchall()
        
        conn.close()
        
        response = format_statistics_detailed(stats, daily_stats, hour_stats, top_users)
        keyboard = get_statistics_keyboard()
        
        await message.answer(response, reply_markup=keyboard)
        
    except Exception as e:
        logger.error(f"Ошибка получения детальной статистики: {e}")
        await message.answer(f"❌ Ошибка получения статистики: {e}")

@dp.callback_query(F.data == "refresh_stats")
async def refresh_statistics(callback: types.CallbackQuery):
    """Обновление статистики"""
    if callback.from_user.id != config.ADMIN_ID:
        return
    
    await admin_statistics_detailed(callback.message)
    await callback.answer("✅ Статистика обновлена")

@dp.callback_query(F.data == "export_stats")
async def export_statistics(callback: types.CallbackQuery):
    """Экспорт статистики в CSV"""
    if callback.from_user.id != config.ADMIN_ID:
        return
    
    try:
        # Создаем CSV с данными
        output = StringIO()
        writer = csv.writer(output)
        
        # Заголовки
        writer.writerow(['Дата', 'Всего анкет', 'Новых', 'Обработанных', 'Пользователей'])
        
        # Получаем данные за последние 30 дней
        conn = db.get_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT 
                DATE(created_at) as date,
                COUNT(*) as total,
                SUM(CASE WHEN status = 'new' THEN 1 ELSE 0 END) as new,
                SUM(CASE WHEN status = 'processed' THEN 1 ELSE 0 END) as processed,
                COUNT(DISTINCT user_id) as users
            FROM questionnaires 
            WHERE created_at >= date('now', '-30 days')
            GROUP BY DATE(created_at)
            ORDER BY date DESC
        ''')
        
        rows = cursor.fetchall()
        conn.close()
        
        for row in rows:
            writer.writerow([
                row['date'],
                row['total'],
                row['new'],
                row['processed'],
                row['users']
            ])
        
        # Создаем файл
        csv_data = output.getvalue()
        file = BufferedInputFile(csv_data.encode('utf-8'), filename='statistics.csv')
        
        await callback.message.answer_document(
            file,
            caption="📊 Статистика за последние 30 дней"
        )
        
        await callback.answer("✅ Файл экспортирован")
        
    except Exception as e:
        logger.error(f"Ошибка экспорта статистики: {e}")
        await callback.answer("❌ Ошибка экспорта")

# =========== АДМИН: УПРАВЛЕНИЕ ===========
@dp.message(F.text == "🔧 Управление")
async def management_menu(message: types.Message):
    """Меню управления"""
    if message.from_user.id != config.ADMIN_ID:
        return
    
    update_user_menu(message.from_user.id, "management")
    await message.answer(
        "🔧 <b>Управление системой</b>\n\n"
        "Выберите действие:",
        reply_markup=get_management_keyboard()
    )

@dp.message(F.text == "💾 Создать бэкап")
async def create_backup(message: types.Message):
    """Создание бэкапа базы данных"""
    if message.from_user.id != config.ADMIN_ID:
        return
    
    await message.answer("🔄 Создаю резервную копию базы данных...")
    backup_path, sql_backup_path = db.backup_db()
    
    if backup_path:
        try:
            with open(backup_path, 'rb') as f:
                await message.answer_document(
                    BufferedInputFile(f.read(), filename=os.path.basename(backup_path)),
                    caption=f"✅ Бэкап создан: {os.path.basename(backup_path)}"
                )
            
            # Также отправляем SQL бэкап
            if sql_backup_path:
                with open(sql_backup_path, 'rb') as f:
                    await message.answer_document(
                        BufferedInputFile(f.read(), filename=os.path.basename(sql_backup_path)),
                        caption="📝 SQL бэкап базы данных"
                    )
                    
            db.add_admin_log(message.from_user.id, "create_backup", "Создан бэкап БД")
            
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
        log_files = []
        
        # Основной лог
        main_log = os.path.join(config.LOGS_DIR, 'bot.log')
        if os.path.exists(main_log):
            with open(main_log, 'rb') as f:
                await message.answer_document(
                    BufferedInputFile(f.read(), filename='bot.log'),
                    caption="📋 Основные логи бота"
                )
        
        # Ищем другие логи
        for file in os.listdir(config.LOGS_DIR):
            if file.endswith('.log') and file != 'bot.log':
                log_path = os.path.join(config.LOGS_DIR, file)
                with open(log_path, 'rb') as f:
                    await message.answer_document(
                        BufferedInputFile(f.read(), filename=file),
                        caption=f"📋 Лог: {file}"
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
        db.add_admin_log(message.from_user.id, "update_db", "Обновлена структура БД")
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
            
            # Экспорт пользователей
            cursor.execute('''
                SELECT DISTINCT 
                    user_id,
                    username,
                    COUNT(*) as questionnaire_count,
                    MAX(created_at) as last_activity
                FROM questionnaires 
                WHERE user_id IS NOT NULL
                GROUP BY user_id, username
            ''')
            
            users = cursor.fetchall()
            
            if users:
                output = StringIO()
                writer = csv.writer(output)
                
                writer.writerow(['User ID', 'Username', 'Анкет', 'Последняя активность'])
                
                for u in users:
                    writer.writerow([
                        u['user_id'], u['username'], 
                        u['questionnaire_count'], u['last_activity']
                    ])
                
                await message.answer_document(
                    BufferedInputFile(output.getvalue().encode(), filename='users.csv'),
                    caption="👥 Экспорт пользователей"
                )
            
            db.add_admin_log(message.from_user.id, "export_data", "Экспорт данных в CSV")
            
        else:
            await message.answer("Нет данных для экспорта")
        
        conn.close()
        
    except Exception as e:
        await message.answer(f"❌ Ошибка экспорта: {e}")

@dp.message(F.text == "📊 Системный отчет")
async def system_report(message: types.Message):
    """Системный отчет"""
    if message.from_user.id != config.ADMIN_ID:
        return
    
    try:
        # Получаем системную информацию
        import platform
        import psutil
        
        system_info = f"""
<b>🖥️ Системный отчет</b>

<b>Система:</b>
• OS: {platform.system()} {platform.release()}
• Python: {platform.python_version()}
• Архитектура: {platform.architecture()[0]}

<b>💾 Дисковое пространство:</b>
"""
        
        # Информация о диске
        disk_usage = psutil.disk_usage('.')
        system_info += f"• Всего: {disk_usage.total // (1024**3)} GB\n"
        system_info += f"• Использовано: {disk_usage.used // (1024**3)} GB\n"
        system_info += f"• Свободно: {disk_usage.free // (1024**3)} GB\n"
        system_info += f"• Заполнено: {disk_usage.percent}%\n"
        
        system_info += f"""
<b>📊 База данных:</b>
• Файл: {config.DB_PATH}
• Размер: {os.path.getsize(config.DB_PATH) // 1024} KB
• Бэкапов: {len(os.listdir(config.BACKUP_DIR)) if os.path.exists(config.BACKUP_DIR) else 0}

<b>👥 Пользователи:</b>
• Активных сессий: {len(user_menus)}
• В памяти: {len(user_menus)}
"""
        
        # Информация о процессе
        process = psutil.Process()
        system_info += f"• Память процесса: {process.memory_info().rss // 1024 // 1024} MB\n"
        
        # Время работы бота
        if 'start_time' in mailing_data and mailing_data['start_time']:
            uptime = datetime.now() - mailing_data['start_time']
            system_info += f"• Время работы: {uptime}\n"
        
        await message.answer(system_info, reply_markup=get_management_keyboard())
        
    except Exception as e:
        logger.error(f"Ошибка системного отчета: {e}")
        await message.answer(f"❌ Ошибка отчета: {e}")

@dp.message(F.text == "🗑️ Очистка БД")
async def cleanup_database(message: types.Message):
    """Очистка базы данных"""
    if message.from_user.id != config.ADMIN_ID:
        return
    
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🗑️ Удалить старые анкеты", callback_data="cleanup_old"),
                InlineKeyboardButton(text="🗑️ Очистить логи", callback_data="cleanup_logs")
            ],
            [
                InlineKeyboardButton(text="🗑️ Оптимизировать БД", callback_data="cleanup_optimize"),
                InlineKeyboardButton(text="❌ Отмена", callback_data="cleanup_cancel")
            ]
        ]
    )
    
    await message.answer(
        "🗑️ <b>Очистка базы данных</b>\n\n"
        "Выберите действие:",
        reply_markup=keyboard
    )

@dp.callback_query(F.data == "cleanup_old")
async def cleanup_old_questionnaires(callback: types.CallbackQuery):
    """Удаление старых анкет"""
    if callback.from_user.id != config.ADMIN_ID:
        return
    
    try:
        conn = db.get_connection()
        cursor = conn.cursor()
        
        # Удаляем анкеты старше 90 дней
        ninety_days_ago = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")
        
        cursor.execute("SELECT COUNT(*) FROM questionnaires WHERE created_at < ? AND status = 'archived'", (ninety_days_ago,))
        count = cursor.fetchone()[0]
        
        if count > 0:
            cursor.execute("DELETE FROM questionnaires WHERE created_at < ? AND status = 'archived'", (ninety_days_ago,))
            conn.commit()
            await callback.message.answer(f"✅ Удалено {count} старых анкет")
            db.add_admin_log(callback.from_user.id, "cleanup", f"Удалено {count} старых анкет")
        else:
            await callback.message.answer("📭 Нет старых анкет для удаления")
        
        conn.close()
        
    except Exception as e:
        logger.error(f"Ошибка очистки анкет: {e}")
        await callback.message.answer("❌ Ошибка очистки")
    
    await callback.answer()

# =========== АДМИН: ВЫГРУЗКА ТЕНДЕРОВ ===========
@dp.message(F.text == "📁 Отправить файл")
async def send_tenders_start(message: types.Message, state: FSMContext):
    """Начало процесса отправки файла клиенту"""
    if message.from_user.id != config.ADMIN_ID:
        return
    
    update_user_menu(message.from_user.id, "send_file")
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
                "INSERT INTO sent_files (questionnaire_id, file_name, file_id, sent_by, sent_at) VALUES (?, ?, ?, ?, ?)",
                (questionnaire['id'], file_name, file_id, message.from_user.id, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            )
            
            # Обновляем статус анкеты
            cursor.execute(
                "UPDATE questionnaires SET status = 'processed', updated_at = ? WHERE id = ?",
                (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), questionnaire['id'])
            )
            conn.commit()
        
        conn.close()
        
        update_user_menu(message.from_user.id, "admin")
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
    
    update_user_menu(message.from_user.id, "write_to_client")
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
        
        update_user_menu(message.from_user.id, "admin")
        await message.answer(
            f"✅ Сообщение отправлено пользователю {user_id}",
            reply_markup=get_admin_keyboard()
        )
        
        db.add_admin_log(message.from_user.id, "send_message", f"Отправлено сообщение пользователю {user_id}")
        
    except Exception as e:
        await message.answer(f"❌ Ошибка отправки сообщения: {e}")
    
    await state.clear()

# =========== АДМИН: РАССЫЛКА ===========
@dp.message(F.text == "📤 Рассылка")
async def start_mailing(message: types.Message, state: FSMContext):
    """Начало процесса рассылки"""
    if message.from_user.id != config.ADMIN_ID:
        return
    
    update_user_menu(message.from_user.id, "mailing")
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
    duration = (datetime.now() - mailing_data['start_time']).total_seconds()
    conn = db.get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO mailings (mailing_date, message_text, total_users, successful_sends, failed_sends, duration_seconds) VALUES (?, ?, ?, ?, ?, ?)",
        (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), mailing_data['message_text'], total, success_count, error_count, duration)
    )
    conn.commit()
    conn.close()
    
    mailing_data['active'] = False
    
    update_user_menu(callback.from_user.id, "admin")
    await callback.message.edit_text(
        f"✅ <b>Рассылка завершена!</b>\n\n"
        f"Всего получателей: {total}\n"
        f"✅ Успешно отправлено: {success_count}\n"
        f"❌ Ошибок: {error_count}\n"
        f"⏱️ Время выполнения: {duration:.1f} сек."
    )
    
    db.add_admin_log(callback.from_user.id, "mailing", f"Рассылка: {success_count}/{total} успешно")

@dp.callback_query(F.data == "cancel_mailing")
async def cancel_mailing(callback: types.CallbackQuery):
    """Отмена рассылки"""
    update_user_menu(callback.from_user.id, "admin")
    await callback.message.edit_text("❌ Рассылка отменена")
    await callback.answer()

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
    user_id = message.from_user.id
    
    if user_id == config.ADMIN_ID:
        update_user_menu(user_id, "admin")
        await message.answer(
            "❌ Действие отменено.",
            reply_markup=get_admin_keyboard()
        )
    else:
        update_user_menu(user_id, "main")
        await message.answer(
            "❌ Действие отменено.",
            reply_markup=get_main_keyboard()
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
            
            response = f"""
Bot Status: {status}
Database: {config.DB_PATH}
Backups: {len(os.listdir(config.BACKUP_DIR)) if os.path.exists(config.BACKUP_DIR) else 0}
Active users: {len(user_menus)}
Mailing active: {mailing_data["active"]}
Uptime: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
"""
            
            self.wfile.write(response.encode())
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
    logger.info("🚀 Запуск улучшенного бота ТРИТИКА...")
    
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
        logger.info(f"✅ Режим: {'ADMIN' if config.ADMIN_ID else 'USER'}")
        
        # Планировщик ежедневных бэкапов
        async def daily_backup():
            while True:
                await asyncio.sleep(24 * 60 * 60)  # 24 часа
                logger.info("🔄 Создание ежедневного бэкапа...")
                db.backup_db()
        
        # Запускаем задачу бэкапа в фоне
        asyncio.create_task(daily_backup())
        
        # Уведомление админа о запуске
        try:
            await bot.send_message(
                config.ADMIN_ID,
                f"🤖 Бот запущен успешно!\n\n"
                f"Версия: 2.0 (Улучшенное меню)\n"
                f"Время: {datetime.now().strftime('%d.%m.%Y %H:%M')}\n"
                f"Пользователей в памяти: {len(user_menus)}"
            )
        except Exception as e:
            logger.error(f"Ошибка отправки уведомления админу: {e}")
        
        # Запускаем polling
        await dp.start_polling(bot, skip_updates=True)
        
    except Exception as e:
        logger.error(f"❌ Ошибка запуска бота: {e}", exc_info=True)
        raise

# =========== ЗАПУСК ПРИЛОЖЕНИЯ ===========
if __name__ == "__main__":
    # Создаем необходимые директории
    os.makedirs(config.BACKUP_DIR, exist_ok=True)
    os.makedirs(config.LOGS_DIR, exist_ok=True)
    
    # Запускаем основную функцию
    asyncio.run(main())
