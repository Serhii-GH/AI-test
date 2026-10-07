"""Validated, user-confirmed finance actions proposed by the AI chat."""

import hashlib
import json
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


ACTION_TYPE_CREATE_TRANSACTION = "create_transaction"
ACTION_TYPE_UPDATE_TRANSACTION = "update_transaction"
ACTION_TYPE_DELETE_TRANSACTION = "delete_transaction"
PENDING_ACTION_STATUS = "pending"
TransactionType = Literal["income", "expense"]
ActionType = Literal[
    "create_transaction",
    "update_transaction",
    "delete_transaction",
]
MainCategory = Literal["Роботи", "Матеріали"]


class TransactionActionPayload(BaseModel):
    """Validated data for creating or replacing one finance operation."""

    model_config = ConfigDict(extra="forbid")

    type: TransactionType
    amount: Annotated[Decimal, Field(gt=0, max_digits=12, decimal_places=2)]
    exchange_rate: Annotated[Decimal, Field(gt=0, max_digits=10, decimal_places=4)]
    category: MainCategory
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

    def canonical_hash(self) -> str:
        return _canonical_hash(ACTION_TYPE_CREATE_TRANSACTION, self.model_dump(mode="json"))


class TransactionReference(BaseModel):
    """An immutable-looking snapshot used to prevent stale AI proposals."""

    model_config = ConfigDict(extra="forbid")

    id: Annotated[int, Field(gt=0)]
    type: TransactionType
    amount: Annotated[Decimal, Field(gt=0, max_digits=12, decimal_places=2)]
    exchange_rate: Annotated[Decimal, Field(gt=0, max_digits=10, decimal_places=4)]
    category: MainCategory
    subcategory: Annotated[str, Field(min_length=1, max_length=100)]
    description: Annotated[str, Field(min_length=1, max_length=255)]
    date: date


class UpdateTransactionActionPayload(TransactionActionPayload):
    """A replacement for one known transaction, with its expected old values."""

    transaction_id: Annotated[int, Field(gt=0)]
    expected: TransactionReference

    def canonical_hash(self) -> str:
        return _canonical_hash(ACTION_TYPE_UPDATE_TRANSACTION, self.model_dump(mode="json"))


class DeleteTransactionActionPayload(BaseModel):
    """A deletion proposal that identifies exactly what will be removed."""

    model_config = ConfigDict(extra="forbid")

    transaction_id: Annotated[int, Field(gt=0)]
    expected: TransactionReference

    def canonical_hash(self) -> str:
        return _canonical_hash(ACTION_TYPE_DELETE_TRANSACTION, self.model_dump(mode="json"))


def _canonical_hash(action_type: ActionType, payload: dict[str, object]) -> str:
    canonical_payload = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(f"{action_type}:{canonical_payload}".encode("utf-8")).hexdigest()


class PendingActionResponse(BaseModel):
    id: str
    thread_id: str
    action_type: ActionType
    status: str
    payload: TransactionActionPayload | UpdateTransactionActionPayload | DeleteTransactionActionPayload
    created_at: datetime
    expires_at: datetime


def pending_action_response(action: dict[str, object]) -> PendingActionResponse:
    """Create the safe action shape shared by the SSE stream and React API."""
    action_type = str(action["action_type"])
    payload_model: type[BaseModel]
    if action_type == ACTION_TYPE_CREATE_TRANSACTION:
        payload_model = TransactionActionPayload
    elif action_type == ACTION_TYPE_UPDATE_TRANSACTION:
        payload_model = UpdateTransactionActionPayload
    elif action_type == ACTION_TYPE_DELETE_TRANSACTION:
        payload_model = DeleteTransactionActionPayload
    else:
        raise ValueError("Unsupported pending AI action type.")

    return PendingActionResponse(
        id=str(action["id"]),
        thread_id=str(action["thread_id"]),
        action_type=action_type,
        status=str(action["status"]),
        payload=payload_model.model_validate(action["payload"]),
        created_at=action["created_at"],
        expires_at=action["expires_at"],
    )
