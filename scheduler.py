from __future__ import annotations

import asyncio
import logging
from typing import Optional

from aiogram import Bot

import db_async as database
from config import CHECK_INTERVAL_MINUTES
from parser import GoldAppleParser, parser

logger = logging.getLogger(__name__)

UNSUBSCRIBE_HINT = (
    "\n\n<i>Чтобы удалить товар из отслеживания, отправьте /list "
    "и нажмите «🗑 Удалить» у нужного товара.</i>"
)


def format_price(price: float) -> str:
    if price == int(price):
        return f"{int(price):,}".replace(",", " ") + " ₽"
    return f"{price:,.2f}".replace(",", " ").replace(".", ",") + " ₽"


async def check_prices(bot: Bot, gold_parser: Optional[GoldAppleParser] = None) -> None:
    active_parser = gold_parser or parser
    products = await database.get_all_products()
    if not products:
        return

    logger.info("Проверка цен для %s товаров", len(products))

    for product in products:
        try:
            info = await active_parser.get_product(product.url)
            current_price = info.price

            should_notify = (
                current_price < product.target_price
                and (
                    product.last_notified_price is None
                    or current_price < product.last_notified_price
                )
            )

            if should_notify:
                text = (
                    f"🔔 Цена снизилась!\n\n"
                    f"<b>{info.name}</b>\n"
                    f"Текущая цена: <b>{format_price(current_price)}</b>\n"
                    f"Ваш порог: {format_price(product.target_price)}\n"
                )
                if info.old_price and info.old_price > current_price:
                    text += f"Старая цена: {format_price(info.old_price)}\n"
                text += f"\n<a href=\"{product.url}\">Открыть товар</a>"
                text += UNSUBSCRIBE_HINT

                await bot.send_message(product.user_id, text, disable_web_page_preview=False)
                await database.update_product_price(
                    product.id,
                    current_price,
                    last_notified_price=current_price,
                    update_notified=True,
                )
                logger.info(
                    "Уведомление отправлено: user=%s item=%s price=%s",
                    product.user_id,
                    product.item_id,
                    current_price,
                )
            else:
                await database.update_product_price(product.id, current_price)
        except Exception:
            logger.exception(
                "Ошибка проверки товара id=%s url=%s", product.id, product.url
            )

        await asyncio.sleep(1)


async def price_checker_loop(bot: Bot) -> None:
    await parser.start()
    interval_seconds = CHECK_INTERVAL_MINUTES * 60

    while True:
        try:
            await check_prices(bot)
        except Exception:
            logger.exception("Ошибка в цикле проверки цен")
        await asyncio.sleep(interval_seconds)
