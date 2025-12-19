import os
import sqlite3
import logging
import asyncio
import shutil
import sys
import threading
import time
import csv
import io
from datetime import datetime, timedelta
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
    ReplyKeyboardRemove
)
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
import http.server
import socketserver
from http.server import BaseHTTPRequestHandler, HTTPServer

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('bot.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Получаем токен и ID админа из переменных окружения
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = os.getenv("ADMIN_ID")

if not BOT_TOKEN:
    logger.error("❌ BOT_TOKEN не установлен! Добавьте в Secrets.")
    sys.exit(1)

if not ADMIN_ID:
    logger.error("❌ ADMIN_ID не установлен! Добавьте в Secrets.")
    sys.exit(1)

ADMIN_ID = int(ADMIN_ID)

# Инициализация бота
bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# Глобальные переменные для хранения данных о рассылке
mailing_data = {
    'active': False,
    'message_text': '',
    'sent_count': 0,
    'error_count': 0,
    'start_time': None
}

# Антифлуд фильтр
class AntiFlood(BaseFilter):
    def __init__(self, seconds: int = 2):
        self.seconds = seconds
        self.users = {}

    async def __call__(self, message: types.Message) -> bool:
        user_id = message.from_user.id
        current_time = datetime.now()
        
        if user_id in self.users:
            last_time = self.users[user_id]
            if (current_time - last_time).seconds < self.seconds:
                return False
        
        self.users[user_id] = current_time
        return True

# Валидация ИНН
def validate_inn(inn: str) -> bool:
    """Проверка валидности ИНН"""
    inn = inn.strip()
    if len(inn) not in (10, 12) or not inn.isdigit():
        return False
    return True  # Упростим валидацию

# Инициализация базы данных
def init_db():
    conn = sqlite3.connect('tenders.db', check_same_thread=False)
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
        last_mailing_date TEXT
    )
    ''')
    
    # Таблица сообщений (общение клиент-админ)
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
    
    conn.commit()
    conn.close()
    logger.info("✅ База данных инициализирована")

# Инициализируем БД
init_db()

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

class UserFeedback(StatesGroup):
    waiting_for_feedback = State()
    waiting_for_feedback_text = State()

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
            [KeyboardButton(text="📤 Сделать рассылку"), KeyboardButton(text="📋 Статистика")],
            [KeyboardButton(text="🏠 Главное меню")]
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

def get_pagination_keyboard(page: int, total_pages: int):
    """Клавиатура для пагинации"""
    buttons = []
    if page > 1:
        buttons.append(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"page_{page-1}"))
    if page < total_pages:
        buttons.append(InlineKeyboardButton(text="Вперёд ➡️", callback_data=f"page_{page+1}"))
    
    return InlineKeyboardMarkup(inline_keyboard=[buttons]) if buttons else None

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

# =========== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ===========
def save_questionnaire_to_db(user_data):
    """Сохраняем анкету в базу данных"""
    try:
        conn = sqlite3.connect('tenders.db', check_same_thread=False)
        cursor = conn.cursor()
        
        cursor.execute('''
        INSERT INTO questionnaires 
        (user_id, username, full_name, company_name, inn, contact_person, phone, email, 
         activity_sphere, industry, contract_amount, regions, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        ))
        
        conn.commit()
        questionnaire_id = cursor.lastrowid
        conn.close()
        
        logger.info(f"✅ Анкета #{questionnaire_id} сохранена в базу данных для пользователя {user_data['user_id']}")
        return questionnaire_id
    except Exception as e:
        logger.error(f"Ошибка сохранения в БД: {e}", exc_info=True)
        return None

def get_questionnaires(status=None, page=1, per_page=10):
    """Получаем заявки из базы с пагинацией"""
    try:
        conn = sqlite3.connect('tenders.db', check_same_thread=False)
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
        logger.error(f"Ошибка получения заявок: {e}")
        return [], 0, 0

def get_questionnaire_by_user_id(user_id):
    """Получаем последнюю анкету пользователя"""
    try:
        conn = sqlite3.connect('tenders.db', check_same_thread=False)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM questionnaires WHERE user_id = ? ORDER BY created_at DESC LIMIT 1", 
            (user_id,)
        )
        questionnaire = cursor.fetchone()
        conn.close()
        return questionnaire
    except Exception as e:
        logger.error(f"Ошибка получения анкеты: {e}")
        return None

def get_all_users():
    """Получаем всех пользователей для рассылки"""
    try:
        conn = sqlite3.connect('tenders.db', check_same_thread=False)
        cursor = conn.cursor()
        cursor.execute("SELECT DISTINCT user_id FROM questionnaires WHERE user_id IS NOT NULL")
        users = [row[0] for row in cursor.fetchall()]
        conn.close()
        return users
    except Exception as e:
        logger.error(f"Ошибка получения пользователей: {e}")
        return []

def save_mailing_stats(total_users, successful, failed, message_text):
    """Сохраняем статистику рассылки"""
    try:
        conn = sqlite3.connect('tenders.db', check_same_thread=False)
        cursor = conn.cursor()
        cursor.execute(
            """INSERT INTO mailings 
            (mailing_date, message_text, total_users, successful_sends, failed_sends)
            VALUES (?, ?, ?, ?, ?)""",
            (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), 
             message_text[:500],  # Обрезаем длинный текст
             total_users, successful, failed)
        )
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logger.error(f"Ошибка сохранения статистики рассылки: {e}")
        return False

def save_feedback(user_id, feedback_text, is_positive=True):
    """Сохраняем отзыв пользователя"""
    try:
        conn = sqlite3.connect('tenders.db', check_same_thread=False)
        cursor = conn.cursor()
        
        # Получаем анкету пользователя
        cursor.execute(
            "SELECT id FROM questionnaires WHERE user_id = ? ORDER BY created_at DESC LIMIT 1",
            (user_id,)
        )
        questionnaire = cursor.fetchone()
        
        if questionnaire:
            questionnaire_id = questionnaire[0]
            cursor.execute(
                """UPDATE questionnaires 
                SET feedback_given = 1, 
                    feedback_date = ?,
                    feedback_text = ?
                WHERE id = ?""",
                (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), feedback_text, questionnaire_id)
            )
        
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logger.error(f"Ошибка сохранения отзыва: {e}")
        return False

async def send_mailing_to_user(user_id, message_text):
    """Отправляем рассылку одному пользователю"""
    try:
        await bot.send_message(
            user_id,
            message_text,
            parse_mode=ParseMode.HTML
        )
        return True
    except Exception as e:
        logger.error(f"Ошибка отправки пользователю {user_id}: {e}")
        return False

async def start_mailing_task(message_text, admin_id):
    """Запускаем задачу рассылки"""
    global mailing_data
    
    users = get_all_users()
    total_users = len(users)
    
    if total_users == 0:
        await bot.send_message(admin_id, "❌ Нет пользователей для рассылки.")
        return
    
    mailing_data['active'] = True
    mailing_data['message_text'] = message_text
    mailing_data['sent_count'] = 0
    mailing_data['error_count'] = 0
    mailing_data['start_time'] = datetime.now()
    
    await bot.send_message(
        admin_id,
        f"🚀 Начинаю рассылку для {total_users} пользователей..."
    )
    
    successful = 0
    failed = 0
    
    for i, user_id in enumerate(users, 1):
        if not mailing_data['active']:
            await bot.send_message(admin_id, "❌ Рассылка остановлена администратором.")
            break
        
        result = await send_mailing_to_user(user_id, message_text)
        if result:
            successful += 1
            mailing_data['sent_count'] += 1
        else:
            failed += 1
            mailing_data['error_count'] += 1
        
        # Отправляем прогресс каждые 10 пользователей или в конце
        if i % 10 == 0 or i == total_users:
            progress = (i / total_users) * 100
            await bot.send_message(
                admin_id,
                f"📊 Прогресс: {i}/{total_users} ({progress:.1f}%)\n"
                f"✅ Успешно: {successful}\n"
                f"❌ Ошибок: {failed}"
            )
        
        # Небольшая задержка, чтобы не превысить лимиты Telegram
        await asyncio.sleep(0.1)
    
    # Сохраняем статистики
    save_mailing_stats(total_users, successful, failed, message_text)
    
    # Итоговый отчет
    duration = (datetime.now() - mailing_data['start_time']).total_seconds()
    await bot.send_message(
        admin_id,
        f"✅ Рассылка завершена!\n\n"
        f"📊 Итоги:\n"
        f"• Всего пользователей: {total_users}\n"
        f"• Успешно отправлено: {successful}\n"
        f"• С ошибками: {failed}\n"
        f"• Время выполнения: {duration:.1f} секунд"
    )
    
    mailing_data['active'] = False

# =========== ОБРАБОТЧИКИ КОМАНД ===========
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    """Обработка команды /start"""
    if message.from_user.id == ADMIN_ID:
        await message.answer(
            "👑 <b>Панель администратора</b>\n\n"
            "Добро пожаловать в админ-панель!\n\n"
            "<b>Доступные функции:</b>\n"
            "• 📊 Все заявки - просмотр всех анкет\n"
            "• 🆕 Новые заявки - только новые заявки\n"
            "• 📤 Сделать рассылку - массовая рассылка клиентам\n"
            "• 📋 Статистика - подробная статистика работы\n\n"
            "Используйте кнопки ниже:",
            reply_markup=get_admin_keyboard()
        )
    else:
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
    if message.from_user.id == ADMIN_ID:
        await message.answer("Главное меню администратора:", reply_markup=get_admin_keyboard())
    else:
        await message.answer("Главное меню:", reply_markup=get_main_keyboard())

# =========== ЗАПОЛНЕНИЕ АНКЕТЫ (ПОЛЬЗОВАТЕЛЬ) ===========
@dp.message(F.text == "📝 Заполнить анкету")
async def start_questionnaire(message: types.Message, state: FSMContext):
    """Начало заполнения анкеты"""
    if message.from_user.id == ADMIN_ID:
        await message.answer("Вы администратор, вам не нужно заполнять анкету.", reply_markup=get_admin_keyboard())
        return
    
    # Проверяем, не заполняется ли уже анкета
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
        reply_mup=get_cancel_keyboard()
    )
    await state.set_state(Questionnaire.waiting_for_inn)

@dp.message(Questionnaire.waiting_for_inn)
async def process_inn(message: types.Message, state: FSMContext):
    """Обработка ИНН"""
    if message.text == "❌ Отменить":
        await cancel_action(message, state)
        return
    
    inn = message.text.strip().replace(' ', '')
    if not validate_inn(inn):
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
    # Простая валидация телефона
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
    
    # Получаем все данные
    user_data = await state.get_data()
    user_data['regions'] = regions
    
    # Сохраняем в базу данных
    questionnaire_id = save_questionnaire_to_db(user_data)
    
    if questionnaire_id:
        # Отправляем подтверждение пользователю
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
        
        # Отправляем уведомление админу
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
            await bot.send_message(ADMIN_ID, admin_message)
            logger.info(f"✅ Анкета #{questionnaire_id} отправлена админу")
        except Exception as e:
            logger.error(f"❌ Ошибка отправки админу: {e}")
    else:
        await message.answer(
            "❌ Произошла ошибка при сохранении анкеты. Пожалуйста, попробуйте позже.",
            reply_markup=get_main_keyboard()
        )
    
    await state.clear()

# =========== ОБРАТНАЯ СВЯЗЬ ОТ ПОЛЬЗОВАТЕЛЯ ===========
@dp.message(F.text == "💬 Оставить отзыв")
async def start_feedback(message: types.Message, state: FSMContext):
    """Начало оставления отзыва"""
    if message.from_user.id == ADMIN_ID:
        await message.answer("Вы администратор, вам не нужно оставлять отзыв.", reply_markup=get_admin_keyboard())
        return
    
    # Проверяем, заполнял ли пользователь анкету
    questionnaire = get_questionnaire_by_user_id(message.from_user.id)
    
    if not questionnaire:
        await message.answer(
            "📝 <b>Сначала заполните анкету!</b>\n\n"
            "Чтобы оставить отзыв о нашей работе, сначала необходимо заполнить анкету для поиска тендеров.",
            reply_markup=get_main_keyboard()
        )
        return
    
    # Проверяем, оставлял ли уже отзыв
    if questionnaire[16]:  # feedback_given
        await message.answer(
            "✅ <b>Вы уже оставляли отзыв!</b>\n\n"
            "Спасибо за вашу обратную связь! Мы ценим ваше мнение.",
            reply_markup=get_main_keyboard()
        )
        return
    
    await message.answer(
        "💬 <b>Оставить отзыв</b>\n\n"
        "Пожалуйста, оцените нашу работу:\n"
        "• Устроило ли вас качество выгрузки тендеров?\n"
        "• Была ли информация полезной?\n"
        "• Какие улучшения вы бы предложили?\n\n"
        "Выберите вариант ниже:",
        reply_markup=get_yes_no_keyboard()
    )
    await state.set_state(UserFeedback.waiting_for_feedback)

@dp.message(UserFeedback.waiting_for_feedback)
async def process_feedback_choice(message: types.Message, state: FSMContext):
    """Обработка выбора оценки"""
    if message.text == "❌ Отменить":
        await cancel_action(message, state)
        return
    
    if message.text not in ["✅ Да, все отлично", "❌ Есть замечания"]:
        await message.answer("Пожалуйста, выберите один из предложенных вариантов:", reply_markup=get_yes_no_keyboard())
        return
    
    is_positive = message.text == "✅ Да, все отлично"
    await state.update_data(feedback_choice=is_positive)
    
    if is_positive:
        await message.answer(
            "🎉 <b>Отлично! Рады, что вы довольны!</b>\n\n"
            "Пожалуйста, напишите пару слов о том, что вам понравилось:",
            reply_markup=get_cancel_keyboard()
        )
    else:
        await message.answer(
            "📝 <b>Спасибо за честность!</b>\n\n"
            "Пожалуйста, опишите, что можно улучшить в нашей работе:",
            reply_markup=get_cancel_keyboard()
        )
    
    await state.set_state(UserFeedback.waiting_for_feedback_text)

@dp.message(UserFeedback.waiting_for_feedback_text)
async def process_feedback_text(message: types.Message, state: FSMContext):
    """Обработка текста отзыва"""
    if message.text == "❌ Отменить":
        await cancel_action(message, state)
        return
    
    if len(message.text.strip()) < 5:
        await message.answer("❌ Отзыв должен содержать минимум 5 символов. Пожалуйста, напишите подробнее:")
        return
    
    data = await state.get_data()
    is_positive = data.get('feedback_choice', True)
    
    # Сохраняем отзыв
    feedback_text = f"{'✅ Положительный: ' if is_positive else '❌ Критика: '}{message.text}"
    success = save_feedback(message.from_user.id, feedback_text, is_positive)
    
    if success:
        await message.answer(
            "🙏 <b>Спасибо за ваш отзыв!</b>\n\n"
            "Ваше мнение очень важно для нас. Мы обязательно учтем ваши пожелания для улучшения нашего сервиса.\n\n"
            "Если у вас есть дополнительные вопросы или предложения, не стесняйтесь написать нам!",
            reply_markup=get_main_keyboard()
        )
        
        # Уведомляем админа
        try:
            await bot.send_message(
                ADMIN_ID,
                f"💬 <b>НОВЫЙ ОТЗЫВ ОТ ПОЛЬЗОВАТЕЛЯ</b>\n\n"
                f"👤 Пользователь: @{message.from_user.username or 'не указан'} (ID: {message.from_user.id})\n"
                f"📝 Отзыв: {feedback_text}\n\n"
                f"⏰ Время: {datetime.now().strftime('%d.%m.%Y %H:%M')}"
            )
        except Exception as e:
            logger.error(f"❌ Ошибка отправки уведомления админу: {e}")
    else:
        await message.answer(
            "❌ Произошла ошибка при сохранении отзыва. Пожалуйста, попробуйте позже.",
            reply_markup=get_main_keyboard()
        )
    
    await state.clear()

# =========== АДМИН: ПРОСМОТР ЗАЯВОК ===========
@dp.message(F.text == "📊 Все заявки")
async def admin_all_requests(message: types.Message):
    """Показываем все заявки админу"""
    if message.from_user.id != ADMIN_ID:
        return
    
    questionnaires, total, total_pages = get_questionnaires(page=1)
    
    if not questionnaires:
        await message.answer("📭 Заявок пока нет.", reply_markup=get_admin_keyboard())
        return
    
    response = f"📊 <b>Все заявки (страница 1/{total_pages}):</b>\n\n"
    
    for q in questionnaires[:5]:  # Показываем только первые 5
        status_icon = "🆕" if q[13] == "new" else "✅" if q[13] == "processed" else "📁"
        feedback_icon = "💬" if q[16] else "💭"
        response += f"""
<b>#{q[0]}</b> - {q[3]} ({q[4]})
👤 ID: {q[1]} | @{q[2]}
📅 {q[14][:10]}
{status_icon} Статус: {q[13]} | {feedback_icon} Отзыв: {'Да' if q[16] else 'Нет'}
──────────────────────
"""
    
    if len(questionnaires) > 5:
        response += f"\n... и еще {len(questionnaires) - 5} заявок"
    
    keyboard = get_pagination_keyboard(1, total_pages)
    if keyboard:
        await message.answer(response, reply_markup=keyboard)
    else:
        await message.answer(response, reply_markup=get_admin_keyboard())

@dp.callback_query(F.data.startswith("page_"))
async def handle_pagination(callback: types.CallbackQuery):
    """Обработка пагинации"""
    if callback.from_user.id != ADMIN_ID:
        return
    
    try:
        page = int(callback.data.split("_")[1])
        questionnaires, total, total_pages = get_questionnaires(page=page)
        
        if not questionnaires:
            await callback.answer("Нет заявок на этой странице")
            return
        
        response = f"📊 <b>Все заявки (страница {page}/{total_pages}):</b>\n\n"
        
        for q in questionnaires:
            status_icon = "🆕" if q[13] == "new" else "✅" if q[13] == "processed" else "📁"
            feedback_icon = "💬" if q[16] else "💭"
            response += f"""
<b>#{q[0]}</b> - {q[3]} ({q[4]})
👤 ID: {q[1]} | @{q[2]}
📅 {q[14][:10]}
{status_icon} Статус: {q[13]} | {feedback_icon} Отзыв: {'Да' if q[16] else 'Нет'}
──────────────────────
"""
        
        keyboard = get_pagination_keyboard(page, total_pages)
        await callback.message.edit_text(response, reply_markup=keyboard)
        await callback.answer()
        
    except Exception as e:
        logger.error(f"Ошибка пагинации: {e}")
        await callback.answer("Ошибка пагинации")

@dp.message(F.text == "🆕 Новые заявки")
async def admin_new_requests(message: types.Message):
    """Показываем только новые заявки"""
    if message.from_user.id != ADMIN_ID:
        return
    
    questionnaires = get_questionnaires("new")[0]
    
    if not questionnaires:
        await message.answer("🆕 Новых заявок нет.", reply_markup=get_admin_keyboard())
        return
    
    response = "🆕 <b>Новые заявки:</b>\n\n"
    
    for q in questionnaires[:10]:  # Ограничиваем 10 заявками
        response += f"""
<b>#{q[0]}</b> - {q[3]}
👤 ID: {q[1]} | @{q[2]}
📞 Телефон: {q[7]}
📧 Email: {q[8]}
📅 {q[14][:16]}
──────────────────────
"""
    
    if len(questionnaires) > 10:
        response += f"\n... и еще {len(questionnaires) - 10} новых заявок"
    
    await message.answer(response, reply_markup=get_admin_keyboard())

# =========== АДМИН: РАССЫЛКА ===========
@dp.message(F.text == "📤 Сделать рассылку")
async def admin_start_mailing(message: types.Message, state: FSMContext):
    """Начало создания рассылки"""
    if message.from_user.id != ADMIN_ID:
        return
    
    # Проверяем, активна ли уже рассылка
    global mailing_data
    if mailing_data['active']:
        await message.answer(
            f"⚠️ <b>Рассылка уже активна!</b>\n\n"
            f"Прогресс: {mailing_data['sent_count']} отправлено\n"
            f"Ошибок: {mailing_data['error_count']}\n"
            f"Время начала: {mailing_data['start_time'].strftime('%H:%M:%S') if mailing_data['start_time'] else 'N/A'}\n\n"
            f"Для остановки рассылки отправьте команду /stop_mailing",
            reply_markup=get_admin_keyboard()
        )
        return
    
    await message.answer(
        "📤 <b>Создание рассылки</b>\n\n"
        "Введите текст сообщения для рассылки всем пользователям:\n\n"
        "<b>Вы можете использовать HTML-разметку:</b>\n"
        "• &lt;b&gt;жирный текст&lt;/b&gt;\n"
        "• &lt;i&gt;курсив&lt;/i&gt;\n"
        "• &lt;u&gt;подчеркнутый&lt;/u&gt;\n"
        "• &lt;a href='ссылка'&gt;текст ссылки&lt;/a&gt;\n\n"
        "Напишите текст рассылки:",
        reply_markup=get_cancel_keyboard()
    )
    await state.set_state(AdminAction.waiting_for_mailing_text)

@dp.message(AdminAction.waiting_for_mailing_text)
async def admin_process_mailing_text(message: types.Message, state: FSMContext):
    """Обработка текста рассылки"""
    if message.text == "❌ Отменить":
        await cancel_action(message, state)
        return
    
    if len(message.text.strip()) < 10:
        await message.answer("❌ Текст рассылки должен содержать минимум 10 символов. Введите снова:")
        return
    
    # Получаем количество пользователей
    users = get_all_users()
    total_users = len(users)
    
    if total_users == 0:
        await message.answer("❌ Нет пользователей для рассылки.", reply_markup=get_admin_keyboard())
        await state.clear()
        return
    
    # Сохраняем текст рассылки
    mailing_text = message.text.strip()
    
    # Подтверждение перед началом
    await message.answer(
        f"✅ <b>Текст рассылки сохранен</b>\n\n"
        f"<b>Количество получателей:</b> {total_users} пользователей\n\n"
        f"<b>Предпросмотр:</b>\n"
        f"{mailing_text[:200]}...\n\n"
        f"<b>Начать рассылку?</b>\n"
        f"Нажмите '✅ Да, начать рассылку' для старта или '❌ Отменить' для отмены.",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="✅ Да, начать рассылку")],
                [KeyboardButton(text="❌ Отменить")]
            ],
            resize_keyboard=True
        )
    )
    
    await state.update_data(mailing_text=mailing_text, total_users=total_users)

@dp.message(F.text == "✅ Да, начать рассылку")
async def admin_confirm_mailing(message: types.Message, state: FSMContext):
    """Подтверждение и запуск рассылки"""
    if message.from_user.id != ADMIN_ID:
        return
    
    data = await state.get_data()
    mailing_text = data.get('mailing_text')
    total_users = data.get('total_users', 0)
    
    if not mailing_text:
        await message.answer("❌ Текст рассылки не найден. Начните заново.", reply_markup=get_admin_keyboard())
        await state.clear()
        return
    
    await message.answer(
        f"🚀 <b>Начинаю рассылку...</b>\n\n"
        f"Получателей: {total_users}\n"
        f"Это может занять некоторое время.\n\n"
        f"<i>Для остановки рассылки отправьте команду /stop_mailing</i>",
        reply_markup=get_admin_keyboard()
    )
    
    # Запускаем рассылку в фоновом режиме
    asyncio.create_task(start_mailing_task(mailing_text, message.from_user.id))
    
    await state.clear()

@dp.message(Command("stop_mailing"))
async def stop_mailing_command(message: types.Message):
    """Остановка активной рассылки"""
    if message.from_user.id != ADMIN_ID:
        return
    
    global mailing_data
    if mailing_data['active']:
        mailing_data['active'] = False
        await message.answer(
            "🛑 <b>Рассылка остановлена!</b>\n\n"
            f"Отправлено сообщений: {mailing_data['sent_count']}\n"
            f"Ошибок: {mailing_data['error_count']}",
            reply_markup=get_admin_keyboard()
        )
    else:
        await message.answer("ℹ️ Нет активной рассылки для остановки.", reply_markup=get_admin_keyboard())

# =========== АДМИН: СТАТИСТИКА ===========
@dp.message(F.text == "📋 Статистика")
async def admin_statistics(message: types.Message):
    """Статистика для админа"""
    if message.from_user.id != ADMIN_ID:
        return
    
    try:
        conn = sqlite3.connect('tenders.db', check_same_thread=False)
        cursor = conn.cursor()
        
        # Общее количество заявок
        cursor.execute("SELECT COUNT(*) FROM questionnaires")
        total = cursor.fetchone()[0]
        
        # Новые заявки
        cursor.execute("SELECT COUNT(*) FROM questionnaires WHERE status = 'new'")
        new = cursor.fetchone()[0]
        
        # Обработанные заявки
        cursor.execute("SELECT COUNT(*) FROM questionnaires WHERE status = 'processed'")
        processed = cursor.fetchone()[0]
        
        # Уникальные пользователи
        cursor.execute("SELECT COUNT(DISTINCT user_id) FROM questionnaires")
        unique_users = cursor.fetchone()[0]
        
        # Отзывы
        cursor.execute("SELECT COUNT(*) FROM questionnaires WHERE feedback_given = 1")
        feedbacks = cursor.fetchone()[0]
        
        # Рассылки
        cursor.execute("SELECT COUNT(*) FROM mailings")
        mailings = cursor.fetchone()[0]
        
        # Статистика по отзывам
        cursor.execute("SELECT COUNT(*) FROM questionnaires WHERE feedback_text LIKE '✅ Положительный:%'")
        positive_feedbacks = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM questionnaires WHERE feedback_text LIKE '❌ Критика:%'")
        negative_feedbacks = cursor.fetchone()[0]
        
        # Эффективность рассылок
        cursor.execute("SELECT SUM(total_users), SUM(successful_sends), SUM(failed_sends) FROM mailings")
        mailing_stats = cursor.fetchone()
        total_mailing_users = mailing_stats[0] or 0
        total_successful = mailing_stats[1] or 0
        total_failed = mailing_stats[2] or 0
        
        conn.close()
        
        # Формируем статистику
        stats_text = f"""
📊 <b>СТАТИСТИКА БОТА</b>

<b>📈 Основные показатели:</b>
• Всего заявок: <b>{total}</b>
• Новые заявки: <b>{new}</b>
• Обработанные: <b>{processed}</b>
• Уникальных пользователей: <b>{unique_users}</b>

<b>⭐ Отзывы:</b>
• Всего отзывов: <b>{feedbacks}</b>
• Положительных: <b>{positive_feedbacks}</b>
• Критических: <b>{negative_feedbacks}</b>
• Процент положительных: <b>{(positive_feedbacks/feedbacks*100 if feedbacks > 0 else 0):.1f}%</b>

<b>📤 Рассылки:</b>
• Всего рассылок: <b>{mailings}</b>
• Всего отправлено: <b>{total_mailing_users}</b>
• Успешно: <b>{total_successful}</b>
• Неудачно: <b>{total_failed}</b>

<b>📅 Дата отчета:</b> {datetime.now().strftime('%d.%m.%Y %H:%M')}
        """
        
        await message.answer(stats_text, reply_markup=get_admin_keyboard())
        
    except Exception as e:
        logger.error(f"Ошибка получения статистики: {e}")
        await message.answer("❌ Ошибка получения статистики", reply_markup=get_admin_keyboard())

# =========== ОБЩЕНИЕ С МЕНЕДЖЕРОМ ===========
@dp.message(F.text == "📨 Написать менеджеру")
async def write_to_manager(message: types.Message):
    """Пользователь хочет написать менеджеру"""
    if message.from_user.id == ADMIN_ID:
        await message.answer("Вы администратор, вы не можете написать самому себе.", reply_markup=get_admin_keyboard())
        return
    
    try:
        # Отправляем уведомление админу
        await bot.send_message(
            ADMIN_ID,
            f"📨 <b>Пользователь хочет написать вам сообщение</b>\n\n"
            f"👤 Пользователь: @{message.from_user.username or 'не указан'} (ID: {message.from_user.id})\n\n"
            f"<i>Пользователь нажал кнопку 'Написать менеджеру' и ожидает вашего ответа.</i>"
        )
        
        await message.answer(
            "✅ Ваше желание написать менеджеру зафиксировано. Менеджер свяжется с вами в ближайшее время.",
            reply_markup=get_main_keyboard()
        )
    except Exception as e:
        await message.answer(
            "❌ Не удалось отправить запрос. Попробуйте позже.",
            reply_markup=get_main_keyboard()
        )

# =========== ИНФОРМАЦИЯ О КОМПАНИИ ===========
@dp.message(F.text == "ℹ️ О компании")
async def about_company(message: types.Message):
    """Информация о компании"""
    keyboard = get_admin_keyboard() if message.from_user.id == ADMIN_ID else get_main_keyboard()
    await message.answer(
        "🏢 <b>О компании ТРИТИКА</b>\n\n"
        "<b>Мы помогаем бизнесу находить выгодные тендеры</b>\n\n"
        "<b>Наши услуги:</b>\n"
        "• Поиск тендеров по вашим параметрам\n"
        "• Персональная выгрузка в течение часа\n"
        "• Консультации по участию в торгах\n"
        "• Сопровождение сделок\n\n"
        "<b>Контакты:</b>\n"
        "📞 Телефон: +7 (904) 653-69-87\n"
        "📧 Email: info@tritika.ru\n"
        "🌐 Сайт: https://tritika.ru/\n\n"
        "<b>График работы:</b>\n"
        "Пн-Пт: 9:00-18:00\n"
        "Сб: 10:00-15:00\n"
        "Вс: выходной",
        reply_markup=keyboard
    )

# =========== ОТМЕНА ===========
@dp.message(F.text == "❌ Отменить")
async def cancel_action(message: types.Message, state: FSMContext):
    """Отмена текущего действия"""
    current_state = await state.get_state()
    if current_state is None:
        # Если состояния нет, показываем главное меню
        keyboard = get_admin_keyboard() if message.from_user.id == ADMIN_ID else get_main_keyboard()
        await message.answer("Главное меню:", reply_markup=keyboard)
        return
    
    await message.answer(
        "❌ Действие отменено.",
        reply_markup=get_admin_keyboard() if message.from_user.id == ADMIN_ID else get_main_keyboard()
    )
    await state.clear()

# =========== ОБРАБОТКА ВСЕХ ОСТАЛЬНЫХ СООБЩЕНИЙ ===========
@dp.message()
async def handle_all_messages(message: types.Message):
    """Обработка всех остальных сообщений"""
    if message.from_user.id == ADMIN_ID:
        await message.answer(
            "Используйте кнопки ниже:\n"
            "📊 Все заявки - просмотр всех анкет\n"
            "🆕 Новые заявки - только новые заявки\n"
            "📤 Сделать рассылку - массовая рассылка\n"
            "📋 Статистика - подробная статистика",
            reply_markup=get_admin_keyboard()
        )
    else:
        await message.answer(
            "Используйте кнопки ниже:\n"
            "📝 Заполнить анкету - поиск тендеров\n"
            "📨 Написать менеджеру - задать вопрос\n"
            "💬 Оставить отзыв - поделиться мнением\n"
            "ℹ️ О компании - информация",
            reply_markup=get_main_keyboard()
        )

# =========== ПРОСТОЙ HTTP СЕРВЕР ДЛЯ HEALTHCHECK ===========
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/' or self.path == '/health':
            self.send_response(200)
            self.send_header('Content-type', 'text/plain')
            self.end_headers()
            self.wfile.write(b'OK')
        else:
            self.send_response(404)
            self.end_headers()
    
    def log_message(self, format, *args):
        pass

def run_healthcheck_server():
    """Запуск простого HTTP сервера для healthcheck"""
    port = int(os.environ.get('PORT', 8080))
    server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
    logger.info(f"✅ Healthcheck сервер запущен на порту {port}")
    server.serve_forever()

# =========== ЗАПУСК БОТА ===========
async def main():
    """Основная функция запуска приложения"""
    logger.info("🚀 Запуск приложения ТРИТИКА...")
    
    # ЗАПУСКАЕМ HTTP СЕРВЕР В ОТДЕЛЬНОМ ПОТОКЕ
    http_thread = threading.Thread(target=run_healthcheck_server, daemon=True)
    http_thread.start()
    
    logger.info(f"✅ Healthcheck сервер запущен в отдельном потоке")
    
    # Даем время серверу запуститься
    time.sleep(2)
    
    # Запускаем Telegram бота
    try:
        # Проверяем соединение с ботом
        bot_info = await bot.get_me()
        logger.info(f"✅ Бот запущен: @{bot_info.username}")
        
        # Запускаем бота
        await dp.start_polling(bot, skip_updates=True)
    except Exception as e:
        logger.error(f"❌ Ошибка запуска бота: {e}", exc_info=True)
        raise

# =========== ЗАПУСК ПРИЛОЖЕНИЯ ===========
if __name__ == "__main__":
    asyncio.run(main())
