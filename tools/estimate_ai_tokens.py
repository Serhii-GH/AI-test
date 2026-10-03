"""Estimate prompt tokens locally with tiktoken (not Gemini billing tokens)."""

from __future__ import annotations

import argparse
from pathlib import Path

import tiktoken


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Estimate input tokens for a UTF-8 prompt file using cl100k_base."
    )
    parser.add_argument("prompt_file", type=Path, help="Path to a UTF-8 text file containing the final prompt")
    args = parser.parse_args()

    prompt = args.prompt_file.read_text(encoding="utf-8")
    token_count = len(tiktoken.get_encoding("cl100k_base").encode(prompt))
    print(f"Estimated input tokens (cl100k_base): {token_count}")
    print("Gemini uses its own tokenizer; use ai_analysis_metrics for billed token counts.")


if __name__ == "__main__":
    main()
