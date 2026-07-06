from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def delete_keyboard(product_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🗑 Удалить",
                    callback_data=f"delete:{product_id}",
                )
            ]
        ]
    )


def product_list_keyboard(products) -> InlineKeyboardMarkup:
    buttons = []
    for product in products:
        label = product.name[:40] + ("…" if len(product.name) > 40 else "")
        buttons.append(
            [
                InlineKeyboardButton(
                    text=f"🗑 {label}",
                    callback_data=f"delete:{product.id}",
                )
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=buttons)
