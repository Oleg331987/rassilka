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
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
import json

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
            region TEXT,
            is_active BOOLEAN DEFAULT 1,
            has_filled_questionnaire BOOLEAN DEFAULT 0,
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
        
        # Рассылки (ручные)
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS manual_mailings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            admin_id INTEGER,
            mailing_text TEXT,
            mailing_type TEXT,
            filter_criteria TEXT,
            sent_count INTEGER DEFAULT 0,
            failed_count INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            sent_at TIMESTAMP
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
    
    def get_users_by_filter(self, filter_type: str):
        """Получение пользователей по фильтру"""
        conn = sqlite3.connect(self.db_name)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        if filter_type == "all":
            cursor.execute('''
            SELECT user_id, username, first_name, last_name, company 
            FROM users 
            WHERE is_active = 1
            ''')
        elif filter_type == "with_questionnaire":
            cursor.execute('''
            SELECT user_id, username, first_name, last_name, company 
            FROM users 
            WHERE is_active = 1 AND has_filled_questionnaire = 1
            ''')
        elif filter_type == "without_questionnaire":
            cursor.execute('''
            SELECT user_id, username, first_name, last_name, company 
            FROM users 
            WHERE is_active = 1 AND has_filled_questionnaire = 0
            ''')
        elif filter_type == "recent_week":
            cursor.execute('''
            SELECT user_id, username, first_name, last_name, company 
            FROM users 
            WHERE is_active = 1 AND date(created_at) >= date('now', '-7 days')
            ''')
        else:
            conn.close()
            return []
        
        users = cursor.fetchall()
        conn.close()
        
        return users
    
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
        SELECT COUNT(*) as count, SUM(sent_count) as total_sent 
        FROM manual_mailings 
        WHERE date(created_at) >= ?
        ''', (start_date,))
        mailings = cursor.fetchone()
        
        conn.close()
        
        return {
            'new_users': new_users,
            'exports_completed': exports_completed,
            'manager_messages': manager_messages,
            'mailings_count': mailings['count'] if mailings['count'] else 0,
            'mailings_sent': mailings['total_sent'] if mailings['total_sent'] else 0
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
            [KeyboardButton(text="📊 Новые анкеты"), KeyboardButton(text="✅ Отметить выгрузку")],
            [KeyboardButton(text="📈 Статистика"), KeyboardButton(text="📨 Создать рассылку")],
            [KeyboardButton(text="👥 Пользователи"), KeyboardButton(text="📩 Сообщения менеджеру")],
            [KeyboardButton(text="⚙️ Настройки"), KeyboardButton(text="👤 Режим пользователя")]
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

def get_mailing_filters_keyboard():
    """Клавиатура фильтров для рассылки"""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="👥 Все пользователи")],
            [KeyboardButton(text="📝 С анкетами")],
            [KeyboardButton(text="📭 Без анкет")],
            [KeyboardButton(text="🆕 За неделю")],
            [KeyboardButton(text="❌ Отмена")]
        ],
        resize_keyboard=True
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

# =========== ГЕНЕРАЦИЯ ДОКУМЕНТОВ ===========
def generate_anketa_docx(user_data: dict = None):
    """Генерация анкеты в формате DOCX (текстовый файл с расширением .docx)"""
    from docx import Document
    from docx.shared import Inches, Pt
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    
    # Создаем документ
    doc = Document()
    
    # Заголовок
    title = doc.add_heading('АНКЕТА ДЛЯ ПОИСКА ТЕНДЕРОВ', 0)
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    
    # Информация о компании
    doc.add_paragraph('Компания: Тритика (TenderGo)')
    doc.add_paragraph('Дата: ' + datetime.now().strftime('%d.%m.%Y'))
    doc.add_paragraph()
    
    # Если есть данные пользователя
    if user_data:
        doc.add_paragraph('Данные заполнены через бота:')
        doc.add_paragraph(f'1. ФИО полностью: {user_data.get("full_name", "___________________")}')
        doc.add_paragraph(f'2. Название компании: {user_data.get("company_name", "___________________")}')
        doc.add_paragraph(f'3. Телефон для связи: {user_data.get("phone", "___________________")}')
        doc.add_paragraph(f'4. Email для отправки тендеров: {user_data.get("email", "___________________")}')
        doc.add_paragraph(f'5. Сфера деятельности компании: {user_data.get("activity", "___________________")}')
        doc.add_paragraph(f'6. Ключевые слова для поиска: {user_data.get("keywords", "___________________")}')
        doc.add_paragraph(f'7. Бюджет контрактов: {user_data.get("budget", "___________________")}')
        doc.add_paragraph(f'8. Регионы работы: {user_data.get("region", "___________________")}')
    else:
        # Пустая анкета
        doc.add_paragraph('1. ФИО полностью: ___________________')
        doc.add_paragraph('2. Название компании: ___________________')
        doc.add_paragraph('3. Телефон для связи: ___________________')
        doc.add_paragraph('4. Email для отправки тендеров: ___________________')
        doc.add_paragraph('5. Сфера деятельности компании: ___________________')
        doc.add_paragraph('6. Ключевые слова для поиска: ___________________')
        doc.add_paragraph('7. Бюджет контрактов: ___________________')
        doc.add_paragraph('8. Регионы работы: ___________________')
    
    doc.add_paragraph()
    doc.add_paragraph('Инструкция по заполнению:')
    doc.add_paragraph('1. Заполните все поля анкеты')
    doc.add_paragraph('2. Сохраните файл')
    doc.add_paragraph('3. Отправьте заполненную анкету:')
    doc.add_paragraph('   • На email: info@tritica.ru')
    doc.add_paragraph('   • Или через бота (кнопка "Написать менеджеру")')
    doc.add_paragraph('   • Или менеджеру в Telegram: @tritica_manager')
    
    # Сохраняем во временный файл
    temp_file = tempfile.NamedTemporaryFile(suffix='.docx', delete=False)
    doc.save(temp_file.name)
    
    return temp_file.name

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
        "• Написать менеджеру (отправить вопрос или заполненную анкету)\n"
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
async def download_questionnaire(message: types.Message, state: FSMContext):
    """Скачать анкету в Word"""
    await state.clear()
    
    try:
        # Генерируем анкету
        anketa_path = generate_anketa_docx()
        
        # Отправляем файл
        with open(anketa_path, 'rb') as anketa_file:
            await message.answer_document(
                anketa_file,
                caption=(
                    "📄 <b>Анкета для заполнения в Word</b>\n\n"
                    "Заполните анкету и отправьте нам одним из способов:\n\n"
                    "1. 📧 <b>Email:</b> info@tritica.ru\n"
                    "2. 🤖 <b>Через бота:</b> кнопка 'Написать менеджеру'\n"
                    "3. 👨‍💼 <b>Менеджер в Telegram:</b> @tritica_manager\n\n"
                    "<i>Или заполните анкету онлайн через бота (быстрее и удобнее)</i>"
                )
            )
        
        # Удаляем временный файл
        os.unlink(anketa_path)
        
    except Exception as e:
        logger.error(f"Ошибка генерации анкеты: {e}")
        
        # Отправляем текстовую версию
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

Заполните и отправьте одним из способов:
• На email: info@tritica.ru
• Через бота (кнопка "Написать менеджеру")
• Менеджеру в Telegram: @tritica_manager"""
        
        await message.answer(
            "📄 <b>Анкета для заполнения</b>\n\n"
            "Вы можете заполнить анкету и отправить нам.\n\n"
            "<b>Способы отправки:</b>\n"
            "📧 <b>Email:</b> info@tritica.ru\n"
            "🤖 <b>Через бота:</b> кнопка 'Написать менеджеру'\n"
            "👨‍💼 <b>Менеджер в Telegram:</b> @tritica_manager\n\n"
            "<i>Или заполните анкету онлайн через бота (быстрее и удобнее)</i>"
        )
        
        await message.answer(f"<pre>{questionnaire_text}</pre>")

@dp.message(F.text == "📤 Написать менеджеру")
async def start_manager_dialog(message: types.Message, state: FSMContext):
    """Начало диалога с менеджером"""
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
    current_state = await state.get_state()
    
    if current_state in [ManagerDialog.waiting_for_message, 
                         ManualMailing.waiting_for_text,
                         ManualMailing.waiting_for_filter,
                         ManualMailing.waiting_for_confirmation]:
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
        f"📧 Email: {questionnaire['email']}"
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

💬 <b>Сообщения менеджеру:</b>
• Всего сообщений: {stats['manager_messages']}

📨 <b>Ручные рассылки:</b>
• Количество рассылок: {stats['mailings_count']}
• Отправлено сообщений: {stats['mailings_sent']}

📅 <b>Дата отчета:</b>
{datetime.now().strftime('%d.%m.%Y %H:%M')}
"""
    
    await message.answer(response)

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
        "👥 Все пользователи": "all",
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
    """Подтверждение и отправка рассылки"""
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
            await bot.send_message(user['user_id'], mailing_text, parse_mode=ParseMode.HTML)
            success_count += 1
            
            # Пауза, чтобы не превысить лимиты Telegram
            await asyncio.sleep(0.05)
            
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
        f"<i>Рассылка сохранена в истории.</i>",
        reply_markup=get_admin_keyboard()
    )
    
    await state.clear()

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
           COUNT(DISTINCT mm.id) as message_count
    FROM users u
    LEFT JOIN questionnaires q ON u.user_id = q.user_id
    LEFT JOIN tender_exports te ON q.id = te.questionnaire_id
    LEFT JOIN manager_messages mm ON u.user_id = mm.user_id
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
        has_anketa = "✅" if user['has_filled_questionnaire'] else "❌"
        
        response += f"{i}. <b>@{user['username'] or 'без username'}</b>\n"
        response += f"   🆔 ID: {user['user_id']}\n"
        response += f"   👤 {user['first_name']} {user['last_name'] or ''}\n"
        response += f"   📋 Анкета: {has_anketa}\n"
        response += f"   📤 Выгрузок: {user['export_count']}\n"
        response += f"   💬 Сообщений: {user['message_count']}\n"
        response += f"   📅 Регистрация: {date_str}\n\n"
    
    await message.answer(response)

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
    
    await message.answer(
        "⚙️ <b>Настройки бота:</b>\n\n"
        "<b>Текущие параметры:</b>\n"
        f"• Время работы: {WORK_START_HOUR}:00-{WORK_END_HOUR}:00 Пн-Пт\n"
        f"• Follow-up через: 1 час\n"
        f"• ID администратора: {ADMIN_ID}\n\n"
        "<b>Функции:</b>\n"
        "✅ Отправка анкет в Word\n"
        "✅ Диалог с менеджером\n"
        "✅ Ручные рассылки\n"
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
