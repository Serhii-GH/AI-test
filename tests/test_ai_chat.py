"""Regression tests for the controlled read-only finance chat helpers."""

import asyncio
from datetime import date
import unittest
from unittest.mock import AsyncMock, patch

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from pydantic import ValidationError

from app.ai_actions import TransactionActionPayload
from app.ai_chat import (
    _parse_period,
    build_short_term_model_history,
    compact_chat_memory_if_needed,
    message_content_to_text,
    serialize_visible_messages,
)
from app.prompts.ai_chat import CHAT_PROMPT_VERSION, build_finance_chat_system_prompt


class FinanceChatTests(unittest.TestCase):
    def test_period_validation_requires_an_ordered_iso_range(self) -> None:
        start, end = _parse_period("2026-06-01", "2026-06-30")

        self.assertEqual(start, date(2026, 6, 1))
        self.assertEqual(end, date(2026, 6, 30))
        with self.assertRaises(ValueError):
            _parse_period("2026-06-30", "2026-06-01")
        with self.assertRaises(ValueError):
            _parse_period("червень", None)

    def test_history_hides_tool_calls_and_tool_results(self) -> None:
        messages = [
            HumanMessage("Покажи витрати за червень"),
            AIMessage("", tool_calls=[{"name": "get_category_totals", "args": {}, "id": "call-1"}]),
            ToolMessage('{"categories": []}', tool_call_id="call-1"),
            AIMessage("За червень витрат не знайдено."),
        ]

        self.assertEqual(
            serialize_visible_messages(messages),
            [
                {"role": "user", "content": "Покажи витрати за червень"},
                {"role": "assistant", "content": "За червень витрат не знайдено."},
            ],
        )

    def test_prompt_has_controlled_actions_and_current_year_contract(self) -> None:
        prompt = build_finance_chat_system_prompt(date(2026, 10, 3))

        self.assertEqual(CHAT_PROMPT_VERSION, "finance-chat-controlled-actions-v2")
        self.assertIn("2026", prompt)
        self.assertIn("read-only", prompt)
        self.assertIn("українською", prompt)
        self.assertIn("Не вигадуй", prompt)
        self.assertIn("propose_create_transaction", prompt)
        self.assertIn("Роботи", prompt)
        self.assertIn("Матеріали", prompt)

    def test_create_transaction_payload_is_strict_and_uses_allowed_categories(self) -> None:
        payload = TransactionActionPayload.model_validate(
            {
                "type": "expense",
                "amount": "450.00",
                "exchange_rate": "41.50",
                "category": "Матеріали",
                "subcategory": "Підлога",
                "description": "Плитка",
                "date": "2026-10-03",
            }
        )

        self.assertEqual(payload.category, "Матеріали")
        self.assertEqual(len(payload.canonical_hash()), 64)
        with self.assertRaises(ValidationError):
            TransactionActionPayload.model_validate(
                {**payload.model_dump(mode="json"), "category": "Транспорт"}
            )
        with self.assertRaises(ValidationError):
            TransactionActionPayload.model_validate(
                {**payload.model_dump(mode="json"), "unexpected": "field"}
            )

    def test_streamed_chunks_preserve_leading_spaces(self) -> None:
        self.assertEqual(
            message_content_to_text("Для") + message_content_to_text(" того"),
            "Для того",
        )

    def test_old_turns_are_compacted_into_summary_before_being_dropped(self) -> None:
        messages = [
            {"role": "user" if index % 2 == 0 else "assistant", "content": f"повідомлення {index}"}
            for index in range(14)
        ]

        async def compact() -> tuple[str | None, int]:
            with patch(
                "app.ai_chat.summarize_chat_memory",
                new=AsyncMock(return_value="Стислий контекст діалогу"),
            ) as summarize:
                summary, covered_count = await compact_chat_memory_if_needed(
                    existing_summary=None,
                    summary_message_count=0,
                    visible_messages=messages,
                )
                summarize.assert_awaited_once()
                return summary, covered_count

        summary, covered_count = asyncio.run(compact())
        self.assertEqual(summary, "Стислий контекст діалогу")
        self.assertEqual(covered_count, 4)


    def test_active_turn_keeps_tool_result_for_the_next_model_call(self) -> None:
        tool_call = AIMessage(
            "",
            tool_calls=[{"name": "get_category_totals", "args": {}, "id": "call-1"}],
        )
        tool_result = ToolMessage('{"categories": []}', tool_call_id="call-1")
        messages = [HumanMessage("Покажи витрати"), tool_call, tool_result]

        self.assertEqual(build_short_term_model_history(messages), messages)


if __name__ == "__main__":
    unittest.main()
