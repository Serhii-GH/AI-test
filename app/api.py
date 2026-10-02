import asyncio
from io import BytesIO
from contextlib import asynccontextmanager
from datetime import date, datetime, time, timezone
from decimal import Decimal
import os
from typing import Annotated, Literal

from aiogram import Bot
from dotenv import load_dotenv
from fastapi import Cookie, Depends, FastAPI, HTTPException, Path, Query, Request, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncEngine

from app.database import (
    check_database_connection,
    create_user_project,
    create_web_session,
    create_database_engine,
    delete_web_session,
    get_financial_summary,
    get_telegram_user_profile,
    get_transactions,
    get_user_project,
    get_web_session_telegram_id,
    initialize_database,
    list_user_projects,
    delete_user_transaction,
    save_transaction,
    verify_web_login_code,
)
from app.gemini_analysis import (
    GeminiAnalysisError,
    TransactionAnalysisResponse,
    analyze_transactions_with_gemini,
)


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


class CreateTransactionRequest(BaseModel):
    project_id: Annotated[int, Field(gt=0)]
    type: Literal["income", "expense"]
    amount: Annotated[Decimal, Field(gt=0, max_digits=12, decimal_places=2)]
    exchange_rate: Annotated[Decimal, Field(gt=0, max_digits=10, decimal_places=4)]
    category: Literal["Роботи", "Матеріали"]
    subcategory: Annotated[str, Field(min_length=1, max_length=100)]
    description: Annotated[str, Field(min_length=1, max_length=255)]
    date: date

    @field_validator("amount", "exchange_rate")
    @classmethod
    def amount_must_be_a_positive_finite_number(cls, value: Decimal) -> Decimal:
        if not value.is_finite() or value <= 0:
            raise ValueError("amount must be a positive number")
        return value

    @field_validator("subcategory", "description")
    @classmethod
    def text_must_not_be_blank(cls, value: str) -> str:
        normalized_value = value.strip()
        if not normalized_value:
            raise ValueError("value must not be blank")
        return normalized_value


class VerifyLoginRequest(BaseModel):
    telegram_id: Annotated[int, Field(gt=0)]
    code: Annotated[str, Field(pattern=r"^\d{6}$")]


class SessionResponse(BaseModel):
    telegram_id: int
    username: str | None


SESSION_COOKIE_NAME = "admin_session"
SESSION_DURATION_SECONDS = 7 * 24 * 60 * 60


def session_cookie_secure() -> bool:
    return os.getenv("SESSION_COOKIE_SECURE", "false").strip().lower() in {"1", "true", "yes"}


@asynccontextmanager
async def lifespan(app: FastAPI):
    load_dotenv(override=True)
    engine = create_database_engine()
    await check_database_connection(engine)
    await initialize_database(engine)
    app.state.database_engine = engine
    try:
        yield
    finally:
        await engine.dispose()


app = FastAPI(title="Apartment Renovation Finance API", lifespan=lifespan)


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


async def require_user_project(engine: AsyncEngine, telegram_id: int, project_id: int) -> None:
    if await get_user_project(engine, telegram_id, project_id) is None:
        raise HTTPException(status_code=404, detail="Проєкт не знайдено.")


@app.post("/api/auth/verify", response_model=SessionResponse)
async def verify_login(
    request: Request,
    response: Response,
    credentials: VerifyLoginRequest,
) -> dict[str, object]:
    """Exchange a one-time Telegram bot code for an HttpOnly browser session."""
    engine: AsyncEngine = request.app.state.database_engine
    verified = await verify_web_login_code(engine, credentials.telegram_id, credentials.code)
    if not verified:
        raise HTTPException(status_code=401, detail="Код недійсний, прострочений або вже використаний.")

    session_token = await create_web_session(engine, credentials.telegram_id)
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=session_token,
        max_age=SESSION_DURATION_SECONDS,
        httponly=True,
        samesite="lax",
        secure=session_cookie_secure(),
    )
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


@app.post("/api/ai/analyze-transactions", response_model=TransactionAnalysisResponse)
async def analyze_transactions(
    request: Request,
    project_id: Annotated[int, Query(gt=0)],
    telegram_id: Annotated[int, Depends(require_authenticated_telegram_id)],
) -> TransactionAnalysisResponse:
    """Analyze the authenticated user's Neon transactions with Gemini on the backend."""
    engine: AsyncEngine = request.app.state.database_engine
    await require_user_project(engine, telegram_id, project_id)
    transactions = await get_transactions(engine, telegram_id, project_id)
    try:
        return await asyncio.to_thread(analyze_transactions_with_gemini, transactions)
    except GeminiAnalysisError as error:
        if not os.getenv("GEMINI_API_KEY"):
            raise HTTPException(status_code=503, detail="AI-аналіз тимчасово недоступний.") from error
        raise HTTPException(status_code=502, detail="Не вдалося отримати коректний AI-аналіз. Спробуйте ще раз.") from error
