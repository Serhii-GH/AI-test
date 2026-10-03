"""Regression tests for the compact finance-analysis prompt contract."""

from datetime import datetime, timezone
from decimal import Decimal
import unittest

from app.gemini_analysis import (
    GeminiTransactionAnalysis,
    build_dashboard_analysis,
    estimate_prepared_analysis_tokens,
    insufficient_data_analysis,
    prepare_transaction_analysis,
)
from app.prompts.transaction_analysis import PROMPT_VERSION, build_transaction_analysis_prompt


def transaction(
    transaction_id: int,
    amount: str,
    category: str,
    description: str,
    *,
    transaction_type: str = "expense",
) -> dict[str, object]:
    return {
        "id": transaction_id,
        "transaction_type": transaction_type,
        "main_category": "Матеріали",
        "subcategory": category,
        "amount": Decimal(amount),
        "description": description,
        "created_at": datetime(2026, 10, transaction_id, tzinfo=timezone.utc),
    }


class GeminiAnalysisTests(unittest.TestCase):
    def test_facts_are_compact_stable_and_derived_on_the_backend(self) -> None:
        ledger = [
            transaction(1, "100.00", "Стіни", "Ґрунтовка"),
            transaction(2, "800.00", "Підлога", "Плитка"),
            transaction(3, "300.00", "Стіни", "Штукатурка"),
            transaction(4, "150.00", "Електрика", "Кабель"),
            transaction(5, "90.00", "Електрика", "Лампа"),
            transaction(6, "120.00", "Вікна", "Профіль"),
        ]

        prepared = prepare_transaction_analysis(ledger)
        reordered = prepare_transaction_analysis(list(reversed(ledger)))

        self.assertTrue(prepared.is_sufficient)
        self.assertEqual(prepared.ledger_hash, reordered.ledger_hash)
        self.assertEqual(prepared.facts["total_expense"], "1560.00")
        self.assertEqual(len(prepared.facts["top_expenses"]), 5)
        self.assertEqual(prepared.facts["top_expenses"][0]["description"], "Плитка")
        self.assertEqual(
            prepared.facts["expense_categories"][0],
            {
                "category": "Матеріали",
                "amount": "1560.00",
                "subcategories": [
                    {"category": "Підлога", "amount": "800.00"},
                    {"category": "Стіни", "amount": "400.00"},
                    {"category": "Електрика", "amount": "240.00"},
                    {"category": "Вікна", "amount": "120.00"},
                ],
            },
        )
        self.assertIn("large_single_expense", [signal["type"] for signal in prepared.facts["signals"]])

    def test_insufficient_ledger_skips_model_narrative(self) -> None:
        prepared = prepare_transaction_analysis([transaction(1, "100.00", "Стіни", "Ґрунтовка")])

        response = build_dashboard_analysis(insufficient_data_analysis(), prepared)

        self.assertFalse(prepared.is_sufficient)
        self.assertIn("Недостатньо даних", response.summary)
        self.assertEqual(response.risks, [])
        self.assertEqual(response.recommendations, [])

    def test_backend_keeps_category_amounts_out_of_model_control(self) -> None:
        prepared = prepare_transaction_analysis(
            [
                transaction(1, "100.00", "Стіни", "Ґрунтовка"),
                transaction(2, "250.00", "Підлога", "Плитка"),
                transaction(3, "50.00", "Стіни", "Клей"),
            ]
        )
        model_analysis = GeminiTransactionAnalysis(
            summary="Найбільша частина витрат пов'язана з матеріалами для підлоги.",
            risks=["Одна покупка суттєво впливає на структуру витрат."],
            recommendations=["Перевірте кошторис категорії підлоги перед наступною закупівлею."],
        )

        response = build_dashboard_analysis(model_analysis, prepared)

        self.assertEqual(response.expense_categories[0].category, "Матеріали")
        self.assertEqual(response.expense_categories[0].amount, Decimal("400.00"))
        self.assertEqual(response.expense_categories[0].subcategories[0].category, "Підлога")
        self.assertEqual(response.expense_categories[0].subcategories[0].amount, Decimal("250.00"))

    def test_prompt_contains_the_versioned_contract(self) -> None:
        prepared = prepare_transaction_analysis([])
        prompt = build_transaction_analysis_prompt(prepared.facts)

        self.assertEqual(PROMPT_VERSION, "renovation-finance-v3")
        self.assertIn("Роль:", prompt)
        self.assertIn("Задача:", prompt)
        self.assertIn("Обмеження:", prompt)
        self.assertIn("українською", prompt)
        self.assertIn("<facts>", prompt)

    def test_tiktoken_estimate_uses_prompt_and_output_budget(self) -> None:
        prepared = prepare_transaction_analysis([
            transaction(1, "100.00", "Стіни", "Ґрунтовка"),
            transaction(2, "250.00", "Підлога", "Плитка"),
            transaction(3, "50.00", "Стіни", "Клей"),
        ])

        estimate = estimate_prepared_analysis_tokens(prepared)

        self.assertEqual(estimate.tokenizer, "cl100k_base")
        self.assertGreater(estimate.input_tokens, 0)
        self.assertGreater(estimate.output_token_budget, 0)
        self.assertEqual(
            estimate.potential_total_tokens,
            estimate.input_tokens + estimate.output_token_budget,
        )


if __name__ == "__main__":
    unittest.main()
