from __future__ import annotations

import logging
import re

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

import db_async as database
from bot.keyboards import delete_keyboard, product_list_keyboard
from parser import extract_goldapple_url, normalize_goldapple_url, parser
from scheduler import format_price

logger = logging.getLogger(__name__)
router = Router()

PRICE_RE = re.compile(r"^\s*(\d+(?:[.,]\d+)?)\s*$")


class AddProduct(StatesGroup):
    waiting_price = State()


def _price_hint(info) -> str:
    text = f"Текущая цена: <b>{format_price(info.price)}</b>"
    if info.old_price and info.old_price > info.price:
        text += f"\nСтарая цена: {format_price(info.old_price)}"
    return text


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(
        "Привет! Я слежу за ценами на <b>Золотом Яблоке</b>.\n\n"
        "Как пользоваться:\n"
        "1. Отправьте ссылку на товар\n"
        "2. Укажите пороговую цену в рублях, при которой прислать уведомление\n\n"
        "Команды:\n"
        "/list — все отслеживаемые товары\n"
        "/cancel — отменить добавление товара"
    )


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Добавление товара отменено.")


@router.message(Command("list"))
async def cmd_list(message: Message) -> None:
    products = await database.get_user_products(message.from_user.id)
    if not products:
        await message.answer("Вы пока не отслеживаете ни одного товара.")
        return

    lines = ["<b>Ваши товары:</b>\n"]
    for index, product in enumerate(products, start=1):
        current = (
            format_price(product.current_price)
            if product.current_price is not None
            else "—"
        )
        lines.append(
            f"{index}. <b>{product.name}</b>\n"
            f"   Цель: {format_price(product.target_price)} | Сейчас: {current}\n"
            f"   <a href=\"{product.url}\">ссылка</a>\n"
        )

    await message.answer(
        "\n".join(lines),
        reply_markup=product_list_keyboard(products),
        disable_web_page_preview=True,
    )


@router.message(AddProduct.waiting_price)
async def process_target_price(message: Message, state: FSMContext) -> None:
    match = PRICE_RE.match(message.text or "")
    if not match:
        await message.answer(
            "Введите сумму в рублях, например: <code>1500</code> или <code>999.99</code>"
        )
        return

    target_price = float(match.group(1).replace(",", "."))
    if target_price <= 0:
        await message.answer("Сумма должна быть больше нуля.")
        return

    data = await state.get_data()
    url = data.get("url")
    item_id = data.get("item_id")
    name = data.get("name")
    current_price = data.get("current_price")

    if not url or not item_id:
        await state.clear()
        await message.answer("Сессия истекла. Отправьте ссылку заново.")
        return

    product = await database.add_product(
        user_id=message.from_user.id,
        url=url,
        item_id=item_id,
        name=name or item_id,
        target_price=target_price,
        current_price=current_price,
    )

    await state.clear()

    summary = (
        "✅ Товар добавлен в отслеживание!\n\n"
        f"<b>{product.name}</b>\n\n"
        f"Текущая цена: <b>{format_price(current_price)}</b>\n"
        f"Порог уведомления: <b>{format_price(target_price)}</b>\n"
    )
    if current_price is not None and current_price < target_price:
        summary += "\n⚠️ Цена уже ниже порога — уведомление придёт при следующей проверке."
    else:
        summary += "\nУведомление придёт, когда цена опустится ниже порога."

    summary += f"\n\n<a href=\"{product.url}\">Открыть товар</a>"

    await message.answer(
        summary,
        reply_markup=delete_keyboard(product.id),
        disable_web_page_preview=True,
    )


@router.message(F.text.contains("goldapple.ru"))
async def process_product_url(message: Message, state: FSMContext) -> None:
    url = extract_goldapple_url(message.text or "")
    if not url:
        return
    wait_message = await message.answer("Проверяю товар, подождите…")

    try:
        normalized_url = normalize_goldapple_url(url)
        info = await parser.get_product(normalized_url)
    except ValueError as exc:
        await wait_message.edit_text(f"❌ {exc}")
        return
    except Exception:
        logger.exception("Ошибка парсинга URL %s", url)
        await wait_message.edit_text(
            "❌ Не удалось получить данные с сайта. Попробуйте позже."
        )
        return

    existing = await database.get_user_products(message.from_user.id)
    if any(item.item_id == info.item_id for item in existing):
        await wait_message.edit_text(
            f"Этот товар уже в списке.\n\n"
            f"<b>{info.name}</b>\n"
            f"{_price_hint(info)}\n"
            f"<a href=\"{info.url}\">Открыть товар</a>",
            disable_web_page_preview=True,
        )
        return

    await state.set_state(AddProduct.waiting_price)
    await state.update_data(
        url=info.url,
        item_id=info.item_id,
        name=info.name,
        current_price=info.price,
    )

    await wait_message.edit_text(
        f"<b>{info.name}</b>\n"
        f"{_price_hint(info)}\n\n"
        "Укажите пороговую сумму в рублях — при какой цене прислать уведомление.\n"
        "Например: <code>2000</code> или <code>999.99</code>",
        disable_web_page_preview=True,
    )


@router.callback_query(F.data.startswith("delete:"))
async def delete_product_callback(callback: CallbackQuery) -> None:
    product_id = int(callback.data.split(":", 1)[1])
    product = await database.get_product(product_id, callback.from_user.id)

    if not product:
        await callback.answer("Товар не найден", show_alert=True)
        return

    await database.delete_product(product_id, callback.from_user.id)
    await callback.answer("Удалено")
    await callback.message.edit_text(
        f"Товар удалён из отслеживания:\n<b>{product.name}</b>"
    )
