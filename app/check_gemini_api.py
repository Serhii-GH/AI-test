"""Verify that GEMINI_API_KEY can make a first Gemini SDK request."""

import os
import sys

from dotenv import load_dotenv
from google import genai


DEFAULT_GEMINI_MODEL = "gemini-3.5-flash-lite"


def check_gemini_api_key() -> int:
    """Return zero only when Gemini generates a response with the configured key."""
    load_dotenv(override=True)
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        print("GEMINI_API_KEY is not configured in .env.", file=sys.stderr)
        return 1

    model = os.getenv("GEMINI_MODEL", DEFAULT_GEMINI_MODEL)
    try:
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model=model,
            contents="Відповідай одним реченням: Gemini API працює?",
        )
    except Exception:
        print(f"Gemini request failed for model '{model}'. Check the model and API key configuration.", file=sys.stderr)
        return 1

    if not response.text:
        print("Gemini returned an empty response.", file=sys.stderr)
        return 1

    print(f"Gemini API key and model '{model}' are working.")
    return 0


if __name__ == "__main__":
    raise SystemExit(check_gemini_api_key())
