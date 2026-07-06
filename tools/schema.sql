SET client_encoding = 'UTF8';

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
);

CREATE INDEX IF NOT EXISTS idx_products_user_id ON products(user_id);
