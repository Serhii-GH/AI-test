from contextlib import asynccontextmanager
from datetime import date, datetime, time, timezone
from decimal import Decimal
from typing import Annotated, Literal

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Path, Query, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncEngine

from app.database import (
    check_database_connection,
    create_database_engine,
    get_financial_summary,
    get_transactions,
    initialize_database,
    delete_user_transaction,
    save_transaction,
    WEB_TRANSACTIONS_CATEGORY,
)


class TransactionResponse(BaseModel):
    id: int
    user_id: int
    category_id: int
    transaction_type: str
    main_category: str | None
    subcategory: str
    amount: Decimal
    description: str | None
    created_at: datetime


class FinancialSummaryResponse(BaseModel):
    total_income: Decimal
    total_expense: Decimal
    balance: Decimal


class CreateTransactionRequest(BaseModel):
    telegram_id: Annotated[int, Field(gt=0)]
    type: Literal["income", "expense"]
    amount: Annotated[Decimal, Field(gt=0, max_digits=12, decimal_places=2)]
    category: Annotated[str, Field(min_length=1, max_length=100)]
    description: Annotated[str, Field(min_length=1, max_length=255)]
    date: date

    @field_validator("amount")
    @classmethod
    def amount_must_be_a_positive_finite_number(cls, value: Decimal) -> Decimal:
        if not value.is_finite() or value <= 0:
            raise ValueError("amount must be a positive number")
        return value

    @field_validator("category", "description")
    @classmethod
    def text_must_not_be_blank(cls, value: str) -> str:
        normalized_value = value.strip()
        if not normalized_value:
            raise ValueError("value must not be blank")
        return normalized_value


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


@app.get("/api/transactions", response_model=list[TransactionResponse])
async def list_transactions(
    request: Request,
    telegram_id: int = Query(ge=1),
) -> list[dict[str, object]]:
    """Return the supplied Telegram user's transactions as JSON, newest first."""
    engine: AsyncEngine = request.app.state.database_engine
    return await get_transactions(engine, telegram_id)


@app.post("/api/transactions", response_model=TransactionResponse, status_code=201)
async def create_transaction(
    request: Request,
    transaction: CreateTransactionRequest,
) -> dict[str, object]:
    """Validate and store a dashboard transaction for the supplied Telegram user."""
    engine: AsyncEngine = request.app.state.database_engine
    created_at = datetime.combine(transaction.date, time.min, tzinfo=timezone.utc)
    return await save_transaction(
        engine=engine,
        telegram_id=transaction.telegram_id,
        username=None,
        amount=transaction.amount,
        main_category_name=WEB_TRANSACTIONS_CATEGORY,
        subcategory_name=transaction.category,
        description=transaction.description,
        transaction_type=transaction.type,
        created_at=created_at,
    )


@app.delete("/api/transactions/{transaction_id}", status_code=204)
async def delete_transaction(
    request: Request,
    transaction_id: int = Path(gt=0),
    telegram_id: int = Query(ge=1),
) -> None:
    """Delete one transaction only when it belongs to the supplied Telegram user."""
    engine: AsyncEngine = request.app.state.database_engine
    deleted = await delete_user_transaction(engine, telegram_id, transaction_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Операцію не знайдено.")


@app.get("/api/summary", response_model=FinancialSummaryResponse)
async def get_summary(
    request: Request,
    telegram_id: int = Query(ge=1),
) -> dict[str, Decimal]:
    """Return the supplied Telegram user's financial totals as JSON."""
    engine: AsyncEngine = request.app.state.database_engine
    return await get_financial_summary(engine, telegram_id)
