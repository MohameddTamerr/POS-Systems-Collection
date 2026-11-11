from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


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


def get_currency(code: str) -> CurrencySpec:
    return CURRENCIES.get((code or "USD").upper(), CURRENCIES["USD"])


def parse_major_to_minor(raw_text: str, currency_code: str) -> int:
    """Convert a major currency string (e.g. '4.50') to exact integer minor units."""
    spec = get_currency(currency_code)
    cleaned = (raw_text or "").strip().replace(",", "")
    if not cleaned:
        raise ValueError("Amount is required.")
    try:
        dec = Decimal(cleaned)
    except InvalidOperation as exc:
        raise ValueError("Enter a valid numeric amount.") from exc

    if not dec.is_finite() or dec < 0:
        raise ValueError("Amount cannot be negative.")

    scaled = (dec * spec.minor_factor).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return int(scaled)


def minor_to_decimal(amount_minor: int, currency_code: str) -> Decimal:
    spec = get_currency(currency_code)
    return Decimal(int(amount_minor)) / Decimal(spec.minor_factor)


def format_minor(amount_minor: int, currency_code: str, include_symbol: bool = True) -> str:
    spec = get_currency(currency_code)
    dec = minor_to_decimal(amount_minor, currency_code)
    quantizer = Decimal("1") if spec.decimals == 0 else Decimal(f"1.{'0' * spec.decimals}")
    formatted_num = f"{dec.quantize(quantizer, rounding=ROUND_HALF_UP):,}"
    if not include_symbol:
        return formatted_num
    return f"{spec.symbol}{formatted_num}"


def format_major_input(amount_minor: int, currency_code: str) -> str:
    spec = get_currency(currency_code)
    dec = minor_to_decimal(amount_minor, currency_code)
    quantizer = Decimal("1") if spec.decimals == 0 else Decimal(f"1.{'0' * spec.decimals}")
    return str(dec.quantize(quantizer, rounding=ROUND_HALF_UP))

