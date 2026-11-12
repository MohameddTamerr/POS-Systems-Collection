from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from .i18n import bidi_ltr, get_language, normalize_digits, normalize_language, tr


@dataclass(frozen=True)
class CurrencySpec:
    code: str
    symbol: str
    decimals: int
    label: str

    @property
    def minor_factor(self) -> int:
        return 10 ** self.decimals


CURRENCIES: dict[str, CurrencySpec] = {
    "USD": CurrencySpec("USD", "$", 2, "USD ($) — US Dollar"),
    "EUR": CurrencySpec("EUR", "€", 2, "EUR (€) — Euro"),
    "GBP": CurrencySpec("GBP", "£", 2, "GBP (£) — British Pound"),
    "EGP": CurrencySpec("EGP", "E£", 2, "EGP (E£) — Egyptian Pound"),
    "SAR": CurrencySpec("SAR", "SAR ", 2, "SAR — Saudi Riyal"),
    "AED": CurrencySpec("AED", "AED ", 2, "AED — UAE Dirham"),
    "CAD": CurrencySpec("CAD", "C$", 2, "CAD (C$) — Canadian Dollar"),
    "AUD": CurrencySpec("AUD", "A$", 2, "AUD (A$) — Australian Dollar"),
    "JPY": CurrencySpec("JPY", "¥", 0, "JPY (¥) — Japanese Yen"),
}

_AR_SUFFIX_SYMBOLS: dict[str, str] = {
    "EGP": "ج.م",
    "SAR": "ر.س",
    "AED": "د.إ",
}


def get_currency(code: str) -> CurrencySpec:
    return CURRENCIES.get((code or "USD").upper(), CURRENCIES["USD"])


def get_currency_label(code: str, lang: str | None = None) -> str:
    spec = get_currency(code)
    translated = tr(f"currency.{spec.code}", lang=lang)
    return spec.label if translated.startswith("currency.") else translated


def get_currency_symbol(code: str, lang: str | None = None) -> tuple[str, bool]:
    """Return (symbol_text, place_after_number) for the requested language."""
    spec = get_currency(code)
    eff = normalize_language(lang) if lang is not None else get_language()
    if eff == "ar-EG" and spec.code in _AR_SUFFIX_SYMBOLS:
        return _AR_SUFFIX_SYMBOLS[spec.code], True
    return spec.symbol, False


def parse_major_to_minor(raw_text: str, currency_code: str) -> int:
    """Convert a major currency string (ASCII or Arabic-Indic digits) to exact integer minor units."""
    spec = get_currency(currency_code)
    normalized = normalize_digits(raw_text or "")
    cleaned = normalized.strip().replace(",", "").replace(" ", "")
    if not cleaned:
        raise ValueError(tr("backend.err.money_required"))
    try:
        dec = Decimal(cleaned)
    except InvalidOperation as exc:
        raise ValueError(tr("backend.err.money_invalid")) from exc

    if not dec.is_finite() or dec < 0:
        raise ValueError(tr("backend.err.money_negative"))

    scaled = (dec * spec.minor_factor).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return int(scaled)


def minor_to_decimal(amount_minor: int, currency_code: str) -> Decimal:
    spec = get_currency(currency_code)
    return Decimal(int(amount_minor)) / Decimal(spec.minor_factor)


def format_minor(
    amount_minor: int,
    currency_code: str,
    include_symbol: bool = True,
    lang: str | None = None,
) -> str:
    spec = get_currency(currency_code)
    dec = minor_to_decimal(amount_minor, currency_code)
    quantizer = Decimal("1") if spec.decimals == 0 else Decimal(f"1.{'0' * spec.decimals}")
    formatted_num = f"{dec.quantize(quantizer, rounding=ROUND_HALF_UP):,}"
    eff = normalize_language(lang) if lang is not None else get_language()
    if not include_symbol:
        return bidi_ltr(formatted_num) if eff == "ar-EG" else formatted_num

    sym, after = get_currency_symbol(spec.code, lang=eff)
    if eff == "ar-EG":
        if after:
            return f"{bidi_ltr(formatted_num)} {sym}"
        return bidi_ltr(f"{sym}{formatted_num}")
    return f"{sym}{formatted_num}"


def format_major_input(amount_minor: int, currency_code: str) -> str:
    spec = get_currency(currency_code)
    dec = minor_to_decimal(amount_minor, currency_code)
    quantizer = Decimal("1") if spec.decimals == 0 else Decimal(f"1.{'0' * spec.decimals}")
    return str(dec.quantize(quantizer, rounding=ROUND_HALF_UP))

