import asyncio
import csv
import json
import logging
from io import BytesIO, StringIO
from contextlib import asynccontextmanager
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
import os
from time import perf_counter
from typing import Annotated, Literal
from uuid import uuid4

from aiogram import Bot
from dotenv import load_dotenv
from fastapi import Cookie, Depends, FastAPI, HTTPException, Path, Query, Request, Response
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from pydantic import BaseModel, Field, ValidationError, field_validator
from sqlalchemy.ext.asyncio import AsyncEngine

from app.ai_actions import PendingActionResponse, TransactionActionPayload, pending_action_response
from app.ai_chat import (
    FinanceChatError,
    compact_chat_memory_if_needed,
    create_finance_chat_graph,
    message_content_to_text,
    serialize_visible_messages,
)
from app.database import (
    check_database_connection,
    create_ai_chat_thread,
    create_web_login_challenge,
    create_user_project,
    create_web_session,
    create_guest_access,
    create_guest_session,
    create_database_engine,
    claim_pending_ai_action,
    confirm_pending_create_transaction_action,
    consume_api_rate_limit,
    consume_web_login_challenge,
    delete_ai_chat_threads,
    delete_web_session,
    delete_guest_session,
    get_ai_chat_thread,
    get_financial_summary,
    get_cached_ai_analysis,
    get_telegram_user_profile,
    get_transactions,
    get_user_project,
    get_web_session_telegram_id,
    get_guest_session,
    initialize_database,
    list_expired_ai_chat_thread_ids,
    list_pending_ai_actions,
    list_ai_chat_threads,
    list_user_projects,
    list_guest_accesses,
    record_ai_analysis_metric,
    record_ai_action_audit,
    save_cached_ai_analysis,
    delete_user_transaction,
    save_transaction,
    save_ai_chat_memory_summary,
    revoke_guest_access,
    touch_ai_chat_thread,
    verify_web_login_code,
)
from app.gemini_analysis import (
    GeminiAnalysisError,
    GeminiTransactionAnalysis,
    TransactionAnalysisResponse,
    analyze_prepared_transactions_with_gemini,
    build_dashboard_analysis,
    estimate_prepared_analysis_tokens,
    insufficient_data_analysis,
    prepare_transaction_analysis,
)
from app.prompts.transaction_analysis import PROMPT_VERSION


logger = logging.getLogger(__name__)


class TransactionResponse(BaseModel):
    id: int
    user_id: int
    project_id: int
    category_id: int
    transaction_type: str
    main_category: str | None
    subcategory: str
    amount: Decimal
    exchange_rate: Decimal | None
    amount_usd: Decimal | None
    description: str | None
    created_at: datetime


class FinancialSummaryResponse(BaseModel):
    total_income: Decimal
    total_expense: Decimal
    balance: Decimal


class TokenEstimateResponse(BaseModel):
    input_tokens: int = Field(ge=0)
    output_token_budget: int = Field(ge=0)
    potential_total_tokens: int = Field(ge=0)
    tokenizer: str
    note: str


class ProjectResponse(BaseModel):
    id: int
    name: str
    created_at: datetime


class CreateProjectRequest(BaseModel):
    name: Annotated[str, Field(min_length=1, max_length=150)]

    @field_validator("name")
    @classmethod
    def name_must_not_be_blank(cls, value: str) -> str:
        normalized_value = value.strip()
        if not normalized_value:
            raise ValueError("project name must not be blank")
        return normalized_value


class CreateTransactionRequest(TransactionActionPayload):
    project_id: Annotated[int, Field(gt=0)]


class VerifyLoginRequest(BaseModel):
    telegram_id: Annotated[int, Field(gt=0)]
    code: Annotated[str, Field(pattern=r"^\d{6}$")]


class SessionResponse(BaseModel):
    telegram_id: int
    username: str | None


class LoginChallengeResponse(BaseModel):
    challenge: str
    telegram_url: str
    bot_username: str
    expires_in_seconds: int


class LoginChallengeStatusResponse(BaseModel):
    status: Literal["pending", "authenticated"]
    telegram_id: int | None = None
    username: str | None = None


class GuestLoginRequest(BaseModel):
    login: Annotated[str, Field(min_length=6, max_length=64)]
    password: Annotated[str, Field(min_length=12, max_length=128)]


class GuestAccessResponse(BaseModel):
    id: int
    login: str
    created_at: datetime


class CreatedGuestAccessResponse(GuestAccessResponse):
    password: str


class GuestSessionResponse(BaseModel):
    project_id: int
    project_name: str


class GuestDashboardResponse(GuestSessionResponse):
    summary: FinancialSummaryResponse
    transactions: list[TransactionResponse]


class ChatRequest(BaseModel):
    message: Annotated[str, Field(min_length=1, max_length=2_000)]
    project_id: Annotated[int, Field(gt=0)]
    thread_id: str | None = Field(default=None, min_length=1, max_length=36)

    @field_validator("message")
    @classmethod
    def message_must_not_be_blank(cls, value: str) -> str:
        normalized_value = value.strip()
        if not normalized_value:
            raise ValueError("message must not be blank")
        return normalized_value


class ChatThreadResponse(BaseModel):
    id: str
    title: str
    created_at: datetime
    updated_at: datetime
    expires_at: datetime


class ChatHistoryResponse(ChatThreadResponse):
    messages: list[dict[str, str]]
    pending_actions: list[PendingActionResponse]


class ActionExecutionResponse(BaseModel):
    action: PendingActionResponse
    transaction: TransactionResponse | None = None


SESSION_COOKIE_NAME = "admin_session"
GUEST_SESSION_COOKIE_NAME = "guest_session"
SESSION_DURATION_SECONDS = 7 * 24 * 60 * 60
CHAT_RETENTION = timedelta(days=7)
AUTH_VERIFY_LIMIT = 10
AUTH_VERIFY_WINDOW = timedelta(minutes=10)
AUTH_CHALLENGE_LIMIT = 10
AUTH_CHALLENGE_WINDOW = timedelta(minutes=10)
LOGIN_CHALLENGE_DURATION_SECONDS = 5 * 60
CHAT_REQUEST_LIMIT = 10
CHAT_REQUEST_WINDOW = timedelta(minutes=1)
ANALYSIS_REQUEST_LIMIT = 5
ANALYSIS_REQUEST_WINDOW = timedelta(minutes=10)


def session_cookie_secure() -> bool:
    return os.getenv("SESSION_COOKIE_SECURE", "false").strip().lower() in {"1", "true", "yes"}


def telegram_bot_username() -> str:
    return os.getenv("TELEGRAM_BOT_USERNAME", "my_first_131313_bot").strip().lstrip("@")


def telegram_login_url(challenge: str) -> str:
    """Build the bot deep link without exposing a bot token to the browser."""
    return f"https://t.me/{telegram_bot_username()}?start=login_{challenge}"


def set_session_cookie(response: Response, session_token: str) -> None:
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=session_token,
        max_age=SESSION_DURATION_SECONDS,
        httponly=True,
        samesite="lax",
        secure=session_cookie_secure(),
    )


def set_guest_session_cookie(response: Response, session_token: str) -> None:
    response.set_cookie(
        key=GUEST_SESSION_COOKIE_NAME, value=session_token, max_age=SESSION_DURATION_SECONDS,
        httponly=True, samesite="lax", secure=session_cookie_secure(),
    )


async def enforce_rate_limit(
    engine: AsyncEngine,
    *,
    scope: str,
    subject: str,
    limit: int,
    window: timedelta,
) -> None:
    """Raise a user-safe HTTP 429 after an atomic persistent quota is exhausted."""
    retry_after = await consume_api_rate_limit(
        engine,
        scope=scope,
        subject=subject,
        limit=limit,
        window=window,
    )
    if retry_after is not None:
        raise HTTPException(
            status_code=429,
            detail="Забагато запитів. Спробуйте ще раз трохи пізніше.",
            headers={"Retry-After": str(retry_after)},
        )


def request_client_ip(request: Request) -> str:
    """Return the direct peer address; reverse proxies must be explicitly trusted at deploy time."""
    return request.client.host if request.client is not None else "unknown"


def get_checkpoint_database_url() -> str:
    """Keep PostgreSQL's libpq URL intact for psycopg/LangGraph."""
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is not configured.")
    return database_url.replace("postgresql+asyncpg://", "postgresql://", 1)


@asynccontextmanager
async def lifespan(app: FastAPI):
    load_dotenv(override=True)
    engine = create_database_engine()
    await check_database_connection(engine)
    await initialize_database(engine)
    app.state.database_engine = engine
    try:
        async with AsyncPostgresSaver.from_conn_string(get_checkpoint_database_url()) as checkpointer:
            await checkpointer.setup()
            app.state.chat_checkpointer = checkpointer
            try:
                yield
            finally:
                del app.state.chat_checkpointer
    finally:
        await engine.dispose()


app = FastAPI(title="Apartment Renovation Finance API", lifespan=lifespan)


@app.get("/health")
async def health_check() -> dict[str, str]:
    """Report that the web service is ready to accept HTTP requests."""
    return {"status": "ok"}


async def require_authenticated_telegram_id(
    request: Request,
    session_token: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None,
) -> int:
    if not session_token:
        raise HTTPException(status_code=401, detail="Потрібна авторизація.")

    engine: AsyncEngine = request.app.state.database_engine
    telegram_id = await get_web_session_telegram_id(engine, session_token)
    if telegram_id is None:
        raise HTTPException(status_code=401, detail="Сесія недійсна або завершилася.")
    return telegram_id


async def require_guest_session(
    request: Request,
    session_token: Annotated[str | None, Cookie(alias=GUEST_SESSION_COOKIE_NAME)] = None,
) -> dict[str, object]:
    if not session_token:
        raise HTTPException(status_code=401, detail="Потрібен гостьовий доступ.")
    engine: AsyncEngine = request.app.state.database_engine
    guest = await get_guest_session(engine, session_token)
    if guest is None:
        raise HTTPException(status_code=401, detail="Гостьовий доступ недійсний або відкликаний.")
    return guest


async def require_user_project(engine: AsyncEngine, telegram_id: int, project_id: int) -> None:
    if await get_user_project(engine, telegram_id, project_id) is None:
        raise HTTPException(status_code=404, detail="Проєкт не знайдено.")


async def purge_expired_chat_threads(request: Request) -> None:
    """Remove expired metadata and the matching LangGraph checkpoints together."""
    engine: AsyncEngine = request.app.state.database_engine
    expired_thread_ids = await list_expired_ai_chat_thread_ids(engine)
    checkpointer: AsyncPostgresSaver = request.app.state.chat_checkpointer
    deleted_thread_ids: list[str] = []
    for thread_id in expired_thread_ids:
        try:
            await checkpointer.adelete_thread(thread_id)
            deleted_thread_ids.append(thread_id)
        except Exception:
            logger.exception("could not delete expired AI chat checkpoint %s", thread_id)
    await delete_ai_chat_threads(engine, deleted_thread_ids)


def sse_event(event: str, payload: dict[str, object]) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def transactions_csv(transactions: list[dict[str, object]]) -> str:
    """Serialize dashboard transactions into an Excel-friendly UTF-8 CSV."""
    output = StringIO(newline="")
    writer = csv.writer(output, delimiter=";")
    writer.writerow(
        [
            "ID",
            "Дата",
            "Тип операції",
            "Категорія",
            "Підкатегорія",
            "Опис",
            "Сума, грн",
            "Курс грн/USD",
            "Сума, USD",
        ]
    )
    for transaction in transactions:
        created_at = transaction["created_at"]
        writer.writerow(
            [
                transaction["id"],
                created_at.date().isoformat() if isinstance(created_at, datetime) else created_at,
                "Дохід" if transaction["transaction_type"] == "income" else "Витрата",
                transaction.get("main_category") or "",
                transaction["subcategory"],
                transaction.get("description") or "",
                transaction["amount"],
                transaction["exchange_rate"] if transaction.get("exchange_rate") is not None else "",
                transaction["amount_usd"] if transaction.get("amount_usd") is not None else "",
            ]
        )
    return "\ufeff" + output.getvalue()


@app.post("/api/auth/challenges", response_model=LoginChallengeResponse)
async def create_login_challenge(
    request: Request,
    response: Response,
) -> dict[str, object]:
    """Create a one-time browser challenge and its Telegram bot deep link."""
    engine: AsyncEngine = request.app.state.database_engine
    await enforce_rate_limit(
        engine,
        scope="auth_challenge_ip",
        subject=request_client_ip(request),
        limit=AUTH_CHALLENGE_LIMIT,
        window=AUTH_CHALLENGE_WINDOW,
    )
    challenge = await create_web_login_challenge(engine)
    response.headers["Cache-Control"] = "no-store"
    return {
        "challenge": challenge,
        "telegram_url": telegram_login_url(challenge),
        "bot_username": telegram_bot_username(),
        "expires_in_seconds": LOGIN_CHALLENGE_DURATION_SECONDS,
    }


@app.get(
    "/api/auth/challenges/{challenge}",
    response_model=LoginChallengeStatusResponse,
)
async def poll_login_challenge(
    request: Request,
    response: Response,
    challenge: Annotated[
        str,
        Path(min_length=43, max_length=43, pattern=r"^[A-Za-z0-9_-]+$"),
    ],
) -> dict[str, object]:
    """Poll the browser challenge and create its session after bot approval."""
    engine: AsyncEngine = request.app.state.database_engine
    state, telegram_id = await consume_web_login_challenge(engine, challenge)
    response.headers["Cache-Control"] = "no-store"
    if state == "expired":
        raise HTTPException(status_code=410, detail="Посилання для входу прострочене. Створіть нове.")
    if state == "pending":
        return {"status": "pending"}
    if telegram_id is None:
        raise HTTPException(status_code=410, detail="Посилання для входу недійсне. Створіть нове.")

    session_token = await create_web_session(engine, telegram_id)
    set_session_cookie(response, session_token)
    profile = await get_telegram_user_profile(engine, telegram_id)
    return {
        "status": "authenticated",
        "telegram_id": telegram_id,
        "username": profile.get("username") if profile else None,
    }


@app.post("/api/auth/verify", response_model=SessionResponse)
async def verify_login(
    request: Request,
    response: Response,
    credentials: VerifyLoginRequest,
) -> dict[str, object]:
    """Exchange a one-time Telegram bot code for an HttpOnly browser session."""
    engine: AsyncEngine = request.app.state.database_engine
    await enforce_rate_limit(
        engine,
        scope="auth_verify_ip",
        subject=request_client_ip(request),
        limit=AUTH_VERIFY_LIMIT,
        window=AUTH_VERIFY_WINDOW,
    )
    await enforce_rate_limit(
        engine,
        scope="auth_verify_telegram",
        subject=str(credentials.telegram_id),
        limit=AUTH_VERIFY_LIMIT,
        window=AUTH_VERIFY_WINDOW,
    )
    verified = await verify_web_login_code(engine, credentials.telegram_id, credentials.code)
    if not verified:
        raise HTTPException(status_code=401, detail="Код недійсний, прострочений або вже використаний.")

    session_token = await create_web_session(engine, credentials.telegram_id)
    set_session_cookie(response, session_token)
    profile = await get_telegram_user_profile(engine, credentials.telegram_id)
    return {"telegram_id": credentials.telegram_id, "username": profile.get("username") if profile else None}


@app.get("/api/auth/session", response_model=SessionResponse)
async def get_session(
    request: Request,
    telegram_id: Annotated[int, Depends(require_authenticated_telegram_id)],
) -> dict[str, object]:
    engine: AsyncEngine = request.app.state.database_engine
    profile = await get_telegram_user_profile(engine, telegram_id)
    return {"telegram_id": telegram_id, "username": profile.get("username") if profile else None}


@app.get("/api/auth/avatar")
async def get_telegram_avatar(
    telegram_id: Annotated[int, Depends(require_authenticated_telegram_id)],
) -> Response:
    """Proxy the authenticated user's Telegram avatar without exposing the bot token."""
    bot_token = os.getenv("BOT_TOKEN")
    if not bot_token:
        return Response(status_code=204)

    bot = Bot(token=bot_token)
    try:
        photos = await bot.get_user_profile_photos(telegram_id, limit=1)
        if not photos.photos:
            return Response(status_code=204)
        image = await bot.download(photos.photos[0][-1].file_id, destination=BytesIO())
        if image is None:
            return Response(status_code=204)
        return Response(
            content=image.getvalue(),
            media_type="image/jpeg",
            headers={"Cache-Control": "private, max-age=3600"},
        )
    except Exception:
        return Response(status_code=204)
    finally:
        await bot.session.close()


@app.post("/api/auth/logout", status_code=204)
async def logout(
    request: Request,
    response: Response,
    session_token: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None,
) -> None:
    if session_token:
        engine: AsyncEngine = request.app.state.database_engine
        await delete_web_session(engine, session_token)
    response.delete_cookie(
        key=SESSION_COOKIE_NAME,
        httponly=True,
        samesite="lax",
        secure=session_cookie_secure(),
    )


@app.post("/api/guest/login", response_model=GuestSessionResponse)
async def guest_login(request: Request, response: Response, credentials: GuestLoginRequest) -> dict[str, object]:
    engine: AsyncEngine = request.app.state.database_engine
    await enforce_rate_limit(engine, scope="guest_login", subject=request_client_ip(request), limit=10, window=timedelta(minutes=10))
    token = await create_guest_session(engine, credentials.login.strip(), credentials.password)
    if token is None:
        raise HTTPException(status_code=401, detail="Невірний логін або пароль.")
    set_guest_session_cookie(response, token)
    guest = await get_guest_session(engine, token)
    return {"project_id": guest["project_id"], "project_name": guest["project_name"]}


@app.get("/api/guest/session", response_model=GuestSessionResponse)
async def get_guest_access_session(guest: Annotated[dict[str, object], Depends(require_guest_session)]) -> dict[str, object]:
    return {"project_id": guest["project_id"], "project_name": guest["project_name"]}


@app.get("/api/guest/dashboard", response_model=GuestDashboardResponse)
async def get_guest_dashboard(
    request: Request,
    guest: Annotated[dict[str, object], Depends(require_guest_session)],
) -> dict[str, object]:
    engine: AsyncEngine = request.app.state.database_engine
    project_id = int(guest["project_id"])
    owner_id = int(guest["owner_telegram_id"])
    return {
        "project_id": project_id, "project_name": guest["project_name"],
        "summary": await get_financial_summary(engine, int(owner_id), project_id),
        "transactions": await get_transactions(engine, int(owner_id), project_id),
    }


@app.post("/api/guest/logout", status_code=204)
async def guest_logout(request: Request, response: Response) -> None:
    token = request.cookies.get(GUEST_SESSION_COOKIE_NAME)
    if token:
        await delete_guest_session(request.app.state.database_engine, token)
    response.delete_cookie(key=GUEST_SESSION_COOKIE_NAME, httponly=True, samesite="lax", secure=session_cookie_secure())


@app.get("/api/projects", response_model=list[ProjectResponse])
async def list_projects(
    request: Request,
    telegram_id: Annotated[int, Depends(require_authenticated_telegram_id)],
) -> list[dict[str, object]]:
    engine: AsyncEngine = request.app.state.database_engine
    return await list_user_projects(engine, telegram_id)


@app.post("/api/projects", response_model=ProjectResponse, status_code=201)
async def create_project(
    request: Request,
    project: CreateProjectRequest,
    telegram_id: Annotated[int, Depends(require_authenticated_telegram_id)],
) -> dict[str, object]:
    engine: AsyncEngine = request.app.state.database_engine
    created_project = await create_user_project(engine, telegram_id, project.name)
    if created_project is None:
        raise HTTPException(status_code=409, detail="Проєкт із такою назвою вже існує.")
    return created_project


@app.get("/api/projects/{project_id}/guest-accesses", response_model=list[GuestAccessResponse])
async def get_project_guest_accesses(
    request: Request, project_id: Annotated[int, Path(gt=0)],
    telegram_id: Annotated[int, Depends(require_authenticated_telegram_id)],
) -> list[dict[str, object]]:
    engine: AsyncEngine = request.app.state.database_engine
    await require_user_project(engine, telegram_id, project_id)
    return await list_guest_accesses(engine, telegram_id, project_id)


@app.post("/api/projects/{project_id}/guest-accesses", response_model=CreatedGuestAccessResponse, status_code=201)
async def create_project_guest_access(
    request: Request, project_id: Annotated[int, Path(gt=0)],
    telegram_id: Annotated[int, Depends(require_authenticated_telegram_id)],
) -> dict[str, object]:
    engine: AsyncEngine = request.app.state.database_engine
    await require_user_project(engine, telegram_id, project_id)
    access, password = await create_guest_access(engine, telegram_id, project_id)
    return {**access, "password": password}


@app.delete("/api/projects/{project_id}/guest-accesses/{access_id}", status_code=204)
async def delete_project_guest_access(
    request: Request, project_id: Annotated[int, Path(gt=0)], access_id: Annotated[int, Path(gt=0)],
    telegram_id: Annotated[int, Depends(require_authenticated_telegram_id)],
) -> None:
    engine: AsyncEngine = request.app.state.database_engine
    if not await revoke_guest_access(engine, telegram_id, project_id, access_id):
        raise HTTPException(status_code=404, detail="Гостьовий доступ не знайдено.")


@app.get("/api/transactions", response_model=list[TransactionResponse])
async def list_transactions(
    request: Request,
    project_id: Annotated[int, Query(gt=0)],
    telegram_id: Annotated[int, Depends(require_authenticated_telegram_id)],
) -> list[dict[str, object]]:
    """Return the authenticated Telegram user's transactions, newest first."""
    engine: AsyncEngine = request.app.state.database_engine
    await require_user_project(engine, telegram_id, project_id)
    return await get_transactions(engine, telegram_id, project_id)


@app.get("/api/transactions/export")
async def export_transactions_csv(
    request: Request,
    project_id: Annotated[int, Query(gt=0)],
    telegram_id: Annotated[int, Depends(require_authenticated_telegram_id)],
) -> Response:
    """Download all transactions from one owned project as a UTF-8 CSV file."""
    engine: AsyncEngine = request.app.state.database_engine
    await require_user_project(engine, telegram_id, project_id)
    transactions = await get_transactions(engine, telegram_id, project_id)
    filename = f"finance-export-project-{project_id}-{date.today().isoformat()}.csv"
    return Response(
        content=transactions_csv(transactions),
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )


@app.post("/api/transactions", response_model=TransactionResponse, status_code=201)
async def create_transaction(
    request: Request,
    transaction: CreateTransactionRequest,
    telegram_id: Annotated[int, Depends(require_authenticated_telegram_id)],
) -> dict[str, object]:
    """Validate and store a dashboard transaction for the authenticated user."""
    engine: AsyncEngine = request.app.state.database_engine
    await require_user_project(engine, telegram_id, transaction.project_id)
    created_at = datetime.combine(transaction.date, time.min, tzinfo=timezone.utc)
    return await save_transaction(
        engine=engine,
        telegram_id=telegram_id,
        username=None,
        amount=transaction.amount,
        project_id=transaction.project_id,
        exchange_rate=transaction.exchange_rate,
        main_category_name=transaction.category,
        subcategory_name=transaction.subcategory,
        description=transaction.description,
        transaction_type=transaction.type,
        created_at=created_at,
    )


@app.delete("/api/transactions/{transaction_id}", status_code=204)
async def delete_transaction(
    request: Request,
    transaction_id: Annotated[int, Path(gt=0)],
    project_id: Annotated[int, Query(gt=0)],
    telegram_id: Annotated[int, Depends(require_authenticated_telegram_id)],
) -> None:
    """Delete a transaction only when it belongs to the authenticated user."""
    engine: AsyncEngine = request.app.state.database_engine
    await require_user_project(engine, telegram_id, project_id)
    deleted = await delete_user_transaction(engine, telegram_id, transaction_id, project_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Операцію не знайдено.")


@app.get("/api/summary", response_model=FinancialSummaryResponse)
async def get_summary(
    request: Request,
    project_id: Annotated[int, Query(gt=0)],
    telegram_id: Annotated[int, Depends(require_authenticated_telegram_id)],
) -> dict[str, Decimal]:
    """Return the authenticated Telegram user's financial totals as JSON."""
    engine: AsyncEngine = request.app.state.database_engine
    await require_user_project(engine, telegram_id, project_id)
    return await get_financial_summary(engine, telegram_id, project_id)


@app.get("/api/ai/token-estimate", response_model=TokenEstimateResponse)
async def estimate_ai_tokens(
    request: Request,
    project_id: Annotated[int, Query(gt=0)],
    telegram_id: Annotated[int, Depends(require_authenticated_telegram_id)],
) -> TokenEstimateResponse:
    """Estimate the next analysis request locally without calling Gemini."""
    engine: AsyncEngine = request.app.state.database_engine
    await require_user_project(engine, telegram_id, project_id)
    transactions = await get_transactions(engine, telegram_id, project_id)
    estimate = estimate_prepared_analysis_tokens(prepare_transaction_analysis(transactions))
    return TokenEstimateResponse(
        input_tokens=estimate.input_tokens,
        output_token_budget=estimate.output_token_budget,
        potential_total_tokens=estimate.potential_total_tokens,
        tokenizer=estimate.tokenizer,
        note="Оцінка tiktoken; фактичні токени Gemini можуть відрізнятися.",
    )


@app.get("/api/ai/chat/threads", response_model=list[ChatThreadResponse])
async def list_chat_threads(
    request: Request,
    project_id: Annotated[int, Query(gt=0)],
    telegram_id: Annotated[int, Depends(require_authenticated_telegram_id)],
) -> list[dict[str, object]]:
    """List the current project's non-expired short-term conversations."""
    engine: AsyncEngine = request.app.state.database_engine
    await require_user_project(engine, telegram_id, project_id)
    await purge_expired_chat_threads(request)
    return await list_ai_chat_threads(engine, telegram_id, project_id)


@app.get("/api/ai/chat/threads/{thread_id}", response_model=ChatHistoryResponse)
async def get_chat_thread(
    request: Request,
    thread_id: Annotated[str, Path(min_length=1, max_length=36)],
    project_id: Annotated[int, Query(gt=0)],
    telegram_id: Annotated[int, Depends(require_authenticated_telegram_id)],
) -> dict[str, object]:
    """Return visible short-term history after enforcing project ownership."""
    engine: AsyncEngine = request.app.state.database_engine
    await require_user_project(engine, telegram_id, project_id)
    await purge_expired_chat_threads(request)
    thread = await get_ai_chat_thread(engine, telegram_id, project_id, thread_id)
    if thread is None:
        raise HTTPException(status_code=404, detail="Діалог не знайдено або він уже прострочений.")

    checkpointer: AsyncPostgresSaver = request.app.state.chat_checkpointer
    checkpoint = await checkpointer.aget_tuple({"configurable": {"thread_id": thread_id}})
    messages = checkpoint.checkpoint["channel_values"].get("messages", []) if checkpoint else []
    pending_actions = await list_pending_ai_actions(
        engine,
        telegram_id=telegram_id,
        project_id=project_id,
        thread_id=thread_id,
    )
    return {
        **thread,
        "messages": serialize_visible_messages(messages),
        "pending_actions": [pending_action_response(action) for action in pending_actions],
    }


@app.post("/api/ai/actions/{action_id}/confirm", response_model=ActionExecutionResponse)
async def confirm_ai_action(
    request: Request,
    action_id: Annotated[str, Path(min_length=1, max_length=36)],
    project_id: Annotated[int, Query(gt=0)],
    telegram_id: Annotated[int, Depends(require_authenticated_telegram_id)],
) -> ActionExecutionResponse:
    """Validate and execute one user-confirmed pending action exactly once."""
    engine: AsyncEngine = request.app.state.database_engine
    await require_user_project(engine, telegram_id, project_id)
    try:
        action, created_transaction, replayed = await confirm_pending_create_transaction_action(
            engine=engine,
            telegram_id=telegram_id,
            project_id=project_id,
            action_id=action_id,
        )
    except Exception as error:
        logger.exception("confirmed AI action %s failed", action_id)
        raise HTTPException(status_code=500, detail="Не вдалося виконати підтверджену дію.") from error

    if action is not None and action.get("status") == "failed":
        raise HTTPException(status_code=422, detail="Чернетка дії не пройшла перевірку.")
    if action is None or created_transaction is None:
        raise HTTPException(
            status_code=409,
            detail="Дію неможливо підтвердити: вона не існує, вже оброблена або прострочена.",
        )
    if replayed:
        logger.info("idempotent AI action confirmation replayed for %s", action_id)
    return ActionExecutionResponse(
        action=pending_action_response(action),
        transaction=TransactionResponse.model_validate(created_transaction),
    )


@app.post("/api/ai/actions/{action_id}/cancel", response_model=ActionExecutionResponse)
async def cancel_ai_action(
    request: Request,
    action_id: Annotated[str, Path(min_length=1, max_length=36)],
    project_id: Annotated[int, Query(gt=0)],
    telegram_id: Annotated[int, Depends(require_authenticated_telegram_id)],
) -> ActionExecutionResponse:
    """Cancel a pending action before it changes the financial ledger."""
    engine: AsyncEngine = request.app.state.database_engine
    await require_user_project(engine, telegram_id, project_id)
    action = await claim_pending_ai_action(
        engine,
        telegram_id=telegram_id,
        project_id=project_id,
        action_id=action_id,
        target_status="cancelled",
    )
    if action is None:
        raise HTTPException(
            status_code=409,
            detail="Дію неможливо скасувати: вона не існує, вже оброблена або прострочена.",
        )
    await record_ai_action_audit(
        engine,
        user_id=int(action["user_id"]),
        project_id=int(action["project_id"]),
        thread_id=str(action["thread_id"]),
        action_id=str(action["id"]),
        action_type=str(action["action_type"]),
        event="cancelled",
        result={"status": "cancelled"},
    )
    return ActionExecutionResponse(action=pending_action_response(action))


@app.post("/api/ai/chat")
async def chat_with_ai(
    request: Request,
    chat_request: ChatRequest,
    telegram_id: Annotated[int, Depends(require_authenticated_telegram_id)],
) -> StreamingResponse:
    """Stream one controlled Gemini reply while LangGraph persists the conversation."""
    engine: AsyncEngine = request.app.state.database_engine
    await require_user_project(engine, telegram_id, chat_request.project_id)
    await enforce_rate_limit(
        engine,
        scope="ai_chat",
        subject=f"{telegram_id}:{chat_request.project_id}",
        limit=CHAT_REQUEST_LIMIT,
        window=CHAT_REQUEST_WINDOW,
    )
    await purge_expired_chat_threads(request)

    now = datetime.now(timezone.utc)
    expires_at = now + CHAT_RETENTION
    thread_id = chat_request.thread_id
    thread: dict[str, object] | None = None
    if thread_id:
        thread = await get_ai_chat_thread(engine, telegram_id, chat_request.project_id, thread_id)
        if thread is None:
            raise HTTPException(status_code=404, detail="Діалог не знайдено або він уже прострочений.")
        renewed = await touch_ai_chat_thread(
            engine,
            telegram_id,
            chat_request.project_id,
            thread_id,
            expires_at,
        )
        if not renewed:
            raise HTTPException(status_code=404, detail="Діалог не знайдено або він уже прострочений.")
    else:
        thread_id = str(uuid4())
        title = " ".join(chat_request.message.split())[:120]
        thread = await create_ai_chat_thread(
            engine,
            telegram_id,
            chat_request.project_id,
            thread_id,
            title or "Новий діалог",
            expires_at,
        )

    checkpointer: AsyncPostgresSaver = request.app.state.chat_checkpointer

    async def generate_events():
        assert thread_id is not None
        yield sse_event("thread", {"thread_id": thread_id})
        streamed_text = ""
        try:
            conversation_summary = str(thread.get("memory_summary") or "") if thread else ""
            summary_message_count = int(thread.get("summary_message_count") or 0) if thread else 0
            checkpoint = await checkpointer.aget_tuple({"configurable": {"thread_id": thread_id}})
            previous_messages = checkpoint.checkpoint["channel_values"].get("messages", []) if checkpoint else []
            try:
                refreshed_summary, covered_message_count = await compact_chat_memory_if_needed(
                    existing_summary=conversation_summary or None,
                    summary_message_count=summary_message_count,
                    visible_messages=serialize_visible_messages(previous_messages),
                )
                if refreshed_summary != (conversation_summary or None):
                    saved = await save_ai_chat_memory_summary(
                        engine,
                        telegram_id=telegram_id,
                        project_id=chat_request.project_id,
                        thread_id=thread_id,
                        summary=refreshed_summary or "",
                        message_count=covered_message_count,
                    )
                    if saved:
                        conversation_summary = refreshed_summary or ""
            except Exception:
                logger.exception("could not compact AI chat memory for thread %s", thread_id)
            graph = create_finance_chat_graph(
                engine=engine,
                telegram_id=telegram_id,
                project_id=chat_request.project_id,
                thread_id=thread_id,
                checkpointer=checkpointer,
                conversation_summary=conversation_summary or None,
            )
            config = {"configurable": {"thread_id": thread_id}}
            async for chunk, metadata in graph.astream(
                {"messages": [("user", chat_request.message)]},
                config,
                stream_mode="messages",
            ):
                if metadata.get("langgraph_node") != "assistant":
                    continue
                text_chunk = message_content_to_text(chunk.content)
                if text_chunk:
                    streamed_text += text_chunk
                    yield sse_event("delta", {"text": text_chunk})

            if not streamed_text:
                state = await graph.aget_state(config)
                messages = state.values.get("messages", [])
                visible_messages = serialize_visible_messages(messages)
                if visible_messages and visible_messages[-1]["role"] == "assistant":
                    fallback_text = visible_messages[-1]["content"]
                    streamed_text = fallback_text
                    yield sse_event("delta", {"text": fallback_text})

            pending_actions = await list_pending_ai_actions(
                engine,
                telegram_id=telegram_id,
                project_id=chat_request.project_id,
                thread_id=thread_id,
            )
            for action in pending_actions:
                yield sse_event(
                    "pending_action",
                    {"action": pending_action_response(action).model_dump(mode="json")},
                )

            touched = await touch_ai_chat_thread(
                engine,
                telegram_id,
                chat_request.project_id,
                thread_id,
                datetime.now(timezone.utc) + CHAT_RETENTION,
            )
            if not touched:
                raise FinanceChatError("AI chat thread expired during response generation.")
            yield sse_event("done", {"thread_id": thread_id, "answer": streamed_text})
        except FinanceChatError:
            yield sse_event("error", {"message": "AI-помічник тимчасово недоступний. Спробуйте ще раз."})
        except Exception as error:
            logger.exception("AI chat request failed for project %s", chat_request.project_id)
            error_text = str(error)
            if "429" in error_text or "RESOURCE_EXHAUSTED" in error_text:
                yield sse_event(
                    "error",
                    {"message": "Gemini тимчасово досяг ліміту запитів. Зачекайте близько хвилини та спробуйте ще раз."},
                )
            else:
                yield sse_event("error", {"message": "Не вдалося сформувати відповідь AI-помічника. Спробуйте ще раз."})

    return StreamingResponse(
        generate_events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/ai/analyze-transactions", response_model=TransactionAnalysisResponse)
async def analyze_transactions(
    request: Request,
    project_id: Annotated[int, Query(gt=0)],
    telegram_id: Annotated[int, Depends(require_authenticated_telegram_id)],
) -> TransactionAnalysisResponse:
    """Analyze compact project facts with Gemini, reusing a matching cached result."""
    engine: AsyncEngine = request.app.state.database_engine
    await require_user_project(engine, telegram_id, project_id)
    transactions = await get_transactions(engine, telegram_id, project_id)
    prepared = prepare_transaction_analysis(transactions)
    started_at = perf_counter()

    async def record_metric(**values: object) -> None:
        try:
            await record_ai_analysis_metric(
                engine,
                project_id=project_id,
                ledger_hash=prepared.ledger_hash,
                prompt_version=PROMPT_VERSION,
                latency_ms=round((perf_counter() - started_at) * 1000),
                **values,
            )
        except Exception:
            logger.exception("could not record AI analysis metric")

    if not prepared.is_sufficient:
        response = build_dashboard_analysis(insufficient_data_analysis(), prepared).model_copy(
            update={"generated_at": datetime.now(timezone.utc)}
        )
        await record_metric(result_source="fallback", status="success")
        return response

    cached_analysis = await get_cached_ai_analysis(
        engine,
        project_id,
        prepared.ledger_hash,
        PROMPT_VERSION,
    )
    if cached_analysis is not None:
        try:
            narrative = GeminiTransactionAnalysis.model_validate(cached_analysis["analysis"])
            response = build_dashboard_analysis(narrative, prepared).model_copy(
                update={"cached": True, "generated_at": cached_analysis["created_at"]}
            )
            await record_metric(result_source="cache", status="success")
            return response
        except (KeyError, ValidationError):
            logger.warning("ignoring invalid cached AI analysis for project %s", project_id)

    await enforce_rate_limit(
        engine,
        scope="ai_analysis",
        subject=f"{telegram_id}:{project_id}",
        limit=ANALYSIS_REQUEST_LIMIT,
        window=ANALYSIS_REQUEST_WINDOW,
    )
    try:
        gemini_result = await asyncio.to_thread(
            analyze_prepared_transactions_with_gemini,
            prepared,
        )
        response = build_dashboard_analysis(gemini_result.analysis, prepared).model_copy(
            update={"generated_at": datetime.now(timezone.utc)}
        )
        try:
            await save_cached_ai_analysis(
                engine,
                project_id,
                prepared.ledger_hash,
                PROMPT_VERSION,
                gemini_result.analysis.model_dump(mode="json"),
            )
        except Exception:
            logger.exception("could not cache AI analysis")
        await record_metric(
            result_source="gemini",
            status="success",
            model=gemini_result.model,
            input_tokens=gemini_result.usage.input_tokens,
            output_tokens=gemini_result.usage.output_tokens,
            thought_tokens=gemini_result.usage.thought_tokens,
            cached_tokens=gemini_result.usage.cached_tokens,
        )
        return response
    except GeminiAnalysisError as error:
        await record_metric(result_source="gemini", status="error")
        if not os.getenv("GEMINI_API_KEY"):
            raise HTTPException(status_code=503, detail="AI-аналіз тимчасово недоступний.") from error
        raise HTTPException(status_code=502, detail="Не вдалося отримати коректний AI-аналіз. Спробуйте ще раз.") from error


app.mount(
    "/",
    StaticFiles(directory="frontend/dist", html=True, check_dir=False),
    name="frontend",
)
