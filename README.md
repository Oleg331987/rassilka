# Тендерный бот для ООО "Тритика"

Бот для заполнения анкет и поиска тендеров с автоматической рассылкой и статистикой эффективности.

## Особенности

1. **Заполнение анкеты** - 9 вопросов согласно документу
2. **Автоматическая рассылка** - каждые 2 недели активным пользователям
3. **Статистика эффективности** - отчеты каждые 2 недели
4. **Хранение данных** - в GitHub репозитории
5. **Админ-панель** - управление и просмотр статистики

## Деплой на Render.com

### 1. Подготовка GitHub репозитория

1. Создайте новый репозиторий на GitHub
2. Загрузите все файлы из этой папки в репозиторий

### 2. Создание GitHub Token

1. Зайдите в GitHub → Settings → Developer settings → Personal access tokens
2. Создайте новый токен с правами `repo`
3. Сохраните токен (он покажется только один раз!)

### 3. Создание Telegram бота

1. Найдите @BotFather в Telegram
2. Создайте нового бота командой `/newbot`
3. Сохраните токен бота

### 4. Получение вашего Telegram ID

1. Найдите @userinfobot в Telegram
2. Отправьте команду `/start`
3. Сохраните ваш ID

### 5. Деплой на Render.com

1. Зайдите на [render.com](https://render.com)
2. Нажмите "New +" → "Web Service"
3. Подключите ваш GitHub репозиторий
4. Настройте сервис:

**Настройки Web Service:**
- **Name:** `tender-bot` (или любое другое)
- **Environment:** `Python`
- **Region:** `Frankfurt` (ближайший к России)
- **Branch:** `main`
- **Build Command:** `pip install -r requirements.txt`
- **Start Command:** `python app.py`

**Environment Variables (Web Service):**
- `BOT_TOKEN` = ваш_токен_бота
- `GITHUB_TOKEN` = ваш_github_token
- `GITHUB_REPO` = ваш_username/название_репозитория
- `ADMIN_IDS` = ваш_telegram_id
- `RENDER` = `true`

5. Нажмите "Create Web Service"

### 6. Создание Cron Job для отчетов

1. На Render.com нажмите "New +" → "Cron Job"
2. Настройки:

**Настройки Cron Job:**
- **Name:** `efficiency-report`
- **Environment:** `Python`
- **Region:** `Frankfurt`
- **Schedule:** `0 10 */14 * *` (каждые 14 дней в 10:00)
- **Branch:** `main`
- **Build Command:** `pip install -r requirements.txt`
- **Start Command:** `python scheduler_job.py`

**Environment Variables (Cron Job):**
- `BOT_TOKEN` = ваш_токен_бота
- `GITHUB_TOKEN` = ваш_github_token
- `GITHUB_REPO` = ваш_username/название_репозитория
- `ADMIN_IDS` = ваш_telegram_id

### 7. Проверка работы

1. После деплоя найдите вашего бота в Telegram
2. Отправьте `/start`
3. Проверьте команды:
   - `/questionnaire` - заполнить анкету
   - `/my_data` - посмотреть данные
   - `/admin_stats` - статистика (для админа)

## Локальная разработка

1. Установите Python 3.11+
2. Создайте виртуальное окружение:
   ```bash
   python -m venv venv
   source venv/bin/activate  # Linux/Mac
   venv\Scripts\activate     # Windows
