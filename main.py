import os
import logging
import asyncio
from datetime import datetime
from aiohttp import web
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from dotenv import load_dotenv

# Загружаем переменные окружения
load_dotenv()

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Получаем токен и ID админа
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = os.getenv("ADMIN_ID")  # Добавьте свой ID в .env или Railway variables
if not BOT_TOKEN:
    logger.error("❌ BOT_TOKEN не установлен!")
    exit(1)

# Инициализация бота
bot = Bot(token=BOT_TOKEN)
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
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="/questionnaire")],
            [KeyboardButton(text="/my_data"), KeyboardButton(text="/help")]
        ],
        resize_keyboard=True,
        one_time_keyboard=True
    )
    return keyboard

def get_cancel_keyboard():
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="❌ Отменить заполнение")]
        ],
        resize_keyboard=True,
        one_time_keyboard=True
    )
    return keyboard

# =========== КОМАНДЫ БОТА ===========
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer(
        "👋 Привет! Я бот для поиска тендеров ООО \"Тритика\"\n\n"
        "📋 Я помогу вам заполнить анкету для подбора подходящих тендеров.\n"
        "После заполнения все данные будут переданы нашему специалисту.\n\n"
        "🚀 Начнем работу!",
        reply_markup=get_main_keyboard()
    )

@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    await message.answer(
        "🆘 Помощь по боту:\n\n"
        "1. /questionnaire - заполнить анкету из 9 вопросов\n"
        "2. /my_data - посмотреть свои данные (после заполнения)\n"
        "3. /feedback - оставить отзыв\n\n"
        "📞 Контакты:\n"
        "ООО \"Тритика\"\n"
        "Телефон: +7 (4922) 223-222",
        reply_markup=get_main_keyboard()
    )

@dp.message(Command("questionnaire"))
async def cmd_questionnaire(message: types.Message, state: FSMContext):
    # Проверяем, не заполняется ли уже анкета
    current_state = await state.get_state()
    if current_state:
        await message.answer("Вы уже заполняете анкету. Продолжайте или отмените текущее заполнение.")
        return
    
    await message.answer(
        "📝 Начинаем заполнение анкеты для поиска тендеров.\n\n"
        "Анкета состоит из 9 вопросов. Заполняйте внимательно!\n"
        "Чтобы отменить заполнение, нажмите кнопку ниже или напишите 'отмена'.",
        reply_markup=get_cancel_keyboard()
    )
    await message.answer("1️⃣ Введите <b>наименование вашей компании</b>:")
    await state.set_state(Questionnaire.company_name)

@dp.message(F.text.lower() == "❌ отменить заполнение")
@dp.message(F.text.lower().in_(["отмена", "отменить", "cancel"]))
async def cancel_questionnaire(message: types.Message, state: FSMContext):
    current_state = await state.get_state()
    if current_state is None:
        await message.answer("Нет активного заполнения анкеты.", reply_markup=get_main_keyboard())
        return
    
    await message.answer(
        "❌ Заполнение анкеты отменено.\n"
        "Вы можете начать заново командой /questionnaire",
        reply_markup=get_main_keyboard()
    )
    await state.clear()

# Обработчики для каждого состояния
@dp.message(Questionnaire.company_name)
async def process_company_name(message: types.Message, state: FSMContext):
    await state.update_data(company_name=message.text)
    await message.answer("✅ Сохранено!\n\n2️⃣ Введите <b>ИНН вашей компании</b> (10 или 12 цифр):")
    await state.set_state(Questionnaire.inn)

@dp.message(Questionnaire.inn)
async def process_inn(message: types.Message, state: FSMContext):
    inn = message.text.strip()
    if not (inn.isdigit() and (len(inn) == 10 or len(inn) == 12)):
        await message.answer("❌ ИНН должен содержать 10 или 12 цифр. Введите корректный ИНН:")
        return
    await state.update_data(inn=inn)
    await message.answer("✅ Сохранено!\n\n3️⃣ Введите <b>контактное лицо</b> (ФИО):")
    await state.set_state(Questionnaire.contact_person)

@dp.message(Questionnaire.contact_person)
async def process_contact_person(message: types.Message, state: FSMContext):
    await state.update_data(contact_person=message.text)
    await message.answer("✅ Сохранено!\n\n4️⃣ Введите <b>контактный телефон</b> (например, +7XXX-XXX-XX-XX):")
    await state.set_state(Questionnaire.phone)

@dp.message(Questionnaire.phone)
async def process_phone(message: types.Message, state: FSMContext):
    await state.update_data(phone=message.text)
    await message.answer("✅ Сохранено!\n\n5️⃣ Введите <b>E-mail</b>:")
    await state.set_state(Questionnaire.email)

@dp.message(Questionnaire.email)
async def process_email(message: types.Message, state: FSMContext):
    email = message.text.strip()
    if "@" not in email or "." not in email:
        await message.answer("❌ Пожалуйста, введите корректный email адрес:")
        return
    await state.update_data(email=email)
    await message.answer("✅ Сохранено!\n\n6️⃣ Введите <b>сферу деятельности, ОКВЭД</b>:")
    await state.set_state(Questionnaire.activity_sphere)

@dp.message(Questionnaire.activity_sphere)
async def process_activity_sphere(message: types.Message, state: FSMContext):
    await state.update_data(activity_sphere=message.text)
    await message.answer("✅ Сохранено!\n\n7️⃣ Введите <b>отрасль / ключевые слова</b> (через запятую):")
    await state.set_state(Questionnaire.industry)

@dp.message(Questionnaire.industry)
async def process_industry(message: types.Message, state: FSMContext):
    await state.update_data(industry=message.text)
    await message.answer("✅ Сохранено!\n\n8️⃣ Введите <b>желаемую сумму контракта</b> (в рублях):")
    await state.set_state(Questionnaire.contract_amount)

@dp.message(Questionnaire.contract_amount)
async def process_contract_amount(message: types.Message, state: FSMContext):
    amount = message.text.strip()
    if not amount.replace(" ", "").replace(",", "").replace(".", "").isdigit():
        await message.answer("❌ Введите числовое значение суммы:")
        return
    await state.update_data(contract_amount=amount)
    await message.answer("✅ Сохранено!\n\n9️⃣ Введите <b>регионы исполнения контрактов</b> (через запятую):")
    await state.set_state(Questionnaire.regions)

@dp.message(Questionnaire.regions)
async def process_regions(message: types.Message, state: FSMContext):
    user_data = await state.get_data()
    user_data['regions'] = message.text
    user_data['username'] = message.from_user.username
    user_data['user_id'] = message.from_user.id
    user_data['filled_date'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    # Формируем сообщение для администратора
    admin_message = (
        "📋 <b>НОВАЯ АНКЕТА ДЛЯ ПОИСКА ТЕНДЕРОВ</b>\n\n"
        f"👤 <b>Пользователь:</b> @{message.from_user.username or 'нет'} (ID: {message.from_user.id})\n"
        f"📅 <b>Дата заполнения:</b> {user_data['filled_date']}\n\n"
        f"1. <b>Компания:</b> {user_data['company_name']}\n"
        f"2. <b>ИНН:</b> {user_data['inn']}\n"
        f"3. <b>Контактное лицо:</b> {user_data['contact_person']}\n"
        f"4. <b>Телефон:</b> {user_data['phone']}\n"
        f"5. <b>E-mail:</b> {user_data['email']}\n"
        f"6. <b>Сфера деятельности:</b> {user_data['activity_sphere']}\n"
        f"7. <b>Отрасль:</b> {user_data['industry']}\n"
        f"8. <b>Сумма контракта:</b> {user_data['contract_amount']} руб.\n"
        f"9. <b>Регионы:</b> {user_data['regions']}\n"
    )
    
    # Отправляем данные администратору
    if ADMIN_ID:
        try:
            await bot.send_message(ADMIN_ID, admin_message, parse_mode="HTML")
            logger.info(f"✅ Анкета отправлена администратору (ID: {ADMIN_ID})")
        except Exception as e:
            logger.error(f"❌ Ошибка отправки администратору: {e}")
    
    # Сохраняем данные пользователя
    await state.update_data(**user_data)
    
    # Отправляем подтверждение пользователю
    await message.answer(
        "🎉 <b>Анкета успешно заполнена!</b>\n\n"
        "✅ Ваши данные переданы нашему специалисту.\n"
        "📞 Мы свяжемся с вами в ближайшее время для уточнения деталей.\n\n"
        "Спасибо за сотрудничество!",
        reply_markup=get_main_keyboard(),
        parse_mode="HTML"
    )
    
    # Очищаем состояние
    await state.clear()

@dp.message(Command("my_data"))
async def cmd_my_data(message: types.Message, state: FSMContext):
    user_data = await state.get_data()
    
    if not user_data:
        await message.answer(
            "📭 У вас нет сохраненных данных анкеты.\n"
            "Заполните анкету командой /questionnaire",
            reply_markup=get_main_keyboard()
        )
        return
    
    user_info = (
        "📋 <b>Ваши данные:</b>\n\n"
        f"1. <b>Компания:</b> {user_data.get('company_name', 'не указано')}\n"
        f"2. <b>ИНН:</b> {user_data.get('inn', 'не указано')}\n"
        f"3. <b>Контактное лицо:</b> {user_data.get('contact_person', 'не указано')}\n"
        f"4. <b>Телефон:</b> {user_data.get('phone', 'не указано')}\n"
        f"5. <b>E-mail:</b> {user_data.get('email', 'не указано')}\n"
        f"6. <b>Сфера деятельности:</b> {user_data.get('activity_sphere', 'не указано')}\n"
        f"7. <b>Отрасль:</b> {user_data.get('industry', 'не указано')}\n"
        f"8. <b>Сумма контракта:</b> {user_data.get('contract_amount', 'не указано')} руб.\n"
        f"9. <b>Регионы:</b> {user_data.get('regions', 'не указано')}\n\n"
        f"📅 <b>Дата заполнения:</b> {user_data.get('filled_date', 'неизвестно')}\n\n"
        "Чтобы изменить данные, заполните анкету заново."
    )
    
    await message.answer(user_info, parse_mode="HTML", reply_markup=get_main_keyboard())

@dp.message(F.text)
async def handle_text(message: types.Message, state: FSMContext):
    # Проверяем, находится ли пользователь в процессе заполнения анкеты
    current_state = await state.get_state()
    if current_state:
        # Если пользователь в процессе заполнения, игнорируем общие текстовые сообщения
        await message.answer("Пожалуйста, ответьте на текущий вопрос анкеты или отмените заполнение.")
        return
    
    # Если не в процессе заполнения
    if not message.text.startswith('/'):
        await message.answer(
            "Используйте команды:\n"
            "/start - начало работы\n"
            "/questionnaire - заполнить анкету\n"
            "/my_data - мои данные",
            reply_markup=get_main_keyboard()
        )

# =========== ВЕБ-СЕРВЕР ДЛЯ HEALTHCHECK ===========
async def handle_health(request):
    """Обработчик health check"""
    return web.Response(text="Bot is running")

async def start_web_server():
    """Запуск веб-сервера"""
    app = web.Application()
    app.router.add_get('/', handle_health)
    app.router.add_get('/health', handle_health)
    
    port = int(os.getenv("PORT", 8080))
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    
    logger.info(f"✅ Веб-сервер запущен на порту {port}")
    logger.info(f"✅ Health check доступен по адресу: http://0.0.0.0:{port}/health")
    
    return runner

# =========== ГЛАВНАЯ ФУНКЦИЯ ===========
async def main():
    """Запуск всего приложения"""
    logger.info("🚀 Запуск Telegram бота на Railway...")
    
    try:
        # Запускаем веб-сервер для health check
        web_runner = await start_web_server()
        
        logger.info("🤖 Запускаю Telegram бота...")
        
        # Запускаем бота
        await dp.start_polling(bot)
        
        # После остановки бота
        await web_runner.cleanup()
        
    except Exception as e:
        logger.error(f"❌ Критическая ошибка: {e}")
        raise

if __name__ == "__main__":
    # Запускаем асинхронное приложение
    asyncio.run(main())
