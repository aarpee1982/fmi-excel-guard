from __future__ import annotations

import os


ALLOWED_DOMAIN = "futuremarketinsights.com"


def is_allowed_email(email: str) -> bool:
    normalized = email.strip().lower()
    return normalized.endswith(f"@{ALLOWED_DOMAIN}") and "@" in normalized


def get_app_password() -> str | None:
    password = os.getenv("FMI_APP_PASSWORD", "").strip()
    return password or None
