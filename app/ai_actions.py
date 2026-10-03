"""Validated, user-confirmed finance actions proposed by the AI chat."""

import hashlib
import json
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


ACTION_TYPE_CREATE_TRANSACTION = "create_transaction"
PENDING_ACTION_STATUS = "pending"
TransactionType = Literal["income", "expense"]
MainCategory = Literal["Роботи", "Матеріали"]


class TransactionActionPayload(BaseModel):
    """The only write payload the first controlled-actions release accepts."""

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
        """Return a stable key that prevents duplicate pending proposals in a thread."""
        canonical_payload = json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(
            f"{ACTION_TYPE_CREATE_TRANSACTION}:{canonical_payload}".encode("utf-8")
        ).hexdigest()


class PendingActionResponse(BaseModel):
    id: str
    thread_id: str
    action_type: Literal["create_transaction"]
    status: str
    payload: TransactionActionPayload
    created_at: datetime
    expires_at: datetime


def pending_action_response(action: dict[str, object]) -> PendingActionResponse:
    """Create the safe action shape shared by the SSE stream and React API."""
    return PendingActionResponse(
        id=str(action["id"]),
        thread_id=str(action["thread_id"]),
        action_type=ACTION_TYPE_CREATE_TRANSACTION,
        status=str(action["status"]),
        payload=TransactionActionPayload.model_validate(action["payload"]),
        created_at=action["created_at"],
        expires_at=action["expires_at"],
    )
