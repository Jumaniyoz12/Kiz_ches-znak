from __future__ import annotations

import re

from models import MarkCode


GTIN_RE = re.compile(r"\(01\)(\d{14})|01(\d{14})|^(\d{14})")


class LocalCodeValidator:
    """Local checks only. No requests to Honest Sign APIs."""

    def check_code(self, code: str, expected_gtin: str = "") -> MarkCode:
        gtin = extract_gtin(code)
        expected = normalize_gtin(expected_gtin)

        if not code.strip():
            return MarkCode(raw=code, gtin=gtin, is_valid=False, message="Пустой код маркировки")

        if expected and gtin and expected != normalize_gtin(gtin):
            return MarkCode(
                raw=code,
                gtin=gtin,
                is_valid=False,
                message=f"GTIN кода {normalize_gtin(gtin)} не совпадает с таблицей {expected}",
            )

        if expected and not gtin:
            return MarkCode(raw=code, gtin=None, is_valid=True, message="Код напечатан без проверки GTIN")

        return MarkCode(raw=code, gtin=gtin, is_valid=True, message="")



def extract_gtin(code: str) -> str | None:
    compact = code.strip()
    match = GTIN_RE.search(compact)
    if not match:
        return None
    return next(group for group in match.groups() if group)


def normalize_gtin(value: str | None) -> str:
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    if len(digits) == 14 and digits.startswith("0"):
        return digits[1:]
    return digits

