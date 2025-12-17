import os
import logging
import asyncio
from datetime import datetime
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from flask import Flask, request
import threading

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Получаем токен из переменных окружения Replit
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = os.getenv("ADMIN_ID")

if not BOT_TOKEN:
    logger.error("❌ BOT_TOKEN не установлен!")
    # В Replit можно ввести токен вручную при первом запуске
    BOT_TOKEN = input("Введите BOT_TOKEN: ")

# Инициализация бота с настройками по умолчанию
bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# Определяем состояния для анкеты
class Questionnaire(StatesGroup):
    company_name = State()
    inn = State()
    contact_person = State()
    phone = State()
    email = State()
    activity_sphere = State()
    industry = State()
    contract_amount = State()
    regions = State()

# =========== КЛАВИАТУРЫ ===========
def get_main_keyboard():
    """Главное меню"""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📝 Заполнить анкету")],
            [KeyboardButton(text="📋 Мои данные"), KeyboardButton(text="ℹ️ Помощь")],
            [KeyboardButton(text="📞 Контакты")]
        ],
        resize_keyboard=True,
        input_field_placeholder="Выберите действие"
    )

def get_cancel_keyboard():
    """Клавиатура для отмены"""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🚫 Отменить")]
        ],
        resize_keyboard=True
    )

# =========== КОМАНДЫ БОТА ===========
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    """Начало работы с ботом"""
    await message.answer(
        "🏢 <b>Добро пожаловать в бот для поиска тендеров</b>\n\n"
        "Я помогу вам найти подходящие тендеры для вашего бизнеса.\n\n"
        "<b>Основные функции:</b>\n"
        "• 📝 Заполнить анкету для поиска\n"
        "• 📋 Просмотреть свои данные\n"
        "• 📞 Контакты компании\n\n"
        "Выберите действие ниже 👇",
        reply_markup=get_main_keyboard()
    )

@dp.message(F.text == "📝 Заполнить анкету")
async def start_questionnaire(message: types.Message, state: FSMContext):
    """Начало заполнения анкеты"""
    current_state = await state.get_state()
    if current_state:
        await message.answer("Вы уже заполняете анкету. Закончите или отмените её.")
        return
    
    await message.answer(
        "📋 <b>Начинаем заполнение анкеты</b>\n\n"
        "Ответьте на 9 вопросов для поиска тендеров.\n"
        "Для отмены нажмите кнопку ниже.\n\n"
        "<b>Введите название вашей компании:</b>",
        reply_markup=get_cancel_keyboard()
    )
    await state.set_state(Questionnaire.company_name)

@dp.message(Questionnaire.company_name)
async def process_company_name(message: types.Message, state: FSMContext):
    await state.update_data(company_name=message.text)
    await message.answer(
        "✅ <b>Название компании сохранено</b>\n\n"
        "<b>Введите ИНН компании (10 или 12 цифр):</b>\n"
        "<i>Пример: 1234567890</i>",
        reply_markup=get_cancel_keyboard()
    )
    await state.set_state(Questionnaire.inn)

@dp.message(Questionnaire.inn)
async def process_inn(message: types.Message, state: FSMContext):
    inn = message.text.strip()
    if not inn.isdigit() or len(inn) not in [10, 12]:
        await message.answer("❌ ИНН должен содержать 10 или 12 цифр. Введите снова:")
        return
    await state.update_data(inn=inn)
    await message.answer(
        "✅ <b>ИНН сохранен</b>\n\n"
        "<b>Введите ФИО контактного лица:</b>\n"
        "<i>Пример: Иванов Иван Иванович</i>",
        reply_markup=get_cancel_keyboard()
    )
    await state.set_state(Questionnaire.contact_person)

@dp.message(Questionnaire.contact_person)
async def process_contact_person(message: types.Message, state: FSMContext):
    await state.update_data(contact_person=message.text)
    await message.answer(
        "✅ <b>Контактное лицо сохранено</b>\n\n"
        "<b>Введите номер телефона:</b>\n"
        "<i>Пример: +7 999 123-45-67</i>",
        reply_markup=get_cancel_keyboard()
    )
    await state.set_state(Questionnaire.phone)

@dp.message(Questionnaire.phone)
async def process_phone(message: types.Message, state: FSMContext):
    await state.update_data(phone=message.text)
    await message.answer(
        "✅ <b>Телефон сохранен</b>\n\n"
        "<b>Введите email:</b>\n"
        "<i>Пример: info@company.ru</i>",
        reply_markup=get_cancel_keyboard()
    )
    await state.set_state(Questionnaire.email)

@dp.message(Questionnaire.email)
async def process_email(message: types.Message, state: FSMContext):
    email = message.text.strip()
    if "@" not in email:
        await message.answer("❌ Введите корректный email:")
        return
    await state.update_data(email=email)
    await message.answer(
        "✅ <b>Email сохранен</b>\n\n"
        "<b>Введите сферу деятельности:</b>\n"
        "<i>Пример: Строительство, ОКВЭД 41.20</i>",
        reply_markup=get_cancel_keyboard()
    )
    await state.set_state(Questionnaire.activity_sphere)

@dp.message(Questionnaire.activity_sphere)
async def process_activity_sphere(message: types.Message, state: FSMContext):
    await state.update_data(activity_sphere=message.text)
    await message.answer(
        "✅ <b>Сфера деятельности сохранена</b>\n\n"
        "<b>Введите ключевые слова/отрасль:</b>\n"
        "<i>Пример: строительство, ремонт, монтаж</i>",
        reply_markup=get_cancel_keyboard()
    )
    await state.set_state(Questionnaire.industry)

@dp.message(Questionnaire.industry)
async def process_industry(message: types.Message, state: FSMContext):
    await state.update_data(industry=message.text)
    await message.answer(
        "✅ <b>Ключевые слова сохранены</b>\n\n"
        "<b>Введите сумму контракта:</b>\n"
        "<i>Пример: 500 000 рублей</i>",
        reply_markup=get_cancel_keyboard()
    )
    await state.set_state(Questionnaire.contract_amount)

@dp.message(Questionnaire.contract_amount)
async def process_contract_amount(message: types.Message, state: FSMContext):
    await state.update_data(contract_amount=message.text)
    await message.answer(
        "✅ <b>Сумма контракта сохранена</b>\n\n"
        "<b>Введите регионы работы:</b>\n"
        "<i>Пример: Москва, Московская область</i>",
        reply_markup=get_cancel_keyboard()
    )
    await state.set_state(Questionnaire.regions)

@dp.message(Questionnaire.regions)
async def process_regions(message: types.Message, state: FSMContext):
    """Завершение анкеты"""
    user_data = await state.get_data()
    user_data['regions'] = message.text
    user_data['username'] = message.from_user.username
    user_data['user_id'] = message.from_user.id
    user_data['filled_date'] = datetime.now().strftime("%d.%m.%Y %H:%M")
    
    # Сообщение для администратора
    if ADMIN_ID:
        admin_message = f"""
        📋 <b>НОВАЯ АНКЕТА</b>
        
        👤 <b>Пользователь:</b> @{user_data['username'] or 'нет'}
        🆔 <b>ID:</b> {user_data['user_id']}
        📅 <b>Дата:</b> {user_data['filled_date']}
        
        🏢 <b>Компания:</b> {user_data['company_name']}
        🔢 <b>ИНН:</b> {user_data['inn']}
        👤 <b>Контактное лицо:</b> {user_data['contact_person']}
        📞 <b>Телефон:</b> {user_data['phone']}
        📧 <b>Email:</b> {user_data['email']}
        🏭 <b>Сфера:</b> {user_data['activity_sphere']}
        🔑 <b>Ключевые слова:</b> {user_data['industry']}
        💰 <b>Сумма:</b> {user_data['contract_amount']}
        🌍 <b>Регионы:</b> {user_data['regions']}
        """
        try:
            await bot.send_message(ADMIN_ID, admin_message)
        except:
            pass
    
    # Сообщение пользователю
    await message.answer(
        "🎉 <b>Анкета успешно заполнена!</b>\n\n"
        "✅ Ваши данные переданы специалисту.\n"
        "📞 Мы свяжемся с вами в ближайшее время.\n\n"
        "Спасибо за сотрудничество!",
        reply_markup=get_main_keyboard()
    )
    
    await state.clear()

@dp.message(F.text == "🚫 Отменить")
async def cancel_questionnaire(message: types.Message, state: FSMContext):
    await message.answer(
        "Заполнение анкеты отменено.",
        reply_markup=get_main_keyboard()
    )
    await state.clear()

@dp.message(F.text == "📋 Мои данные")
async def show_my_data(message: types.Message, state: FSMContext):
    user_data = await state.get_data()
    if not user_data:
        await message.answer("Вы еще не заполняли анкету.")
        return
    
    response = f"""
    📋 <b>Ваши данные:</b>
    
    🏢 <b>Компания:</b> {user_data.get('company_name', '—')}
    🔢 <b>ИНН:</b> {user_data.get('inn', '—')}
    👤 <b>Контактное лицо:</b> {user_data.get('contact_person', '—')}
    📞 <b>Телефон:</b> {user_data.get('phone', '—')}
    📧 <b>Email:</b> {user_data.get('email', '—')}
    🏭 <b>Сфера:</b> {user_data.get('activity_sphere', '—')}
    🔑 <b>Ключевые слова:</b> {user_data.get('industry', '—')}
    💰 <b>Сумма:</b> {user_data.get('contract_amount', '—')}
    🌍 <b>Регионы:</b> {user_data.get('regions', '—')}
    """
    await message.answer(response)

@dp.message(F.text == "📞 Контакты")
async def show_contacts(message: types.Message):
    await message.answer(
        "📞 <b>Контакты компании</b>\n\n"
        "<b>ООО \"Тритика\"</b>\n"
        "📍 Адрес: г. Владимир\n"
        "📱 Телефон: +7 (4922) 223-222\n"
        "✉️ Email: info@tritika.ru\n"
        "🌐 Сайт: www.tritika.ru"
    )

@dp.message(F.text == "ℹ️ Помощь")
async def show_help(message: types.Message):
    await message.answer(
        "ℹ️ <b>Помощь по боту</b>\n\n"
        "<b>Как пользоваться:</b>\n"
        "1. Нажмите '📝 Заполнить анкету'\n"
        "2. Ответьте на все вопросы\n"
        "3. Получите подтверждение\n\n"
        "<b>Доступные команды:</b>\n"
        "• /start - Главное меню\n"
        "• 📝 Заполнить анкету - поиск тендеров\n"
        "• 📋 Мои данные - просмотр анкеты\n"
        "• 📞 Контакты - информация о компании"
    )

@dp.message()
async def handle_all_messages(message: types.Message):
    """Обработка всех остальных сообщений"""
    await message.answer(
        "Используйте кнопки ниже для навигации:",
        reply_markup=get_main_keyboard()
    )

# =========== FLASK СЕРВЕР ДЛЯ REPLIT ===========
app = Flask(__name__)

@app.route('/')
def home():
    return "🤖 Telegram бот для поиска тендеров работает!"

@app.route('/health')
def health():
    return "✅ Бот активен"

# Функция для запуска бота в отдельном потоке
def run_bot():
    """Запуск Telegram бота"""
    logger.info("🚀 Запускаю Telegram бота...")
    asyncio.run(main())

async def main():
    """Основная функция запуска бота"""
    try:
        await dp.start_polling(bot)
    except Exception as e:
        logger.error(f"Ошибка: {e}")

if __name__ == "__main__":
    # Запускаем бота в отдельном потоке
    bot_thread = threading.Thread(target=run_bot)
    bot_thread.daemon = True
    bot_thread.start()
    
    # Запускаем Flask сервер
    app.run(host='0.0.0.0', port=8080)
