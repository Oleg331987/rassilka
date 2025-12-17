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
ADMIN_ID = os.getenv("ADMIN_ID")
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
    """Главное меню"""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📝 Заполнить анкету")],
            [KeyboardButton(text="📋 Мои данные"), KeyboardButton(text="ℹ️ Помощь")],
            [KeyboardButton(text="📞 Контакты компании")]
        ],
        resize_keyboard=True,
        input_field_placeholder="Выберите действие"
    )

def get_cancel_keyboard():
    """Клавиатура для отмены"""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🚫 Отменить заполнение")]
        ],
        resize_keyboard=True,
        one_time_keyboard=True
    )

def get_skip_keyboard():
    """Клавиатура с опцией пропуска"""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="⏭ Пропустить")],
            [KeyboardButton(text="🚫 Отменить заполнение")]
        ],
        resize_keyboard=True,
        one_time_keyboard=True
    )

# =========== КОМАНДЫ БОТА ===========
@dp.message(Command("start"))
@dp.message(F.text == "🏠 Главное меню")
async def cmd_start(message: types.Message):
    """Начало работы с ботом"""
    welcome_text = """
    🏢 <b>Добро пожаловать в бот для поиска тендеров ООО "Тритика"</b>

    Я помогу вам найти подходящие тендеры для вашего бизнеса. 
    Для этого нужно заполнить анкету, чтобы мы поняли ваши потребности.

    ⚡️ <b>Преимущества работы с нами:</b>
    • Профессиональный поиск тендеров
    • Экономия вашего времени
    • Повышение шансов на победу
    • Консультации по участию

    Выберите действие ниже 👇
    """
    await message.answer(welcome_text, reply_markup=get_main_keyboard(), parse_mode="HTML")

@dp.message(F.text == "ℹ️ Помощь")
@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    """Помощь по использованию бота"""
    help_text = """
    🆘 <b>Помощь по использованию бота</b>

    <b>Основные возможности:</b>
    1. 📝 <b>Заполнить анкету</b> - 9 вопросов о вашей компании
    2. 📋 <b>Мои данные</b> - просмотр сохранённой анкеты
    3. 📞 <b>Контакты компании</b> - как с нами связаться

    <b>Процесс работы:</b>
    1. Заполняете анкету (5-7 минут)
    2. Мы анализируем ваши данные
    3. Подбираем подходящие тендеры
    4. Связываемся с вами для обсуждения

    ❓ <b>Частые вопросы:</b>
    <i>• Данные конфиденциальны и не передаются третьим лицам
    • Вы можете отредактировать анкету в любой момент
    • Ответы на вопросы помогут точнее подобрать тендеры</i>
    """
    await message.answer(help_text, reply_markup=get_main_keyboard(), parse_mode="HTML")

@dp.message(F.text == "📞 Контакты компании")
async def cmd_contacts(message: types.Message):
    """Контакты компании"""
    contacts_text = """
    📞 <b>Контакты ООО "Тритика"</b>

    <b>Адрес:</b>
    г. Владимир, ул. Примерная, д. 123

    <b>Телефоны:</b>
    • +7 (4922) 223-222 (основной)
    • +7 (999) 123-45-67 (мобильный)

    <b>Email:</b>
    info@tritika.ru

    <b>График работы:</b>
    Пн-Пт: 9:00 - 18:00
    Сб: 10:00 - 15:00
    Вс: выходной

    🌐 <b>Сайт:</b>
    www.tritika.ru

    <i>Будем рады помочь вашему бизнесу!</i>
    """
    await message.answer(contacts_text, parse_mode="HTML")

@dp.message(F.text == "📝 Заполнить анкету")
@dp.message(Command("questionnaire"))
async def cmd_questionnaire(message: types.Message, state: FSMContext):
    """Начало заполнения анкеты"""
    # Проверяем, не заполняется ли уже анкета
    current_state = await state.get_state()
    if current_state:
        await message.answer("⚠️ <b>Вы уже заполняете анкету!</b>\n\nПродолжайте или отмените текущее заполнение.", parse_mode="HTML")
        return
    
    intro_text = """
    📋 <b>Анкета для поиска тендеров</b>

    <i>Заполнение займет 5-7 минут. 
    Чем подробнее вы ответите, тем точнее мы подберем тендеры.</i>

    🎯 <b>Что мы узнаем из анкеты:</b>
    1️⃣ Основную информацию о компании
    2️⃣ Сферу деятельности
    3️⃣ Финансовые предпочтения
    4️⃣ Географию работы

    📊 <b>После заполнения:</b>
    • Ваши данные передаются специалисту
    • Мы проводим анализ рынка
    • Подбираем подходящие тендеры
    • Связываемся с вами в течение 24 часов

    <b>Начнем?</b> Нажмите "Продолжить" или "Отменить" ниже.
    """
    
    # Клавиатура для начала заполнения
    start_keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="✅ Продолжить")],
            [KeyboardButton(text="🚫 Отменить")]
        ],
        resize_keyboard=True
    )
    
    await message.answer(intro_text, reply_markup=start_keyboard, parse_mode="HTML")
    await state.set_state(Questionnaire.company_name)

@dp.message(F.text == "✅ Продолжить", Questionnaire.company_name)
async def start_filling(message: types.Message, state: FSMContext):
    """Начало заполнения после подтверждения"""
    await message.answer("""
    🏢 <b>Шаг 1 из 9: Информация о компании</b>

    <b>Введите полное наименование вашей компании:</b>
    <i>Пример: Общество с ограниченной ответственностью "Ромашка"</i>
    """, reply_markup=get_cancel_keyboard(), parse_mode="HTML")
    await state.set_state(Questionnaire.company_name)

@dp.message(Questionnaire.company_name)
async def process_company_name(message: types.Message, state: FSMContext):
    if len(message.text) < 2:
        await message.answer("❌ <b>Название компании слишком короткое.</b>\n\nВведите полное наименование:", parse_mode="HTML")
        return
    
    await state.update_data(company_name=message.text)
    
    await message.answer("""
    ✅ <b>Сохранено!</b>
    
    🔢 <b>Шаг 2 из 9: ИНН компании</b>
    
    <b>Введите ИНН вашей компании:</b>
    <i>• 10 цифр для юридических лиц
    • 12 цифр для индивидуальных предпринимателей</i>
    
    <i>Пример: 1234567890</i>
    """, reply_markup=get_cancel_keyboard(), parse_mode="HTML")
    await state.set_state(Questionnaire.inn)

@dp.message(Questionnaire.inn)
async def process_inn(message: types.Message, state: FSMContext):
    inn = message.text.strip()
    
    # Проверка ИНН
    if not inn.isdigit():
        await message.answer("❌ <b>ИНН должен содержать только цифры.</b>\n\nВведите корректный ИНН:", parse_mode="HTML")
        return
    
    if len(inn) not in [10, 12]:
        await message.answer("❌ <b>ИНН должен содержать 10 или 12 цифр.</b>\n\nВведите корректный ИНН:", parse_mode="HTML")
        return
    
    await state.update_data(inn=inn)
    
    await message.answer("""
    ✅ <b>Сохранено!</b>
    
    👤 <b>Шаг 3 из 9: Контактное лицо</b>
    
    <b>Введите ФИО контактного лица:</b>
    <i>Того, с кем мы будем общаться по поводу тендеров</i>
    
    <i>Пример: Иванов Иван Иванович</i>
    """, reply_markup=get_cancel_keyboard(), parse_mode="HTML")
    await state.set_state(Questionnaire.contact_person)

@dp.message(Questionnaire.contact_person)
async def process_contact_person(message: types.Message, state: FSMContext):
    if len(message.text.split()) < 2:
        await message.answer("❌ <b>Пожалуйста, введите Фамилию Имя Отчество полностью.</b>", parse_mode="HTML")
        return
    
    await state.update_data(contact_person=message.text)
    
    await message.answer("""
    ✅ <b>Сохранено!</b>
    
    📞 <b>Шаг 4 из 9: Контактный телефон</b>
    
    <b>Введите номер телефона для связи:</b>
    <i>Формат: +7XXX-XXX-XX-XX или 8XXX-XXX-XX-XX</i>
    
    <i>Пример: +7 (999) 123-45-67</i>
    """, reply_markup=get_cancel_keyboard(), parse_mode="HTML")
    await state.set_state(Questionnaire.phone)

@dp.message(Questionnaire.phone)
async def process_phone(message: types.Message, state: FSMContext):
    phone = message.text.strip()
    
    # Простая валидация телефона
    digits = ''.join(filter(str.isdigit, phone))
    if len(digits) not in [10, 11]:
        await message.answer("❌ <b>Проверьте правильность номера телефона.</b>\n\nОн должен содержать 10-11 цифр.", parse_mode="HTML")
        return
    
    await state.update_data(phone=phone)
    
    await message.answer("""
    ✅ <b>Сохранено!</b>
    
    📧 <b>Шаг 5 из 9: Электронная почта</b>
    
    <b>Введите адрес электронной почты:</b>
    <i>На эту почту мы вышлем подобранные тендеры</i>
    
    <i>Пример: info@company.ru</i>
    """, reply_markup=get_cancel_keyboard(), parse_mode="HTML")
    await state.set_state(Questionnaire.email)

@dp.message(Questionnaire.email)
async def process_email(message: types.Message, state: FSMContext):
    email = message.text.strip().lower()
    
    if "@" not in email or "." not in email:
        await message.answer("❌ <b>Неверный формат email.</b>\n\nВведите корректный email адрес:", parse_mode="HTML")
        return
    
    await state.update_data(email=email)
    
    await message.answer("""
    ✅ <b>Сохранено!</b>
    
    🏭 <b>Шаг 6 из 9: Сфера деятельности</b>
    
    <b>Опишите сферу деятельности вашей компании:</b>
    <i>• Основные виды работ/услуг
    • Коды ОКВЭД (если знаете)
    • Основные направления</i>
    
    <i>Пример: Строительство зданий и сооружений, ОКВЭД 41.20</i>
    """, reply_markup=get_skip_keyboard(), parse_mode="HTML")
    await state.set_state(Questionnaire.activity_sphere)

@dp.message(F.text == "⏭ Пропустить", Questionnaire.activity_sphere)
async def skip_activity_sphere(message: types.Message, state: FSMContext):
    await state.update_data(activity_sphere="Не указано")
    await process_activity_sphere(message, state)

@dp.message(Questionnaire.activity_sphere)
async def process_activity_sphere(message: types.Message, state: FSMContext):
    await state.update_data(activity_sphere=message.text)
    
    await message.answer("""
    ✅ <b>Сохранено!</b>
    
    🔑 <b>Шаг 7 из 9: Ключевые слова/отрасль</b>
    
    <b>Введите ключевые слова или отрасли:</b>
    <i>• По каким словам искать тендеры
    • Товары/услуги, которые вы предлагаете
    • Отрасли, в которых работаете</i>
    
    <i>Пример: строительство, ремонт, отделка, монтаж</i>
    """, reply_markup=get_skip_keyboard(), parse_mode="HTML")
    await state.set_state(Questionnaire.industry)

@dp.message(F.text == "⏭ Пропустить", Questionnaire.industry)
async def skip_industry(message: types.Message, state: FSMContext):
    await state.update_data(industry="Не указано")
    await process_industry(message, state)

@dp.message(Questionnaire.industry)
async def process_industry(message: types.Message, state: FSMContext):
    await state.update_data(industry=message.text)
    
    await message.answer("""
    ✅ <b>Сохранено!</b>
    
    💰 <b>Шаг 8 из 9: Бюджет тендеров</b>
    
    <b>Введите желаемую сумму контрактов:</b>
    <i>• Минимальная сумма (от)
    • Максимальная сумма (до)
    • Или примерный диапазон</i>
    
    <i>Примеры:
    • От 100 000 до 500 000 рублей
    • До 1 000 000 рублей
    • 500 000 рублей</i>
    """, reply_markup=get_cancel_keyboard(), parse_mode="HTML")
    await state.set_state(Questionnaire.contract_amount)

@dp.message(Questionnaire.contract_amount)
async def process_contract_amount(message: types.Message, state: FSMContext):
    await state.update_data(contract_amount=message.text)
    
    await message.answer("""
    ✅ <b>Сохранено!</b>
    
    🌍 <b>Шаг 9 из 9: Регионы работы</b>
    
    <b>Введите регионы исполнения контрактов:</b>
    <i>• В каких регионах/городах готовы работать
    • Можно несколько через запятую
    • Или "по всей России"</i>
    
    <i>Примеры:
    • Владимирская область, Москва
    • По всей России
    • Центральный федеральный округ</i>
    """, reply_markup=get_cancel_keyboard(), parse_mode="HTML")
    await state.set_state(Questionnaire.regions)

@dp.message(Questionnaire.regions)
async def process_regions(message: types.Message, state: FSMContext):
    """Завершение анкеты"""
    user_data = await state.get_data()
    user_data['regions'] = message.text
    user_data['username'] = message.from_user.username or "Не указан"
    user_data['user_id'] = message.from_user.id
    user_data['filled_date'] = datetime.now().strftime("%d.%m.%Y %H:%M")
    
    # Формируем красивое сообщение для пользователя
    user_summary = f"""
    🎉 <b>Анкета успешно заполнена!</b>

    📊 <b>Ваши данные:</b>
    ──────────────────────
    🏢 <b>Компания:</b> {user_data['company_name']}
    🔢 <b>ИНН:</b> {user_data['inn']}
    👤 <b>Контактное лицо:</b> {user_data['contact_person']}
    📞 <b>Телефон:</b> {user_data['phone']}
    📧 <b>Email:</b> {user_data['email']}
    🏭 <b>Сфера деятельности:</b> {user_data['activity_sphere']}
    🔑 <b>Ключевые слова:</b> {user_data['industry']}
    💰 <b>Бюджет:</b> {user_data['contract_amount']}
    🌍 <b>Регионы:</b> {user_data['regions']}
    ──────────────────────
    📅 <b>Дата заполнения:</b> {user_data['filled_date']}

    ✅ <b>Ваши данные переданы нашему специалисту.</b>

    ⏳ <b>Что дальше?</b>
    1. Мы анализируем информацию
    2. Ищем подходящие тендеры
    3. Связываемся с вами в течение 24 часов

    📞 <b>Если есть срочные вопросы:</b>
    +7 (4922) 223-222

    Спасибо за доверие! Желаем успешных побед в тендерах! 🏆
    """
    
    # Формируем сообщение для администратора
    admin_message = f"""
    📋 <b>НОВАЯ АНКЕТА ДЛЯ ПОИСКА ТЕНДЕРОВ</b>
    ──────────────────────
    👤 <b>Пользователь:</b> @{user_data['username']}
    🆔 <b>ID:</b> {user_data['user_id']}
    📅 <b>Дата:</b> {user_data['filled_date']}
    ──────────────────────
    🏢 <b>Компания:</b> {user_data['company_name']}
    🔢 <b>ИНН:</b> {user_data['inn']}
    👤 <b>Контактное лицо:</b> {user_data['contact_person']}
    📞 <b>Телефон:</b> {user_data['phone']}
    📧 <b>Email:</b> {user_data['email']}
    🏭 <b>Сфера деятельности:</b> {user_data['activity_sphere']}
    🔑 <b>Ключевые слова:</b> {user_data['industry']}
    💰 <b>Бюджет:</b> {user_data['contract_amount']}
    🌍 <b>Регионы:</b> {user_data['regions']}
    ──────────────────────
    """
    
    # Отправляем данные администратору
    if ADMIN_ID:
        try:
            await bot.send_message(ADMIN_ID, admin_message, parse_mode="HTML")
            logger.info(f"✅ Анкета отправлена администратору (ID: {ADMIN_ID})")
        except Exception as e:
            logger.error(f"❌ Ошибка отправки администратору: {e}")
    
    # Отправляем подтверждение пользователю
    await message.answer(user_summary, parse_mode="HTML", reply_markup=get_main_keyboard())
    
    # Очищаем состояние
    await state.clear()

@dp.message(F.text == "📋 Мои данные")
@dp.message(Command("my_data"))
async def cmd_my_data(message: types.Message, state: FSMContext):
    """Просмотр сохраненных данных"""
    user_data = await state.get_data()
    
    if not user_data.get('company_name'):
        await message.answer("""
        📭 <b>У вас нет сохраненных данных</b>
        
        Вы еще не заполняли анкету. 
        Нажмите "📝 Заполнить анкету" чтобы начать.
        """, reply_markup=get_main_keyboard(), parse_mode="HTML")
        return
    
    user_info = f"""
    📋 <b>Ваша анкета</b>
    
    📅 <b>Заполнена:</b> {user_data.get('filled_date', 'Неизвестно')}
    ──────────────────────
    1️⃣ <b>Компания:</b> {user_data.get('company_name', 'Не указано')}
    2️⃣ <b>ИНН:</b> {user_data.get('inn', 'Не указано')}
    3️⃣ <b>Контактное лицо:</b> {user_data.get('contact_person', 'Не указано')}
    4️⃣ <b>Телефон:</b> {user_data.get('phone', 'Не указано')}
    5️⃣ <b>Email:</b> {user_data.get('email', 'Не указано')}
    6️⃣ <b>Сфера деятельности:</b> {user_data.get('activity_sphere', 'Не указано')}
    7️⃣ <b>Ключевые слова:</b> {user_data.get('industry', 'Не указано')}
    8️⃣ <b>Бюджет:</b> {user_data.get('contract_amount', 'Не указано')}
    9️⃣ <b>Регионы:</b> {user_data.get('regions', 'Не указано')}
    ──────────────────────
    
    <i>Чтобы изменить данные, заполните анкету заново.</i>
    """
    
    await message.answer(user_info, parse_mode="HTML", reply_markup=get_main_keyboard())

@dp.message(F.text == "🚫 Отменить")
@dp.message(F.text == "🚫 Отменить заполнение")
async def cancel_questionnaire(message: types.Message, state: FSMContext):
    """Отмена заполнения анкеты"""
    current_state = await state.get_state()
    if current_state is None:
        await message.answer("Нет активного заполнения анкеты.", reply_markup=get_main_keyboard())
        return
    
    await message.answer("""
    ❌ <b>Заполнение анкеты отменено</b>
    
    Вы можете начать заново в любое время.
    Нажмите "📝 Заполнить анкету" когда будете готовы.
    """, reply_markup=get_main_keyboard(), parse_mode="HTML")
    await state.clear()

@dp.message(F.text)
async def handle_text(message: types.Message, state: FSMContext):
    """Обработка текстовых сообщений"""
    current_state = await state.get_state()
    
    if current_state:
        # Если пользователь в процессе заполнения, игнорируем другие команды
        return
    
    # Обработка общих текстовых сообщений
    if message.text.lower() in ["привет", "hello", "hi", "здравствуйте"]:
        await cmd_start(message)
    elif message.text.lower() in ["спасибо", "благодарю"]:
        await message.answer("🤝 Рады помочь! Обращайтесь, если будут вопросы.", reply_markup=get_main_keyboard())
    else:
        await message.answer("""
        🤔 <b>Я не понял вашего сообщения</b>
        
        Используйте кнопки ниже или выберите действие:
        • 📝 Заполнить анкету - для поиска тендеров
        • 📋 Мои данные - просмотреть анкету
        • ℹ️ Помощь - инструкция по использованию
        • 📞 Контакты компании - как с нами связаться
        """, reply_markup=get_main_keyboard(), parse_mode="HTML")

# =========== ВЕБ-СЕРВЕР ДЛЯ HEALTHCHECK ===========
async def handle_health(request):
    """Обработчик health check"""
    return web.Response(text="✅ Bot is running and ready")

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
    logger.info("🚀 Запуск Telegram бота...")
    logger.info("✨ Версия с красивым интерфейсом")
    
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
