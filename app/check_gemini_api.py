"""Verify that GEMINI_API_KEY can authenticate with the Gemini API."""

import json
import os
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from dotenv import load_dotenv


GEMINI_MODELS_URL = "https://generativelanguage.googleapis.com/v1beta/models"


def check_gemini_api_key() -> int:
    """Return zero only when Gemini accepts the configured API key."""
    load_dotenv(override=True)
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        print("GEMINI_API_KEY is not configured in .env.", file=sys.stderr)
        return 1

    request = Request(
        GEMINI_MODELS_URL,
        headers={"x-goog-api-key": api_key},
        method="GET",
    )
    try:
        with urlopen(request, timeout=15) as response:
            payload = json.load(response)
    except HTTPError as error:
        if error.code in {401, 403}:
            print("Gemini rejected GEMINI_API_KEY. Check the key and its API restrictions.", file=sys.stderr)
        else:
            print(f"Gemini API returned HTTP {error.code}.", file=sys.stderr)
        return 1
    except URLError as error:
        print(f"Could not connect to Gemini API: {error.reason}", file=sys.stderr)
        return 1

    model_count = len(payload.get("models", []))
    print(f"Gemini API key is valid. Available models: {model_count}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(check_gemini_api_key())
