from contextlib import asynccontextmanager
from datetime import datetime
from decimal import Decimal

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncEngine

from app.database import (
    check_database_connection,
    create_database_engine,
    get_financial_summary,
    get_transactions,
    initialize_database,
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
async def list_transactions(request: Request) -> list[dict[str, object]]:
    """Return transactions from the database as JSON, newest first."""
    engine: AsyncEngine = request.app.state.database_engine
    return await get_transactions(engine)


@app.get("/api/summary", response_model=FinancialSummaryResponse)
async def get_summary(request: Request) -> dict[str, Decimal]:
    """Return the user's financial totals as JSON."""
    engine: AsyncEngine = request.app.state.database_engine
    return await get_financial_summary(engine)
