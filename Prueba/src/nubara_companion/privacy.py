from __future__ import annotations

import logging
import re
from collections.abc import Iterable

from .models import Passenger

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


def mask_email(value: str) -> str:
    local, _, domain = value.partition("@")
    return f"{local[:1]}***@{domain}"


def mask_phone(value: str) -> str:
    digits = re.sub(r"\D", "", value)
    return f"***{digits[-2:]}"


def mask_document(value: str) -> str:
    return f"***{value[-3:]}"


def pii_values(passengers: Iterable[Passenger]) -> set[str]:
    values: set[str] = set()
    for p in passengers:
        for v in (p.name, p.email, p.phone, p.document_id):
            if v:
                values.add(v)
    return values


def find_pii(text: str, passengers: Iterable[Passenger]) -> list[str]:
    """Return which kind of PII leaked into `text` (never the value itself)."""
    leaks: list[str] = []
    for p in passengers:
        for kind, v in (("name", p.name), ("email", p.email), ("phone", p.phone), ("document", p.document_id)):
            if v and v in text:
                leaks.append(kind)
    if _EMAIL.search(text):
        leaks.append("email_pattern")
    return sorted(set(leaks))


class PiiMaskingFilter(logging.Filter):
    """Defense in depth: masks e-mails that slip into log records."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = _EMAIL.sub(lambda m: mask_email(m.group(0)), str(record.msg))
        return True
