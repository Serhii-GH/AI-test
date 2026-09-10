import os
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    MetaData,
    Numeric,
    String,
    Table,
    Column,
    UniqueConstraint,
    case,
    desc,
    func,
    select,
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
    Column("parent_id", BigInteger, ForeignKey("categories.id")),
    Column("name", String(100), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    UniqueConstraint("user_id", "parent_id", "name", name="uq_categories_user_parent_name"),
    Index(
        "uq_main_categories_user_name",
        "user_id",
        "name",
        unique=True,
        postgresql_where=text("parent_id IS NULL"),
    ),
)

transactions = Table(
    "transactions",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("user_id", BigInteger, ForeignKey("users.id"), nullable=False),
    Column("category_id", BigInteger, ForeignKey("categories.id"), nullable=False),
    Column("amount", Numeric(12, 2), nullable=False),
    Column("transaction_type", String(20), nullable=False, server_default="expense"),
    Column("description", String(255)),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint(
        "transaction_type IN ('income', 'expense')",
        name="ck_transactions_transaction_type",
    ),
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
    """Create tables and upgrade the category hierarchy when necessary."""
    async with engine.begin() as connection:
        # `create_all` does not add columns to a table created by an earlier
        # application version. Add the column first so existing databases can
        # safely receive the new hierarchy.
        await connection.execute(
            text(
                """
                DO $$
                BEGIN
                    IF to_regclass('categories') IS NOT NULL THEN
                        ALTER TABLE categories ADD COLUMN IF NOT EXISTS parent_id BIGINT;
                    END IF;

                    IF to_regclass('transactions') IS NOT NULL THEN
                        ALTER TABLE transactions
                            ADD COLUMN IF NOT EXISTS transaction_type VARCHAR(20)
                            NOT NULL DEFAULT 'expense';
                    END IF;
                END $$;
                """
            )
        )
        await connection.run_sync(metadata.create_all)

        # The previous version stored expenses in a flat category structure.
        # This one-time migration intentionally removes those old transactions,
        # then records completion so transactions added after the upgrade remain.
        await connection.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    name VARCHAR(100) PRIMARY KEY,
                    applied_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now()
                )
                """
            )
        )
        await connection.execute(
            text(
                """
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1
                        FROM pg_constraint
                        WHERE conrelid = 'transactions'::regclass
                          AND conname = 'ck_transactions_transaction_type'
                    ) THEN
                        ALTER TABLE transactions
                            ADD CONSTRAINT ck_transactions_transaction_type
                            CHECK (transaction_type IN ('income', 'expense'));
                    END IF;
                END $$;
                """
            )
        )
        migration_name = "category_hierarchy_v1"
        migration_applied = await connection.scalar(
            text(
                """
                INSERT INTO schema_migrations (name)
                VALUES (:migration_name)
                ON CONFLICT (name) DO NOTHING
                RETURNING name
                """
            ),
            {"migration_name": migration_name},
        )
        if migration_applied is not None:
            await connection.execute(text("DELETE FROM transactions"))
        await connection.execute(
            text(
                """
                DO $$
                DECLARE legacy_constraint text;
                BEGIN
                    IF to_regclass('categories') IS NULL THEN
                        RETURN;
                    END IF;

                    IF NOT EXISTS (
                        SELECT 1
                        FROM pg_constraint
                        WHERE conrelid = 'categories'::regclass
                          AND contype = 'f'
                          AND pg_get_constraintdef(oid) LIKE
                              'FOREIGN KEY (parent_id) REFERENCES categories(id)%'
                    ) THEN
                        ALTER TABLE categories
                            ADD CONSTRAINT fk_categories_parent
                            FOREIGN KEY (parent_id) REFERENCES categories(id);
                    END IF;

                    FOR legacy_constraint IN
                        SELECT conname
                        FROM pg_constraint
                        WHERE conrelid = 'categories'::regclass
                          AND contype = 'u'
                          AND pg_get_constraintdef(oid) = 'UNIQUE (user_id, name)'
                    LOOP
                        EXECUTE format(
                            'ALTER TABLE categories DROP CONSTRAINT %I',
                            legacy_constraint
                        );
                    END LOOP;

                    IF NOT EXISTS (
                        SELECT 1
                        FROM pg_constraint
                        WHERE conrelid = 'categories'::regclass
                          AND conname = 'uq_categories_user_parent_name'
                    ) THEN
                        ALTER TABLE categories
                            ADD CONSTRAINT uq_categories_user_parent_name
                            UNIQUE (user_id, parent_id, name);
                    END IF;

                    CREATE UNIQUE INDEX IF NOT EXISTS uq_main_categories_user_name
                        ON categories (user_id, name)
                        WHERE parent_id IS NULL;
                END $$;
                """
            )
        )


async def save_expense(
    engine: AsyncEngine,
    telegram_id: int,
    username: str | None,
    amount: Decimal,
    main_category_name: str,
    subcategory_name: str,
) -> None:
    """Create an expense in a two-level category hierarchy."""
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

        main_category_id = await connection.scalar(
            insert(categories)
            .values(user_id=user_id, parent_id=None, name=main_category_name)
            .on_conflict_do_update(
                index_elements=[categories.c.user_id, categories.c.name],
                index_where=categories.c.parent_id.is_(None),
                set_={"name": main_category_name},
            )
            .returning(categories.c.id)
        )

        subcategory_id = await connection.scalar(
            insert(categories)
            .values(
                user_id=user_id,
                parent_id=main_category_id,
                name=subcategory_name,
            )
            .on_conflict_do_update(
                index_elements=[
                    categories.c.user_id,
                    categories.c.parent_id,
                    categories.c.name,
                ],
                set_={"name": subcategory_name},
            )
            .returning(categories.c.id)
        )

        await connection.execute(
            insert(transactions).values(
                user_id=user_id,
                category_id=subcategory_id,
                amount=amount,
                transaction_type="expense",
            )
        )


async def get_transactions(engine: AsyncEngine) -> list[dict[str, object]]:
    """Return all transactions, newest first, with their category hierarchy."""
    main_categories = categories.alias("main_categories")
    statement = (
        select(
            transactions.c.id,
            transactions.c.user_id,
            transactions.c.category_id,
            main_categories.c.name.label("main_category"),
            categories.c.name.label("subcategory"),
            transactions.c.amount,
            transactions.c.description,
            transactions.c.created_at,
        )
        .select_from(
            transactions.join(categories).outerjoin(
                main_categories,
                categories.c.parent_id == main_categories.c.id,
            )
        )
        .order_by(desc(transactions.c.created_at), desc(transactions.c.id))
    )

    async with engine.connect() as connection:
        result = await connection.execute(statement)
        return [dict(row) for row in result.mappings().all()]


async def get_financial_summary(engine: AsyncEngine) -> dict[str, Decimal]:
    """Calculate totals and balance from all income and expense transactions."""
    total_income = func.coalesce(
        func.sum(
            case(
                (transactions.c.transaction_type == "income", transactions.c.amount),
                else_=0,
            )
        ),
        0,
    ).label("total_income")
    total_expense = func.coalesce(
        func.sum(
            case(
                (transactions.c.transaction_type == "expense", transactions.c.amount),
                else_=0,
            )
        ),
        0,
    ).label("total_expense")

    async with engine.connect() as connection:
        result = await connection.execute(select(total_income, total_expense))
        row = result.mappings().one()
        income = Decimal(row["total_income"])
        expense = Decimal(row["total_expense"])
        return {
            "total_income": income,
            "total_expense": expense,
            "balance": income - expense,
        }
