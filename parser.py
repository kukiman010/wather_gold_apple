from __future__ import annotations

import asyncio
import re
import time
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
API_BLOCK_COOLDOWN_SECONDS = 15 * 60


class ApiResponseError(RuntimeError):
    def __init__(self, status: int) -> None:
        self.status = status
        super().__init__(f"API status {status}")


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
    hostname = (parsed.hostname or "").lower()
    if hostname != "goldapple.ru" and not hostname.endswith(".goldapple.ru"):
        raise ValueError("Ссылка должна вести на goldapple.ru")
    item_id = extract_item_id(url)
    if not item_id:
        raise ValueError("Не удалось найти ID товара в ссылке")
    path = parsed.path.rstrip("/") or f"/{item_id}"
    return f"https://goldapple.ru{path}"


def _choose_canonical_url(item_id: str, *candidates: Optional[str]) -> str:
    """Возвращает первый безопасный URL, который ведёт на нужный товар."""
    for candidate in candidates:
        if not candidate:
            continue
        if candidate.startswith("/"):
            candidate = f"https://goldapple.ru{candidate}"
        try:
            normalized = normalize_goldapple_url(candidate)
        except ValueError:
            continue
        if extract_item_id(normalized) == item_id:
            return normalized
    return f"https://goldapple.ru/{item_id}"


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
        self._api_blocked_until = 0.0

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
        context = self._context
        browser = self._browser
        playwright = self._playwright
        self._browser = None
        self._context = None
        self._page = None
        self._playwright = None
        self._session_ready = False

        for resource in (context, browser, playwright):
            if resource is None:
                continue
            try:
                if resource is playwright:
                    await resource.stop()
                else:
                    await resource.close()
            except Exception:
                # Ресурс уже мог закрыться при остановке драйвера или процесса.
                pass

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

    async def _renew_session(self, navigation_url: str) -> None:
        await self.stop()
        await self.start()
        await self._ensure_session()
        assert self._page is not None
        await self._page.goto(navigation_url, wait_until="load", timeout=90_000)
        for _ in range(20):
            title = (await self._page.title()).lower()
            if "checking device" not in title:
                return
            await self._page.wait_for_timeout(2_000)
        raise RuntimeError("Не удалось обновить сессию Gold Apple")

    async def _fetch_from_api(
        self, item_id: str, navigation_url: str, retries: int = 3
    ) -> dict:
        assert self._page is not None
        last_error: Exception | None = None

        for attempt in range(retries):
            try:
                result = await self._page.evaluate(
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
                            return { ok: false, status: response.status };
                        }
                        return { ok: true, payload: await response.json() };
                    }
                    """,
                    {"itemId": item_id, "cityId": CITY_ID},
                )
                if result.get("ok"):
                    return result["payload"]

                status = int(result.get("status") or 0)
                last_error = ApiResponseError(status)
                if attempt < retries - 1:
                    if status in (401, 403):
                        if attempt > 0:
                            self._api_blocked_until = (
                                time.monotonic() + API_BLOCK_COOLDOWN_SECONDS
                            )
                            raise last_error
                        await self._renew_session(navigation_url)
                    else:
                        await self._page.wait_for_timeout(2_000)
                        await self._page.reload(wait_until="load", timeout=90_000)
                continue
            except ApiResponseError:
                raise
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

        if isinstance(last_error, ApiResponseError):
            raise last_error
        raise RuntimeError(f"Не удалось получить данные товара: {last_error}")

    async def _get_product_from_page(
        self, item_id: str, navigation_url: str
    ) -> ProductInfo:
        assert self._page is not None
        price_locator = self._page.locator(
            '[data-test-id="price"] meta[itemprop="price"][content]'
        ).first
        if await price_locator.count() == 0:
            raise ValueError("Цена не найдена на странице товара")

        price_value = await price_locator.get_attribute("content")
        if not price_value:
            raise ValueError("Цена не найдена на странице товара")

        name_locator = self._page.locator("h1").first
        name = (
            (await name_locator.inner_text()).strip()
            if await name_locator.count()
            else item_id
        )
        canonical_url = _choose_canonical_url(
            item_id,
            self._page.url,
            navigation_url,
        )
        return ProductInfo(
            item_id=item_id,
            name=name,
            brand="",
            url=canonical_url,
            price=float(price_value.replace(",", ".")),
            old_price=None,
            has_discount=False,
        )

    async def get_product(self, url: str) -> ProductInfo:
        normalized_url = normalize_goldapple_url(url)
        item_id = extract_item_id(normalized_url)
        if not item_id:
            raise ValueError("Некорректная ссылка на товар")
        return await self.get_product_by_item_id(item_id, normalized_url)

    async def get_product_by_item_id(
        self, item_id: str, url: Optional[str] = None
    ) -> ProductInfo:
        if not item_id.isdigit():
            raise ValueError("Некорректный ID товара")

        navigation_url = _choose_canonical_url(item_id, url)
        async with self._lock:
            await self._ensure_session()
            assert self._page is not None

            await self._page.goto(navigation_url, wait_until="load", timeout=90_000)
            for _ in range(20):
                title = (await self._page.title()).lower()
                if "checking device" not in title:
                    break
                await self._page.wait_for_timeout(2_000)

            await self._page.wait_for_timeout(1_500)
            canonical_href = await self._page.evaluate(
                """
                () => {
                    const element = document.querySelector('link[rel="canonical"]');
                    return element ? element.href : null;
                }
                """
            )
            if time.monotonic() < self._api_blocked_until:
                try:
                    return await self._get_product_from_page(
                        item_id, navigation_url
                    )
                except ValueError:
                    pass

            try:
                payload = await self._fetch_from_api(item_id, navigation_url)
            except ApiResponseError as exc:
                if exc.status not in (401, 403):
                    raise
                try:
                    return await self._get_product_from_page(
                        item_id, navigation_url
                    )
                except ValueError:
                    raise exc
            data = payload.get("data") or {}
            variants = data.get("variants") or []
            if not variants:
                raise ValueError("Товар не найден")

            variant = next(
                (v for v in variants if v.get("itemId") == item_id),
                variants[0],
            )
            canonical_url = _choose_canonical_url(
                item_id,
                variant.get("url"),
                data.get("url"),
                canonical_href,
                self._page.url,
                navigation_url,
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
                url=canonical_url,
                price=current_price,
                old_price=old_price,
                has_discount=has_discount,
            )


parser = GoldAppleParser()
