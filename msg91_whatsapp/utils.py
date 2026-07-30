"""Shared helpers."""

import re


def normalize_phone(num):
    """Strip everything that isn't a digit."""
    return re.sub(r"\D", "", str(num or ""))


def phone_key(num):
    """Last 10 digits — tolerant match across differing country-code formats."""
    digits = normalize_phone(num)
    return digits[-10:] if len(digits) >= 10 else digits
