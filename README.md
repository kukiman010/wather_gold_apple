# Gold Apple Price Watcher

Telegram-бот для отслеживания цен на [goldapple.ru](https://goldapple.ru).

## Возможности

- Добавление товара по ссылке и порогу цены
- Проверка цен раз в час
- Уведомление, когда цена опускается ниже заданной
- Повторное уведомление, если цена стала ещё ниже
- Просмотр списка товаров (`/list`) и удаление из отслеживания

## Установка

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium
```

### База данных PostgreSQL

На том же сервере, что и tg_gpt, создайте базу:

```sql
CREATE DATABASE tg_gold_apple;
```

Примените схему (или бот создаст таблицу сам при первом запуске):

```bash
psql -U postgres -d tg_gold_apple -f tools/schema.sql
```

Скопируйте `.env.example` в `.env` и укажите настройки:

```env
BOT_TOKEN=123456:ABC...
DB_HOST=localhost
DB_PORT=5432
DB_NAME=tg_gold_apple
DB_USER=postgres
DB_PASSWORD=your_password
```

Токен бота можно получить у [@BotFather](https://t.me/BotFather).

## Запуск

```bash
python main.py
```

## Как пользоваться

1. Отправьте боту ссылку, например:  
   `https://goldapple.ru/19000007133-tall-drink`
2. Бот покажет текущую цену и спросит порог уведомления
3. Отправьте число, например: `200`
4. Команда `/list` — все отслеживаемые товары
5. Команда `/cancel` — отменить добавление товара

## Парсер

Сайт Золотого Яблока защищён антибот-системой. Парсер использует Playwright (headless Chromium): проходит проверку и запрашивает внутренний API `product-card/base/v2`.

Поддерживаются:

- фиксированная цена (`actual`)
- цена со скидкой (`actual` + `old`)
- цена по карте лояльности (`loyalty` при `bestLoyalty`)

## Архитектура БД

Слой данных повторяет паттерн из tg_gpt:

- `database.py` — пул соединений psycopg2
- `databaseapi.py` — методы для таблицы `products`
- `db_async.py` — async-обёртки для aiogram

## Настройки (.env)

| Переменная | Описание | По умолчанию |
|---|---|---|
| `BOT_TOKEN` | Токен Telegram-бота | — |
| `CHECK_INTERVAL_MINUTES` | Интервал проверки цен (мин) | `60` |
| `CITY_ID` | ID города для API (Москва) | `c2deb16a-...` |
| `DB_HOST` | Хост PostgreSQL | `localhost` |
| `DB_PORT` | Порт PostgreSQL | `5432` |
| `DB_NAME` | Имя базы | `tg_gold_apple` |
| `DB_USER` | Пользователь PostgreSQL | `postgres` |
| `DB_PASSWORD` | Пароль PostgreSQL | — |
