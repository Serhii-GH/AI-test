import os
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    MetaData,
    Numeric,
    String,
    Table,
    Column,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

metadata = MetaData()

users = Table(
    "users",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("telegram_id", BigInteger, nullable=False, unique=True),
    Column("username", String(255)),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)

categories = Table(
    "categories",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("user_id", BigInteger, ForeignKey("users.id"), nullable=False),
    Column("name", String(100), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    UniqueConstraint("user_id", "name"),
)

transactions = Table(
    "transactions",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("user_id", BigInteger, ForeignKey("users.id"), nullable=False),
    Column("category_id", BigInteger, ForeignKey("categories.id"), nullable=False),
    Column("amount", Numeric(12, 2), nullable=False),
    Column("description", String(255)),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)


def get_database_url() -> URL:
    """Read DATABASE_URL and adapt a standard PostgreSQL URL for asyncpg."""
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("Змінна середовища DATABASE_URL не встановлена.")

    url = make_url(database_url)
    if url.drivername in {"postgres", "postgresql"}:
        url = url.set(drivername="postgresql+asyncpg")
    elif url.drivername != "postgresql+asyncpg":
        raise RuntimeError("DATABASE_URL має бути рядком підключення PostgreSQL.")

    # Neon often provides libpq parameters. asyncpg expects `ssl` instead.
    query = dict(url.query)
    if "sslmode" in query:
        query.setdefault("ssl", query.pop("sslmode"))
    query.pop("channel_binding", None)
    return url.set(query=query)


def create_database_engine() -> AsyncEngine:
    return create_async_engine(get_database_url(), pool_pre_ping=True)


async def check_database_connection(engine: AsyncEngine) -> None:
    """Raise an exception when PostgreSQL is unavailable."""
    async with engine.connect() as connection:
        await connection.execute(text("SELECT 1"))


async def initialize_database(engine: AsyncEngine) -> None:
    """Create the application's tables when they do not exist yet."""
    async with engine.begin() as connection:
        await connection.run_sync(metadata.create_all)


async def save_expense(
    engine: AsyncEngine,
    telegram_id: int,
    username: str | None,
    amount: Decimal,
    category_name: str,
) -> None:
    """Create a user's expense and its category if needed."""
    async with engine.begin() as connection:
        user_id = await connection.scalar(
            insert(users)
            .values(telegram_id=telegram_id, username=username)
            .on_conflict_do_update(
                index_elements=[users.c.telegram_id],
                set_={"username": username},
            )
            .returning(users.c.id)
        )

        category_id = await connection.scalar(
            insert(categories)
            .values(user_id=user_id, name=category_name)
            .on_conflict_do_update(
                index_elements=[categories.c.user_id, categories.c.name],
                set_={"name": category_name},
            )
            .returning(categories.c.id)
        )

        await connection.execute(
            insert(transactions).values(
                user_id=user_id,
                category_id=category_id,
                amount=amount,
            )
        )
