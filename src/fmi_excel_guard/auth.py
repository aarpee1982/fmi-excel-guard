from __future__ import annotations

import os

import streamlit as st


ALLOWED_DOMAIN = "futuremarketinsights.com"


def is_allowed_email(email: str) -> bool:
    normalized = email.strip().lower()
    return normalized.endswith(f"@{ALLOWED_DOMAIN}") and "@" in normalized


def get_app_password() -> str | None:
    password = os.getenv("FMI_APP_PASSWORD", "").strip()
    if password:
        return password
    try:
        if "FMI_APP_PASSWORD" in st.secrets:
            secret_password = str(st.secrets["FMI_APP_PASSWORD"]).strip()
            if secret_password:
                return secret_password
    except Exception:
        pass
    return None
