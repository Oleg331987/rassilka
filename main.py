#!/usr/bin/env python3
"""
🤖 БОТ "ТРИТИКА" (ТЕНДЕРПОИСК)
Интеллектуальный ассистент для поиска тендеров
"""

import os
import asyncio
import logging
import sqlite3
import tempfile
import json
import io
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any, Tuple
from pathlib import Path

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardRemove, BufferedInputFile, FSInputFile
)
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from docx import Document
from docx.shared import Inches

# Импорты для HTTP сервера Railway
from aiohttp import web, ClientSession

# =========== НАСТРОЙКИ ===========
BOT_TOKEN = os.getenv("BOT_TOKEN", "8120629620:AAH2ZjoCPEoE39KRIrf8x9JYhOpScphnKgo")
ADMIN_ID = int(os.getenv("ADMIN_ID", "6003624437")) if os.getenv("ADMIN_ID") else None
PORT = int(os.getenv("PORT", 8080))

# Настройки времени работы (пн-чт 8:30-17:30 пт 8:30-16:30)
WORK_START_HOUR = 9
WORK_END_HOUR = 17
WORK_DAYS = [0, 1, 2, 3, 4]  # Пн-Пт

# Ссылка на файл анкеты в GitHub
ANKETA_GITHUB_URL = "https://github.com/Oleg331987/rassilka/raw/main/Anketa.docx"
ANKETA_LOCAL_PATH = "Anketa.docx"

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

# =========== ФУНКЦИЯ ДЛЯ СОЗДАНИЯ ЗАПОЛНЕННОЙ АНКЕТЫ ===========
def create_filled_anketa(user_data: dict) -> Optional[str]:
    """Создание заполненной анкеты на основе данных пользователя"""
    try:
        # Создаем новый документ
        doc = Document()
        
        # Заголовок
        title = doc.add_heading('Анкета для поиска тендеров', 0)
        title.alignment = 1
        
        # Информация о заполнении
        doc.add_paragraph(f'Дата заполнения: {datetime.now().strftime("%d.%m.%Y %H:%M")}')
        doc.add_paragraph('Заполнено через бота Тритика')
        
        # Информация о компании
        doc.add_heading('Информация о компании', level=1)
        
        # Заполняем поля
        fields = [
            ('1. ФИО полностью:', user_data.get('full_name', 'Не указано')),
            ('2. Название компании:', user_data.get('company_name', 'Не указано')),
            ('3. Телефон для связи:', user_data.get('phone', 'Не указано')),
            ('4. Email для отправки тендеров:', user_data.get('email', 'Не указано')),
            ('5. Сфера деятельности компании:', user_data.get('activity', 'Не указано')),
            ('6. Регионы работы (города, области):', user_data.get('region', 'Не указано')),
            ('7. Предпочтительный бюджет контрактов:', user_data.get('budget', 'Не указано')),
            ('8. Ключевые слова для поиска (через запятую):', user_data.get('keywords', 'Не указано')),
        ]
        
        for label, value in fields:
            p = doc.add_paragraph()
            p.add_run(label).bold = True
            doc.add_paragraph(value)
            doc.add_paragraph()  # Пустая строка
        
        # Подвал
        doc.add_page_break()
        doc.add_paragraph('\n\n')
        doc.add_paragraph('Анкета заполнена через Telegram-бота Тритика')
        doc.add_paragraph('https://t.me/tritika_tender_bot')
        
        # Сохраняем во временный файл
        temp_file = tempfile.NamedTemporaryFile(suffix='.docx', delete=False)
        temp_path = temp_file.name
        doc.save(temp_path)
        temp_file.close()
        
        logger.info(f"✅ Файл анкеты создан: {temp_path}")
        return temp_path
        
    except Exception as e:
        logger.error(f"❌ Ошибка создания заполненной анкеты: {e}")
        return None

# =========== СКАЧИВАНИЕ ФАЙЛА ANKETA.DOCX ===========
async def download_anketa_file():
    """Скачивание файла анкеты с GitHub"""
    try:
        print("⬇️ Скачиваю файл анкеты с GitHub...")
        async with ClientSession() as session:
            async with session.get(ANKETA_GITHUB_URL, timeout=30) as response:
                if response.status == 200:
                    content = await response.read()
                    # Создаем папку, если её нет
                    os.makedirs(os.path.dirname(ANKETA_LOCAL_PATH), exist_ok=True)
                    with open(ANKETA_LOCAL_PATH, 'wb') as f:
                        f.write(content)
                    print(f"✅ Файл анкеты сохранен: {ANKETA_LOCAL_PATH} ({len(content)} байт)")
                    return True
                else:
                    print(f"❌ Ошибка скачивания файла: HTTP {response.status}")
                    return False
    except Exception as e:
        print(f"❌ Ошибка скачивания анкеты: {e}")
        return False

# =========== БАЗА ДАННЫХ ===========
class Database:
    def __init__(self, db_name="tenders.db"):
        self.db_name = db_name
        self.init_db()
    
    def init_db(self):
        """Инициализация базы данных с новыми таблицами"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        
        # Пользователи - добавляем поле для управления рассылкой
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
            region TEXT,
            is_active BOOLEAN DEFAULT 1,
            has_filled_questionnaire BOOLEAN DEFAULT 0,
            mailing_subscribed BOOLEAN DEFAULT 1,  -- Новое: подписка на рассылку
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
            filled_anketa_path TEXT,
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
            file_name TEXT,
            status TEXT DEFAULT 'sent',
            admin_notified BOOLEAN DEFAULT 0,
            follow_up_sent BOOLEAN DEFAULT 0,
            follow_up_at TIMESTAMP,
            follow_up_response TEXT,
            follow_up_scheduled BOOLEAN DEFAULT 0
        )
        ''')
        
        # Рассылки (ручные) - основная таблица
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS manual_mailings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            admin_id INTEGER,
            mailing_text TEXT,
            mailing_type TEXT,
            filter_criteria TEXT,
            sent_count INTEGER DEFAULT 0,
            failed_count INTEGER DEFAULT 0,
            feedback_count INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            sent_at TIMESTAMP
        )
        ''')
        
        # Отправленные сообщения рассылки (каждому пользователю)
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS sent_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mailing_id INTEGER,
            user_id INTEGER,
            telegram_message_id INTEGER,
            sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            feedback_received BOOLEAN DEFAULT 0
        )
        ''')
        
        # Обратная связь по рассылкам
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS mailing_feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mailing_id INTEGER,
            user_id INTEGER,
            sent_message_id INTEGER,
            feedback_type TEXT,  -- like, dislike, comment, unsubscribe
            feedback_text TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        ''')
        
        # Сообщения менеджеру
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS manager_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            message_type TEXT,
            message_text TEXT,
            file_id TEXT,
            file_name TEXT,
            admin_notified BOOLEAN DEFAULT 0,
            processed BOOLEAN DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        ''')
        
        conn.commit()
        conn.close()
        logger.info("✅ База данных инициализирована")
    
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
    
    def save_questionnaire(self, user_id: int, data: dict, anketa_path: str = None):
        """Сохранение анкеты"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        
        cursor.execute('''
        INSERT INTO questionnaires 
        (user_id, full_name, company_name, phone, email, activity, region, budget, keywords, filled_anketa_path)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            user_id,
            data.get('full_name'),
            data.get('company_name'),
            data.get('phone'),
            data.get('email'),
            data.get('activity'),
            data.get('region'),
            data.get('budget'),
            data.get('keywords'),
            anketa_path
        ))
        
        conn.commit()
        last_id = cursor.lastrowid
        
        # Обновляем статус пользователя
        cursor.execute('''
        UPDATE users 
        SET phone = ?, email = ?, company = ?, activity = ?, region = ?, has_filled_questionnaire = 1
        WHERE user_id = ?
        ''', (
            data.get('phone'),
            data.get('email'),
            data.get('company_name'),
            data.get('activity'),
            data.get('region'),
            user_id
        ))
        
        conn.commit()
        conn.close()
        
        return last_id
    
    def create_tender_export(self, questionnaire_id: int, user_id: int, file_path: str = None, file_name: str = None):
        """Создание записи о выгрузке тендеров"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        
        sent_at = datetime.now() if self.is_working_hours() else self.get_next_working_time()
        
        cursor.execute('''
        INSERT INTO tender_exports 
        (questionnaire_id, user_id, sent_at, file_path, file_name, follow_up_scheduled)
        VALUES (?, ?, ?, ?, ?, ?)
        ''', (questionnaire_id, user_id, sent_at, file_path, file_name, 1))
        
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
    
    def save_export_file(self, export_id: int, file_path: str, file_name: str):
        """Сохранение пути к файлу выгрузки"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        
        cursor.execute('''
        UPDATE tender_exports 
        SET file_path = ?, file_name = ?, status = 'sent', sent_at = datetime('now')
        WHERE id = ?
        ''', (file_path, file_name, export_id))
        
        conn.commit()
        conn.close()
    
    def get_exports_for_followup(self):
        """Получение выгрузок, для которых нужно отправить follow-up"""
        conn = sqlite3.connect(self.db_name)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # Выгрузки, отправленные более 1 часа назад, но follow-up еще не отправлен
        one_hour_ago = (datetime.now() - timedelta(hours=1)).strftime('%Y-%m-%d %H:%M:%S')
        
        cursor.execute('''
        SELECT te.*, q.full_name, q.company_name, u.user_id, u.username
        FROM tender_exports te
        JOIN questionnaires q ON te.questionnaire_id = q.id
        JOIN users u ON te.user_id = u.user_id
        WHERE te.status = 'sent' 
        AND te.follow_up_scheduled = 1
        AND te.follow_up_sent = 0
        AND te.sent_at <= ?
        ''', (one_hour_ago,))
        
        exports = cursor.fetchall()
        conn.close()
        
        return exports
    
    def mark_followup_sent(self, export_id: int):
        """Отметка, что follow-up отправлен"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        
        cursor.execute('''
        UPDATE tender_exports 
        SET follow_up_sent = 1, follow_up_at = datetime('now')
        WHERE id = ?
        ''', (export_id,))
        
        conn.commit()
        conn.close()
    
    def save_followup_response(self, export_id: int, response: str):
        """Сохранение ответа на follow-up"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        
        cursor.execute('''
        UPDATE tender_exports 
        SET follow_up_response = ?
        WHERE id = ?
        ''', (response, export_id))
        
        conn.commit()
        conn.close()
    
    # =========== УПРАВЛЕНИЕ РАССЫЛКОЙ ===========
    def toggle_user_mailing_subscription(self, user_id: int):
        """Включение/выключение подписки на рассылку"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        
        # Получаем текущий статус
        cursor.execute('SELECT mailing_subscribed FROM users WHERE user_id = ?', (user_id,))
        current = cursor.fetchone()
        
        if current:
            new_status = not bool(current[0])
            cursor.execute('''
            UPDATE users 
            SET mailing_subscribed = ?
            WHERE user_id = ?
            ''', (1 if new_status else 0, user_id))
            
            conn.commit()
            conn.close()
            return new_status
        
        conn.close()
        return None
    
    def get_user_mailing_status(self, user_id: int):
        """Получение статуса подписки на рассылку"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        
        cursor.execute('''
        SELECT mailing_subscribed, username, first_name, last_name 
        FROM users 
        WHERE user_id = ?
        ''', (user_id,))
        
        result = cursor.fetchone()
        conn.close()
        
        if result:
            return {
                'subscribed': bool(result[0]),
                'username': result[1],
                'first_name': result[2],
                'last_name': result[3]
            }
        return None
    
    def get_users_by_filter(self, filter_type: str):
        """Получение пользователей по фильтру с учетом подписки"""
        conn = sqlite3.connect(self.db_name)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        if filter_type == "all":
            cursor.execute('''
            SELECT user_id, username, first_name, last_name, company, mailing_subscribed
            FROM users 
            WHERE is_active = 1 AND mailing_subscribed = 1
            ''')
        elif filter_type == "with_questionnaire":
            cursor.execute('''
            SELECT user_id, username, first_name, last_name, company, mailing_subscribed
            FROM users 
            WHERE is_active = 1 AND has_filled_questionnaire = 1 AND mailing_subscribed = 1
            ''')
        elif filter_type == "without_questionnaire":
            cursor.execute('''
            SELECT user_id, username, first_name, last_name, company, mailing_subscribed
            FROM users 
            WHERE is_active = 1 AND has_filled_questionnaire = 0 AND mailing_subscribed = 1
            ''')
        elif filter_type == "recent_week":
            cursor.execute('''
            SELECT user_id, username, first_name, last_name, company, mailing_subscribed
            FROM users 
            WHERE is_active = 1 AND mailing_subscribed = 1 
            AND date(created_at) >= date('now', '-7 days')
            ''')
        elif filter_type == "subscribed":
            cursor.execute('''
            SELECT user_id, username, first_name, last_name, company, mailing_subscribed
            FROM users 
            WHERE is_active = 1 AND mailing_subscribed = 1
            ''')
        elif filter_type == "unsubscribed":
            cursor.execute('''
            SELECT user_id, username, first_name, last_name, company, mailing_subscribed
            FROM users 
            WHERE is_active = 1 AND mailing_subscribed = 0
            ''')
        else:
            conn.close()
            return []
        
        users = cursor.fetchall()
        conn.close()
        
        return users
    
    def get_all_users_with_subscription(self, limit: int = 50):
        """Получение всех пользователей с информацией о подписке"""
        conn = sqlite3.connect(self.db_name)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute('''
        SELECT user_id, username, first_name, last_name, company, 
               mailing_subscribed, has_filled_questionnaire, created_at
        FROM users 
        WHERE is_active = 1
        ORDER BY created_at DESC
        LIMIT ?
        ''', (limit,))
        
        users = cursor.fetchall()
        conn.close()
        
        return users
    
    # =========== РАБОТА С РАССЫЛКАМИ ===========
    def create_manual_mailing(self, admin_id: int, mailing_text: str, mailing_type: str, filter_criteria: str):
        """Создание ручной рассылки"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        
        cursor.execute('''
        INSERT INTO manual_mailings (admin_id, mailing_text, mailing_type, filter_criteria)
        VALUES (?, ?, ?, ?)
        ''', (admin_id, mailing_text, mailing_type, filter_criteria))
        
        conn.commit()
        mailing_id = cursor.lastrowid
        conn.close()
        
        return mailing_id
    
    def save_sent_message(self, mailing_id: int, user_id: int, telegram_message_id: int):
        """Сохранение отправленного сообщения"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        
        cursor.execute('''
        INSERT INTO sent_messages (mailing_id, user_id, telegram_message_id)
        VALUES (?, ?, ?)
        ''', (mailing_id, user_id, telegram_message_id))
        
        conn.commit()
        message_id = cursor.lastrowid
        conn.close()
        
        return message_id
    
    def update_mailing_stats(self, mailing_id: int, sent_count: int, failed_count: int):
        """Обновление статистики рассылки"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        
        cursor.execute('''
        UPDATE manual_mailings 
        SET sent_count = ?, failed_count = ?, sent_at = datetime('now')
        WHERE id = ?
        ''', (sent_count, failed_count, mailing_id))
        
        conn.commit()
        conn.close()
    
    def save_mailing_feedback(self, mailing_id: int, user_id: int, sent_message_id: int, 
                             feedback_type: str, feedback_text: str = ""):
        """Сохранение обратной связи по рассылке"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        
        cursor.execute('''
        INSERT INTO mailing_feedback 
        (mailing_id, user_id, sent_message_id, feedback_type, feedback_text)
        VALUES (?, ?, ?, ?, ?)
        ''', (mailing_id, user_id, sent_message_id, feedback_type, feedback_text))
        
        # Обновляем статистику обратной связи в основной таблице
        cursor.execute('''
        UPDATE manual_mailings 
        SET feedback_count = feedback_count + 1
        WHERE id = ?
        ''', (mailing_id,))
        
        # Отмечаем сообщение как получившее обратную связь
        cursor.execute('''
        UPDATE sent_messages 
        SET feedback_received = 1
        WHERE id = ?
        ''', (sent_message_id,))
        
        conn.commit()
        feedback_id = cursor.lastrowid
        conn.close()
        
        return feedback_id
    
    def get_sent_message_by_telegram_id(self, user_id: int, telegram_message_id: int):
        """Получение отправленного сообщения по ID Telegram"""
        conn = sqlite3.connect(self.db_name)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute('''
        SELECT sm.*, mm.mailing_text
        FROM sent_messages sm
        JOIN manual_mailings mm ON sm.mailing_id = mm.id
        WHERE sm.user_id = ? AND sm.telegram_message_id = ?
        ''', (user_id, telegram_message_id))
        
        result = cursor.fetchone()
        conn.close()
        
        return result
    
    def get_mailing_feedback(self, mailing_id: int):
        """Получение обратной связи по рассылке"""
        conn = sqlite3.connect(self.db_name)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute('''
        SELECT mf.*, u.username, u.first_name, u.last_name
        FROM mailing_feedback mf
        JOIN users u ON mf.user_id = u.user_id
        WHERE mf.mailing_id = ?
        ORDER BY mf.created_at DESC
        ''', (mailing_id,))
        
        feedback = cursor.fetchall()
        conn.close()
        
        return feedback
    
    def get_mailing_feedback_for_user(self, user_id: int):
        """Получение обратной связи по пользователю"""
        conn = sqlite3.connect(self.db_name)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute('''
        SELECT mf.*, mm.mailing_text
        FROM mailing_feedback mf
        JOIN manual_mailings mm ON mf.mailing_id = mm.id
        WHERE mf.user_id = ?
        ORDER BY mf.created_at DESC
        LIMIT 10
        ''', (user_id,))
        
        feedback = cursor.fetchall()
        conn.close()
        
        return feedback
    
    # =========== ОСТАЛЬНЫЕ МЕТОДЫ ===========
    def save_manager_message(self, user_id: int, message_type: str, message_text: str, file_id: str = None, file_name: str = None):
        """Сохранение сообщения менеджеру"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        
        cursor.execute('''
        INSERT INTO manager_messages (user_id, message_type, message_text, file_id, file_name)
        VALUES (?, ?, ?, ?, ?)
        ''', (user_id, message_type, message_text, file_id, file_name))
        
        conn.commit()
        message_id = cursor.lastrowid
        conn.close()
        
        return message_id
    
    def get_pending_exports(self):
        """Получение ожидающих выгрузок"""
        conn = sqlite3.connect(self.db_name)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute('''
        SELECT te.*, q.full_name, q.company_name, q.email, q.phone
        FROM tender_exports te
        JOIN questionnaires q ON te.questionnaire_id = q.id
        WHERE te.status = 'pending' OR te.status = 'sent'
        ORDER BY te.sent_at DESC
        LIMIT 10
        ''')
        
        exports = cursor.fetchall()
        conn.close()
        
        return exports
    
    def get_questionnaire_by_id(self, questionnaire_id: int):
        """Получение анкеты по ID"""
        conn = sqlite3.connect(self.db_name)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute('''
        SELECT q.*, u.username, u.user_id
        FROM questionnaires q
        JOIN users u ON q.user_id = u.user_id
        WHERE q.id = ?
        ''', (questionnaire_id,))
        
        questionnaire = cursor.fetchone()
        conn.close()
        
        return questionnaire
    
    def get_export_by_id(self, export_id: int):
        """Получение выгрузки по ID"""
        conn = sqlite3.connect(self.db_name)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute('''
        SELECT te.*, q.full_name, q.company_name, q.email, u.username
        FROM tender_exports te
        JOIN questionnaires q ON te.questionnaire_id = q.id
        JOIN users u ON te.user_id = u.user_id
        WHERE te.id = ?
        ''', (export_id,))
        
        export = cursor.fetchone()
        conn.close()
        
        return export
    
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
        
        # Сообщения менеджеру
        cursor.execute('''
        SELECT COUNT(*) as count FROM manager_messages 
        WHERE date(created_at) >= ?
        ''', (start_date,))
        manager_messages = cursor.fetchone()['count']
        
        # Ручные рассылки
        cursor.execute('''
        SELECT 
            COUNT(*) as count, 
            SUM(sent_count) as total_sent,
            SUM(feedback_count) as total_feedback
        FROM manual_mailings 
        WHERE date(created_at) >= ?
        ''', (start_date,))
        mailings = cursor.fetchone()
        
        # Пользователи с подпиской
        cursor.execute('''
        SELECT 
            SUM(CASE WHEN mailing_subscribed = 1 THEN 1 ELSE 0 END) as subscribed,
            SUM(CASE WHEN mailing_subscribed = 0 THEN 1 ELSE 0 END) as unsubscribed
        FROM users 
        WHERE is_active = 1
        ''')
        subscriptions = cursor.fetchone()
        
        # Анкеты
        cursor.execute('''
        SELECT COUNT(*) as count FROM questionnaires 
        WHERE date(created_at) >= ?
        ''', (start_date,))
        new_questionnaires = cursor.fetchone()['count']
        
        conn.close()
        
        return {
            'new_users': new_users,
            'exports_completed': exports_completed,
            'manager_messages': manager_messages,
            'mailings_count': mailings['count'] if mailings['count'] else 0,
            'mailings_sent': mailings['total_sent'] if mailings['total_sent'] else 0,
            'mailings_feedback': mailings['total_feedback'] if mailings['total_feedback'] else 0,
            'subscribed_users': subscriptions['subscribed'] if subscriptions['subscribed'] else 0,
            'unsubscribed_users': subscriptions['unsubscribed'] if subscriptions['unsubscribed'] else 0,
            'new_questionnaires': new_questionnaires
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
            [KeyboardButton(text="📤 Написать менеджеру")],
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
            [KeyboardButton(text="📊 Новые анкеты"), KeyboardButton(text="📤 Отправить выгрузку")],
            [KeyboardButton(text="📈 Статистика"), KeyboardButton(text="📨 Создать рассылку")],
            [KeyboardButton(text="👥 Управление подписками"), KeyboardButton(text="📩 Сообщения менеджеру")],
            [KeyboardButton(text="📋 Обратная связь"), KeyboardButton(text="⚙️ Настройки")],
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

def get_follow_up_keyboard(export_id: int):
    """Клавиатура для follow-up"""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Да, нашел подходящее", callback_data=f"follow_yes_{export_id}"),
                InlineKeyboardButton(text="❌ Нет, не нашел", callback_data=f"follow_no_{export_id}")
            ],
            [
                InlineKeyboardButton(text="🤔 Нужна консультация", callback_data=f"follow_consult_{export_id}")
            ]
        ]
    )

def get_mailing_filters_keyboard():
    """Клавиатура фильтров для рассылки"""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="👥 Все подписанные")],
            [KeyboardButton(text="📝 С анкетами")],
            [KeyboardButton(text="📭 Без анкет")],
            [KeyboardButton(text="🆕 За неделю")],
            [KeyboardButton(text="❌ Отмена")]
        ],
        resize_keyboard=True
    )

def get_mailing_feedback_keyboard(sent_message_id: int):
    """Клавиатура для обратной связи по рассылке"""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="👍 Понравилось", callback_data=f"feedback_like_{sent_message_id}"),
                InlineKeyboardButton(text="👎 Не понравилось", callback_data=f"feedback_dislike_{sent_message_id}")
            ],
            [
                InlineKeyboardButton(text="💬 Комментарий", callback_data=f"feedback_comment_{sent_message_id}"),
                InlineKeyboardButton(text="🚫 Отписаться", callback_data=f"feedback_unsubscribe_{sent_message_id}")
            ]
        ]
    )

def get_subscription_management_keyboard(user_id: int, current_status: bool):
    """Клавиатура управления подпиской пользователя"""
    status_text = "✅ Подписан" if current_status else "❌ Отписан"
    
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"{status_text} - Переключить", 
                    callback_data=f"toggle_sub_{user_id}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="📊 Статистика пользователя", 
                    callback_data=f"user_stats_{user_id}"
                )
            ]
        ]
    )

def get_manager_response_keyboard(message_id: int):
    """Клавиатура для ответа менеджеру"""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📞 Позвонить", callback_data=f"call_{message_id}")],
            [InlineKeyboardButton(text="💬 Написать в Telegram", callback_data=f"write_{message_id}")],
            [InlineKeyboardButton(text="✅ Обработано", callback_data=f"done_{message_id}")]
        ]
    )

def get_export_confirmation_keyboard(export_id: int):
    """Клавиатура подтверждения отправки выгрузки"""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Подтвердить отправку", callback_data=f"confirm_export_{export_id}"),
                InlineKeyboardButton(text="❌ Отменить", callback_data=f"cancel_export_{export_id}")
            ]
        ]
    )

# =========== СОСТОЯНИЯ ===========
class Questionnaire(StatesGroup):
    waiting_for_name = State()
    waiting_for_company = State()
    waiting_for_phone = State()
    waiting_for_email = State()
    waiting_for_activity = State()
    waiting_for_region = State()
    waiting_for_budget = State()
    waiting_for_keywords = State()

class ManagerDialog(StatesGroup):
    waiting_for_message = State()

class ManualMailing(StatesGroup):
    waiting_for_text = State()
    waiting_for_filter = State()
    waiting_for_confirmation = State()

class FeedbackComment(StatesGroup):
    waiting_for_comment = State()

class SendExport(StatesGroup):
    waiting_for_questionnaire_id = State()
    waiting_for_export_file = State()

# =========== ФУНКЦИЯ ОТПРАВКИ АНКЕТЫ АДМИНИСТРАТОРУ ===========
async def send_questionnaire_to_admin(questionnaire_id: int, user_id: int, user_data: dict, username: str, anketa_path: str = None):
    """Отправка заполненной анкеты администратору"""
    if not ADMIN_ID:
        logger.warning("ADMIN_ID не установлен, анкета не отправлена администратору")
        return
    
    try:
        # Формируем красивое сообщение с анкетой
        admin_message = f"""
📋 <b>НОВАЯ АНКЕТА #{questionnaire_id}</b>

👤 <b>Пользователь:</b> @{username or 'без username'}
🆔 <b>Telegram ID:</b> {user_id}
📅 <b>Дата заполнения:</b> {datetime.now().strftime('%d.%m.%Y %H:%M')}

<b>Данные анкеты:</b>

<b>1. ФИО полностью:</b>
{user_data.get('full_name', 'Не указано')}

<b>2. Название компании:</b>
{user_data.get('company_name', 'Не указано')}

<b>3. Телефон для связи:</b>
{user_data.get('phone', 'Не указано')}

<b>4. Email для отправки тендеров:</b>
{user_data.get('email', 'Не указано')}

<b>5. Сфера деятельности компании:</b>
{user_data.get('activity', 'Не указано')}

<b>6. Регионы работы:</b>
{user_data.get('region', 'Не указано')}

<b>7. Бюджет контрактов:</b>
{user_data.get('budget', 'Не указано')}

<b>8. Ключевые слова для поиска:</b>
{user_data.get('keywords', 'Не указано')}

{'✅ <b>Заполнено в рабочее время</b>' if db.is_working_hours() else '⏰ <b>Заполнено в нерабочее время</b>'}
        """
        
        # Отправляем администратору
        if anketa_path and os.path.exists(anketa_path):
            # Используем BufferedInputFile для отправки файла
            with open(anketa_path, 'rb') as f:
                file_content = f.read()
            
            input_file = BufferedInputFile(
                file_content, 
                filename=f"Анкета_{questionnaire_id}_{username or 'user'}.docx"
            )
            
            # Отправляем с файлом
            await bot.send_document(
                ADMIN_ID,
                input_file,
                caption=admin_message
            )
            logger.info(f"Анкета #{questionnaire_id} с файлом отправлена администратору {ADMIN_ID}")
        else:
            # Отправляем только текст
            await bot.send_message(ADMIN_ID, admin_message)
            logger.info(f"Анкета #{questionnaire_id} отправлена администратору {ADMIN_ID}")
        
    except Exception as e:
        logger.error(f"Ошибка при отправке анкеты администратору: {e}")

# =========== ФУНКЦИЯ ОТПРАВКИ ФАЙЛА ANKETA.DOCX ===========
async def send_anketa_file(user_id: int):
    """Отправка файла анкеты пользователю"""
    try:
        # Проверяем, существует ли файл
        if os.path.exists(ANKETA_LOCAL_PATH) and os.path.getsize(ANKETA_LOCAL_PATH) > 0:
            # Используем BufferedInputFile для отправки файла
            with open(ANKETA_LOCAL_PATH, 'rb') as f:
                file_content = f.read()
            
            input_file = BufferedInputFile(
                file_content, 
                filename="Анкета_Тритика_шаблон.docx"
            )
            
            await bot.send_document(
                user_id,
                input_file,
                caption=(
                    "📄 <b>Шаблон анкеты для заполнения</b>\n\n"
                    "Вы можете заполнить эту анкету и отправить нам:\n\n"
                    "1. 📧 <b>На email:</b> info@tritika.ru\n"
                    "2. 🤖 <b>Через бота:</b> кнопка 'Написать менеджеру'\n"
                    "3. 👨‍💼 <b>Менеджеру в Telegram:</b> @tritikaru\n\n"
                    "<i>Или заполните анкету онлайн ниже (быстрее и удобнее)</i>"
                ),
                parse_mode=ParseMode.HTML
            )
            return True
        else:
            # Пытаемся скачать файл заново
            print("Файл анкеты не найден или пустой, пытаюсь скачать...")
            if await download_anketa_file():
                return await send_anketa_file(user_id)
            else:
                await bot.send_message(
                    user_id,
                    "📄 <b>Шаблон анкеты для заполнения</b>\n\n"
                    "К сожалению, файл анкеты временно недоступен.\n\n"
                    "Вы можете заполнить анкету онлайн или отправить запрос на email: info@tritika.ru",
                    parse_mode=ParseMode.HTML
                )
                return False
    except Exception as e:
        logger.error(f"Ошибка при отправке файла анкеты: {e}")
        await bot.send_message(
            user_id,
            "❌ Произошла ошибка при отправке файла. Попробуйте позже или свяжитесь с поддержкой.",
            parse_mode=ParseMode.HTML
        )
        return False

# =========== ФУНКЦИЯ ДЛЯ ОТПРАВКИ FOLLOW-UP СООБЩЕНИЙ ===========
async def send_follow_up_messages():
    """Отправка follow-up сообщений через 1 час после выгрузки"""
    try:
        exports = db.get_exports_for_followup()
        
        for export in exports:
            export_id = export['id']
            user_id = export['user_id']
            username = export['username'] or "Пользователь"
            
            try:
                # Отправляем follow-up сообщение
                await bot.send_message(
                    user_id,
                    f"📨 <b>Подборка тендеров отправлена!</b>\n\n"
                    f"Удалось ли найти что-то подходящее?",
                    reply_markup=get_follow_up_keyboard(export_id)
                )
                
                # Отмечаем, что follow-up отправлен
                db.mark_followup_sent(export_id)
                
                logger.info(f"Follow-up отправлен пользователю {user_id} для выгрузки #{export_id}")
                
            except Exception as e:
                logger.error(f"Ошибка отправки follow-up пользователю {user_id}: {e}")
                
    except Exception as e:
        logger.error(f"Ошибка в send_follow_up_messages: {e}")

# =========== ФУНКЦИЯ ДЛЯ ПЛАНИРОВАНИЯ FOLLOW-UP ===========
async def schedule_follow_ups():
    """Планирование отправки follow-up сообщений"""
    while True:
        try:
            await send_follow_up_messages()
        except Exception as e:
            logger.error(f"Ошибка в schedule_follow_ups: {e}")
        
        # Проверяем каждые 5 минут
        await asyncio.sleep(300)

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
            "Помогаю организациям находить выгодные тендеры. "
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
        "• Написать менеджеру (отправить вопрос или заполненную анкету)\n"
        "• Получить бесплатную подборку тендеров\n"
        "• Консультация по участию в тендерах\n\n"
        "<b>Контакты поддержки:</b>\n"
        "📧 info@tritika.ru\n"
        "📱 +7 (904) 653-69-87"
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
    
    # Начинаем заполнение анкеты онлайн
    await message.answer(
        "📝 <b>Заполнение анкеты онлайн</b>\n\n"
        "Заполнение займет 3-5 минут. Введите ваше <b>ФИО полностью</b>:",
        reply_markup=get_cancel_keyboard()
    )
    await state.set_state(Questionnaire.waiting_for_name)

@dp.message(F.text == "📥 Скачать анкету в Word")
async def download_questionnaire(message: types.Message, state: FSMContext):
    """Скачать анкету в Word"""
    await state.clear()
    
    await message.answer("📄 <b>Отправляю вам шаблон анкеты...</b>")
    
    # Пытаемся скачать файл, если его нет
    if not os.path.exists(ANKETA_LOCAL_PATH) or os.path.getsize(ANKETA_LOCAL_PATH) == 0:
        await message.answer("🔄 Файл анкеты не найден, скачиваю с GitHub...")
        success = await download_anketa_file()
        if not success:
            await message.answer(
                "❌ Не удалось скачать файл анкеты. Пожалуйста, попробуйте позже.\n\n"
                "Вы можете заполнить анкету онлайн через соответствующую кнопку.",
                reply_markup=get_main_keyboard()
            )
            return
    
    # Отправляем файл
    sent = await send_anketa_file(message.from_user.id)
    
    if sent:
        # После отправки шаблона, предлагаем заполнить онлайн или отправить менеджеру
        await message.answer(
            "📝 <b>Что дальше?</b>\n\n"
            "1. Заполните анкету на компьютере\n"
            "2. Сохраните файл\n"
            "3. Отправьте его менеджеру через кнопку <b>'Написать менеджеру'</b>\n\n"
            "Или вы можете заполнить анкету прямо здесь через <b>'Заполнить анкету онлайн'</b>",
            reply_markup=get_main_keyboard()
        )
    else:
        await message.answer(
            "❌ Не удалось отправить файл анкеты. Попробуйте позже или свяжитесь с поддержкой.",
            reply_markup=get_main_keyboard()
        )

@dp.message(F.text == "📤 Написать менеджеру")
async def start_manager_dialog(message: types.Message, state: FSMContext):
    """Начало диалога с менеджеру"""
    await state.set_state(ManagerDialog.waiting_for_message)
    await message.answer(
        "💬 <b>Напишите ваше сообщение менеджеру</b>\n\n"
        "Вы можете отправить:\n"
        "• Текст с вопросом\n"
        "• Заполненную анкету (файл Word)\n"
        "• Документы\n"
        "• Фотографии\n\n"
        "<i>Мы получим ваше сообщение и ответим в ближайшее время.</i>",
        reply_markup=get_cancel_keyboard()
    )

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
        "• Телефон: +7 (904) 653-69-87\n"
        "• Email: info@tritika.ru\n"
        "• Telegram: @tritikaru\n\n"
        "<b>Техническая поддержка:</b>\n"
        "• Email: info@tritika.ru\n"
        "• Telegram: @tritikaru\n\n"
        "<b>Время работы:</b>\n"
        "Пн-Чт: 8:30-17:30\n"
        "Пт: 8:30-16:30\n"
        "Сб-Вс: выходные"
    )

@dp.message(F.text == "ℹ️ Помощь")
async def show_help(message: types.Message):
    """Показать помощь"""
    await cmd_help(message)

@dp.message(F.text == "❌ Отмена")
async def cancel_action(message: types.Message, state: FSMContext):
    """Отмена действия"""
    current_state = await state.get_state()
    
    if current_state in [ManagerDialog.waiting_for_message, 
                         ManualMailing.waiting_for_text,
                         ManualMailing.waiting_for_filter,
                         ManualMailing.waiting_for_confirmation,
                         FeedbackComment.waiting_for_comment,
                         SendExport.waiting_for_questionnaire_id,
                         SendExport.waiting_for_export_file]:
        await state.clear()
        is_admin = ADMIN_ID and message.from_user.id == ADMIN_ID
        
        if is_admin:
            await message.answer("❌ Действие отменено", reply_markup=get_admin_keyboard())
        else:
            await message.answer(
                "❌ Действие отменено.\n\n"
                "Вы можете выбрать другое действие.",
                reply_markup=get_main_keyboard()
            )
    else:
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

# =========== ДИАЛОГ С МЕНЕДЖЕРОМ ===========
@dp.message(ManagerDialog.waiting_for_message)
async def process_manager_message(message: types.Message, state: FSMContext):
    """Обработка сообщения для менеджера"""
    user = message.from_user
    user_id = user.id
    
    # Определяем тип сообщения
    message_type = "text"
    file_id = None
    file_name = None
    
    if message.document:
        message_type = "document"
        file_id = message.document.file_id
        file_name = message.document.file_name
        message_text = f"Документ: {message.document.file_name}"
    elif message.photo:
        message_type = "photo"
        file_id = message.photo[-1].file_id
        message_text = "Фотография"
    elif message.text:
        message_text = message.text
    else:
        await message.answer("❌ Извините, я могу принимать только текст, документы и фотографии.")
        return
    
    # Сохраняем сообщение в БД
    message_id = db.save_manager_message(user_id, message_type, message_text, file_id, file_name)
    
    # Отправляем уведомление администратору
    if ADMIN_ID:
        try:
            # Формируем сообщение для админа
            admin_message = f"📩 <b>НОВОЕ СООБЩЕНИЕ ОТ ПОЛЬЗОВАТЕЛЯ</b>\n\n"
            admin_message += f"👤 <b>Пользователь:</b> @{user.username or 'без username'}\n"
            admin_message += f"🆔 <b>ID:</b> {user_id}\n"
            admin_message += f"👤 <b>Имя:</b> {user.first_name} {user.last_name or ''}\n"
            admin_message += f"📅 <b>Время:</b> {datetime.now().strftime('%H:%M %d.%m.%Y')}\n"
            admin_message += f"📝 <b>Тип:</b> {message_type}\n\n"
            
            if message_type == "text":
                admin_message += f"💬 <b>Сообщение:</b>\n{message_text[:500]}"
                if len(message_text) > 500:
                    admin_message += "..."
            
            elif message_type == "document":
                admin_message += f"📎 <b>Документ:</b> {file_name}\n"
                admin_message += f"💬 <b>Сообщение:</b>\n{message_text}"
                
            elif message_type == "photo":
                admin_message += f"🖼 <b>Фотография</b>\n"
                admin_message += f"💬 <b>Сообщение:</b>\n{message_text}"
            
            # Отправляем администратору
            keyboard = get_manager_response_keyboard(message_id)
            await bot.send_message(ADMIN_ID, admin_message, reply_markup=keyboard)
            
            # Если есть файл - пересылаем его
            if file_id:
                if message_type == "document":
                    await bot.send_document(ADMIN_ID, file_id, caption=f"Документ от пользователя {user_id}")
                elif message_type == "photo":
                    await bot.send_photo(ADMIN_ID, file_id, caption=f"Фото от пользователя {user_id}")
            
        except Exception as e:
            logger.error(f"Не удалось отправить уведомление админу: {e}")
    
    await message.answer(
        "✅ <b>Ваше сообщение отправлено менеджеру!</b>\n\n"
        "Мы получили ваше сообщение и свяжемся с вами в ближайшее время.\n\n"
        "<i>Обычно мы отвечаем в течение 15 минут в рабочее время.</i>",
        reply_markup=get_main_keyboard()
    )
    
    await state.clear()

# =========== CALLBACK ОБРАБОТЧИКИ ДЛЯ АДМИНА ===========
@dp.callback_query(F.data.startswith("call_"))
async def handle_call_callback(callback: types.CallbackQuery):
    """Обработка кнопки "Позвонить" для сообщения менеджеру"""
    if not ADMIN_ID or callback.from_user.id != ADMIN_ID:
        await callback.answer("⛔ Доступ запрещен", show_alert=True)
        return
    
    message_id = int(callback.data.split("_")[1])
    
    # Получаем информацию о сообщении
    conn = sqlite3.connect("tenders.db")
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    cursor.execute('''
    SELECT mm.*, u.phone, u.first_name, u.last_name 
    FROM manager_messages mm
    JOIN users u ON mm.user_id = u.user_id
    WHERE mm.id = ?
    ''', (message_id,))
    
    message = cursor.fetchone()
    conn.close()
    
    if not message:
        await callback.answer("Сообщение не найдено", show_alert=True)
        return
    
    phone = message['phone']
    user_name = f"{message['first_name']} {message['last_name'] or ''}".strip()
    
    if phone:
        response = f"📞 <b>Телефон пользователя:</b> {phone}\n"
        response += f"👤 <b>Имя:</b> {user_name}\n"
        response += f"🆔 <b>ID:</b> {message['user_id']}\n"
        response += f"📅 <b>Время сообщения:</b> {message['created_at'][:19]}"
    else:
        response = "❌ У пользователя не указан телефон в анкете."
    
    await callback.message.answer(response)
    await callback.answer()

@dp.callback_query(F.data.startswith("write_"))
async def handle_write_callback(callback: types.CallbackQuery):
    """Обработка кнопки "Написать в Telegram" для сообщения менеджеру"""
    if not ADMIN_ID or callback.from_user.id != ADMIN_ID:
        await callback.answer("⛔ Доступ запрещен", show_alert=True)
        return
    
    message_id = int(callback.data.split("_")[1])
    
    # Получаем информацию о сообщении
    conn = sqlite3.connect("tenders.db")
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    cursor.execute('''
    SELECT mm.*, u.username, u.first_name, u.last_name 
    FROM manager_messages mm
    JOIN users u ON mm.user_id = u.user_id
    WHERE mm.id = ?
    ''', (message_id,))
    
    message = cursor.fetchone()
    conn.close()
    
    if not message:
        await callback.answer("Сообщение не найдено", show_alert=True)
        return
    
    username = message['username']
    user_name = f"{message['first_name']} {message['last_name'] or ''}".strip()
    
    if username:
        response = f"✏️ <b>Написать пользователю:</b>\n"
        response += f"👤 <b>Username:</b> @{username}\n"
        response += f"👤 <b>Имя:</b> {user_name}\n"
        response += f"🆔 <b>ID:</b> {message['user_id']}\n"
        response += f"🔗 <b>Ссылка:</b> https://t.me/{username}"
    else:
        response = f"✏️ <b>Написать пользователю:</b>\n"
        response += f"👤 <b>Имя:</b> {user_name}\n"
        response += f"🆔 <b>ID:</b> {message['user_id']}\n"
        response += f"🔗 <b>Ссылка:</b> tg://user?id={message['user_id']}"
    
    await callback.message.answer(response)
    await callback.answer()

@dp.callback_query(F.data.startswith("done_"))
async def handle_done_callback(callback: types.CallbackQuery):
    """Обработка кнопки "Обработано" для сообщения менеджеру"""
    if not ADMIN_ID or callback.from_user.id != ADMIN_ID:
        await callback.answer("⛔ Доступ запрещен", show_alert=True)
        return
    
    message_id = int(callback.data.split("_")[1])
    
    # Отмечаем сообщение как обработанное
    conn = sqlite3.connect("tenders.db")
    cursor = conn.cursor()
    
    cursor.execute('''
    UPDATE manager_messages 
    SET processed = 1
    WHERE id = ?
    ''', (message_id,))
    
    conn.commit()
    conn.close()
    
    # Обновляем сообщение
    await callback.message.edit_text(
        callback.message.text + "\n\n✅ <b>ОБРАБОТАНО</b>",
        reply_markup=None
    )
    
    await callback.answer("Сообщение отмечено как обработанное")

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

@dp.message(F.text == "📤 Отправить выгрузку")
async def start_send_export(message: types.Message, state: FSMContext):
    """Начало отправки выгрузки пользователю"""
    if not ADMIN_ID or message.from_user.id != ADMIN_ID:
        await message.answer("⛔ Доступ запрещен")
        return
    
    await state.set_state(SendExport.waiting_for_questionnaire_id)
    await message.answer(
        "📤 <b>Отправка выгрузки пользователю</b>\n\n"
        "Введите ID анкеты для которой нужно отправить выгрузку:\n"
        "<i>(ID можно взять из списка новых анкет)</i>",
        reply_markup=get_cancel_keyboard()
    )

@dp.message(SendExport.waiting_for_questionnaire_id)
async def process_export_questionnaire_id(message: types.Message, state: FSMContext):
    """Обработка ID анкеты для отправки выгрузки"""
    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer("❌ Отправка выгрузки отменена", reply_markup=get_admin_keyboard())
        return
    
    if not message.text.isdigit():
        await message.answer("❌ Пожалуйста, введите числовой ID анкеты")
        return
    
    questionnaire_id = int(message.text)
    
    # Проверяем существование анкеты
    questionnaire = db.get_questionnaire_by_id(questionnaire_id)
    
    if not questionnaire:
        await message.answer("❌ Анкета с таким ID не найдена")
        return
    
    await state.update_data(questionnaire_id=questionnaire_id)
    await state.set_state(SendExport.waiting_for_export_file)
    
    await message.answer(
        f"✅ <b>Анкета #{questionnaire_id} найдена</b>\n\n"
        f"👤 <b>Пользователь:</b> {questionnaire['full_name']}\n"
        f"🏢 <b>Компания:</b> {questionnaire['company_name']}\n"
        f"📧 <b>Email:</b> {questionnaire['email']}\n\n"
        f"Теперь отправьте файл с выгрузкой тендеров:\n"
        f"<i>(Поддерживаются файлы: PDF, Excel, Word, ZIP, RAR)</i>",
        reply_markup=get_cancel_keyboard()
    )

@dp.message(SendExport.waiting_for_export_file)
async def process_export_file(message: types.Message, state: FSMContext):
    """Обработка файла выгрузки"""
    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer("❌ Отправка выгрузки отменена", reply_markup=get_admin_keyboard())
        return
    
    if not message.document:
        await message.answer("❌ Пожалуйста, отправьте файл с выгрузкой")
        return
    
    data = await state.get_data()
    questionnaire_id = data.get('questionnaire_id')
    
    if not questionnaire_id:
        await message.answer("❌ Ошибка: ID анкеты не найден")
        await state.clear()
        return
    
    questionnaire = db.get_questionnaire_by_id(questionnaire_id)
    if not questionnaire:
        await message.answer("❌ Анкета не найдена")
        await state.clear()
        return
    
    # Сохраняем файл во временное хранилище
    file_id = message.document.file_id
    file_name = message.document.file_name
    
    try:
        # Скачиваем файл
        file = await bot.get_file(file_id)
        file_path = file.file_path
        
        # Создаем временный файл
        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=f"_{file_name}")
        temp_path = temp_file.name
        temp_file.close()
        
        # Скачиваем файл
        await bot.download_file(file_path, temp_path)
        
        # Создаем запись о выгрузке
        export_id = db.create_tender_export(
            questionnaire_id, 
            questionnaire['user_id'],
            temp_path,
            file_name
        )
        
        # Показываем подтверждение
        keyboard = get_export_confirmation_keyboard(export_id)
        
        await message.answer(
            f"📤 <b>Подтверждение отправки выгрузки</b>\n\n"
            f"📄 <b>Файл:</b> {file_name}\n"
            f"👤 <b>Пользователь:</b> {questionnaire['full_name']}\n"
            f"🏢 <b>Компания:</b> {questionnaire['company_name']}\n"
            f"📧 <b>Email:</b> {questionnaire['email']}\n"
            f"🆔 <b>ID выгрузки:</b> {export_id}\n\n"
            f"<i>Подтвердите отправку выгрузки пользователю.</i>",
            reply_markup=keyboard
        )
        
    except Exception as e:
        logger.error(f"Ошибка обработки файла выгрузки: {e}")
        await message.answer(f"❌ Ошибка обработки файла: {e}")
    
    await state.clear()

@dp.callback_query(F.data.startswith("confirm_export_"))
async def handle_confirm_export(callback: types.CallbackQuery):
    """Подтверждение отправки выгрузки"""
    if not ADMIN_ID or callback.from_user.id != ADMIN_ID:
        await callback.answer("⛔ Доступ запрещен", show_alert=True)
        return
    
    export_id = int(callback.data.split("_")[2])
    
    # Получаем информацию о выгрузке
    export = db.get_export_by_id(export_id)
    
    if not export:
        await callback.answer("Выгрузка не найдена", show_alert=True)
        return
    
    try:
        # Отправляем файл пользователю
        user_id = export['user_id']
        file_path = export['file_path']
        file_name = export['file_name'] or "Выгрузка_тендеров.pdf"
        
        if file_path and os.path.exists(file_path):
            # Используем BufferedInputFile для отправки файла
            with open(file_path, 'rb') as f:
                file_content = f.read()
            
            input_file = BufferedInputFile(
                file_content,
                filename=file_name
            )
            
            await bot.send_document(
                user_id,
                input_file,
                caption=(
                    f"📨 <b>Ваша выгрузка тендеров готова!</b>\n\n"
                    f"🏢 <b>Компания:</b> {export['company_name']}\n"
                    f"🎯 <b>Сфера:</b> {export.get('activity', 'Не указано')}\n"
                    f"📅 <b>Дата отправки:</b> {datetime.now().strftime('%d.%m.%Y %H:%M')}\n\n"
                    f"<i>В ближайшее время с вами свяжется менеджер для уточнения деталей.</i>"
                )
            )
            
            # Обновляем статус выгрузки
            db.mark_export_completed(export_id, callback.from_user.first_name)
            
            # Удаляем временный файл
            try:
                os.remove(file_path)
            except Exception as e:
                logger.error(f"Не удалось удалить временный файл {file_path}: {e}")
            
            await callback.message.edit_text(
                callback.message.text + "\n\n✅ <b>ВЫГРУЗКА ОТПРАВЛЕНА</b>",
                reply_markup=None
            )
            
            # Отправляем подтверждение админу
            await callback.message.answer(
                f"✅ <b>Выгрузка #{export_id} отправлена пользователю</b>\n\n"
                f"👤 Пользователь: {export['full_name']}\n"
                f"🏢 Компания: {export['company_name']}\n"
                f"📄 Файл: {file_name}\n\n"
                f"<i>Через 1 час пользователь получит follow-up сообщение.</i>"
            )
            
        else:
            await callback.answer("Файл выгрузки не найден", show_alert=True)
            
    except Exception as e:
        logger.error(f"Ошибка отправки выгрузки: {e}")
        await callback.answer(f"Ошибка отправки: {e}", show_alert=True)

@dp.callback_query(F.data.startswith("cancel_export_"))
async def handle_cancel_export(callback: types.CallbackQuery):
    """Отмена отправки выгрузки"""
    if not ADMIN_ID or callback.from_user.id != ADMIN_ID:
        await callback.answer("⛔ Доступ запрещен", show_alert=True)
        return
    
    export_id = int(callback.data.split("_")[2])
    
    # Удаляем временный файл если есть
    export = db.get_export_by_id(export_id)
    if export and export['file_path'] and os.path.exists(export['file_path']):
        try:
            os.remove(export['file_path'])
        except Exception as e:
            logger.error(f"Не удалось удалить файл при отмене выгрузки: {e}")
    
    # Удаляем запись из БД
    conn = sqlite3.connect("tenders.db")
    cursor = conn.cursor()
    cursor.execute('DELETE FROM tender_exports WHERE id = ?', (export_id,))
    conn.commit()
    conn.close()
    
    await callback.message.edit_text(
        callback.message.text + "\n\n❌ <b>ОТПРАВКА ОТМЕНЕНА</b>",
        reply_markup=None
    )
    
    await callback.answer("Отправка выгрузки отменена")

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
• Новых анкет: {stats['new_questionnaires']}
• С подпиской: {stats['subscribed_users']}
• Без подписки: {stats['unsubscribed_users']}

📋 <b>Выгрузки:</b>
• Выполненных выгрузок: {stats['exports_completed']}

💬 <b>Сообщения менеджеру:</b>
• Всего сообщений: {stats['manager_messages']}

📨 <b>Ручные рассылки:</b>
• Количество рассылок: {stats['mailings_count']}
• Отправлено сообщений: {stats['mailings_sent']}
• Получено отзывов: {stats['mailings_feedback']}

📅 <b>Дата отчета:</b>
{datetime.now().strftime('%d.%m.%Y %H:%M')}
"""
    
    await message.answer(response)

# =========== ОБРАБОТЧИКИ FOLLOW-UP СООБЩЕНИЙ ===========
@dp.callback_query(F.data.startswith("follow_"))
async def handle_follow_up_response(callback: types.CallbackQuery):
    """Обработка ответа на follow-up сообщение"""
    try:
        parts = callback.data.split("_")
        response_type = parts[1]  # yes, no, consult
        export_id = int(parts[2])
        
        user_id = callback.from_user.id
        username = callback.from_user.username or "без username"
        
        # Сохраняем ответ
        response_map = {
            "yes": "Да, нашел подходящее",
            "no": "Нет, не нашел",
            "consult": "Нужна консультация"
        }
        
        response_text = response_map.get(response_type, "Неизвестно")
        db.save_followup_response(export_id, response_text)
        
        # Отправляем благодарность
        thank_you_text = {
            "yes": "Отлично! Мы рады, что вы нашли подходящие тендеры. 🎉",
            "no": "Жаль, что не нашли подходящее. Мы можем сделать более точную подборку. 📊",
            "consult": "Хорошо! Наш менеджер свяжется с вами для консультации. 👨‍💼"
        }
        
        await callback.message.edit_text(
            callback.message.text + f"\n\n✅ <b>Спасибо за ваш ответ!</b>\n{thank_you_text.get(response_type, '')}",
            reply_markup=None
        )
        
        # Уведомляем администратора
        if ADMIN_ID:
            try:
                export = db.get_export_by_id(export_id)
                if export:
                    await bot.send_message(
                        ADMIN_ID,
                        f"📨 <b>ПОЛЬЗОВАТЕЛЬ ОТВЕТИЛ НА FOLLOW-UP</b>\n\n"
                        f"👤 Пользователь: @{username}\n"
                        f"🆔 ID: {user_id}\n"
                        f"🏢 Компания: {export['company_name']}\n"
                        f"💬 Ответ: {response_text}\n"
                        f"📅 Время: {datetime.now().strftime('%H:%M %d.%m.%Y')}"
                    )
            except Exception as e:
                logger.error(f"Не удалось уведомить админа о follow-up: {e}")
        
        await callback.answer()
        
    except Exception as e:
        logger.error(f"Ошибка обработки follow-up ответа: {e}")
        await callback.answer("Произошла ошибка", show_alert=True)

# =========== УПРАВЛЕНИЕ ПОДПИСКАМИ ===========
@dp.message(F.text == "👥 Управление подписками")
async def manage_subscriptions(message: types.Message):
    """Управление подписками пользователей"""
    if not ADMIN_ID or message.from_user.id != ADMIN_ID:
        await message.answer("⛔ Доступ запрещен")
        return
    
    # Получаем пользователей
    users = db.get_all_users_with_subscription(30)
    
    if not users:
        await message.answer("👥 Пользователей нет")
        return
    
    # Создаем инлайн-клавиатуру для управления
    keyboard = InlineKeyboardMarkup(inline_keyboard=[])
    
    for user in users:
        status_icon = "✅" if user['mailing_subscribed'] else "❌"
        has_anketa = "📋" if user['has_filled_questionnaire'] else "📭"
        
        button_text = f"{status_icon} {has_anketa} {user['first_name']}"
        if user['last_name']:
            button_text += f" {user['last_name']}"
        
        if user['username']:
            button_text += f" (@{user['username']})"
        
        keyboard.inline_keyboard.append([
            InlineKeyboardButton(
                text=button_text[:50],  # Ограничиваем длину
                callback_data=f"manage_user_{user['user_id']}"
            )
        ])
    
    # Добавляем кнопки фильтров
    keyboard.inline_keyboard.append([
        InlineKeyboardButton(text="✅ Только подписанные", callback_data="filter_subscribed"),
        InlineKeyboardButton(text="❌ Только отписанные", callback_data="filter_unsubscribed")
    ])
    
    keyboard.inline_keyboard.append([
        InlineKeyboardButton(text="📊 Статистика подписок", callback_data="subscription_stats"),
        InlineKeyboardButton(text="🔄 Обновить список", callback_data="refresh_subs")
    ])
    
    await message.answer(
        "👥 <b>Управление подписками на рассылку</b>\n\n"
        "Выберите пользователя для управления его подпиской:\n\n"
        "<b>Легенда:</b>\n"
        "✅ - подписан на рассылку\n"
        "❌ - отписан от рассылки\n"
        "📋 - заполнил анкету\n"
        "📭 - без анкеты",
        reply_markup=keyboard
    )

@dp.callback_query(F.data.startswith("manage_user_"))
async def handle_manage_user(callback: types.CallbackQuery):
    """Обработка выбора пользователя для управления подпиской"""
    if not ADMIN_ID or callback.from_user.id != ADMIN_ID:
        await callback.answer("⛔ Доступ запрещен", show_alert=True)
        return
    
    user_id = int(callback.data.split("_")[2])
    
    # Получаем информацию о пользователе
    user_info = db.get_user_mailing_status(user_id)
    
    if not user_info:
        await callback.answer("Пользователь не найден", show_alert=True)
        return
    
    keyboard = get_subscription_management_keyboard(user_id, user_info['subscribed'])
    
    user_name = f"{user_info['first_name']} {user_info['last_name'] or ''}".strip()
    username = f"@{user_info['username']}" if user_info['username'] else "без username"
    
    await callback.message.edit_text(
        f"👤 <b>Управление подпиской пользователя</b>\n\n"
        f"<b>Пользователь:</b> {user_name}\n"
        f"<b>Username:</b> {username}\n"
        f"<b>ID:</b> {user_id}\n"
        f"<b>Текущий статус:</b> {'✅ Подписан на рассылку' if user_info['subscribed'] else '❌ Отписан от рассылки'}\n\n"
        f"<i>Используйте кнопки ниже для управления:</i>",
        reply_markup=keyboard
    )
    
    await callback.answer()

@dp.callback_query(F.data.startswith("toggle_sub_"))
async def handle_toggle_subscription(callback: types.CallbackQuery):
    """Переключение статуса подписки"""
    if not ADMIN_ID or callback.from_user.id != ADMIN_ID:
        await callback.answer("⛔ Доступ запрещен", show_alert=True)
        return
    
    user_id = int(callback.data.split("_")[2])
    
    # Переключаем подписку
    new_status = db.toggle_user_mailing_subscription(user_id)
    
    if new_status is None:
        await callback.answer("Ошибка при изменении подписки", show_alert=True)
        return
    
    # Получаем обновленную информацию
    user_info = db.get_user_mailing_status(user_id)
    
    if not user_info:
        await callback.answer("Пользователь не найден", show_alert=True)
        return
    
    keyboard = get_subscription_management_keyboard(user_id, new_status)
    
    user_name = f"{user_info['first_name']} {user_info['last_name'] or ''}".strip()
    
    # Обновляем сообщение
    await callback.message.edit_text(
        f"👤 <b>Управление подпиской пользователя</b>\n\n"
        f"<b>Пользователь:</b> {user_name}\n"
        f"<b>ID:</b> {user_id}\n"
        f"<b>Текущий статус:</b> {'✅ Подписан на рассылку' if new_status else '❌ Отписан от рассылки'}\n\n"
        f"<i>Статус успешно обновлен!</i>",
        reply_markup=keyboard
    )
    
    await callback.answer(f"Статус подписки изменен: {'✅ Подписан' if new_status else '❌ Отписан'}")

@dp.callback_query(F.data.startswith("user_stats_"))
async def handle_user_stats(callback: types.CallbackQuery):
    """Показать статистику пользователя"""
    if not ADMIN_ID or callback.from_user.id != ADMIN_ID:
        await callback.answer("⛔ Доступ запрещен", show_alert=True)
        return
    
    user_id = int(callback.data.split("_")[2])
    
    # Получаем статистику пользователя
    conn = sqlite3.connect("tenders.db")
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    # Информация о пользователе
    cursor.execute('''
    SELECT u.*, 
           COUNT(DISTINCT q.id) as questionnaire_count,
           COUNT(DISTINCT te.id) as export_count,
           COUNT(DISTINCT mm.id) as message_count,
           COUNT(DISTINCT mf.id) as feedback_count
    FROM users u
    LEFT JOIN questionnaires q ON u.user_id = q.user_id
    LEFT JOIN tender_exports te ON q.id = te.questionnaire_id
    LEFT JOIN manager_messages mm ON u.user_id = mm.user_id
    LEFT JOIN mailing_feedback mf ON u.user_id = mf.user_id
    WHERE u.user_id = ?
    GROUP BY u.user_id
    ''', (user_id,))
    
    user = cursor.fetchone()
    conn.close()
    
    if not user:
        await callback.answer("Пользователь не найден", show_alert=True)
        return
    
    user_name = f"{user['first_name']} {user['last_name'] or ''}".strip()
    username = f"@{user['username']}" if user['username'] else "без username"
    
    # Получаем последние отзывы
    feedback = db.get_mailing_feedback_for_user(user_id)
    
    response = f"""
📊 <b>Статистика пользователя</b>

👤 <b>Пользователь:</b> {user_name}
📱 <b>Username:</b> {username}
🆔 <b>ID:</b> {user_id}

<b>Статусы:</b>
• Подписка на рассылку: {'✅ Подписан' if user['mailing_subscribed'] else '❌ Отписан'}
• Заполнил анкету: {'✅ Да' if user['has_filled_questionnaire'] else '❌ Нет'}
• Активен: {'✅ Да' if user['is_active'] else '❌ Нет'}

<b>Активность:</b>
• Анкет: {user['questionnaire_count']}
• Выгрузок: {user['export_count']}
• Сообщений менеджеру: {user['message_count']}
• Отзывов на рассылки: {user['feedback_count']}

<b>Контактные данные:</b>
• Телефон: {user['phone'] or 'Не указан'}
• Email: {user['email'] or 'Не указан'}
• Компания: {user['company'] or 'Не указана'}

<b>Дата регистрации:</b>
{user['created_at'][:19] if user['created_at'] else 'Неизвестно'}
"""
    
    if feedback:
        response += "\n<b>Последние отзывы:</b>\n"
        for i, fb in enumerate(feedback[:3], 1):
            fb_type = "👍" if fb['feedback_type'] == 'like' else "👎" if fb['feedback_type'] == 'dislike' else "💬" if fb['feedback_type'] == 'comment' else "🚫"
            response += f"{i}. {fb_type} {fb['feedback_text'] or fb['feedback_type']} ({fb['created_at'][:16]})\n"
    
    await callback.message.answer(response)
    await callback.answer()

@dp.callback_query(F.data == "subscription_stats")
async def handle_subscription_stats(callback: types.CallbackQuery):
    """Статистика подписок"""
    if not ADMIN_ID or callback.from_user.id != ADMIN_ID:
        await callback.answer("⛔ Доступ запрещен", show_alert=True)
        return
    
    conn = sqlite3.connect("tenders.db")
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    # Общая статистика
    cursor.execute('''
    SELECT 
        COUNT(*) as total_users,
        SUM(CASE WHEN mailing_subscribed = 1 THEN 1 ELSE 0 END) as subscribed,
        SUM(CASE WHEN mailing_subscribed = 0 THEN 1 ELSE 0 END) as unsubscribed,
        SUM(CASE WHEN has_filled_questionnaire = 1 THEN 1 ELSE 0 END) as with_anketa,
        SUM(CASE WHEN has_filled_questionnaire = 0 THEN 1 ELSE 0 END) as without_anketa
    FROM users 
    WHERE is_active = 1
    ''')
    
    stats = cursor.fetchone()
    
    # Статистика по отпискам за последний месяц
    cursor.execute('''
    SELECT COUNT(*) as recent_unsubscribes
    FROM mailing_feedback 
    WHERE feedback_type = 'unsubscribe'
    AND date(created_at) >= date('now', '-30 days')
    ''')
    
    recent = cursor.fetchone()
    
    conn.close()
    
    percentage = (stats['subscribed'] / stats['total_users'] * 100) if stats['total_users'] > 0 else 0
    
    response = f"""
📊 <b>Статистика подписок</b>

<b>Общая статистика:</b>
• Всего активных пользователей: {stats['total_users']}
• Подписано на рассылку: {stats['subscribed']}
• Отписано от рассылки: {stats['unsubscribed']}
• Процент подписки: {percentage:.1f}%

<b>По анкетам:</b>
• С заполненной анкетой: {stats['with_anketa']}
• Без анкеты: {stats['without_anketa']}

<b>Отписки за 30 дней:</b>
• Всего отписок: {recent['recent_unsubscribes']}
"""
    
    await callback.message.answer(response)
    await callback.answer()

@dp.callback_query(F.data == "refresh_subs")
async def handle_refresh_subs(callback: types.CallbackQuery):
    """Обновление списка подписок"""
    await manage_subscriptions(callback.message)
    await callback.answer("Список обновлен")

@dp.callback_query(F.data.startswith("filter_"))
async def handle_filter_subs(callback: types.CallbackQuery):
    """Фильтрация списка подписок"""
    if not ADMIN_ID or callback.from_user.id != ADMIN_ID:
        await callback.answer("⛔ Доступ запрещен", show_alert=True)
        return
    
    filter_type = callback.data.split("_")[1]
    
    # Получаем пользователей по фильтру
    if filter_type == "subscribed":
        users = db.get_users_by_filter("subscribed")
        filter_name = "подписанные"
    elif filter_type == "unsubscribed":
        users = db.get_users_by_filter("unsubscribed")
        filter_name = "отписанные"
    else:
        users = db.get_all_users_with_subscription(30)
        filter_name = "все"
    
    if not users:
        await callback.answer(f"Нет пользователей с фильтром '{filter_name}'", show_alert=True)
        return
    
    # Создаем инлайн-клавиатуру
    keyboard = InlineKeyboardMarkup(inline_keyboard=[])
    
    for user in users:
        status_icon = "✅" if user['mailing_subscribed'] else "❌"
        has_anketa = "📋" if user['has_filled_questionnaire'] else "📭"
        
        button_text = f"{status_icon} {has_anketa} {user['first_name']}"
        if user['last_name']:
            button_text += f" {user['last_name']}"
        
        if user['username']:
            button_text += f" (@{user['username']})"
        
        keyboard.inline_keyboard.append([
            InlineKeyboardButton(
                text=button_text[:50],
                callback_data=f"manage_user_{user['user_id']}"
            )
        ])
    
    keyboard.inline_keyboard.append([
        InlineKeyboardButton(text="👥 Все пользователи", callback_data="filter_all"),
        InlineKeyboardButton(text="🔄 Обновить список", callback_data="refresh_subs")
    ])
    
    await callback.message.edit_text(
        f"👥 <b>Управление подписками на рассылку</b>\n\n"
        f"<b>Фильтр:</b> {filter_name}\n"
        f"<b>Найдено пользователей:</b> {len(users)}\n\n"
        "<b>Легенда:</b>\n"
        "✅ - подписан на рассылку\n"
        "❌ - отписан от рассылки\n"
        "📋 - заполнил анкету\n"
        "📭 - без анкеты",
        reply_markup=keyboard
    )
    
    await callback.answer()

# =========== СОЗДАНИЕ РАССЫЛКИ ===========
@dp.message(F.text == "📨 Создать рассылку")
async def start_create_mailing(message: types.Message, state: FSMContext):
    """Начало создания ручной рассылки"""
    if not ADMIN_ID or message.from_user.id != ADMIN_ID:
        await message.answer("⛔ Доступ запрещен")
        return
    
    await state.set_state(ManualMailing.waiting_for_text)
    await message.answer(
        "📨 <b>Создание ручной рассылки</b>\n\n"
        "Введите текст рассылки. Вы можете использовать HTML-разметку:\n"
        "<b>жирный</b>, <i>курсив</i>, <code>код</code>\n\n"
        "<i>Для отмены нажмите '❌ Отмена'</i>",
        reply_markup=get_cancel_keyboard()
    )

@dp.message(ManualMailing.waiting_for_text)
async def process_mailing_text(message: types.Message, state: FSMContext):
    """Обработка текста рассылки"""
    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer("❌ Создание рассылки отменено.", reply_markup=get_admin_keyboard())
        return
    
    # Сохраняем текст рассылки
    await state.update_data(mailing_text=message.text)
    await state.set_state(ManualMailing.waiting_for_filter)
    
    await message.answer(
        "✅ <b>Текст рассылки сохранен</b>\n\n"
        "Теперь выберите категорию пользователей для рассылки:",
        reply_markup=get_mailing_filters_keyboard()
    )

@dp.message(ManualMailing.waiting_for_filter)
async def process_mailing_filter(message: types.Message, state: FSMContext):
    """Обработка фильтра для рассылки"""
    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer("❌ Создание рассылки отменено.", reply_markup=get_admin_keyboard())
        return
    
    filter_map = {
        "👥 Все подписанные": "all",
        "📝 С анкетами": "with_questionnaire",
        "📭 Без анкет": "without_questionnaire",
        "🆕 За неделю": "recent_week"
    }
    
    if message.text not in filter_map:
        await message.answer("❌ Пожалуйста, выберите категорию из предложенных кнопок.")
        return
    
    filter_type = filter_map[message.text]
    
    # Получаем пользователей по фильтру
    users = db.get_users_by_filter(filter_type)
    
    if not users:
        await message.answer(
            f"❌ Нет пользователей по выбранному фильтру: {message.text}\n"
            "Попробуйте выбрать другую категорию.",
            reply_markup=get_mailing_filters_keyboard()
        )
        return
    
    await state.update_data(filter_type=filter_type, user_count=len(users))
    await state.set_state(ManualMailing.waiting_for_confirmation)
    
    data = await state.get_data()
    mailing_text = data['mailing_text'][:200] + "..." if len(data['mailing_text']) > 200 else data['mailing_text']
    
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="✅ Да, отправить")],
            [KeyboardButton(text="❌ Нет, отменить")]
        ],
        resize_keyboard=True
    )
    
    await message.answer(
        f"📨 <b>Подтверждение рассылки</b>\n\n"
        f"<b>Текст:</b>\n{mailing_text}\n\n"
        f"<b>Категория:</b> {message.text}\n"
        f"<b>Количество пользователей:</b> {len(users)}\n\n"
        f"<i>Отправить рассылку?</i>",
        reply_markup=keyboard
    )

@dp.message(ManualMailing.waiting_for_confirmation)
async def process_mailing_confirmation(message: types.Message, state: FSMContext):
    """Подтверждение и отправка рассылки С ОБРАТНОЙ СВЯЗЬЮ"""
    if message.text == "❌ Нет, отменить":
        await state.clear()
        await message.answer("❌ Рассылка отменена.", reply_markup=get_admin_keyboard())
        return
    
    if message.text != "✅ Да, отправить":
        await message.answer("❌ Пожалуйста, используйте кнопки для подтверждения.")
        return
    
    data = await state.get_data()
    mailing_text = data['mailing_text']
    filter_type = data['filter_type']
    user_count = data['user_count']
    
    # Получаем пользователей
    users = db.get_users_by_filter(filter_type)
    
    if not users:
        await message.answer("❌ Ошибка: пользователи не найдены.", reply_markup=get_admin_keyboard())
        await state.clear()
        return
    
    # Создаем запись о рассылке
    mailing_id = db.create_manual_mailing(
        message.from_user.id,
        mailing_text,
        filter_type,
        json.dumps({"user_count": user_count})
    )
    
    # Отправляем рассылку
    await message.answer(f"🔄 Начинаю отправку рассылки для {len(users)} пользователей...")
    
    success_count = 0
    failed_count = 0
    
    for user in users:
        try:
            # Отправляем сообщение с клавиатурой для обратной связи
            sent_message = await bot.send_message(
                user['user_id'], 
                mailing_text, 
                parse_mode=ParseMode.HTML
            )
            
            # Сохраняем отправленное сообщение
            sent_message_id = db.save_sent_message(mailing_id, user['user_id'], sent_message.message_id)
            
            # Отправляем клавиатуру для обратной связи отдельным сообщением
            feedback_keyboard = get_mailing_feedback_keyboard(sent_message_id)
            await bot.send_message(
                user['user_id'],
                "💬 <b>Как вам эта рассылка?</b>\n\n"
                "Пожалуйста, оставьте обратную связь:",
                reply_markup=feedback_keyboard
            )
            
            success_count += 1
            
            # Пауза, чтобы не превысить лимиты Telegram
            await asyncio.sleep(0.1)
            
        except Exception as e:
            logger.error(f"Не удалось отправить рассылку пользователю {user['user_id']}: {e}")
            failed_count += 1
    
    # Обновляем статистику рассылки
    db.update_mailing_stats(mailing_id, success_count, failed_count)
    
    await message.answer(
        f"✅ <b>Рассылка завершена!</b>\n\n"
        f"📨 <b>ID рассылки:</b> {mailing_id}\n"
        f"👥 <b>Всего пользователей:</b> {len(users)}\n"
        f"✅ <b>Успешно отправлено:</b> {success_count}\n"
        f"❌ <b>Не удалось отправить:</b> {failed_count}\n\n"
        f"<i>Рассылка сохранена в истории. Пользователи получили возможность оставить обратную связь.</i>",
        reply_markup=get_admin_keyboard()
    )
    
    await state.clear()

# =========== ОБРАТНАЯ СВЯЗЬ ПО РАССЫЛКАМ ===========
@dp.callback_query(F.data.startswith("feedback_"))
async def handle_mailing_feedback(callback: types.CallbackQuery, state: FSMContext):
    """Обработка обратной связи по рассылке"""
    try:
        # Парсим callback data
        parts = callback.data.split("_")
        feedback_type = parts[1]  # like, dislike, comment, unsubscribe
        sent_message_id = int(parts[2])
        
        user_id = callback.from_user.id
        username = callback.from_user.username or "без username"
        
        # Получаем информацию о сообщении
        conn = sqlite3.connect("tenders.db")
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute('''
        SELECT sm.*, mm.mailing_text, mm.id as mailing_id
        FROM sent_messages sm
        JOIN manual_mailings mm ON sm.mailing_id = mm.id
        WHERE sm.id = ? AND sm.user_id = ?
        ''', (sent_message_id, user_id))
        
        sent_message = cursor.fetchone()
        conn.close()
        
        if not sent_message:
            await callback.answer("Сообщение не найдено", show_alert=True)
            return
        
        mailing_id = sent_message['mailing_id']
        
        if feedback_type == "unsubscribe":
            # Отписываем пользователя
            db.toggle_user_mailing_subscription(user_id)
            
            # Сохраняем отзыв
            db.save_mailing_feedback(
                mailing_id, 
                user_id, 
                sent_message_id, 
                "unsubscribe", 
                "Пользователь отписался от рассылки"
            )
            
            # Уведомляем пользователя
            await callback.message.edit_text(
                callback.message.text + "\n\n✅ <b>Вы отписаны от рассылок</b>",
                reply_markup=None
            )
            
            await callback.answer("Вы отписаны от рассылок")
            
            # Уведомляем администратора
            if ADMIN_ID:
                try:
                    await bot.send_message(
                        ADMIN_ID,
                        f"🚫 <b>ПОЛЬЗОВАТЕЛЬ ОТПИСАЛСЯ ОТ РАССЫЛКИ</b>\n\n"
                        f"👤 Пользователь: @{username}\n"
                        f"🆔 ID: {user_id}\n"
                        f"📨 Рассылка ID: {mailing_id}\n"
                        f"📅 Время: {datetime.now().strftime('%H:%M %d.%m.%Y')}"
                    )
                except Exception as e:
                    logger.error(f"Не удалось уведомить админа об отписке: {e}")
            
            return
        
        elif feedback_type == "comment":
            # Запрашиваем комментарий
            await state.set_state(FeedbackComment.waiting_for_comment)
            await state.update_data(sent_message_id=sent_message_id, mailing_id=mailing_id)
            
            await callback.message.answer(
                "💬 <b>Напишите ваш комментарий к рассылке:</b>\n\n"
                "<i>Что понравилось или не понравилось? Что можно улучшить?</i>",
                reply_markup=get_cancel_keyboard()
            )
            
            await callback.answer()
            return
        
        else:  # like или dislike
            feedback_text_map = {
                "like": "Понравилось",
                "dislike": "Не понравилось"
            }
            
            # Сохраняем отзыв
            db.save_mailing_feedback(
                mailing_id, 
                user_id, 
                sent_message_id, 
                feedback_type, 
                feedback_text_map.get(feedback_type, "")
            )
            
            # Обновляем сообщение
            feedback_icon = "👍" if feedback_type == "like" else "👎"
            await callback.message.edit_text(
                callback.message.text + f"\n\n{feedback_icon} <b>Спасибо за ваш отзыв!</b>",
                reply_markup=None
            )
            
            await callback.answer(f"Спасибо за ваш отзыв: {feedback_text_map.get(feedback_type, '')}")
            
            # Уведомляем администратора
            if ADMIN_ID:
                try:
                    feedback_type_text = "Понравилось" if feedback_type == "like" else "Не понравилось"
                    
                    await bot.send_message(
                        ADMIN_ID,
                        f"{feedback_icon} <b>НОВЫЙ ОТЗЫВ НА РАССЫЛКУ</b>\n\n"
                        f"👤 Пользователь: @{username}\n"
                        f"🆔 ID: {user_id}\n"
                        f"📨 Рассылка ID: {mailing_id}\n"
                        f"💬 Отзыв: {feedback_type_text}\n"
                        f"📅 Время: {datetime.now().strftime('%H:%M %d.%m.%Y')}"
                    )
                except Exception as e:
                    logger.error(f"Не удалось уведомить админа об отзыве: {e}")
    
    except Exception as e:
        logger.error(f"Ошибка обработки обратной связи: {e}")
        await callback.answer("Произошла ошибка", show_alert=True)

@dp.message(FeedbackComment.waiting_for_comment)
async def process_feedback_comment(message: types.Message, state: FSMContext):
    """Обработка комментария к рассылке"""
    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer("❌ Отправка комментария отменена.", reply_markup=get_main_keyboard())
        return
    
    data = await state.get_data()
    sent_message_id = data.get('sent_message_id')
    mailing_id = data.get('mailing_id')
    user_id = message.from_user.id
    username = message.from_user.username or "без username"
    
    # Сохраняем комментарий
    db.save_mailing_feedback(
        mailing_id, 
        user_id, 
        sent_message_id, 
        "comment", 
        message.text
    )
    
    await message.answer(
        "💬 <b>Спасибо за ваш комментарий!</b>\n\n"
        "Мы учтем ваше мнение для улучшения наших рассылок.",
        reply_markup=get_main_keyboard()
    )
    
    # Уведомляем администратора
    if ADMIN_ID:
        try:
            await bot.send_message(
                ADMIN_ID,
                f"💬 <b>НОВЫЙ КОММЕНТАРИЙ К РАССЫЛКЕ</b>\n\n"
                f"👤 Пользователь: @{username}\n"
                f"🆔 ID: {user_id}\n"
                f"📨 Рассылка ID: {mailing_id}\n"
                f"📝 Комментарий: {message.text[:500]}\n"
                f"📅 Время: {datetime.now().strftime('%H:%M %d.%m.%Y')}"
            )
        except Exception as e:
            logger.error(f"Не удалось уведомить админа о комментарии: {e}")
    
    await state.clear()

# =========== ПРОСМОТР ОБРАТНОЙ СВЯЗИ ===========
@dp.message(F.text == "📋 Обратная связь")
async def show_feedback(message: types.Message):
    """Показать обратную связь по рассылкам"""
    if not ADMIN_ID or message.from_user.id != ADMIN_ID:
        await message.answer("⛔ Доступ запрещен")
        return
    
    conn = sqlite3.connect("tenders.db")
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    # Получаем последние рассылки с обратной связью
    cursor.execute('''
    SELECT mm.id, mm.mailing_text, mm.created_at, 
           mm.sent_count, mm.feedback_count,
           (SELECT COUNT(DISTINCT mf.user_id) 
            FROM mailing_feedback mf 
            WHERE mf.mailing_id = mm.id) as feedback_users
    FROM manual_mailings mm
    WHERE mm.sent_count > 0
    ORDER BY mm.created_at DESC
    LIMIT 10
    ''')
    
    mailings = cursor.fetchall()
    conn.close()
    
    if not mailings:
        await message.answer("📭 Нет рассылок с обратной связью")
        return
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[])
    
    for mailing in mailings:
        date_str = mailing['created_at'][:10] if mailing['created_at'] else "??.??.????"
        feedback_percent = (mailing['feedback_count'] / mailing['sent_count'] * 100) if mailing['sent_count'] > 0 else 0
        
        button_text = f"📨 #{mailing['id']} ({date_str}) - {feedback_percent:.1f}% отзывов"
        keyboard.inline_keyboard.append([
            InlineKeyboardButton(
                text=button_text,
                callback_data=f"view_feedback_{mailing['id']}"
            )
        ])
    
    keyboard.inline_keyboard.append([
        InlineKeyboardButton(text="📊 Статистика отзывов", callback_data="feedback_stats"),
        InlineKeyboardButton(text="🔄 Обновить", callback_data="refresh_feedback")
    ])
    
    await message.answer(
        "📋 <b>Обратная связь по рассылкам</b>\n\n"
        "Выберите рассылку для просмотра отзывов:",
        reply_markup=keyboard
    )

@dp.callback_query(F.data.startswith("view_feedback_"))
async def handle_view_feedback(callback: types.CallbackQuery):
    """Просмотр обратной связи по конкретной рассылке"""
    if not ADMIN_ID or callback.from_user.id != ADMIN_ID:
        await callback.answer("⛔ Доступ запрещен", show_alert=True)
        return
    
    mailing_id = int(callback.data.split("_")[2])
    
    # Получаем обратную связь
    feedback = db.get_mailing_feedback(mailing_id)
    
    if not feedback:
        await callback.answer("Нет обратной связи по этой рассылке", show_alert=True)
        return
    
    # Статистика по типам отзывов
    likes = sum(1 for f in feedback if f['feedback_type'] == 'like')
    dislikes = sum(1 for f in feedback if f['feedback_type'] == 'dislike')
    comments = sum(1 for f in feedback if f['feedback_type'] == 'comment')
    unsubscribes = sum(1 for f in feedback if f['feedback_type'] == 'unsubscribe')
    
    response = f"""
📋 <b>Обратная связь по рассылке #{mailing_id}</b>

<b>Статистика:</b>
• Всего отзывов: {len(feedback)}
• 👍 Понравилось: {likes}
• 👎 Не понравилось: {dislikes}
• 💬 Комментарии: {comments}
• 🚫 Отписки: {unsubscribes}

<b>Последние отзывы:</b>
"""
    
    for i, fb in enumerate(feedback[:10], 1):
        fb_type = "👍" if fb['feedback_type'] == 'like' else "👎" if fb['feedback_type'] == 'dislike' else "💬" if fb['feedback_type'] == 'comment' else "🚫"
        user_name = f"@{fb['username']}" if fb['username'] else f"{fb['first_name']} {fb['last_name'] or ''}"
        date_str = fb['created_at'][:16] if fb['created_at'] else "??.?? ??:??"
        
        response += f"\n{i}. {fb_type} <b>{user_name}</b> ({date_str})"
        if fb['feedback_text']:
            response += f"\n   {fb['feedback_text'][:100]}..."
    
    if len(feedback) > 10:
        response += f"\n\n... и еще {len(feedback) - 10} отзывов"
    
    await callback.message.answer(response)
    await callback.answer()

@dp.callback_query(F.data == "feedback_stats")
async def handle_feedback_stats(callback: types.CallbackQuery):
    """Статистика обратной связи"""
    if not ADMIN_ID or callback.from_user.id != ADMIN_ID:
        await callback.answer("⛔ Доступ запрещен", show_alert=True)
        return
    
    conn = sqlite3.connect("tenders.db")
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    # Общая статистика
    cursor.execute('''
    SELECT 
        COUNT(*) as total_feedback,
        SUM(CASE WHEN feedback_type = 'like' THEN 1 ELSE 0 END) as likes,
        SUM(CASE WHEN feedback_type = 'dislike' THEN 1 ELSE 0 END) as dislikes,
        SUM(CASE WHEN feedback_type = 'comment' THEN 1 ELSE 0 END) as comments,
        SUM(CASE WHEN feedback_type = 'unsubscribe' THEN 1 ELSE 0 END) as unsubscribes
    FROM mailing_feedback
    ''')
    
    stats = cursor.fetchone()
    
    # Статистика за 30 дней
    cursor.execute('''
    SELECT 
        COUNT(*) as recent_feedback,
        SUM(CASE WHEN feedback_type = 'unsubscribe' THEN 1 ELSE 0 END) as recent_unsubscribes
    FROM mailing_feedback 
    WHERE date(created_at) >= date('now', '-30 days')
    ''')
    
    recent = cursor.fetchone()
    
    # Самые популярные рассылки
    cursor.execute('''
    SELECT mm.id, mm.mailing_text, COUNT(mf.id) as feedback_count
    FROM manual_mailings mm
    LEFT JOIN mailing_feedback mf ON mm.id = mf.mailing_id
    GROUP BY mm.id
    ORDER BY feedback_count DESC
    LIMIT 5
    ''')
    
    popular = cursor.fetchall()
    
    conn.close()
    
    response = f"""
📊 <b>Статистика обратной связи</b>

<b>Общая статистика:</b>
• Всего отзывов: {stats['total_feedback'] or 0}
• 👍 Понравилось: {stats['likes'] or 0}
• 👎 Не понравилось: {stats['dislikes'] or 0}
• 💬 Комментарии: {stats['comments'] or 0}
• 🚫 Отписки: {stats['unsubscribes'] or 0}

<b>За последние 30 дней:</b>
• Новых отзывов: {recent['recent_feedback'] or 0}
• Отписок: {recent['recent_unsubscribes'] or 0}

<b>Самые обсуждаемые рассылки:</b>
"""
    
    for i, mailing in enumerate(popular, 1):
        mailing_text_preview = mailing['mailing_text'][:50] + "..." if len(mailing['mailing_text']) > 50 else mailing['mailing_text']
        response += f"\n{i}. ID#{mailing['id']}: {mailing_text_preview}"
        response += f"\n   Отзывов: {mailing['feedback_count']}"
    
    await callback.message.answer(response)
    await callback.answer()

@dp.callback_query(F.data == "refresh_feedback")
async def handle_refresh_feedback(callback: types.CallbackQuery):
    """Обновление списка обратной связи"""
    await show_feedback(callback.message)
    await callback.answer("Список обновлен")

# =========== ОСТАЛЬНЫЕ АДМИН ФУНКЦИИ ===========
@dp.message(F.text == "📩 Сообщения менеджеру")
async def show_manager_messages(message: types.Message):
    """Показать сообщения менеджеру"""
    if not ADMIN_ID or message.from_user.id != ADMIN_ID:
        await message.answer("⛔ Доступ запрещен")
        return
    
    conn = sqlite3.connect("tenders.db")
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    cursor.execute('''
    SELECT mm.*, u.username, u.first_name, u.last_name 
    FROM manager_messages mm
    JOIN users u ON mm.user_id = u.user_id
    WHERE mm.processed = 0
    ORDER BY mm.created_at DESC
    LIMIT 10
    ''')
    
    messages = cursor.fetchall()
    conn.close()
    
    if not messages:
        await message.answer("📭 Новых сообщений менеджеру нет")
        return
    
    response = f"📩 <b>Новые сообщения менеджеру ({len(messages)}):</b>\n\n"
    
    for i, msg in enumerate(messages, 1):
        date_str = msg['created_at'][:16] if msg['created_at'] else "??.?? ??:??"
        type_icon = "💬" if msg['message_type'] == 'text' else "📎" if msg['message_type'] == 'document' else "🖼"
        
        response += f"{i}. <b>#{msg['id']}</b> {type_icon}\n"
        response += f"   👤 @{msg['username'] or 'без username'}\n"
        response += f"   📝 {msg['message_text'][:50]}...\n"
        response += f"   ⏰ {date_str}\n\n"
    
    await message.answer(response)

@dp.message(F.text == "⚙️ Настройки")
async def show_settings(message: types.Message):
    """Показать настройки"""
    if not ADMIN_ID or message.from_user.id != ADMIN_ID:
        await message.answer("⛔ Доступ запрещен")
        return
    
    stats = db.get_statistics(7)
    
    await message.answer(
        "⚙️ <b>Настройки бота:</b>\n\n"
        "<b>Текущие параметры:</b>\n"
        f"• Время работы: {WORK_START_HOUR}:00-{WORK_END_HOUR}:00 Пн-Пт\n"
        f"• Follow-up через: 1 час\n"
        f"• ID администратора: {ADMIN_ID}\n\n"
        "<b>Статистика за неделю:</b>\n"
        f"• Новых пользователей: {stats['new_users']}\n"
        f"• Пользователей с подпиской: {stats['subscribed_users']}\n"
        f"• Пользователей без подписки: {stats['unsubscribed_users']}\n"
        f"• Получено отзывов: {stats['mailings_feedback']}\n\n"
        "<b>Функции:</b>\n"
        "✅ Отправка анкет в Word\n"
        "✅ Диалог с менеджером\n"
        "✅ Ручные рассылки с обратной связью\n"
        "✅ Управление подписками\n"
        "✅ Просмотр обратной связи\n"
        "✅ Автоматические отчеты\n\n"
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
    """Завершение анкеты - СОЗДАНИЕ И ОТПРАВКА ФАЙЛА АНКЕТЫ ПОЛЬЗОВАТЕЛЮ"""
    user_data = await state.get_data()
    user_data['keywords'] = message.text.strip()
    user_id = message.from_user.id
    username = message.from_user.username or "без username"
    
    try:
        # Создаем заполненную анкету в формате Word
        anketa_path = create_filled_anketa(user_data)
        
        if anketa_path:
            try:
                # Используем BufferedInputFile для отправки файла
                with open(anketa_path, 'rb') as f:
                    file_content = f.read()
                
                input_file = BufferedInputFile(
                    file_content, 
                    filename=f"Анкета_Тритика_{user_data.get('company_name', 'Компания')}.docx"
                )
                
                # Отправляем заполненную анкету пользователю
                await bot.send_document(
                    user_id,
                    input_file,
                    caption=(
                        "📄 <b>Ваша анкета заполнена и сохранена!</b>\n\n"
                        "✅ <b>Вы можете:</b>\n"
                        "1. Сохранить этот файл на компьютере\n"
                        "2. Отправить его менеджеру через кнопку '📤 Написать менеджеру'\n"
                        "3. Или мы обработаем ее автоматически\n\n"
                        "<i>Анкета также отправлена менеджеру для обработки.</i>"
                    )
                )
                
                # Сохраняем анкету в БД с путем к файлу
                questionnaire_id = db.save_questionnaire(user_id, user_data, anketa_path)
                
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
                        f"{time_info}\n\n"
                        f"<i>Заполненная анкета отправлена вам выше. Вы можете отправить ее менеджеру для ускорения обработки.</i>",
                        reply_markup=get_main_keyboard()
                    )
                    
                    # Отправляем анкету администратору
                    await send_questionnaire_to_admin(questionnaire_id, user_id, user_data, username, anketa_path)
                    
                    logger.info(f"✅ Анкета #{questionnaire_id} сохранена, файл создан и отправлен администратору")
                    
                    # Удаляем временный файл
                    try:
                        os.remove(anketa_path)
                        logger.info(f"Временный файл анкеты удален: {anketa_path}")
                    except Exception as e:
                        logger.error(f"Ошибка удаления временного файла: {e}")
                else:
                    await message.answer(
                        "❌ <b>Ошибка при сохранении анкеты в базе данных</b>\n\n"
                        "Пожалуйста, попробуйте еще раз позже или свяжитесь с поддержкой.",
                        reply_markup=get_main_keyboard()
                    )
                
            except Exception as e:
                logger.error(f"❌ Ошибка отправки файла анкеты: {e}")
                # Попробуем сохранить без файла
                questionnaire_id = db.save_questionnaire(user_id, user_data)
                
                if questionnaire_id:
                    export_id = db.create_tender_export(questionnaire_id, user_id)
                    
                    if db.is_working_hours():
                        time_info = "⏱️ <b>Сейчас ищу для вас актуальные тендеры. Не пройдет и часа, как я пришлю подборку на почту и (или) в телеграм.</b>"
                    else:
                        next_time = db.get_next_working_time()
                        time_info = f"⏱️ <b>Запрос получен в нерабочее время. Вышлю с 9:00 до 17:00 {next_time.strftime('%d.%m.%Y')}.</b>"
                    
                    await message.answer(
                        f"🎉 <b>Анкета #{questionnaire_id} сохранена!</b>\n\n"
                        f"{time_info}\n\n"
                        f"<i>Приносим извинения, но нам не удалось отправить файл анкеты. Данные сохранены и отправлены менеджеру.</i>",
                        reply_markup=get_main_keyboard()
                    )
                    
                    await send_questionnaire_to_admin(questionnaire_id, user_id, user_data, username)
                    logger.info(f"✅ Анкета #{questionnaire_id} сохранена (без файла) и отправлена администратору")
        else:
            # Если не удалось создать файл, сохраняем без него
            questionnaire_id = db.save_questionnaire(user_id, user_data)
            
            if questionnaire_id:
                export_id = db.create_tender_export(questionnaire_id, user_id)
                
                if db.is_working_hours():
                    time_info = "⏱️ <b>Сейчас ищу для вас актуальные тендеры. Не пройдет и часа, как я пришлю подборку на почту и (или) в телеграм.</b>"
                else:
                    next_time = db.get_next_working_time()
                    time_info = f"⏱️ <b>Запрос получен в нерабочее время. Вышлю с 9:00 до 17:00 {next_time.strftime('%d.%m.%Y')}.</b>"
                
                await message.answer(
                    f"🎉 <b>Анкета #{questionnaire_id} сохранена!</b>\n\n"
                    f"{time_info}\n\n"
                    f"<i>Приносим извинения, но нам не удалось сформировать файл анкеты. Данные сохранены и отправлены менеджеру.</i>",
                    reply_markup=get_main_keyboard()
                )
                
                await send_questionnaire_to_admin(questionnaire_id, user_id, user_data, username)
                logger.info(f"✅ Анкета #{questionnaire_id} сохранена (без файла) и отправлена администратору")
            else:
                await message.answer(
                    "❌ <b>Ошибка при сохранении анкеты</b>\n\n"
                    "Пожалуйста, попробуйте еще раз позже или свяжитесь с поддержкой.",
                    reply_markup=get_main_keyboard()
                )
    
    except Exception as e:
        logger.error(f"❌ Критическая ошибка при сохранении анкеты: {e}")
        await message.answer(
            "❌ <b>Произошла критическая ошибка при сохранении анкеты</b>\n\n"
            "Пожалуйста, попробуйте еще раз позже или свяжитесь с поддержкой.",
            reply_markup=get_main_keyboard()
        )
    
    await state.clear()

# =========== ЗАПУСК БОТА И HTTP СЕРВЕРА ===========
async def main():
    """Основная функция запуска"""
    print("\n" + "="*60)
    print("🚀 ЗАПУСК БОТА ТРИТИКА (ТЕНДЕРПОИСК)")
    print("="*60)
    
    # Скачиваем файл анкеты при запуске
    print("📥 Проверяю наличие файла анкеты...")
    if not os.path.exists(ANKETA_LOCAL_PATH) or os.path.getsize(ANKETA_LOCAL_PATH) == 0:
        print("Файл анкеты не найден или пустой, скачиваю с GitHub...")
        success = await download_anketa_file()
        if not success:
            print("⚠️ Внимание: Файл анкеты не скачан. Функция отправки анкет будет ограничена.")
    else:
        file_size = os.path.getsize(ANKETA_LOCAL_PATH)
        print(f"✅ Файл анкеты уже существует ({file_size} байт)")
    
    # Проверяем бота
    try:
        bot_info = await bot.get_me()
        print(f"✅ Бот: @{bot_info.username}")
        print(f"✅ Имя: {bot_info.first_name}")
        print(f"✅ ID: {bot_info.id}")
        
        if ADMIN_ID:
            print(f"✅ Администратор: {ADMIN_ID}")
        else:
            print("⚠️ Администратор не установлен (ADMIN_ID)")
    except Exception as e:
        print(f"❌ Ошибка проверки бота: {e}")
        print("⚠️ Проверьте токен бота")
        return
    
    # Запускаем задачу для follow-up сообщений
    asyncio.create_task(schedule_follow_ups())
    print("✅ Follow-up система запущена")
    
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
    
    print("\n" + "="*60)
    print("🤖 БОТ УСПЕШНО ЗАПУЩЕН!")
    print("="*60)
    print(f"\n📱 Откройте Telegram и найдите бота:")
    print(f"   👉 https://t.me/{bot_info.username}")
    print("\n👤 Обычный режим: /start")
    print("🛠️ Админ-панель: /admin (если настроен ADMIN_ID)")
    print("\n🔄 Ожидание сообщений...")
    print(f"🌐 Health check активен на порту {PORT}\n")
    print("⏰ Follow-up система активна (проверка каждые 5 минут)")
    
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
