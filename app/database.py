import os
import hashlib
import secrets
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP

from pydantic import ValidationError
from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    MetaData,
    Numeric,
    String,
    Table,
    Text,
    Column,
    UniqueConstraint,
    case,
    delete,
    desc,
    func,
    select,
    text,
    update,
)
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

from app.ai_actions import ACTION_TYPE_CREATE_TRANSACTION, TransactionActionPayload

metadata = MetaData()
DEFAULT_PROJECT_NAME = "Фінансовий огляд ремонту 1-кімнатної квартири в ЖК Нова Англія"

users = Table(
    "users",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("telegram_id", BigInteger, nullable=False, unique=True),
    Column("username", String(255)),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)

projects = Table(
    "projects",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("user_id", BigInteger, ForeignKey("users.id"), nullable=False),
    Column("name", String(150), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    UniqueConstraint("user_id", "name", name="uq_projects_user_name"),
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
    Column("project_id", BigInteger, ForeignKey("projects.id"), nullable=False),
    Column("category_id", BigInteger, ForeignKey("categories.id"), nullable=False),
    Column("amount", Numeric(12, 2), nullable=False),
    Column("exchange_rate", Numeric(10, 4)),
    Column("amount_usd", Numeric(12, 2)),
    Column("transaction_type", String(20), nullable=False, server_default="expense"),
    Column("description", String(255)),
    Column("source_action_id", String(36)),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint(
        "transaction_type IN ('income', 'expense')",
        name="ck_transactions_transaction_type",
    ),
    CheckConstraint(
        "exchange_rate IS NULL OR exchange_rate > 0",
        name="ck_transactions_exchange_rate_positive",
    ),
)

web_login_codes = Table(
    "web_login_codes",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("telegram_id", BigInteger, nullable=False, index=True),
    Column("code_hash", String(64), nullable=False),
    Column("attempts", Integer, nullable=False, server_default="0"),
    Column("consumed_at", DateTime(timezone=True)),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)

web_sessions = Table(
    "web_sessions",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("telegram_id", BigInteger, nullable=False, index=True),
    Column("token_hash", String(64), nullable=False, unique=True),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)

ai_analysis_cache = Table(
    "ai_analysis_cache",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("project_id", BigInteger, ForeignKey("projects.id"), nullable=False),
    Column("ledger_hash", String(64), nullable=False),
    Column("prompt_version", String(100), nullable=False),
    Column("analysis", JSON, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    UniqueConstraint(
        "project_id",
        "ledger_hash",
        "prompt_version",
        name="uq_ai_analysis_cache_project_ledger_prompt",
    ),
)

ai_analysis_metrics = Table(
    "ai_analysis_metrics",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("project_id", BigInteger, ForeignKey("projects.id"), nullable=False),
    Column("ledger_hash", String(64), nullable=False),
    Column("prompt_version", String(100), nullable=False),
    Column("model", String(100)),
    Column("result_source", String(20), nullable=False),
    Column("status", String(20), nullable=False),
    Column("input_tokens", Integer),
    Column("output_tokens", Integer),
    Column("thought_tokens", Integer),
    Column("cached_tokens", Integer),
    Column("latency_ms", Integer),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Index("ix_ai_analysis_metrics_project_created", "project_id", "created_at"),
)

ai_chat_threads = Table(
    "ai_chat_threads",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("user_id", BigInteger, ForeignKey("users.id"), nullable=False),
    Column("project_id", BigInteger, ForeignKey("projects.id"), nullable=False),
    Column("title", String(120), nullable=False),
    Column("memory_summary", Text),
    Column("summary_message_count", Integer, nullable=False, server_default="0"),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Index("ix_ai_chat_threads_project_updated", "project_id", "updated_at"),
    Index("ix_ai_chat_threads_expiry", "expires_at"),
)

api_rate_limit_windows = Table(
    "api_rate_limit_windows",
    metadata,
    Column("scope", String(50), nullable=False),
    Column("subject_hash", String(64), nullable=False),
    Column("window_started_at", DateTime(timezone=True), nullable=False),
    Column("request_count", Integer, nullable=False, server_default="0"),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    UniqueConstraint("scope", "subject_hash", "window_started_at", name="uq_api_rate_limit_window"),
    Index("ix_api_rate_limit_windows_expiry", "window_started_at"),
)

pending_ai_actions = Table(
    "pending_ai_actions",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("user_id", BigInteger, ForeignKey("users.id"), nullable=False),
    Column("project_id", BigInteger, ForeignKey("projects.id"), nullable=False),
    Column("thread_id", String(36), nullable=False),
    Column("action_type", String(50), nullable=False),
    Column("payload", JSON, nullable=False),
    Column("payload_hash", String(64), nullable=False),
    Column("status", String(20), nullable=False, server_default="pending"),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("confirmed_at", DateTime(timezone=True)),
    Column("cancelled_at", DateTime(timezone=True)),
    CheckConstraint(
        "status IN ('pending', 'processing', 'confirmed', 'cancelled', 'failed')",
        name="ck_pending_ai_actions_status",
    ),
    Index("ix_pending_ai_actions_project_status", "project_id", "status", "created_at"),
    Index("ix_pending_ai_actions_thread_status", "thread_id", "status"),
    Index(
        "uq_pending_ai_actions_active_payload",
        "thread_id",
        "action_type",
        "payload_hash",
        unique=True,
        postgresql_where=text("status = 'pending'"),
    ),
)

ai_action_audit_log = Table(
    "ai_action_audit_log",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("user_id", BigInteger, ForeignKey("users.id"), nullable=False),
    Column("project_id", BigInteger, ForeignKey("projects.id"), nullable=False),
    Column("thread_id", String(36), nullable=False),
    Column("action_id", String(36), ForeignKey("pending_ai_actions.id"), nullable=False),
    Column("action_type", String(50), nullable=False),
    Column("event", String(30), nullable=False),
    Column("result", JSON, nullable=False, server_default=text("'{}'::json")),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Index("ix_ai_action_audit_log_project_created", "project_id", "created_at"),
    Index("ix_ai_action_audit_log_action", "action_id", "created_at"),
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
                        ALTER TABLE transactions
                            ADD COLUMN IF NOT EXISTS exchange_rate NUMERIC(10, 4);
                        ALTER TABLE transactions
                            ADD COLUMN IF NOT EXISTS amount_usd NUMERIC(12, 2);
                        ALTER TABLE transactions
                            ADD COLUMN IF NOT EXISTS project_id BIGINT;
                        ALTER TABLE transactions
                            ADD COLUMN IF NOT EXISTS source_action_id VARCHAR(36);
                    END IF;

                    IF to_regclass('ai_chat_threads') IS NOT NULL THEN
                        ALTER TABLE ai_chat_threads
                            ADD COLUMN IF NOT EXISTS memory_summary TEXT;
                        ALTER TABLE ai_chat_threads
                            ADD COLUMN IF NOT EXISTS summary_message_count INTEGER NOT NULL DEFAULT 0;
                    END IF;
                END $$;
                """
            )
        )
        await connection.run_sync(metadata.create_all)
        await connection.execute(
            text(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS uq_transactions_source_action_id
                    ON transactions (source_action_id)
                    WHERE source_action_id IS NOT NULL
                """
            )
        )

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
                INSERT INTO schema_migrations (name)
                VALUES ('project_scoping_v1')
                ON CONFLICT (name) DO NOTHING
                """
            )
        )
        await connection.execute(
            text(
                """
                INSERT INTO projects (user_id, name)
                SELECT id, :project_name FROM users
                ON CONFLICT (user_id, name) DO NOTHING
                """
            ),
            {"project_name": DEFAULT_PROJECT_NAME},
        )
        await connection.execute(
            text(
                """
                UPDATE transactions AS transaction
                SET project_id = project.id
                FROM projects AS project
                WHERE transaction.project_id IS NULL
                  AND project.user_id = transaction.user_id
                  AND project.name = :project_name
                """
            ),
            {"project_name": DEFAULT_PROJECT_NAME},
        )
        await connection.execute(
            text(
                """
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM pg_constraint
                        WHERE conrelid = 'transactions'::regclass
                          AND conname = 'fk_transactions_project'
                    ) THEN
                        ALTER TABLE transactions
                            ADD CONSTRAINT fk_transactions_project
                            FOREIGN KEY (project_id) REFERENCES projects(id);
                    END IF;
                    ALTER TABLE transactions ALTER COLUMN project_id SET NOT NULL;
                END $$;
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
                          AND conname = 'ck_transactions_exchange_rate_positive'
                    ) THEN
                        ALTER TABLE transactions
                            ADD CONSTRAINT ck_transactions_exchange_rate_positive
                            CHECK (exchange_rate IS NULL OR exchange_rate > 0);
                    END IF;
                END $$;
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


async def _save_transaction_in_connection(
    connection: AsyncConnection,
    *,
    user_id: int,
    amount: Decimal,
    main_category_name: str,
    subcategory_name: str,
    description: str,
    transaction_type: str,
    exchange_rate: Decimal | None = None,
    created_at: datetime | None = None,
    project_id: int,
    source_action_id: str | None = None,
) -> dict[str, object]:
    """Create one transaction using an existing DB transaction."""
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

    transaction_values: dict[str, object] = {
        "user_id": user_id,
        "project_id": project_id,
        "category_id": subcategory_id,
        "amount": amount,
        "transaction_type": transaction_type,
        "description": description,
    }
    if source_action_id is not None:
        transaction_values["source_action_id"] = source_action_id
    if exchange_rate is not None:
        transaction_values["exchange_rate"] = exchange_rate
        transaction_values["amount_usd"] = (amount / exchange_rate).quantize(
            Decimal("0.01"),
            rounding=ROUND_HALF_UP,
        )
    if created_at is not None:
        transaction_values["created_at"] = created_at

    result = await connection.execute(
        insert(transactions)
        .values(**transaction_values)
        .returning(
            transactions.c.id,
            transactions.c.user_id,
            transactions.c.project_id,
            transactions.c.category_id,
            transactions.c.transaction_type,
            transactions.c.amount,
            transactions.c.exchange_rate,
            transactions.c.amount_usd,
            transactions.c.description,
            transactions.c.created_at,
        )
    )
    transaction = dict(result.mappings().one())
    transaction["main_category"] = main_category_name
    transaction["subcategory"] = subcategory_name
    return transaction


async def save_transaction(
    engine: AsyncEngine,
    telegram_id: int,
    username: str | None,
    amount: Decimal,
    main_category_name: str,
    subcategory_name: str,
    description: str,
    transaction_type: str,
    exchange_rate: Decimal | None = None,
    created_at: datetime | None = None,
    project_id: int | None = None,
) -> dict[str, object]:
    """Create an income or expense in a two-level category hierarchy."""
    async with engine.begin() as connection:
        user_id = await connection.scalar(
            insert(users)
            .values(telegram_id=telegram_id, username=username)
            .on_conflict_do_update(
                index_elements=[users.c.telegram_id],
                set_={
                    "username": func.coalesce(
                        insert(users).excluded.username,
                        users.c.username,
                    )
                },
            )
            .returning(users.c.id)
        )
        if project_id is None:
            project_id = await connection.scalar(
                insert(projects)
                .values(user_id=user_id, name=DEFAULT_PROJECT_NAME)
                .on_conflict_do_update(
                    index_elements=[projects.c.user_id, projects.c.name],
                    set_={"name": DEFAULT_PROJECT_NAME},
                )
                .returning(projects.c.id)
            )
        return await _save_transaction_in_connection(
            connection,
            user_id=int(user_id),
            amount=amount,
            main_category_name=main_category_name,
            subcategory_name=subcategory_name,
            description=description,
            transaction_type=transaction_type,
            exchange_rate=exchange_rate,
            created_at=created_at,
            project_id=int(project_id),
        )


def _hash_auth_value(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


async def consume_api_rate_limit(
    engine: AsyncEngine,
    *,
    scope: str,
    subject: str,
    limit: int,
    window: timedelta,
) -> int | None:
    """Consume one persistent fixed-window quota unit and return retry seconds if blocked."""
    if limit <= 0 or window.total_seconds() <= 0:
        raise ValueError("Rate limit configuration must be positive.")
    now = datetime.now(timezone.utc)
    window_seconds = int(window.total_seconds())
    window_start = datetime.fromtimestamp(
        int(now.timestamp()) - (int(now.timestamp()) % window_seconds),
        tz=timezone.utc,
    )
    async with engine.begin() as connection:
        result = await connection.execute(
            insert(api_rate_limit_windows)
            .values(
                scope=scope,
                subject_hash=_hash_auth_value(subject),
                window_started_at=window_start,
                request_count=1,
                updated_at=now,
            )
            .on_conflict_do_update(
                index_elements=[
                    api_rate_limit_windows.c.scope,
                    api_rate_limit_windows.c.subject_hash,
                    api_rate_limit_windows.c.window_started_at,
                ],
                set_={
                    "request_count": api_rate_limit_windows.c.request_count + 1,
                    "updated_at": now,
                },
                where=api_rate_limit_windows.c.request_count < limit,
            )
            .returning(api_rate_limit_windows.c.request_count)
        )
        if result.scalar_one_or_none() is not None:
            return None
    return max(1, int((window_start + window - now).total_seconds()))


async def create_web_login_code(engine: AsyncEngine, telegram_id: int) -> str:
    """Create a one-time six-digit web login code for a Telegram user."""
    code = f"{secrets.randbelow(1_000_000):06d}"
    now = datetime.now(timezone.utc)
    async with engine.begin() as connection:
        await connection.execute(
            update(web_login_codes)
            .where(
                web_login_codes.c.telegram_id == telegram_id,
                web_login_codes.c.consumed_at.is_(None),
            )
            .values(consumed_at=now)
        )
        await connection.execute(
            insert(web_login_codes).values(
                telegram_id=telegram_id,
                code_hash=_hash_auth_value(code),
                expires_at=now + timedelta(minutes=5),
            )
        )
    return code


async def verify_web_login_code(engine: AsyncEngine, telegram_id: int, code: str) -> bool:
    """Consume a valid code atomically; five incorrect attempts invalidate it."""
    now = datetime.now(timezone.utc)
    async with engine.begin() as connection:
        result = await connection.execute(
            select(web_login_codes)
            .where(
                web_login_codes.c.telegram_id == telegram_id,
                web_login_codes.c.consumed_at.is_(None),
            )
            .order_by(desc(web_login_codes.c.created_at), desc(web_login_codes.c.id))
            .limit(1)
            .with_for_update()
        )
        login_code = result.mappings().one_or_none()
        if (
            login_code is None
            or login_code["expires_at"] <= now
            or login_code["attempts"] >= 5
        ):
            return False

        if secrets.compare_digest(login_code["code_hash"], _hash_auth_value(code)):
            await connection.execute(
                update(web_login_codes)
                .where(web_login_codes.c.id == login_code["id"])
                .values(consumed_at=now)
            )
            return True

        await connection.execute(
            update(web_login_codes)
            .where(web_login_codes.c.id == login_code["id"])
            .values(attempts=login_code["attempts"] + 1)
        )
        return False


async def create_web_session(engine: AsyncEngine, telegram_id: int) -> str:
    """Create a seven-day web session and return its opaque browser token."""
    token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    async with engine.begin() as connection:
        await connection.execute(delete(web_sessions).where(web_sessions.c.expires_at <= now))
        await connection.execute(
            insert(web_sessions).values(
                telegram_id=telegram_id,
                token_hash=_hash_auth_value(token),
                expires_at=now + timedelta(days=7),
            )
        )
    return token


async def get_web_session_telegram_id(engine: AsyncEngine, token: str) -> int | None:
    """Return the Telegram user for a non-expired browser session token."""
    statement = select(web_sessions.c.telegram_id).where(
        web_sessions.c.token_hash == _hash_auth_value(token),
        web_sessions.c.expires_at > datetime.now(timezone.utc),
    )
    async with engine.connect() as connection:
        telegram_id = await connection.scalar(statement)
        return int(telegram_id) if telegram_id is not None else None


async def get_telegram_user_profile(
    engine: AsyncEngine,
    telegram_id: int,
) -> dict[str, object] | None:
    """Return the display data stored for one Telegram user."""
    statement = select(users.c.telegram_id, users.c.username).where(users.c.telegram_id == telegram_id)
    async with engine.connect() as connection:
        result = await connection.execute(statement)
        row = result.mappings().one_or_none()
        return dict(row) if row is not None else None


async def delete_web_session(engine: AsyncEngine, token: str) -> None:
    """Revoke the browser session identified by its opaque token."""
    async with engine.begin() as connection:
        await connection.execute(
            delete(web_sessions).where(web_sessions.c.token_hash == _hash_auth_value(token))
        )


async def list_user_projects(engine: AsyncEngine, telegram_id: int) -> list[dict[str, object]]:
    """Return all finance projects available to one Telegram user."""
    statement = (
        select(projects.c.id, projects.c.name, projects.c.created_at)
        .select_from(projects.join(users))
        .where(users.c.telegram_id == telegram_id)
        .order_by(projects.c.created_at, projects.c.id)
    )
    async with engine.connect() as connection:
        result = await connection.execute(statement)
        return [dict(row) for row in result.mappings().all()]


async def get_user_project(
    engine: AsyncEngine,
    telegram_id: int,
    project_id: int,
) -> dict[str, object] | None:
    """Return a project only when it belongs to the supplied Telegram user."""
    statement = (
        select(projects.c.id, projects.c.name, projects.c.created_at)
        .select_from(projects.join(users))
        .where(users.c.telegram_id == telegram_id, projects.c.id == project_id)
    )
    async with engine.connect() as connection:
        result = await connection.execute(statement)
        row = result.mappings().one_or_none()
        return dict(row) if row is not None else None


async def create_user_project(
    engine: AsyncEngine,
    telegram_id: int,
    name: str,
) -> dict[str, object] | None:
    """Create a project for one Telegram user, or return None for a duplicate name."""
    async with engine.begin() as connection:
        user_id = await connection.scalar(
            insert(users)
            .values(telegram_id=telegram_id)
            .on_conflict_do_nothing(index_elements=[users.c.telegram_id])
            .returning(users.c.id)
        )
        if user_id is None:
            user_id = await connection.scalar(
                select(users.c.id).where(users.c.telegram_id == telegram_id)
            )
        result = await connection.execute(
            insert(projects)
            .values(user_id=user_id, name=name)
            .on_conflict_do_nothing(index_elements=[projects.c.user_id, projects.c.name])
            .returning(projects.c.id, projects.c.name, projects.c.created_at)
        )
        row = result.mappings().one_or_none()
        return dict(row) if row is not None else None


async def get_default_project_id(engine: AsyncEngine, telegram_id: int) -> int | None:
    """Return the project used by Telegram bot commands."""
    statement = (
        select(projects.c.id)
        .select_from(projects.join(users))
        .where(users.c.telegram_id == telegram_id, projects.c.name == DEFAULT_PROJECT_NAME)
    )
    async with engine.connect() as connection:
        project_id = await connection.scalar(statement)
        return int(project_id) if project_id is not None else None


async def get_transactions(
    engine: AsyncEngine,
    telegram_id: int,
    project_id: int,
    start_date: date | None = None,
    end_date: date | None = None,
) -> list[dict[str, object]]:
    """Return one project's transactions, newest first, with category hierarchy."""
    main_categories = categories.alias("main_categories")
    statement = (
        select(
            transactions.c.id,
            transactions.c.user_id,
            transactions.c.project_id,
            transactions.c.category_id,
            transactions.c.transaction_type,
            main_categories.c.name.label("main_category"),
            categories.c.name.label("subcategory"),
            transactions.c.amount,
            transactions.c.exchange_rate,
            transactions.c.amount_usd,
            transactions.c.description,
            transactions.c.created_at,
        )
        .select_from(
            transactions.join(users).join(
                categories,
                transactions.c.category_id == categories.c.id,
            ).outerjoin(
                main_categories,
                categories.c.parent_id == main_categories.c.id,
            )
        )
        .where(users.c.telegram_id == telegram_id, transactions.c.project_id == project_id)
        .order_by(desc(transactions.c.created_at), desc(transactions.c.id))
    )

    if start_date is not None:
        statement = statement.where(
            transactions.c.created_at >= datetime.combine(start_date, datetime.min.time(), tzinfo=timezone.utc)
        )
    if end_date is not None:
        statement = statement.where(
            transactions.c.created_at < datetime.combine(
                end_date + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc
            )
        )

    async with engine.connect() as connection:
        result = await connection.execute(statement)
        return [dict(row) for row in result.mappings().all()]


async def create_ai_chat_thread(
    engine: AsyncEngine,
    telegram_id: int,
    project_id: int,
    thread_id: str,
    title: str,
    expires_at: datetime,
) -> dict[str, object]:
    """Store only the ownership and lifecycle metadata for one AI chat."""
    async with engine.begin() as connection:
        user_id = await connection.scalar(
            select(users.c.id).where(users.c.telegram_id == telegram_id)
        )
        if user_id is None:
            raise RuntimeError("Cannot create an AI chat for an unknown user.")
        result = await connection.execute(
            insert(ai_chat_threads)
            .values(
                id=thread_id,
                user_id=user_id,
                project_id=project_id,
                title=title,
                expires_at=expires_at,
            )
            .returning(
                ai_chat_threads.c.id,
                ai_chat_threads.c.title,
                ai_chat_threads.c.created_at,
                ai_chat_threads.c.updated_at,
                ai_chat_threads.c.expires_at,
            )
        )
        return dict(result.mappings().one())


async def get_ai_chat_thread(
    engine: AsyncEngine,
    telegram_id: int,
    project_id: int,
    thread_id: str,
) -> dict[str, object] | None:
    """Return a non-expired chat only when it belongs to this user and project."""
    statement = (
        select(
            ai_chat_threads.c.id,
            ai_chat_threads.c.title,
            ai_chat_threads.c.created_at,
            ai_chat_threads.c.updated_at,
            ai_chat_threads.c.expires_at,
            ai_chat_threads.c.memory_summary,
            ai_chat_threads.c.summary_message_count,
        )
        .select_from(ai_chat_threads.join(users))
        .where(
            users.c.telegram_id == telegram_id,
            ai_chat_threads.c.project_id == project_id,
            ai_chat_threads.c.id == thread_id,
            ai_chat_threads.c.expires_at > datetime.now(timezone.utc),
        )
    )
    async with engine.connect() as connection:
        row = (await connection.execute(statement)).mappings().one_or_none()
        return dict(row) if row is not None else None


async def list_ai_chat_threads(
    engine: AsyncEngine,
    telegram_id: int,
    project_id: int,
) -> list[dict[str, object]]:
    """List active chats; message contents stay in the LangGraph checkpoint."""
    statement = (
        select(
            ai_chat_threads.c.id,
            ai_chat_threads.c.title,
            ai_chat_threads.c.created_at,
            ai_chat_threads.c.updated_at,
            ai_chat_threads.c.expires_at,
        )
        .select_from(ai_chat_threads.join(users))
        .where(
            users.c.telegram_id == telegram_id,
            ai_chat_threads.c.project_id == project_id,
            ai_chat_threads.c.expires_at > datetime.now(timezone.utc),
        )
        .order_by(desc(ai_chat_threads.c.updated_at), desc(ai_chat_threads.c.created_at))
    )
    async with engine.connect() as connection:
        result = await connection.execute(statement)
        return [dict(row) for row in result.mappings().all()]


async def touch_ai_chat_thread(
    engine: AsyncEngine,
    telegram_id: int,
    project_id: int,
    thread_id: str,
    expires_at: datetime,
) -> bool:
    """Renew the seven-day retention window for an owned active chat."""
    async with engine.begin() as connection:
        result = await connection.execute(
            update(ai_chat_threads)
            .where(
                ai_chat_threads.c.id == thread_id,
                ai_chat_threads.c.project_id == project_id,
                ai_chat_threads.c.user_id
                == select(users.c.id).where(users.c.telegram_id == telegram_id).scalar_subquery(),
                ai_chat_threads.c.expires_at > datetime.now(timezone.utc),
            )
            .values(updated_at=datetime.now(timezone.utc), expires_at=expires_at)
        )
        return result.rowcount == 1


async def save_ai_chat_memory_summary(
    engine: AsyncEngine,
    *,
    telegram_id: int,
    project_id: int,
    thread_id: str,
    summary: str,
    message_count: int,
) -> bool:
    """Persist a compact, owned memory only when it covers more chat messages."""
    now = datetime.now(timezone.utc)
    async with engine.begin() as connection:
        result = await connection.execute(
            update(ai_chat_threads)
            .where(
                ai_chat_threads.c.id == thread_id,
                ai_chat_threads.c.project_id == project_id,
                ai_chat_threads.c.user_id
                == select(users.c.id).where(users.c.telegram_id == telegram_id).scalar_subquery(),
                ai_chat_threads.c.expires_at > now,
                ai_chat_threads.c.summary_message_count < message_count,
            )
            .values(
                memory_summary=summary,
                summary_message_count=message_count,
                updated_at=now,
            )
        )
        return result.rowcount == 1


async def list_expired_ai_chat_thread_ids(engine: AsyncEngine) -> list[str]:
    """Return expired checkpoint IDs before deleting their metadata."""
    now = datetime.now(timezone.utc)
    async with engine.connect() as connection:
        result = await connection.execute(
            select(ai_chat_threads.c.id).where(ai_chat_threads.c.expires_at <= now)
        )
        return [str(thread_id) for thread_id in result.scalars().all()]


async def delete_ai_chat_threads(engine: AsyncEngine, thread_ids: list[str]) -> None:
    """Remove metadata only after the related checkpoints were cleared."""
    if not thread_ids:
        return
    async with engine.begin() as connection:
        await connection.execute(delete(ai_chat_threads).where(ai_chat_threads.c.id.in_(thread_ids)))


async def create_pending_ai_action(
    engine: AsyncEngine,
    *,
    telegram_id: int,
    project_id: int,
    thread_id: str,
    action_id: str,
    action_type: str,
    payload: dict[str, object],
    payload_hash: str,
    expires_at: datetime,
) -> dict[str, object]:
    """Persist an AI proposal only; this function never changes financial data."""
    async with engine.begin() as connection:
        user_id = await connection.scalar(select(users.c.id).where(users.c.telegram_id == telegram_id))
        if user_id is None:
            raise RuntimeError("Cannot create a pending action for an unknown user.")

        # The partial unique index covers every pending row, including an expired
        # one. Retire a matching expired proposal before inserting a fresh one.
        # That keeps a user from being permanently blocked after the 24-hour TTL.
        now = datetime.now(timezone.utc)
        await connection.execute(
            update(pending_ai_actions)
            .where(
                pending_ai_actions.c.user_id == user_id,
                pending_ai_actions.c.project_id == project_id,
                pending_ai_actions.c.thread_id == thread_id,
                pending_ai_actions.c.action_type == action_type,
                pending_ai_actions.c.payload_hash == payload_hash,
                pending_ai_actions.c.status == "pending",
                pending_ai_actions.c.expires_at <= now,
            )
            .values(status="failed", updated_at=now)
        )

        existing = await connection.execute(
            select(pending_ai_actions)
            .where(
                pending_ai_actions.c.user_id == user_id,
                pending_ai_actions.c.project_id == project_id,
                pending_ai_actions.c.thread_id == thread_id,
                pending_ai_actions.c.action_type == action_type,
                pending_ai_actions.c.payload_hash == payload_hash,
                pending_ai_actions.c.status == "pending",
                pending_ai_actions.c.expires_at > now,
            )
            .limit(1)
        )
        existing_action = existing.mappings().one_or_none()
        if existing_action is not None:
            return dict(existing_action)

        result = await connection.execute(
            insert(pending_ai_actions)
            .values(
                id=action_id,
                user_id=user_id,
                project_id=project_id,
                thread_id=thread_id,
                action_type=action_type,
                payload=payload,
                payload_hash=payload_hash,
                expires_at=expires_at,
            )
            .on_conflict_do_nothing(
                index_elements=[
                    pending_ai_actions.c.thread_id,
                    pending_ai_actions.c.action_type,
                    pending_ai_actions.c.payload_hash,
                ],
                index_where=text("status = 'pending'"),
            )
            .returning(pending_ai_actions)
        )
        inserted_action = result.mappings().one_or_none()
        if inserted_action is None:
            existing = await connection.execute(
                select(pending_ai_actions)
                .where(
                    pending_ai_actions.c.user_id == user_id,
                    pending_ai_actions.c.project_id == project_id,
                    pending_ai_actions.c.thread_id == thread_id,
                    pending_ai_actions.c.action_type == action_type,
                    pending_ai_actions.c.payload_hash == payload_hash,
                    pending_ai_actions.c.status == "pending",
                    pending_ai_actions.c.expires_at > now,
                )
                .limit(1)
            )
            existing_action = existing.mappings().one_or_none()
            if existing_action is None:
                raise RuntimeError("Could not create or retrieve the pending action.")
            return dict(existing_action)

        action = dict(inserted_action)
        await connection.execute(
            insert(ai_action_audit_log).values(
                user_id=user_id,
                project_id=project_id,
                thread_id=thread_id,
                action_id=action_id,
                action_type=action_type,
                event="pending_created",
                result={"status": "pending"},
            )
        )
        return action


async def list_pending_ai_actions(
    engine: AsyncEngine,
    *,
    telegram_id: int,
    project_id: int,
    thread_id: str,
) -> list[dict[str, object]]:
    """Return only active proposals visible to the action's owner."""
    statement = (
        select(pending_ai_actions)
        .select_from(pending_ai_actions.join(users))
        .where(
            users.c.telegram_id == telegram_id,
            pending_ai_actions.c.project_id == project_id,
            pending_ai_actions.c.thread_id == thread_id,
            pending_ai_actions.c.status == "pending",
            pending_ai_actions.c.expires_at > datetime.now(timezone.utc),
        )
        .order_by(pending_ai_actions.c.created_at)
    )
    async with engine.connect() as connection:
        result = await connection.execute(statement)
        return [dict(row) for row in result.mappings().all()]


async def _get_transaction_by_source_action(
    connection: AsyncConnection,
    source_action_id: str,
) -> dict[str, object] | None:
    main_categories = categories.alias("main_categories")
    result = await connection.execute(
        select(
            transactions.c.id,
            transactions.c.user_id,
            transactions.c.project_id,
            transactions.c.category_id,
            transactions.c.transaction_type,
            main_categories.c.name.label("main_category"),
            categories.c.name.label("subcategory"),
            transactions.c.amount,
            transactions.c.exchange_rate,
            transactions.c.amount_usd,
            transactions.c.description,
            transactions.c.created_at,
        )
        .select_from(
            transactions.join(categories, transactions.c.category_id == categories.c.id).outerjoin(
                main_categories,
                categories.c.parent_id == main_categories.c.id,
            )
        )
        .where(transactions.c.source_action_id == source_action_id)
    )
    transaction = result.mappings().one_or_none()
    return dict(transaction) if transaction is not None else None


async def confirm_pending_create_transaction_action(
    engine: AsyncEngine,
    *,
    telegram_id: int,
    project_id: int,
    action_id: str,
) -> tuple[dict[str, object] | None, dict[str, object] | None, bool]:
    """Atomically execute an owned pending transaction action exactly once.

    The returned boolean is true when a previous successful confirmation is replayed.
    A malformed action is marked failed and returned without a transaction.
    """
    now = datetime.now(timezone.utc)
    async with engine.begin() as connection:
        result = await connection.execute(
            select(pending_ai_actions)
            .where(
                pending_ai_actions.c.id == action_id,
                pending_ai_actions.c.project_id == project_id,
                pending_ai_actions.c.user_id
                == select(users.c.id).where(users.c.telegram_id == telegram_id).scalar_subquery(),
            )
            .with_for_update()
        )
        action_row = result.mappings().one_or_none()
        if action_row is None:
            return None, None, False
        action = dict(action_row)

        if action["status"] == "confirmed":
            transaction = await _get_transaction_by_source_action(connection, action_id)
            if transaction is None:
                raise RuntimeError("Confirmed AI action has no source transaction.")
            return action, transaction, True

        if action["status"] != "pending" or action["expires_at"] <= now:
            return None, None, False
        if action["action_type"] != ACTION_TYPE_CREATE_TRANSACTION:
            await connection.execute(
                update(pending_ai_actions)
                .where(pending_ai_actions.c.id == action_id, pending_ai_actions.c.status == "pending")
                .values(status="failed", updated_at=now)
            )
            await connection.execute(
                insert(ai_action_audit_log).values(
                    user_id=action["user_id"],
                    project_id=action["project_id"],
                    thread_id=action["thread_id"],
                    action_id=action_id,
                    action_type=action["action_type"],
                    event="failed",
                    result={"reason": "unsupported_action_type"},
                )
            )
            action.update(status="failed", updated_at=now)
            return action, None, False

        try:
            payload = TransactionActionPayload.model_validate(action["payload"])
        except ValidationError as error:
            await connection.execute(
                update(pending_ai_actions)
                .where(pending_ai_actions.c.id == action_id, pending_ai_actions.c.status == "pending")
                .values(status="failed", updated_at=now)
            )
            await connection.execute(
                insert(ai_action_audit_log).values(
                    user_id=action["user_id"],
                    project_id=action["project_id"],
                    thread_id=action["thread_id"],
                    action_id=action_id,
                    action_type=action["action_type"],
                    event="failed",
                    result={"reason": "invalid_payload"},
                )
            )
            action.update(status="failed", updated_at=now)
            return action, None, False

        await connection.execute(
            insert(ai_action_audit_log).values(
                user_id=action["user_id"],
                project_id=action["project_id"],
                thread_id=action["thread_id"],
                action_id=action_id,
                action_type=action["action_type"],
                event="confirm_requested",
                result={"status": "processing"},
            )
        )
        transaction = await _save_transaction_in_connection(
            connection,
            user_id=int(action["user_id"]),
            project_id=int(action["project_id"]),
            amount=payload.amount,
            main_category_name=payload.category,
            subcategory_name=payload.subcategory,
            description=payload.description,
            transaction_type=payload.type,
            exchange_rate=payload.exchange_rate,
            created_at=datetime.combine(payload.date, datetime.min.time(), tzinfo=timezone.utc),
            source_action_id=action_id,
        )
        await connection.execute(
            update(pending_ai_actions)
            .where(pending_ai_actions.c.id == action_id, pending_ai_actions.c.status == "pending")
            .values(status="confirmed", confirmed_at=now, updated_at=now)
        )
        await connection.execute(
            insert(ai_action_audit_log).values(
                user_id=action["user_id"],
                project_id=action["project_id"],
                thread_id=action["thread_id"],
                action_id=action_id,
                action_type=action["action_type"],
                event="confirmed",
                result={"transaction_id": int(transaction["id"])},
            )
        )
        action.update(status="confirmed", confirmed_at=now, updated_at=now)
        return action, transaction, False


async def claim_pending_ai_action(
    engine: AsyncEngine,
    *,
    telegram_id: int,
    project_id: int,
    action_id: str,
    target_status: str,
) -> dict[str, object] | None:
    """Atomically move one unexpired pending action to processing or cancelled."""
    if target_status not in {"processing", "cancelled"}:
        raise ValueError("Invalid pending action target status.")
    now = datetime.now(timezone.utc)
    values: dict[str, object] = {"status": target_status, "updated_at": now}
    if target_status == "cancelled":
        values["cancelled_at"] = now

    async with engine.begin() as connection:
        result = await connection.execute(
            update(pending_ai_actions)
            .where(
                pending_ai_actions.c.id == action_id,
                pending_ai_actions.c.project_id == project_id,
                pending_ai_actions.c.user_id
                == select(users.c.id).where(users.c.telegram_id == telegram_id).scalar_subquery(),
                pending_ai_actions.c.status == "pending",
                pending_ai_actions.c.expires_at > now,
            )
            .values(**values)
            .returning(pending_ai_actions)
        )
        action = result.mappings().one_or_none()
        return dict(action) if action is not None else None


async def finalize_pending_ai_action(
    engine: AsyncEngine,
    *,
    action_id: str,
    status: str,
) -> bool:
    """Finish an already claimed action after business validation and execution."""
    if status not in {"confirmed", "failed"}:
        raise ValueError("Invalid final pending action status.")
    values: dict[str, object] = {"status": status, "updated_at": datetime.now(timezone.utc)}
    if status == "confirmed":
        values["confirmed_at"] = datetime.now(timezone.utc)
    async with engine.begin() as connection:
        result = await connection.execute(
            update(pending_ai_actions)
            .where(pending_ai_actions.c.id == action_id, pending_ai_actions.c.status == "processing")
            .values(**values)
        )
        return result.rowcount == 1


async def record_ai_action_audit(
    engine: AsyncEngine,
    *,
    user_id: int,
    project_id: int,
    thread_id: str,
    action_id: str,
    action_type: str,
    event: str,
    result: dict[str, object],
) -> None:
    """Write an immutable, secret-free record of a controlled AI action event."""
    async with engine.begin() as connection:
        await connection.execute(
            insert(ai_action_audit_log).values(
                user_id=user_id,
                project_id=project_id,
                thread_id=thread_id,
                action_id=action_id,
                action_type=action_type,
                event=event,
                result=result,
            )
        )


async def get_cached_ai_analysis(
    engine: AsyncEngine,
    project_id: int,
    ledger_hash: str,
    prompt_version: str,
) -> dict[str, object] | None:
    """Return a narrative generated for exactly this project, ledger, and prompt."""
    statement = (
        select(ai_analysis_cache.c.analysis, ai_analysis_cache.c.created_at)
        .where(
            ai_analysis_cache.c.project_id == project_id,
            ai_analysis_cache.c.ledger_hash == ledger_hash,
            ai_analysis_cache.c.prompt_version == prompt_version,
        )
        .limit(1)
    )
    async with engine.connect() as connection:
        row = (await connection.execute(statement)).mappings().one_or_none()
        return dict(row) if row is not None else None


async def save_cached_ai_analysis(
    engine: AsyncEngine,
    project_id: int,
    ledger_hash: str,
    prompt_version: str,
    analysis: dict[str, object],
) -> None:
    """Persist a validated Gemini narrative for future identical requests."""
    statement = insert(ai_analysis_cache).values(
        project_id=project_id,
        ledger_hash=ledger_hash,
        prompt_version=prompt_version,
        analysis=analysis,
    ).on_conflict_do_update(
        constraint="uq_ai_analysis_cache_project_ledger_prompt",
        set_={"analysis": analysis, "updated_at": func.now()},
    )
    async with engine.begin() as connection:
        await connection.execute(statement)


async def record_ai_analysis_metric(
    engine: AsyncEngine,
    *,
    project_id: int,
    ledger_hash: str,
    prompt_version: str,
    result_source: str,
    status: str,
    model: str | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    thought_tokens: int | None = None,
    cached_tokens: int | None = None,
    latency_ms: int | None = None,
) -> None:
    """Store usage and latency for cost monitoring without storing prompt data."""
    async with engine.begin() as connection:
        await connection.execute(
            insert(ai_analysis_metrics).values(
                project_id=project_id,
                ledger_hash=ledger_hash,
                prompt_version=prompt_version,
                model=model,
                result_source=result_source,
                status=status,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                thought_tokens=thought_tokens,
                cached_tokens=cached_tokens,
                latency_ms=latency_ms,
            )
        )


async def get_financial_summary(
    engine: AsyncEngine,
    telegram_id: int,
    project_id: int,
) -> dict[str, Decimal]:
    """Calculate totals and balance for one Telegram user's project."""
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
        statement = (
            select(total_income, total_expense)
            .select_from(transactions.join(users))
            .where(users.c.telegram_id == telegram_id, transactions.c.project_id == project_id)
        )
        result = await connection.execute(statement)
        row = result.mappings().one()
        income = Decimal(row["total_income"])
        expense = Decimal(row["total_expense"])
        return {
            "total_income": income,
            "total_expense": expense,
            "balance": income - expense,
        }


async def get_user_transactions(
    engine: AsyncEngine,
    telegram_id: int,
    limit: int = 10,
) -> list[dict[str, object]]:
    """Return recent transactions for the Telegram bot's default project."""
    project_id = await get_default_project_id(engine, telegram_id)
    if project_id is None:
        return []
    main_categories = categories.alias("main_categories")
    statement = (
        select(
            transactions.c.id,
            transactions.c.transaction_type,
            main_categories.c.name.label("main_category"),
            categories.c.name.label("subcategory"),
            transactions.c.amount,
            transactions.c.exchange_rate,
            transactions.c.amount_usd,
            transactions.c.description,
            transactions.c.created_at,
        )
        .select_from(
            transactions.join(users).join(
                categories,
                transactions.c.category_id == categories.c.id,
            ).outerjoin(
                main_categories,
                categories.c.parent_id == main_categories.c.id,
            )
        )
        .where(users.c.telegram_id == telegram_id, transactions.c.project_id == project_id)
        .order_by(desc(transactions.c.created_at), desc(transactions.c.id))
        .limit(limit)
    )

    async with engine.connect() as connection:
        result = await connection.execute(statement)
        return [dict(row) for row in result.mappings().all()]


async def get_user_transaction(
    engine: AsyncEngine,
    telegram_id: int,
    transaction_id: int,
) -> dict[str, object] | None:
    """Return one transaction only from the Telegram bot's default project."""
    project_id = await get_default_project_id(engine, telegram_id)
    if project_id is None:
        return None
    main_categories = categories.alias("main_categories")
    statement = (
        select(
            transactions.c.id,
            transactions.c.transaction_type,
            main_categories.c.name.label("main_category"),
            categories.c.name.label("subcategory"),
            transactions.c.amount,
            transactions.c.exchange_rate,
            transactions.c.amount_usd,
            transactions.c.description,
        )
        .select_from(
            transactions.join(users).join(
                categories,
                transactions.c.category_id == categories.c.id,
            ).outerjoin(
                main_categories,
                categories.c.parent_id == main_categories.c.id,
            )
        )
        .where(
            users.c.telegram_id == telegram_id,
            transactions.c.id == transaction_id,
            transactions.c.project_id == project_id,
        )
    )

    async with engine.connect() as connection:
        result = await connection.execute(statement)
        row = result.mappings().one_or_none()
        return dict(row) if row is not None else None


async def delete_user_transaction(
    engine: AsyncEngine,
    telegram_id: int,
    transaction_id: int,
    project_id: int | None = None,
) -> bool:
    """Delete a transaction only when it belongs to the supplied user's project."""
    if project_id is None:
        project_id = await get_default_project_id(engine, telegram_id)
    if project_id is None:
        return False
    user_id_statement = select(users.c.id).where(users.c.telegram_id == telegram_id).scalar_subquery()
    statement = (
        delete(transactions)
        .where(
            transactions.c.id == transaction_id,
            transactions.c.user_id == user_id_statement,
            transactions.c.project_id == project_id,
        )
        .returning(transactions.c.id)
    )

    async with engine.begin() as connection:
        deleted_transaction_id = await connection.scalar(statement)
        return deleted_transaction_id is not None
