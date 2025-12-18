import os
import sqlite3
import logging
import asyncio
import shutil
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
from aiohttp import web
import aiohttp

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
    exit(1)

if not ADMIN_ID:
    logger.error("❌ ADMIN_ID не установлен! Добавьте в Secrets.")
    exit(1)

ADMIN_ID = int(ADMIN_ID)

# Инициализация бота
bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

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
    if len(inn) not in (10, 12) or not inn.isdigit():
        return False
    
    if len(inn) == 10:
        # Проверка контрольной цифры для 10-значного ИНН
        weights = [2, 4, 10, 3, 5, 9, 4, 6, 8]
        check_sum = sum(int(inn[i]) * weights[i] for i in range(9)) % 11
        check_digit = check_sum % 10 if check_sum < 10 else 0
        return int(inn[9]) == check_digit
    return True

# Декоратор для обработки ошибок в состояниях
def catch_state_errors(func):
    """Декоратор для обработки ошибок в состояниях"""
    async def wrapper(message: types.Message, state: FSMContext, *args, **kwargs):
        try:
            return await func(message, state, *args, **kwargs)
        except Exception as e:
            logger.error(f"Ошибка в состоянии: {e}")
            await state.clear()
            keyboard = get_admin_keyboard() if message.from_user.id == ADMIN_ID else get_main_keyboard()
            await message.answer(
                "⚠️ Произошла ошибка. Возврат в главное меню.",
                reply_markup=keyboard
            )
    return wrapper

# Резервное копирование базы данных
def backup_database():
    """Создание резервной копии базы данных"""
    try:
        os.makedirs("backups", exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_name = f"backups/tenders_backup_{timestamp}.db"
        
        if os.path.exists("tenders.db"):
            shutil.copy2("tenders.db", backup_name)
            logger.info(f"✅ Резервная копия создана: {backup_name}")
            
            # Удаляем старые бекапы (оставляем последние 7)
            backups = sorted([f for f in os.listdir("backups") if f.startswith("tenders_backup")])
            if len(backups) > 7:
                for old_backup in backups[:-7]:
                    os.remove(f"backups/{old_backup}")
    except Exception as e:
        logger.error(f"❌ Ошибка создания резервной копии: {e}")

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
        admin_comment TEXT
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
    
    conn.commit()
    conn.close()
    logger.info("✅ База данных инициализирована")

# Инициализируем БД
init_db()
backup_database()

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
    waiting_for_user_id = State()
    waiting_for_file = State()
    waiting_for_message = State()
    waiting_for_file_with_id = State()

# =========== КЛАВИАТУРЫ ===========
def get_main_keyboard():
    """Главное меню для пользователей"""
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📝 Заполнить анкету")],
            [KeyboardButton(text="📨 Написать менеджеру")],
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
            [KeyboardButton(text="📁 Отправить файл клиенту"), KeyboardButton(text="💬 Написать клиенту")],
            [KeyboardButton(text="📋 Статистика"), KeyboardButton(text="🏠 Главное меню")],
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
        
        return questionnaire_id
    except Exception as e:
        logger.error(f"Ошибка сохранения в БД: {e}")
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

def update_questionnaire_status(questionnaire_id, status):
    """Обновляем статус анкеты"""
    try:
        conn = sqlite3.connect('tenders.db', check_same_thread=False)
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE questionnaires SET status = ? WHERE id = ?",
            (status, questionnaire_id)
        )
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logger.error(f"Ошибка обновления статуса: {e}")
        return False

def save_message_to_db(from_id, to_id, message_text):
    """Сохраняем сообщение в базу"""
    try:
        conn = sqlite3.connect('tenders.db', check_same_thread=False)
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

# =========== ОБРАБОТЧИКИ КОМАНД ===========
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    """Обработка команды /start"""
    if message.from_user.id == ADMIN_ID:
        await message.answer(
            "👑 <b>Панель администратора</b>\n\n"
            "Добро пожаловать в админ-панель!\n\n"
            "Доступные функции:\n"
            "• 📊 Все заявки - просмотр всех анкет\n"
            "• 🆕 Новые заявки - только новые заявки\n"
            "• 📁 Отправить файл - отправить выгрузку клиенту\n"
            "• 💬 Написать клиенту - отправить сообщение\n"
            "• 📋 Статистика - статистика работы\n\n"
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
@dp.message(AntiFlood(2), F.text == "📝 Заполнить анкету")
@catch_state_errors
async def start_questionnaire(message: types.Message, state: FSMContext):
    """Начало заполнения анкеты"""
    # Проверяем, не заполняется ли уже анкета
    current_state = await state.get_state()
    if current_state:
        await message.answer("Вы уже заполняете анкету. Продолжайте или отмените.")
        return
    
    await message.answer(
        "📋 <b>Начинаем заполнение анкеты!</b>\n\n"
        "Заполнение займет 2-3 минуты.\n\n"
        "<b>Введите ваше ФИО:</b>",
        reply_markup=get_cancel_keyboard()
    )
    await state.set_state(Questionnaire.waiting_for_name)

@dp.message(Questionnaire.waiting_for_name)
@catch_state_errors
async def process_name(message: types.Message, state: FSMContext):
    if message.text == "❌ Отменить":
        await cancel_action(message, state)
        return
    await state.update_data(full_name=message.text)
    await message.answer(
        "✅ <b>ФИО сохранено</b>\n\n"
        "<b>Введите название вашей компании:</b>",
        reply_markup=get_cancel_keyboard()
    )
    await state.set_state(Questionnaire.waiting_for_company)

@dp.message(Questionnaire.waiting_for_company)
@catch_state_errors
async def process_company(message: types.Message, state: FSMContext):
    if message.text == "❌ Отменить":
        await cancel_action(message, state)
        return
    await state.update_data(company_name=message.text)
    await message.answer(
        "✅ <b>Название компании сохранено</b>\n\n"
        "<b>Введите ИНН компании:</b>\n"
        "<i>10 или 12 цифр</i>",
        reply_markup=get_cancel_keyboard()
    )
    await state.set_state(Questionnaire.waiting_for_inn)

@dp.message(Questionnaire.waiting_for_inn)
@catch_state_errors
async def process_inn(message: types.Message, state: FSMContext):
    if message.text == "❌ Отменить":
        await cancel_action(message, state)
        return
    
    inn = message.text.strip()
    if not validate_inn(inn):
        await message.answer("❌ Неверный ИНН. ИНН должен содержать 10 или 12 цифр. Введите снова:")
        return
    
    await state.update_data(inn=inn)
    await message.answer(
        "✅ <b>ИНН сохранен</b>\n\n"
        "<b>Введите контактное лицо для связи:</b>\n"
        "<i>Кто будет общаться по тендерам</i>",
        reply_markup=get_cancel_keyboard()
    )
    await state.set_state(Questionnaire.waiting_for_contact)

@dp.message(Questionnaire.waiting_for_contact)
@catch_state_errors
async def process_contact(message: types.Message, state: FSMContext):
    if message.text == "❌ Отменить":
        await cancel_action(message, state)
        return
    await state.update_data(contact_person=message.text)
    await message.answer(
        "✅ <b>Контактное лицо сохранено</b>\n\n"
        "<b>Введите телефон для связи:</b>\n"
        "<i>Например: +7 999 123-45-67</i>",
        reply_markup=get_cancel_keyboard()
    )
    await state.set_state(Questionnaire.waiting_for_phone)

@dp.message(Questionnaire.waiting_for_phone)
@catch_state_errors
async def process_phone(message: types.Message, state: FSMContext):
    if message.text == "❌ Отменить":
        await cancel_action(message, state)
        return
    await state.update_data(phone=message.text)
    await message.answer(
        "✅ <b>Телефон сохранен</b>\n\n"
        "<b>Введите email:</b>\n"
        "<i>На этот адрес придет выгрузка</i>",
        reply_markup=get_cancel_keyboard()
    )
    await state.set_state(Questionnaire.waiting_for_email)

@dp.message(Questionnaire.waiting_for_email)
@catch_state_errors
async def process_email(message: types.Message, state: FSMContext):
    if message.text == "❌ Отменить":
        await cancel_action(message, state)
        return
    
    email = message.text.strip()
    if "@" not in email or "." not in email:
        await message.answer("❌ Введите корректный email адрес:")
        return
    
    await state.update_data(email=email)
    await message.answer(
        "✅ <b>Email сохранен</b>\n\n"
        "<b>Введите сферу деятельности:</b>\n"
        "<i>Например: Строительство, ОКВЭД 41.20</i>",
        reply_markup=get_cancel_keyboard()
    )
    await state.set_state(Questionnaire.waiting_for_activity)

@dp.message(Questionnaire.waiting_for_activity)
@catch_state_errors
async def process_activity(message: types.Message, state: FSMContext):
    if message.text == "❌ Отменить":
        await cancel_action(message, state)
        return
    await state.update_data(activity_sphere=message.text)
    await message.answer(
        "✅ <b>Сфера деятельности сохранена</b>\n\n"
        "<b>Введите ключевые слова для поиска:</b>\n"
        "<i>Чем занимается ваша компания (через запятую)</i>\n"
        "<i>Пример: строительство, ремонт, отделка</i>",
        reply_markup=get_cancel_keyboard()
    )
    await state.set_state(Questionnaire.waiting_for_industry)

@dp.message(Questionnaire.waiting_for_industry)
@catch_state_errors
async def process_industry(message: types.Message, state: FSMContext):
    if message.text == "❌ Отменить":
        await cancel_action(message, state)
        return
    await state.update_data(industry=message.text)
    await message.answer(
        "✅ <b>Ключевые слова сохранены</b>\n\n"
        "<b>Введите желаемый бюджет контрактов:</b>\n"
        "<i>Пример: от 100 000 до 500 000 рублей</i>",
        reply_markup=get_cancel_keyboard()
    )
    await state.set_state(Questionnaire.waiting_for_amount)

@dp.message(Questionnaire.waiting_for_amount)
@catch_state_errors
async def process_amount(message: types.Message, state: FSMContext):
    if message.text == "❌ Отменить":
        await cancel_action(message, state)
        return
    await state.update_data(contract_amount=message.text)
    await message.answer(
        "✅ <b>Бюджет сохранен</b>\n\n"
        "<b>Введите регионы работы:</b>\n"
        "<i>В каких регионах готовы работать (через запятую)</i>\n"
        "<i>Пример: Москва, Московская область, Владимир</i>",
        reply_markup=get_cancel_keyboard()
    )
    await state.set_state(Questionnaire.waiting_for_regions)

@dp.message(Questionnaire.waiting_for_regions)
@catch_state_errors
async def process_regions(message: types.Message, state: FSMContext):
    """Завершение заполнения анкеты"""
    if message.text == "❌ Отменить":
        await cancel_action(message, state)
        return
    
    user_data = await state.get_data()
    
    # Добавляем информацию о пользователе
    user_data['user_id'] = message.from_user.id
    user_data['username'] = message.from_user.username or "Не указан"
    user_data['regions'] = message.text
    
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
        
        <i>Для отправки файла используйте кнопку "📁 Отправить файл клиенту" или команду /send_file_{user_data['user_id']}</i>
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
    
    for q in questionnaires:
        status_icon = "🆕" if q[13] == "new" else "✅" if q[13] == "processed" else "📁"
        response += f"""
        <b>#{q[0]}</b> - {q[3]} ({q[4]})
        👤 ID: {q[1]} | @{q[2]}
        📅 {q[14][:10]}
        {status_icon} Статус: {q[13]}
        ──────────────────────
        """
    
    keyboard = get_pagination_keyboard(1, total_pages)
    await message.answer(response, reply_markup=keyboard)

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
            response += f"""
            <b>#{q[0]}</b> - {q[3]} ({q[4]})
            👤 ID: {q[1]} | @{q[2]}
            📅 {q[14][:10]}
            {status_icon} Статус: {q[13]}
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
    
    for q in questionnaires:
        response += f"""
        <b>#{q[0]}</b> - {q[3]}
        👤 ID: {q[1]} | @{q[2]}
        📞 Телефон: {q[7]}
        📧 Email: {q[8]}
        📅 {q[14][:16]}
        
        Для отправки файла: /send_file_{q[1]}
        ──────────────────────
        """
    
    await message.answer(response, reply_markup=get_admin_keyboard())

# =========== АДМИН: ОТПРАВКА ФАЙЛА ===========
@dp.message(F.text == "📁 Отправить файл клиенту")
async def admin_send_file_start(message: types.Message, state: FSMContext):
    """Начало отправки файла"""
    if message.from_user.id != ADMIN_ID:
        return
    
    await message.answer(
        "📁 <b>Отправка файла клиенту</b>\n\n"
        "Введите ID пользователя для отправки файла:",
        reply_markup=get_cancel_keyboard()
    )
    await state.set_state(AdminAction.waiting_for_user_id)

@dp.message(AdminAction.waiting_for_user_id)
@catch_state_errors
async def admin_get_file_user_id(message: types.Message, state: FSMContext):
    """Получаем ID пользователя для отправки файла"""
    if message.text == "❌ Отменить":
        await cancel_action(message, state)
        return
    
    try:
        user_id = int(message.text)
        await state.update_data(target_user_id=user_id)
        
        # Получаем информацию о пользователе
        questionnaire = get_questionnaire_by_user_id(user_id)
        
        if questionnaire:
            await message.answer(
                f"✅ Найден пользователь:\n"
                f"👤 ФИО: {questionnaire[3]}\n"
                f"🏢 Компания: {questionnaire[4]}\n\n"
                f"Теперь отправьте файл (PDF, Word, Excel или архив):",
                reply_markup=get_cancel_keyboard()
            )
        else:
            await message.answer(
                f"👤 Пользователь найден (ID: {user_id})\n\n"
                f"Теперь отправьте файл (PDF, Word, Excel или архив):",
                reply_markup=get_cancel_keyboard()
            )
        
        await state.set_state(AdminAction.waiting_for_file)
    except ValueError:
        await message.answer("❌ Введите корректный ID (число):")

@dp.message(AdminAction.waiting_for_file)
@catch_state_errors
async def handle_waiting_for_file(message: types.Message, state: FSMContext):
    """Обработка файла от админа"""
    if message.text == "❌ Отменить":
        await cancel_action(message, state)
        return
    
    if not message.document and not message.photo:
        await message.answer("Пожалуйста, отправьте файл (документ или фото) или нажмите '❌ Отменить'")
        return
    
    await admin_send_file_to_user(message, state)

async def admin_send_file_to_user(message: types.Message, state: FSMContext):
    """Админ отправляет файл пользователю"""
    data = await state.get_data()
    user_id = data.get('target_user_id')
    
    if not user_id:
        await message.answer("❌ Не указан ID пользователя. Начните заново.")
        await state.clear()
        return
    
    try:
        # Отправляем файл пользователю
        if message.document:
            await bot.send_document(
                user_id,
                document=message.document.file_id,
                caption=f"""
                ✅ <b>Ваша персональная подборка готова!</b>

                Во вложении вы найдете файл с детальной выгрузкой тендеров, соответствующих вашим критериям.

                📎 <b>Файл: {message.document.file_name}</b>
                👉 Если возникнут вопросы по конкретным тендерам — обращайтесь!

                С уважением, команда ТРИТИКА.
                https://tritika.ru/
                """
            )
            file_name = message.document.file_name
        elif message.photo:
            await bot.send_photo(
                user_id,
                photo=message.photo[-1].file_id,
                caption=f"""
                ✅ <b>Ваша персональная подборка готова!</b>

                В приложении вы найдете выгрузку тендеров, соответствующих вашим критериям.

                👉 Если возникнут вопросы по конкретным тендерам — обращайтесь!

                С уважением, команда ТРИТИКА.
                https://tritika.ru/
                """
            )
            file_name = "photo.jpg"
        
        # Обновляем статус анкеты
        questionnaire = get_questionnaire_by_user_id(user_id)
        if questionnaire:
            update_questionnaire_status(questionnaire[0], "processed")
        
        # Сохраняем информацию о отправке
        try:
            conn = sqlite3.connect('tenders.db', check_same_thread=False)
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO sent_files (questionnaire_id, file_name, sent_by, sent_at) VALUES (?, ?, ?, ?)",
                (questionnaire[0] if questionnaire else None, file_name, message.from_user.id, 
                 datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            )
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"Ошибка сохранения информации о файле: {e}")
        
        # Отправляем текстовую выгрузку
        if questionnaire:
            tender_export = f"""
📄 <b>ВЫГРУЗКА ТЕНДЕРОВ | ТРИТИКА</b>

*Сформировано для вас на основе запроса.
————————————————
👤 <b>ДАННЫЕ КЛИЕНТА:</b>
• Запрос от: {questionnaire[3]}
• Сфера: {questionnaire[9]}
• Регион поиска: {questionnaire[12]}
• Ключевые слова: {questionnaire[10]}
• Время запроса: {questionnaire[14]}
————————————————
📊 <b>РЕЗУЛЬТАТЫ ПОИСКА:</b>
Найдено потенциально подходящих торгов: 5+
————————————————
💡 <b>ВАЖНО:</b>
• Данная подборка сформирована автоматически и носит информационный характер.
• Внимательно изучайте документацию перед участием.
• Актуальные условия могут меняться, проверяйте информацию на площадках заказчиков.
————————————————
❓ <b>ВОПРОСЫ?</b>
Мы всегда на связи для консультации.

С уважением, команда ТРИТИКА.
https://tritika.ru/
            """
            await bot.send_message(user_id, tender_export)
        
        # Подтверждение админу
        await message.answer(
            f"✅ Файл успешно отправлен пользователю ID: {user_id}",
            reply_markup=get_admin_keyboard()
        )
        
    except Exception as e:
        logger.error(f"Ошибка отправки файла: {e}")
        await message.answer(
            f"❌ Ошибка отправки файла: {str(e)}",
            reply_markup=get_admin_keyboard()
        )
    
    await state.clear()

@dp.message(Command("send_file"))
async def quick_send_file_command(message: types.Message, state: FSMContext):
    """Быстрая команда для отправки файла"""
    if message.from_user.id != ADMIN_ID:
        return
    
    args = message.text.split()
    if len(args) < 2:
        await message.answer("Использование: /send_file ID_пользователя")
        return
    
    try:
        user_id = int(args[1])
        
        # Проверяем, есть ли такой пользователь в базе
        questionnaire = get_questionnaire_by_user_id(user_id)
        if questionnaire:
            await state.update_data(target_user_id=user_id)
            await message.answer(
                f"✅ Найден пользователь:\n"
                f"👤 ФИО: {questionnaire[3]}\n"
                f"🏢 Компания: {questionnaire[4]}\n\n"
                f"Теперь отправьте файл (PDF, Word, Excel или архив):",
                reply_markup=get_cancel_keyboard()
            )
            await state.set_state(AdminAction.waiting_for_file)
        else:
            await message.answer(
                f"❌ Пользователь с ID {user_id} не найден в базе. "
                f"Введите ID заново или отмените.",
                reply_markup=get_cancel_keyboard()
            )
            await state.set_state(AdminAction.waiting_for_user_id)
    except ValueError:
        await message.answer("❌ Некорректный ID. Введите число.")

# =========== ОБЩЕНИЕ МЕЖДУ КЛИЕНТОМ И АДМИНОМ ===========
@dp.message(F.text == "📨 Написать менеджеру")
@catch_state_errors
async def start_message_to_admin(message: types.Message, state: FSMContext):
    """Пользователь начинает диалог с админом"""
    await message.answer(
        "📨 <b>Написать менеджеру</b>\n\n"
        "Введите ваше сообщение для менеджера:\n"
        "<i>Опишите вопрос или ситуацию подробно</i>",
        reply_markup=get_cancel_keyboard()
    )
    await state.set_state(AdminAction.waiting_for_message)
    await state.update_data(is_user_to_admin=True)

@dp.message(F.text == "💬 Написать клиенту")
async def admin_start_message_to_user(message: types.Message, state: FSMContext):
    """Админ начинает диалог с клиентом"""
    if message.from_user.id != ADMIN_ID:
        return
    
    await message.answer(
        "💬 <b>Написать клиенту</b>\n\n"
        "Введите ID пользователя для отправки сообщения:",
        reply_markup=get_cancel_keyboard()
    )
    await state.set_state(AdminAction.waiting_for_user_id)
    await state.update_data(is_admin_to_user=True)

@dp.message(AdminAction.waiting_for_message)
@catch_state_errors
async def send_message_between_users(message: types.Message, state: FSMContext):
    """Отправка сообщения между пользователем и админом"""
    if message.text == "❌ Отменить":
        await cancel_action(message, state)
        return
    
    data = await state.get_data()
    message_text = message.text
    
    if data.get('is_user_to_admin'):
        # Пользователь пишет админу
        save_message_to_db(message.from_user.id, ADMIN_ID, message_text)
        
        # Отправляем админу
        try:
            await bot.send_message(
                ADMIN_ID,
                f"📨 <b>Сообщение от пользователя ID: {message.from_user.id}</b>\n\n"
                f"👤 Пользователь: @{message.from_user.username or 'не указан'}\n"
                f"💬 Сообщение: {message_text}\n\n"
                f"<i>Для ответа используйте команду: /reply {message.from_user.id} текст</i>"
            )
            await message.answer(
                "✅ Ваше сообщение отправлено менеджеру. Ответ придет в этот чат.",
                reply_markup=get_main_keyboard()
            )
        except Exception as e:
            await message.answer(
                "❌ Не удалось отправить сообщение. Попробуйте позже.",
                reply_markup=get_main_keyboard()
            )
    
    elif data.get('target_user_id'):
        # Админ пишет пользователю
        user_id = data['target_user_id']
        save_message_to_db(ADMIN_ID, user_id, message_text)
        
        try:
            await bot.send_message(
                user_id,
                f"📨 <b>Сообщение от менеджера:</b>\n\n{message_text}\n\n"
                f"<i>Для ответа нажмите '📨 Написать менеджеру'</i>"
            )
            await message.answer(
                f"✅ Сообщение отправлено пользователю ID: {user_id}",
                reply_markup=get_admin_keyboard()
            )
        except Exception as e:
            await message.answer(
                f"❌ Не удалось отправить сообщение: {str(e)}",
                reply_markup=get_admin_keyboard()
            )
    
    await state.clear()

@dp.message(Command("reply"))
async def quick_reply_command(message: types.Message):
    """Быстрый ответ админа пользователю"""
    if message.from_user.id != ADMIN_ID:
        return
    
    args = message.text.split(maxsplit=2)
    if len(args) < 3:
        await message.answer("Использование: /reply ID_пользователя текст_сообщения")
        return
    
    try:
        user_id = int(args[1])
        reply_text = args[2]
        
        save_message_to_db(ADMIN_ID, user_id, reply_text)
        
        await bot.send_message(
            user_id,
            f"📨 <b>Сообщение от менеджера:</b>\n\n{reply_text}\n\n"
            f"<i>Для ответа нажмите '📨 Написать менеджеру'</i>"
        )
        
        await message.answer(f"✅ Ответ отправлен пользователю ID: {user_id}")
        
    except Exception as e:
        await message.answer(f"❌ Ошибка: {str(e)}")

# =========== ДОПОЛНИТЕЛЬНЫЕ КОМАНДЫ ===========
@dp.message(F.text == "ℹ️ О компании")
async def about_company(message: types.Message):
    """Информация о компании"""
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
        reply_markup=get_main_keyboard()
    )

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
        
        # Отправленные файлы
        cursor.execute("SELECT COUNT(*) FROM sent_files")
        sent_files = cursor.fetchone()[0]
        
        # Сообщения
        cursor.execute("SELECT COUNT(*) FROM messages")
        messages = cursor.fetchone()[0]
        
        # Последняя активность
        cursor.execute("SELECT MAX(created_at) FROM questionnaires")
        last_activity = cursor.fetchone()[0] or "Нет данных"
        
        conn.close()
        
        stats_text = f"""
📊 <b>Статистика бота</b>

<b>Заявки:</b>
• Всего заявок: {total}
• Новые заявки: {new}
• Обработанные: {processed}

<b>Активность:</b>
• Отправлено файлов: {sent_files}
• Сообщений в чатах: {messages}

<b>Последняя активность:</b>
{last_activity}

<b>Дата:</b> {datetime.now().strftime('%d.%m.%Y %H:%M')}
        """
        
        await message.answer(stats_text, reply_markup=get_admin_keyboard())
        
    except Exception as e:
        logger.error(f"Ошибка получения статистики: {e}")
        await message.answer("❌ Ошибка получения статистики", reply_markup=get_admin_keyboard())

@dp.message(F.text == "❌ Отменить")
@catch_state_errors
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

# =========== ОБРАБОТКА ВСЕХ СООБЩЕНИЙ ===========
@dp.message()
async def handle_all_messages(message: types.Message):
    """Обработка всех остальных сообщений"""
    if message.from_user.id == ADMIN_ID:
        await message.answer(
            "Используйте кнопки ниже или команды:\n"
            "/start - главное меню\n"
            "/send_file ID - отправить файл клиенту\n"
            "/reply ID текст - ответить клиенту",
            reply_markup=get_admin_keyboard()
        )
    else:
        await message.answer(
            "Используйте кнопки ниже:\n"
            "📝 Заполнить анкету - поиск тендеров\n"
            "📨 Написать менеджеру - задать вопрос\n"
            "ℹ️ О компании - информация",
            reply_markup=get_main_keyboard()
        )

# =========== HTTP SERVER FOR HEALTHCHECK ===========
async def healthcheck(request):
    """Обработчик healthcheck для Replit"""
    return web.Response(text="OK")

async def start_http_server():
    """Запуск HTTP сервера для healthcheck"""
    app = web.Application()
    app.router.add_get('/', healthcheck)
    app.router.add_get('/health', healthcheck)
    
    port = int(os.environ.get('PORT', 8080))
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    logger.info(f"✅ HTTP сервер запущен на порту {port}")
    return runner

# =========== ЗАПУСК БОТА ===========
async def main():
    """Запуск бота"""
    logger.info("🚀 Запуск бота ТРИТИКА на Replit...")
    
    try:
        # Запускаем HTTP сервер для healthcheck
        http_runner = await start_http_server()
        
        # Проверяем соединение с ботом
        bot_info = await bot.get_me()
        logger.info(f"✅ Бот запущен: @{bot_info.username}")
        
        # Запускаем бота
        await dp.start_polling(bot)
        
        # Останавливаем HTTP сервер при завершении
        await http_runner.cleanup()
        
    except Exception as e:
        logger.error(f"❌ Критическая ошибка: {e}")
        raise

if __name__ == "__main__":
    asyncio.run(main())
