from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse

from playwright.async_api import Browser, BrowserContext, Page, async_playwright

from config import CITY_ID

ITEM_ID_RE = re.compile(r"goldapple\.ru/(\d+)", re.IGNORECASE)
GOLDAPPLE_URL_RE = re.compile(
    r"https?://(?:www\.)?goldapple\.ru/\S+",
    re.IGNORECASE,
)


@dataclass
class ProductInfo:
    item_id: str
    name: str
    brand: str
    url: str
    price: float
    old_price: Optional[float]
    has_discount: bool


def extract_item_id(url: str) -> Optional[str]:
    match = ITEM_ID_RE.search(url)
    return match.group(1) if match else None


def extract_goldapple_url(text: str) -> Optional[str]:
    """Извлекает ссылку на товар из текста (в т.ч. «название + ссылка» при шаринге)."""
    match = GOLDAPPLE_URL_RE.search(text)
    return match.group(0).rstrip(".,;!?") if match else None


def normalize_goldapple_url(url: str) -> str:
    parsed = urlparse(url.strip())
    if "goldapple.ru" not in parsed.netloc.lower():
        raise ValueError("Ссылка должна вести на goldapple.ru")
    item_id = extract_item_id(url)
    if not item_id:
        raise ValueError("Не удалось найти ID товара в ссылке")
    path = parsed.path.rstrip("/") or f"/{item_id}"
    return f"https://goldapple.ru{path}"


def _money_value(block: Optional[dict]) -> Optional[float]:
    if not block:
        return None
    amount = block.get("amount")
    denominator = block.get("denominator") or 1
    if amount is None:
        return None
    return float(amount) / float(denominator)


def parse_price_block(price_block: dict) -> tuple[float, Optional[float], bool]:
    view_options = price_block.get("viewOptions") or {}
    price_type = view_options.get("type", "actual")

    if price_type == "bestLoyalty" and price_block.get("loyalty"):
        current = _money_value(price_block["loyalty"])
        old_price = _money_value(price_block.get("regular"))
    else:
        current = _money_value(price_block.get("actual"))
        old_price = _money_value(price_block.get("old"))

    if current is None:
        current = _money_value(price_block.get("discount")) or _money_value(
            price_block.get("regular")
        )

    if current is None:
        raise ValueError("Цена не найдена в ответе API")

    if old_price is None:
        old_price = _money_value(price_block.get("old"))

    has_discount = bool(
        view_options.get("crossPrice")
        or view_options.get("useDiscount")
        or (old_price and old_price > current)
    )
    return current, old_price, has_discount


class GoldAppleParser:
    def __init__(self) -> None:
        self._playwright = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None
        self._lock = asyncio.Lock()
        self._session_ready = False

    async def start(self) -> None:
        if self._browser:
            return
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        self._context = await self._browser.new_context(locale="ru-RU")
        await self._context.add_init_script(
            'Object.defineProperty(navigator, "webdriver", {get: () => undefined})'
        )
        self._page = await self._context.new_page()

    async def stop(self) -> None:
        if self._context:
            await self._context.close()
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()
        self._browser = None
        self._context = None
        self._page = None
        self._playwright = None
        self._session_ready = False

    async def _ensure_session(self) -> None:
        if not self._page:
            await self.start()
        assert self._page is not None

        if self._session_ready:
            return

        await self._page.goto("https://goldapple.ru/", wait_until="load", timeout=90_000)
        for _ in range(45):
            title = (await self._page.title()).lower()
            if "checking device" not in title:
                self._session_ready = True
                return
            await self._page.wait_for_timeout(2_000)

        raise RuntimeError("Не удалось пройти проверку сайта Gold Apple")

    async def _fetch_from_api(self, item_id: str, retries: int = 3) -> dict:
        assert self._page is not None
        last_error: Exception | None = None

        for attempt in range(retries):
            try:
                return await self._page.evaluate(
                    """
                    async ({ itemId, cityId }) => {
                        const apiUrl =
                            "https://goldapple.ru/front/api/catalog/product-card/base/v2"
                            + "?locale=ru&itemId=" + itemId
                            + "&customerGroupId=0&cityId=" + cityId;
                        const response = await fetch(apiUrl, {
                            credentials: "include",
                            headers: { Accept: "application/json" },
                        });
                        if (!response.ok) {
                            throw new Error("API status " + response.status);
                        }
                        return await response.json();
                    }
                    """,
                    {"itemId": item_id, "cityId": CITY_ID},
                )
            except Exception as exc:
                last_error = exc
                if attempt < retries - 1:
                    await self._page.wait_for_timeout(2_000)
                    await self._page.reload(wait_until="load", timeout=90_000)
                    for _ in range(15):
                        title = (await self._page.title()).lower()
                        if "checking device" not in title:
                            break
                        await self._page.wait_for_timeout(2_000)

        raise RuntimeError(f"Не удалось получить данные товара: {last_error}")

    async def get_product(self, url: str) -> ProductInfo:
        async with self._lock:
            normalized_url = normalize_goldapple_url(url)
            item_id = extract_item_id(normalized_url)
            if not item_id:
                raise ValueError("Некорректная ссылка на товар")

            await self._ensure_session()
            assert self._page is not None

            await self._page.goto(normalized_url, wait_until="load", timeout=90_000)
            for _ in range(20):
                title = (await self._page.title()).lower()
                if "checking device" not in title:
                    break
                await self._page.wait_for_timeout(2_000)

            await self._page.wait_for_timeout(1_500)
            payload = await self._fetch_from_api(item_id)
            data = payload.get("data") or {}
            variants = data.get("variants") or []
            if not variants:
                raise ValueError("Товар не найден")

            variant = next(
                (v for v in variants if v.get("itemId") == item_id),
                variants[0],
            )
            price_block = variant.get("price")
            if not price_block:
                raise ValueError("Цена не найдена")

            current_price, old_price, has_discount = parse_price_block(price_block)
            brand = data.get("brand") or ""
            name = data.get("name") or item_id
            full_name = f"{brand} {name}".strip()

            return ProductInfo(
                item_id=item_id,
                name=full_name,
                brand=brand,
                url=normalized_url,
                price=current_price,
                old_price=old_price,
                has_discount=has_discount,
            )


parser = GoldAppleParser()
