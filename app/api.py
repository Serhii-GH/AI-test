import asyncio
import json
import logging
from io import BytesIO
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
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from pydantic import BaseModel, Field, ValidationError, field_validator
from sqlalchemy.ext.asyncio import AsyncEngine

from app.ai_actions import PendingActionResponse, TransactionActionPayload, pending_action_response
from app.ai_chat import (
    FinanceChatError,
    create_finance_chat_graph,
    message_content_to_text,
    serialize_visible_messages,
)
from app.database import (
    check_database_connection,
    create_ai_chat_thread,
    create_user_project,
    create_web_session,
    create_database_engine,
    claim_pending_ai_action,
    delete_ai_chat_threads,
    delete_web_session,
    get_ai_chat_thread,
    get_financial_summary,
    get_cached_ai_analysis,
    get_telegram_user_profile,
    get_transactions,
    get_user_project,
    get_web_session_telegram_id,
    initialize_database,
    list_expired_ai_chat_thread_ids,
    list_pending_ai_actions,
    list_ai_chat_threads,
    list_user_projects,
    record_ai_analysis_metric,
    record_ai_action_audit,
    save_cached_ai_analysis,
    delete_user_transaction,
    save_transaction,
    finalize_pending_ai_action,
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
SESSION_DURATION_SECONDS = 7 * 24 * 60 * 60
CHAT_RETENTION = timedelta(days=7)


def session_cookie_secure() -> bool:
    return os.getenv("SESSION_COOKIE_SECURE", "false").strip().lower() in {"1", "true", "yes"}


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
    action = await claim_pending_ai_action(
        engine,
        telegram_id=telegram_id,
        project_id=project_id,
        action_id=action_id,
        target_status="processing",
    )
    if action is None:
        raise HTTPException(
            status_code=409,
            detail="Дію неможливо підтвердити: вона не існує, вже оброблена або прострочена.",
        )

    action_type = str(action["action_type"])
    audit_context = {
        "user_id": int(action["user_id"]),
        "project_id": int(action["project_id"]),
        "thread_id": str(action["thread_id"]),
        "action_id": str(action["id"]),
        "action_type": action_type,
    }
    await record_ai_action_audit(
        engine,
        **audit_context,
        event="confirm_requested",
        result={"status": "processing"},
    )

    if action_type != "create_transaction":
        await finalize_pending_ai_action(engine, action_id=action_id, status="failed")
        await record_ai_action_audit(
            engine,
            **audit_context,
            event="failed",
            result={"reason": "unsupported_action_type"},
        )
        raise HTTPException(status_code=422, detail="Тип дії не дозволений.")

    try:
        payload = TransactionActionPayload.model_validate(action["payload"])
    except ValidationError as error:
        await finalize_pending_ai_action(engine, action_id=action_id, status="failed")
        await record_ai_action_audit(
            engine,
            **audit_context,
            event="failed",
            result={"reason": "invalid_payload"},
        )
        raise HTTPException(status_code=422, detail="Чернетка дії не пройшла перевірку.") from error

    try:
        created_transaction = await save_transaction(
            engine=engine,
            telegram_id=telegram_id,
            username=None,
            amount=payload.amount,
            project_id=project_id,
            exchange_rate=payload.exchange_rate,
            main_category_name=payload.category,
            subcategory_name=payload.subcategory,
            description=payload.description,
            transaction_type=payload.type,
            created_at=datetime.combine(payload.date, time.min, tzinfo=timezone.utc),
        )
    except Exception as error:
        logger.exception("confirmed AI action %s failed", action_id)
        await finalize_pending_ai_action(engine, action_id=action_id, status="failed")
        await record_ai_action_audit(
            engine,
            **audit_context,
            event="failed",
            result={"reason": "business_operation_failed"},
        )
        raise HTTPException(status_code=500, detail="Не вдалося виконати підтверджену дію.") from error

    if not await finalize_pending_ai_action(engine, action_id=action_id, status="confirmed"):
        logger.error("AI action %s created a transaction but was not finalized", action_id)
        raise HTTPException(status_code=500, detail="Операцію створено, але її статус потребує перевірки.")
    action["status"] = "confirmed"
    await record_ai_action_audit(
        engine,
        **audit_context,
        event="confirmed",
        result={"transaction_id": int(created_transaction["id"])},
    )
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
    await purge_expired_chat_threads(request)

    now = datetime.now(timezone.utc)
    expires_at = now + CHAT_RETENTION
    thread_id = chat_request.thread_id
    if thread_id:
        if await get_ai_chat_thread(engine, telegram_id, chat_request.project_id, thread_id) is None:
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
        await create_ai_chat_thread(
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
            graph = create_finance_chat_graph(
                engine=engine,
                telegram_id=telegram_id,
                project_id=chat_request.project_id,
                thread_id=thread_id,
                checkpointer=checkpointer,
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
