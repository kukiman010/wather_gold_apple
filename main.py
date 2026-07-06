import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from bot.handlers import router
from config import BOT_TOKEN, DB_HOST, DB_NAME, DB_PASSWORD, DB_PORT, DB_USER
import db_async as database
from db_async import close_db_api, init_db_api
from parser import parser
from scheduler import price_checker_loop

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)


async def main() -> None:
    if not BOT_TOKEN:
        logger.error("Укажите BOT_TOKEN в файле .env")
        sys.exit(1)

    init_db_api(DB_NAME, DB_USER, DB_PASSWORD, DB_HOST, DB_PORT)
    await database.init_db()

    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)

    checker_task = asyncio.create_task(price_checker_loop(bot))

    try:
        logger.info("Бот запущен")
        await dp.start_polling(bot)
    finally:
        checker_task.cancel()
        await parser.stop()
        close_db_api()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
