from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from database import Database


@dataclass
class Product:
    id: int
    user_id: int
    url: str
    item_id: str
    name: str
    target_price: float
    current_price: Optional[float]
    last_notified_price: Optional[float]
    created_at: str


PRODUCT_COLUMNS = (
    "id",
    "user_id",
    "url",
    "item_id",
    "name",
    "target_price",
    "current_price",
    "last_notified_price",
    "created_at",
)


def _row_to_product(row: tuple) -> Product:
    data = dict(zip(PRODUCT_COLUMNS, row))
    return Product(
        id=int(data["id"]),
        user_id=int(data["user_id"]),
        url=data["url"],
        item_id=data["item_id"],
        name=data["name"],
        target_price=float(data["target_price"]),
        current_price=float(data["current_price"]) if data["current_price"] is not None else None,
        last_notified_price=(
            float(data["last_notified_price"])
            if data["last_notified_price"] is not None
            else None
        ),
        created_at=(
            data["created_at"].isoformat()
            if isinstance(data["created_at"], datetime)
            else str(data["created_at"])
        ),
    )


class dbApi:
    def __init__(self, dbname, user, password, host, port):
        self.db = Database(dbname, user, password, host, port)

    def init_schema(self) -> None:
        self.db.execute_query(
            """
            CREATE TABLE IF NOT EXISTS products (
                id                  BIGSERIAL PRIMARY KEY,
                user_id             BIGINT NOT NULL,
                url                 TEXT NOT NULL,
                item_id             TEXT NOT NULL,
                name                TEXT NOT NULL DEFAULT '',
                target_price        NUMERIC(12, 2) NOT NULL,
                current_price       NUMERIC(12, 2),
                last_notified_price NUMERIC(12, 2),
                created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE (user_id, item_id)
            )
            """
        )
        self.db.execute_query(
            """
            CREATE INDEX IF NOT EXISTS idx_products_user_id
            ON products(user_id)
            """
        )

    def add_product(
        self,
        user_id: int,
        url: str,
        item_id: str,
        name: str,
        target_price: float,
        current_price: Optional[float],
    ) -> Product:
        rows = self.db.execute_query(
            """
            INSERT INTO products (
                user_id, url, item_id, name, target_price, current_price
            )
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (user_id, item_id) DO UPDATE SET
                url = EXCLUDED.url,
                name = EXCLUDED.name,
                target_price = EXCLUDED.target_price,
                current_price = EXCLUDED.current_price
            RETURNING id, user_id, url, item_id, name, target_price,
                      current_price, last_notified_price, created_at
            """,
            (user_id, url, item_id, name, target_price, current_price),
        )
        if not rows:
            raise RuntimeError("Failed to add product")
        return _row_to_product(rows[0])

    def get_user_products(self, user_id: int) -> list[Product]:
        rows = self.db.execute_query(
            """
            SELECT id, user_id, url, item_id, name, target_price,
                   current_price, last_notified_price, created_at
            FROM products
            WHERE user_id = %s
            ORDER BY created_at DESC
            """,
            (user_id,),
        )
        if not rows:
            return []
        return [_row_to_product(row) for row in rows]

    def get_all_products(self) -> list[Product]:
        rows = self.db.execute_query(
            """
            SELECT id, user_id, url, item_id, name, target_price,
                   current_price, last_notified_price, created_at
            FROM products
            ORDER BY id
            """
        )
        if not rows:
            return []
        return [_row_to_product(row) for row in rows]

    def get_product(self, product_id: int, user_id: int) -> Optional[Product]:
        rows = self.db.execute_query(
            """
            SELECT id, user_id, url, item_id, name, target_price,
                   current_price, last_notified_price, created_at
            FROM products
            WHERE id = %s AND user_id = %s
            """,
            (product_id, user_id),
        )
        if not rows:
            return None
        return _row_to_product(rows[0])

    def delete_product(self, product_id: int, user_id: int) -> bool:
        rows = self.db.execute_query(
            """
            DELETE FROM products
            WHERE id = %s AND user_id = %s
            RETURNING id
            """,
            (product_id, user_id),
        )
        return bool(rows)

    def update_product_price(
        self,
        product_id: int,
        current_price: float,
        last_notified_price: Optional[float] = None,
        *,
        update_notified: bool = False,
    ) -> None:
        if update_notified:
            self.db.execute_query(
                """
                UPDATE products
                SET current_price = %s, last_notified_price = %s
                WHERE id = %s
                """,
                (current_price, last_notified_price, product_id),
            )
        else:
            self.db.execute_query(
                """
                UPDATE products
                SET current_price = %s
                WHERE id = %s
                """,
                (current_price, product_id),
            )

    def close(self) -> None:
        self.db.close_pool()

    def __del__(self):
        self.close()
