from __future__ import annotations

import os


def get_openai_api_key() -> str | None:
    return os.getenv("OPENAI_API_KEY") or None


def get_openai_model() -> str:
    return os.getenv("OPENAI_MODEL") or "gpt-5.4"
