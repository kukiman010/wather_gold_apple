from __future__ import annotations

import unittest
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from databaseapi import Product
from parser import ProductInfo, _choose_canonical_url, normalize_goldapple_url

try:
    import aiogram  # noqa: F401
except ModuleNotFoundError:
    sys.modules["aiogram"] = SimpleNamespace(Bot=object)

from scheduler import check_prices


class CanonicalUrlTests(unittest.TestCase):
    def test_prefers_canonical_url_with_matching_item_id(self) -> None:
        result = _choose_canonical_url(
            "99000117702",
            "/99000117702-myhome-irn-004",
            "https://goldapple.ru/99000117702",
        )

        self.assertEqual(
            result,
            "https://goldapple.ru/99000117702-myhome-irn-004",
        )

    def test_falls_back_when_candidates_are_invalid(self) -> None:
        result = _choose_canonical_url(
            "99000117702",
            "https://example.com/99000117702-fake",
            "https://goldapple.ru/19000396957-other-product",
        )

        self.assertEqual(result, "https://goldapple.ru/99000117702")

    def test_rejects_domains_that_only_contain_goldapple_name(self) -> None:
        with self.assertRaises(ValueError):
            normalize_goldapple_url(
                "https://notgoldapple.ru/99000117702-myhome-irn-004"
            )


class SchedulerTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _product(target_price: float) -> Product:
        return Product(
            id=7,
            user_id=42,
            url="https://goldapple.ru/broken-url",
            item_id="99000117702",
            name="ROMBICA myHome IRN-004",
            target_price=target_price,
            current_price=3499,
            last_notified_price=None,
            created_at="2026-07-01T00:00:00+00:00",
        )

    @staticmethod
    def _info(price: float) -> ProductInfo:
        return ProductInfo(
            item_id="99000117702",
            name="ROMBICA myHome IRN-004",
            brand="ROMBICA",
            url="https://goldapple.ru/99000117702-myhome-irn-004",
            price=price,
            old_price=None,
            has_discount=False,
        )

    async def test_uses_database_item_id_and_repairs_url(self) -> None:
        product = self._product(target_price=100)
        info = self._info(price=90)
        gold_parser = SimpleNamespace(
            get_product_by_item_id=AsyncMock(return_value=info)
        )
        bot = SimpleNamespace(send_message=AsyncMock())

        with (
            patch("scheduler.database.get_all_products", AsyncMock(return_value=[product])),
            patch("scheduler.database.update_product_price", AsyncMock()) as update,
            patch("scheduler.asyncio.sleep", AsyncMock()),
        ):
            await check_prices(bot, gold_parser)

        gold_parser.get_product_by_item_id.assert_awaited_once_with(
            product.item_id, product.url
        )
        bot.send_message.assert_awaited_once()
        update.assert_awaited_once_with(
            product.id,
            info.price,
            last_notified_price=info.price,
            update_notified=True,
            url=info.url,
        )
        self.assertIn(info.url, bot.send_message.await_args.args[1])

    async def test_does_not_notify_when_price_equals_target(self) -> None:
        product = self._product(target_price=100)
        info = self._info(price=100)
        gold_parser = SimpleNamespace(
            get_product_by_item_id=AsyncMock(return_value=info)
        )
        bot = SimpleNamespace(send_message=AsyncMock())

        with (
            patch("scheduler.database.get_all_products", AsyncMock(return_value=[product])),
            patch("scheduler.database.update_product_price", AsyncMock()) as update,
            patch("scheduler.asyncio.sleep", AsyncMock()),
        ):
            await check_prices(bot, gold_parser)

        bot.send_message.assert_not_awaited()
        update.assert_awaited_once_with(product.id, info.price, url=info.url)


if __name__ == "__main__":
    unittest.main()
