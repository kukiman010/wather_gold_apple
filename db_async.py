from __future__ import annotations

import asyncio
from typing import Optional

from databaseapi import Product, dbApi

_api: Optional[dbApi] = None


def init_db_api(dbname: str, user: str, password: str, host: str, port: int) -> dbApi:
    global _api
    _api = dbApi(dbname, user, password, host, port)
    return _api


def get_db_api() -> dbApi:
    if _api is None:
        raise RuntimeError("Database API is not initialized")
    return _api


def close_db_api() -> None:
    global _api
    if _api is not None:
        _api.close()
        _api = None


async def init_db() -> None:
    await asyncio.to_thread(get_db_api().init_schema)


async def add_product(
    user_id: int,
    url: str,
    item_id: str,
    name: str,
    target_price: float,
    current_price: Optional[float],
) -> Product:
    return await asyncio.to_thread(
        get_db_api().add_product,
        user_id,
        url,
        item_id,
        name,
        target_price,
        current_price,
    )


async def get_user_products(user_id: int) -> list[Product]:
    return await asyncio.to_thread(get_db_api().get_user_products, user_id)


async def get_all_products() -> list[Product]:
    return await asyncio.to_thread(get_db_api().get_all_products)


async def get_product(product_id: int, user_id: int) -> Optional[Product]:
    return await asyncio.to_thread(get_db_api().get_product, product_id, user_id)


async def delete_product(product_id: int, user_id: int) -> bool:
    return await asyncio.to_thread(get_db_api().delete_product, product_id, user_id)


async def update_product_price(
    product_id: int,
    current_price: float,
    last_notified_price: Optional[float] = None,
    *,
    update_notified: bool = False,
    url: Optional[str] = None,
) -> None:
    await asyncio.to_thread(
        get_db_api().update_product_price,
        product_id,
        current_price,
        last_notified_price,
        update_notified=update_notified,
        url=url,
    )
