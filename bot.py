#!/usr/bin/env python3
"""
🤖 БОТ ТЕНДЕРПОИСК
Упрощенный, гарантированно работающий код
"""

import os
import asyncio
import logging
import json
import sqlite3
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any

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

# =========== НАСТРОЙКИ ===========
# ВАШ ТОКЕН БОТА
BOT_TOKEN = "8227089023:AAFHtDuflB-wKcxp-bEwfPU0AgD1smFyt5I"

# ID администратора (узнать у @userinfobot)
ADMIN_ID = None  # Замените на ваш ID

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

print("="*60)
print("🤖 ЗАГРУЗКА БОТА ТЕНДЕРПОИСК")
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
    print("✅ Бот инициализирован")
except Exception as e:
    logger.error(f"❌ Ошибка инициализации бота: {e}")
    print(f"❌ Ошибка: {e}")
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
        
        # Таблица пользователей
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER UNIQUE,
            username TEXT,
            first_name TEXT,
            last_name TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        ''')
        
        # Таблица анкет
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS questionnaires (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            full_name TEXT,
            company_name TEXT,
            phone TEXT,
            email TEXT,
            activity TEXT,
            status TEXT DEFAULT 'new',
            tender_sent BOOLEAN DEFAULT 0,
            tender_sent_at TEXT,
            follow_up_sent BOOLEAN DEFAULT 0,
            follow_up_at TEXT,
            follow_up_response TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        ''')
        
        # Таблица рассылок
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS mailings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            message_text TEXT,
            sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        ''')
        
        conn.commit()
        conn.close()
        logger.info("✅ База данных инициализирована")
        print("✅ База данных инициализирована")
    
    def add_user(self, user_id: int, username: str, first_name: str, last_name: str = ""):
        """Добавление пользователя"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        
        try:
            cursor.execute('''
            INSERT OR IGNORE INTO users (user_id, username, first_name, last_name)
            VALUES (?, ?, ?, ?)
            ''', (user_id, username, first_name, last_name))
            conn.commit()
            return True
        except Exception as e:
            logger.error(f"Ошибка добавления пользователя: {e}")
            return False
        finally:
            conn.close()
    
    def save_questionnaire(self, user_id: int, data: dict):
        """Сохранение анкеты"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        
        try:
            cursor.execute('''
            INSERT INTO questionnaires 
            (user_id, full_name, company_name, phone, email, activity)
            VALUES (?, ?, ?, ?, ?, ?)
            ''', (
                user_id,
                data.get('full_name', ''),
                data.get('company_name', ''),
                data.get('phone', ''),
                data.get('email', ''),
                data.get('activity', '')
            ))
            conn.commit()
            return cursor.lastrowid
        except Exception as e:
            logger.error(f"Ошибка сохранения анкеты: {e}")
            return None
        finally:
            conn.close()
    
    def get_user_questionnaires(self, user_id: int):
        """Получение анкет пользователя"""
        conn = sqlite3.connect(self.db_name)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute('''
        SELECT * FROM questionnaires 
        WHERE user_id = ? 
        ORDER BY created_at DESC
        ''', (user_id,))
        
        results = cursor.fetchall()
        conn.close()
        return results
    
    def get_new_questionnaires(self, limit=10):
        """Получение новых анкет"""
        conn = sqlite3.connect(self.db_name)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute('''
        SELECT q.*, u.username 
        FROM questionnaires q
        LEFT JOIN users u ON q.user_id = u.user_id
        WHERE q.status = 'new'
        ORDER BY q.created_at DESC
        LIMIT ?
        ''', (limit,))
        
        results = cursor.fetchall()
        conn.close()
        return results

db = Database()

# =========== КЛАВИАТУРЫ ===========
def get_main_keyboard():
    """Главная клавиатура"""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📝 Заполнить анкету онлайн")],
            [KeyboardButton(text="📥 Скачать анкету в Word")],
            [KeyboardButton(text="❓ Как это работает?")],
            [KeyboardButton(text="📞 Контакты"), KeyboardButton(text="ℹ️ Помощь")]
        ],
        resize_keyboard=True,
        input_field_placeholder="Выберите действие..."
    )

def get_admin_keyboard():
    """Клавиатура администратора"""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📊 Новые анкеты"), KeyboardButton(text="📈 Статистика")],
            [KeyboardButton(text="👤 Все пользователи"), KeyboardButton(text="📋 Все анкеты")],
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

def get_yes_no_keyboard():
    """Клавиатура Да/Нет для follow-up"""
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

# =========== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ===========
def is_working_hours():
    """Проверка рабочего времени (пн-пт, 9:00-17:00)"""
    now = datetime.now()
    
    # Проверяем день недели (0 - понедельник, 4 - пятница)
    if now.weekday() >= 5:  # Суббота или воскресенье
        return False
    
    # Проверяем время (9:00 - 17:00)
    if now.hour < 9 or now.hour >= 17:
        return False
    
    return True

def get_next_working_time():
    """Получить следующее рабочее время"""
    now = datetime.now()
    
    # Если сейчас рабочее время
    if is_working_hours():
        return now
    
    # Вычисляем следующий рабочий день
    days_to_add = 1
    while (now.weekday() + days_to_add) % 7 >= 5:
        days_to_add += 1
    
    next_work_day = now + timedelta(days=days_to_add)
    return next_work_day.replace(hour=9, minute=0, second=0, microsecond=0)

async def send_notification_to_admin(message: str):
    """Отправить уведомление администратору"""
    if ADMIN_ID:
        try:
            await bot.send_message(ADMIN_ID, message)
        except Exception as e:
            logger.error(f"Не удалось отправить уведомление админу: {e}")

# =========== ОБРАБОТЧИКИ КОМАНД ===========
@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    """Обработчик команды /start"""
    await state.clear()
    
    user = message.from_user
    user_id = user.id
    
    # Добавляем пользователя в базу
    db.add_user(user_id, user.username or "", user.first_name, user.last_name or "")
    
    # Определяем режим (админ или пользователь)
    is_admin = ADMIN_ID and user_id == ADMIN_ID
    
    if is_admin:
        await message.answer(
            "🛠️ <b>Панель администратора</b>\n\n"
            "Вы вошли как администратор бота.\n"
            "Используйте кнопки ниже для управления.",
            reply_markup=get_admin_keyboard()
        )
    else:
        await message.answer(
            "👋 <b>Привет! Я бот ТендерПоиск.</b>\n\n"
            "Я помогаю компаниям находить выгодные тендеры. "
            "Хотите бесплатно получить подборку тендеров по вашей сфере? "
            "Вам надо лишь заполнить короткую анкету.\n\n"
            "<i>Выберите действие:</i>",
            reply_markup=get_main_keyboard()
        )
    
    logger.info(f"Пользователь {user_id} (@{user.username}) нажал /start")

@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    """Обработчик команды /help"""
    await message.answer(
        "🤖 <b>Помощь по боту:</b>\n\n"
        "<b>Основные команды:</b>\n"
        "/start - Главное меню\n"
        "/help - Эта справка\n"
        "/my_questionnaires - Мои анкеты\n\n"
        "<b>Основные функции:</b>\n"
        "• Заполнить анкету онлайн\n"
        "• Скачать анкету в Word\n"
        "• Получить подборку тендеров\n"
        "• Консультация по участию\n\n"
        "<b>Контакты поддержки:</b>\n"
        "📧 support@tenderpoisk.ru\n"
        "📱 +7 (999) 123-45-67"
    )

@dp.message(Command("my_questionnaires"))
async def cmd_my_questionnaires(message: types.Message):
    """Мои анкеты"""
    questionnaires = db.get_user_questionnaires(message.from_user.id)
    
    if not questionnaires:
        await message.answer(
            "📭 У вас пока нет заполненных анкет.\n\n"
            "Хотите заполнить анкету для поиска тендеров?",
            reply_markup=get_main_keyboard()
        )
        return
    
    response = f"📋 <b>Ваши анкеты ({len(questionnaires)}):</b>\n\n"
    
    for i, q in enumerate(questionnaires, 1):
        date_str = q['created_at'][:10] if q['created_at'] else "??.??.????"
        status_icon = "✅" if q['tender_sent'] else "⏳"
        status_text = "Тендер отправлен" if q['tender_sent'] else "В обработке"
        
        response += f"{i}. <b>{q['company_name']}</b>\n"
        response += f"   📅 {date_str} | {status_icon} {status_text}\n"
        response += f"   📞 {q['phone']}\n"
        response += f"   📧 {q['email']}\n\n"
    
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
        logger.info(f"Админ {user_id} вошел в панель")
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
    # Создаем текстовую анкету (можно заменить на реальный файл)
    questionnaire_text = """АНКЕТА ДЛЯ ПОИСКА ТЕНДЕРОВ

1. ФИО полностью: ___________________
2. Название компании: ___________________
3. Телефон для связи: ___________________
4. Email для отправки тендеров: ___________________
5. Сфера деятельности компании: ___________________
6. Ключевые слова для поиска: ___________________
7. Бюджет контрактов: ___________________
8. Регионы работы: ___________________

Заполните и отправьте на: info@tenderpoisk.ru
Или перешлите менеджеру в Telegram: @tender_manager"""
    
    await message.answer(
        "📄 <b>Скачайте анкету для заполнения</b>\n\n"
        "Вы можете заполнить анкету в Word и отправить нам.\n\n"
        "📧 <b>Email для отправки:</b> info@tenderpoisk.ru\n"
        "👨‍💼 <b>Менеджер в Telegram:</b> @tender_manager\n\n"
        "Или заполните анкету онлайн через бота (быстрее и удобнее)."
    )
    
    # Отправляем текстовую анкету
    await message.answer(f"<pre>{questionnaire_text}</pre>")

@dp.message(F.text == "❓ Как это работает?")
async def how_it_works(message: types.Message):
    """Объяснение работы сервиса"""
    await message.answer(
        "🔄 <b>Как работает наш сервис:</b>\n\n"
        "1. <b>Заполняете анкету</b> - онлайн или скачиваете шаблон\n"
        "2. <b>Мы анализируем</b> вашу сферу деятельности\n"
        "3. <b>Ищем тендеры</b> по 50+ площадкам\n"
        "4. <b>Формируем подборку</b> релевантных тендеров\n"
        "5. <b>Отправляем вам</b> на почту и в Telegram\n"
        "6. <b>Помогаем</b> с подготовкой документов\n\n"
        "⏱️ <b>Сроки:</b>\n"
        "• Выгрузка в течение 1 часа в рабочее время\n"
        "• С 9:00 до 17:00 по будням\n"
        "• Если запрос в нерабочее время - отправим в 9:00 следующего рабочего дня\n\n"
        "💡 <b>Бесплатно:</b> первая выгрузка тендеров - наш подарок для новых клиентов!"
    )

@dp.message(F.text == "📞 Контакты")
async def show_contacts(message: types.Message):
    """Показать контакты"""
    await message.answer(
        "📞 <b>Контакты компании ТендерПоиск</b>\n\n"
        "<b>Для клиентов:</b>\n"
        "• Телефон: +7 (999) 123-45-67\n"
        "• Email: clients@tenderpoisk.ru\n"
        "• Telegram: @tender_clients\n\n"
        "<b>Техническая поддержка:</b>\n"
        "• Email: support@tenderpoisk.ru\n"
        "• Telegram: @tender_support\n\n"
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
    
    questionnaires = db.get_new_questionnaires(10)
    
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

@dp.message(F.text == "📈 Статистика")
async def show_statistics(message: types.Message):
    """Показать статистику"""
    if not ADMIN_ID or message.from_user.id != ADMIN_ID:
        await message.answer("⛔ Доступ запрещен")
        return
    
    conn = sqlite3.connect("tenders.db")
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    # Общая статистика
    cursor.execute("SELECT COUNT(*) as total FROM questionnaires")
    total = cursor.fetchone()['total']
    
    cursor.execute("SELECT COUNT(*) as sent FROM questionnaires WHERE tender_sent = 1")
    sent = cursor.fetchone()['sent']
    
    cursor.execute("SELECT COUNT(*) as today FROM questionnaires WHERE DATE(created_at) = DATE('now')")
    today = cursor.fetchone()['today']
    
    cursor.execute("SELECT COUNT(DISTINCT user_id) as users FROM questionnaires")
    users = cursor.fetchone()['users']
    
    conn.close()
    
    response = f"""
📊 <b>Статистика бота</b>

📋 <b>Анкеты:</b>
• Всего анкет: {total}
• Отправлено тендеров: {sent}
• Сегодня анкет: {today}

👥 <b>Пользователи:</b>
• Уникальных: {users}

📅 <b>Дата отчета:</b>
{datetime.now().strftime('%d.%m.%Y %H:%M')}
"""
    
    await message.answer(response)

@dp.message(F.text == "👤 Все пользователи")
async def show_all_users(message: types.Message):
    """Показать всех пользователей"""
    if not ADMIN_ID or message.from_user.id != ADMIN_ID:
        await message.answer("⛔ Доступ запрещен")
        return
    
    conn = sqlite3.connect("tenders.db")
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    cursor.execute('''
    SELECT u.*, COUNT(q.id) as questionnaire_count
    FROM users u
    LEFT JOIN questionnaires q ON u.user_id = q.user_id
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
        response += f"   📅 Регистрация: {date_str}\n\n"
    
    await message.answer(response)

@dp.message(F.text == "📋 Все анкеты")
async def show_all_questionnaires(message: types.Message):
    """Показать все анкеты"""
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
    ORDER BY q.created_at DESC
    LIMIT 10
    ''')
    
    questionnaires = cursor.fetchall()
    conn.close()
    
    if not questionnaires:
        await message.answer("📭 Анкет нет")
        return
    
    response = f"📋 <b>Все анкеты (последние 10):</b>\n\n"
    
    for i, q in enumerate(questionnaires, 1):
        date_str = q['created_at'][:16] if q['created_at'] else "??.?? ??:??"
        status_icon = "✅" if q['tender_sent'] else "⏳"
        
        response += f"<b>{i}. #{q['id']} - {q['company_name']}</b>\n"
        response += f"   👤 {q['full_name']} (@{q['username'] or 'без username'})\n"
        response += f"   📞 {q['phone']}\n"
        response += f"   📧 {q['email']}\n"
        response += f"   🎯 {q['activity'][:30]}...\n"
        response += f"   📅 {date_str} | {status_icon} {'Отправлен' if q['tender_sent'] else 'Новый'}\n\n"
    
    await message.answer(response)

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
    """Завершение анкеты"""
    user_data = await state.get_data()
    user_data['activity'] = message.text.strip()
    user_id = message.from_user.id
    
    # Сохраняем анкету в базу
    questionnaire_id = db.save_questionnaire(user_id, user_data)
    
    if questionnaire_id:
        # Определяем время отправки
        if is_working_hours():
            time_info = "⏱️ <b>Сейчас ищу для вас актуальные тендеры. Не пройдет и часа, как я пришлю подборку на почту и в телеграм.</b>"
        else:
            next_time = get_next_working_time()
            time_info = f"⏱️ <b>Запрос получен в нерабочее время. Вышлю подборку {next_time.strftime('%d.%m.%Y')} с 9:00 до 17:00.</b>"
        
        await message.answer(
            f"🎉 <b>Анкета #{questionnaire_id} сохранена!</b>\n\n"
            f"{time_info}\n\n"
            f"📧 <b>Подборку пришлю:</b>\n"
            f"• На email: {user_data['email']}\n"
            f"• В этот чат Telegram\n\n"
            "<i>Следите за сообщениями!</i>",
            reply_markup=get_main_keyboard()
        )
        
        # Уведомление администратору
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
{'✅ В рабочее время' if is_working_hours() else '⏰ В нерабочее время'}
"""
        
        await send_notification_to_admin(notification)
        
        # Запланировать follow-up через 1 час (или в рабочее время)
        await schedule_follow_up(questionnaire_id, user_id)
        
        logger.info(f"Анкета #{questionnaire_id} сохранена для пользователя {user_id}")
    else:
        await message.answer(
            "❌ <b>Ошибка при сохранении анкеты</b>\n\n"
            "Пожалуйста, попробуйте еще раз позже или свяжитесь с поддержкой.",
            reply_markup=get_main_keyboard()
        )
    
    await state.clear()

# =========== FOLLOW-UP СИСТЕМА ===========
async def schedule_follow_up(questionnaire_id: int, user_id: int):
    """Планирование follow-up сообщения"""
    # В реальном боте здесь была бы система планирования
    # Для упрощения просто логируем
    logger.info(f"Запланирован follow-up для анкеты #{questionnaire_id} пользователя {user_id}")
    
    # В реальном приложении здесь был бы asyncio.sleep() или планировщик
    # Но для простоты оставим только логирование

@dp.message(F.text.contains("Да, нашел подходящее"))
async def handle_positive_response(message: types.Message):
    """Обработка положительного ответа на follow-up"""
    user_id = message.from_user.id
    
    await message.answer(
        "🎉 <b>Отлично!</b>\n\n"
        "Рады, что нашли подходящие тендеры!\n\n"
        "🤝 <b>Нужна помощь с подготовкой заявки?</b>\n"
        "Мы можем проконсультировать по:\n"
        "• Подготовке документов\n"
        "• Требованиям организаторов\n"
        "• Стратегии участия\n\n"
        "Напишите <b>«Консультация»</b>, и мы свяжемся с вами в течение 15 минут!",
        reply_markup=get_main_keyboard()
    )
    
    await send_notification_to_admin(
        f"✅ <b>Пользователь нашел подходящие тендеры</b>\n\n"
        f"👤 @{message.from_user.username or 'без username'}\n"
        f"🆔 ID: {user_id}\n"
        f"📅 Время: {datetime.now().strftime('%H:%M %d.%m.%Y')}"
    )

@dp.message(F.text.contains("Нет, не нашел"))
async def handle_negative_response(message: types.Message):
    """Обработка отрицательного ответа на follow-up"""
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

@dp.message(F.text.contains("Нужна консультация") | F.text.contains("Консультация"))
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
    
    await send_notification_to_admin(
        f"📞 <b>ЗАПРОС НА КОНСУЛЬТАЦИЮ</b>\n\n"
        f"👤 Пользователь: @{message.from_user.username or 'без username'}\n"
        f"🆔 ID: {message.from_user.id}\n"
        f"📅 Время: {datetime.now().strftime('%H:%M %d.%m.%Y')}\n"
        f"✉️ Сообщение: {message.text}"
    )

# =========== ОБРАБОТЧИК ВСЕХ СООБЩЕНИЙ ===========
@dp.message()
async def handle_all_messages(message: types.Message):
    """Обработчик всех остальных сообщений"""
    # Если это команда, игнорируем (она уже обработана)
    if message.text and message.text.startswith('/'):
        return
    
    # Если это не кнопка из меню
    if message.text not in [
        "📝 Заполнить анкету онлайн", "📥 Скачать анкету в Word",
        "❓ Как это работает?", "📞 Контакты", "ℹ️ Помощь",
        "❌ Отмена", "📊 Новые анкеты", "📈 Статистика",
        "👤 Все пользователи", "📋 Все анкеты", "👤 Режим пользователя"
    ]:
        # Отвечаем стандартным сообщением
        is_admin = ADMIN_ID and message.from_user.id == ADMIN_ID
        await message.answer(
            "🤖 <b>Я вас не понял</b>\n\n"
            "Используйте кнопки меню или команды:\n"
            "/start - Главное меню\n"
            "/help - Помощь\n\n"
            "<i>Или выберите действие из меню:</i>",
            reply_markup=get_main_keyboard() if not is_admin else get_admin_keyboard()
        )

# =========== ЗАПУСК БОТА ===========
async def main():
    """Основная функция запуска"""
    print("\n" + "="*60)
    print("🚀 ЗАПУСК БОТА ТЕНДЕРПОИСК")
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
    
    # Удаляем старые вебхуки
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        print("✅ Вебхуки очищены")
    except Exception as e:
        print(f"⚠️ Не удалось очистить вебхуки: {e}")
    
    print("\n" + "="*60)
    print("🤖 БОТ УСПЕШНО ЗАПУЩЕН!")
    print("="*60)
    print(f"\n📱 Откройте Telegram и найдите бота:")
    print(f"   👉 https://t.me/{bot_info.username}")
    print("\n👤 Обычный режим: /start")
    print("🛠️ Админ-панель: /admin (если настроен ADMIN_ID)")
    print("\n🔄 Ожидание сообщений...\n")
    
    try:
        await dp.start_polling(bot, skip_updates=True)
    except KeyboardInterrupt:
        print("\n🛑 Бот остановлен")
    finally:
        await bot.session.close()
        print("👋 Сессия бота закрыта")

if __name__ == "__main__":
    # Запускаем бота
    asyncio.run(main())
