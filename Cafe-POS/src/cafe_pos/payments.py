from __future__ import annotations

from dataclasses import dataclass, field
import json
import re
from typing import Iterable

from .i18n import tr

COUNTRY_CODES: tuple[str, ...] = ("EG", "SA", "AE", "US", "CUSTOM")

COUNTRY_DEFAULT_CURRENCY: dict[str, str] = {
    "EG": "EGP",
    "SA": "SAR",
    "AE": "AED",
    "US": "USD",
    "CUSTOM": "USD",
}

METHOD_CASH = "CASH"
METHOD_CARD = "CARD"
METHOD_APPLE_PAY = "APPLE_PAY"
METHOD_INSTAPAY = "INSTAPAY"
METHOD_EWALLET = "EWALLET"
METHOD_CUSTOM = "CUSTOM"

STANDARD_PAYMENT_METHODS: tuple[str, ...] = (
    METHOD_CASH,
    METHOD_APPLE_PAY,
    METHOD_INSTAPAY,
    METHOD_EWALLET,
    METHOD_CARD,
)

ALL_PAYMENT_METHODS: tuple[str, ...] = (
    METHOD_CASH,
    METHOD_CARD,
    METHOD_APPLE_PAY,
    METHOD_INSTAPAY,
    METHOD_EWALLET,
    METHOD_CUSTOM,
)

STATUS_CASH_SETTLED = "CASH_SETTLED"
STATUS_CONFIRMED_EXTERNAL = "CONFIRMED_EXTERNAL"
ALLOWED_PAYMENT_STATUSES = frozenset({STATUS_CASH_SETTLED, STATUS_CONFIRMED_EXTERNAL})

VERIFICATION_CASH_DRAWER = "cashier_cash_drawer"
VERIFICATION_MANUAL_CONFIRM = "cashier_manual_confirmation"
ALLOWED_VERIFICATION_SOURCES = frozenset(
    {VERIFICATION_CASH_DRAWER, VERIFICATION_MANUAL_CONFIRM}
)

# Reject full 13-19 digit card PAN patterns (even spaced or hyphenated) so sensitive card numbers
# are never typed or stored inside external reference notes.
_PAN_LIKE_RE = re.compile(r"(?<!\d)(?:\d[\s-]*){13,19}(?!\d)")

# Regional defaults: ONLY manual POS/external settlement channels (no fake payment gateways).
COUNTRY_PRESETS: dict[str, dict[str, object]] = {
    "EG": {
        "currency": "EGP",
        "enabled_methods": [METHOD_CASH, METHOD_APPLE_PAY, METHOD_INSTAPAY, METHOD_EWALLET],
        "card_brands": ["Meeza", "Visa", "Mastercard"],
        "wallet_providers": ["Vodafone Cash", "Orange Cash", "e& money", "WE Pay"],
    },
    "SA": {
        "currency": "SAR",
        "enabled_methods": [METHOD_CASH, METHOD_CARD, METHOD_APPLE_PAY, METHOD_EWALLET],
        "card_brands": ["mada", "Visa", "Mastercard"],
        "wallet_providers": ["STC Pay", "urpay"],
    },
    "AE": {
        "currency": "AED",
        "enabled_methods": [METHOD_CASH, METHOD_CARD, METHOD_APPLE_PAY, METHOD_EWALLET],
        "card_brands": ["Visa", "Mastercard", "American Express"],
        "wallet_providers": ["Google Pay", "Samsung Pay", "Botim Pay"],
    },
    "US": {
        "currency": "USD",
        "enabled_methods": [METHOD_CASH, METHOD_CARD, METHOD_APPLE_PAY, METHOD_EWALLET],
        "card_brands": ["Visa", "Mastercard", "American Express", "Discover"],
        "wallet_providers": ["Google Pay", "Cash App / Venmo"],
    },
    "CUSTOM": {
        "currency": "USD",
        "enabled_methods": [METHOD_CASH, METHOD_CARD],
        "card_brands": ["Visa", "Mastercard", "Local Card"],
        "wallet_providers": ["Mobile Wallet"],
    },
}


@dataclass(frozen=True)
class CustomPaymentMethodDef:
    id: str
    label_en: str
    label_ar: str = ""
    requires_reference: bool = False
    enabled: bool = True

    def display_label(self, lang: str | None = None) -> str:
        from .i18n import normalize_language

        resolved = normalize_language(lang)
        if resolved == "ar-EG" and self.label_ar.strip():
            return self.label_ar.strip()
        return self.label_en.strip() or self.label_ar.strip() or self.id


@dataclass
class PaymentSettingsConfig:
    country: str = "US"
    customized: bool = False
    enabled_methods: list[str] = field(
        default_factory=lambda: list(COUNTRY_PRESETS["US"]["enabled_methods"])  # type: ignore[arg-type]
    )
    card_brands: list[str] = field(
        default_factory=lambda: list(COUNTRY_PRESETS["US"]["card_brands"])  # type: ignore[arg-type]
    )
    wallet_providers: list[str] = field(
        default_factory=lambda: list(COUNTRY_PRESETS["US"]["wallet_providers"])  # type: ignore[arg-type]
    )
    disabled_wallet_providers: list[str] = field(default_factory=list)
    custom_methods: list[CustomPaymentMethodDef] = field(default_factory=list)
    require_card_reference: bool = False
    require_apple_pay_reference: bool = False
    require_instapay_reference: bool = False
    require_ewallet_reference: bool = False
    instapay_instructions: str = ""

    def active_wallet_providers(self) -> list[str]:
        disabled = {p.casefold() for p in (self.disabled_wallet_providers or []) if p}
        active = [p for p in (self.wallet_providers or []) if p.casefold() not in disabled]
        return active if active else list((self.wallet_providers or [])[:1])


@dataclass(frozen=True)
class ValidatedPaymentPayload:
    payment_method: str
    payment_provider: str
    custom_method_label: str
    payment_amount_minor: int
    cash_received_minor: int
    change_minor: int
    payment_status: str
    external_reference: str
    verification_source: str


def normalize_country_code(code: str | None) -> str:
    upper = (code or "").strip().upper()
    return upper if upper in COUNTRY_PRESETS else "US"


def _dedupe_clean_strings(items: Iterable[object], *, max_len: int = 40) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in items:
        s = " ".join(str(raw or "").strip().split())[:max_len]
        if not s:
            continue
        k = s.casefold()
        if k not in seen:
            seen.add(k)
            out.append(s)
    return out


def default_payment_settings(country: str = "US") -> PaymentSettingsConfig:
    norm = normalize_country_code(country)
    preset = COUNTRY_PRESETS[norm]
    return PaymentSettingsConfig(
        country=norm,
        customized=False,
        enabled_methods=list(preset["enabled_methods"]),  # type: ignore[arg-type]
        card_brands=list(preset["card_brands"]),  # type: ignore[arg-type]
        wallet_providers=list(preset["wallet_providers"]),  # type: ignore[arg-type]
        disabled_wallet_providers=[],
        custom_methods=[],
        require_card_reference=False,
        require_apple_pay_reference=False,
        require_instapay_reference=False,
        require_ewallet_reference=False,
        instapay_instructions="",
    )


def parse_payment_settings(raw_json: str | None, fallback_country: str = "US") -> PaymentSettingsConfig:
    base = default_payment_settings(fallback_country)
    text = (raw_json or "").strip()
    if not text:
        return base
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return base
    if not isinstance(data, dict):
        return base

    country = normalize_country_code(data.get("country") or fallback_country)
    preset = COUNTRY_PRESETS[country]

    customized = bool(data.get("customized", False))
    raw_methods = data.get("enabled_methods")
    methods: list[str] = []
    if isinstance(raw_methods, list):
        for m in raw_methods:
            mu = str(m or "").strip().upper()
            if mu in STANDARD_PAYMENT_METHODS and mu not in methods:
                methods.append(mu)
    if not methods:
        methods = list(preset["enabled_methods"])  # type: ignore[arg-type]

    # When an upgraded store never customized its regional payment settings, refresh preset defaults
    if not customized and country == "EG":
        if methods == [METHOD_CASH, METHOD_CARD, METHOD_INSTAPAY, METHOD_EWALLET]:
            methods = list(preset["enabled_methods"])  # type: ignore[arg-type]

    custom_methods: list[CustomPaymentMethodDef] = []
    seen_ids: set[str] = set()
    raw_custom = data.get("custom_methods")
    if isinstance(raw_custom, list):
        for item in raw_custom:
            if not isinstance(item, dict):
                continue
            cid = re.sub(r"[^A-Za-z0-9_-]+", "_", str(item.get("id") or "").strip()).strip("_")[:32]
            len_en = " ".join(str(item.get("label_en") or "").strip().split())[:48]
            len_ar = " ".join(str(item.get("label_ar") or "").strip().split())[:48]
            if not (len_en or len_ar):
                continue
            if not cid:
                cid = f"custom_{len(custom_methods) + 1}"
            if cid.casefold() in seen_ids:
                cid = f"{cid}_{len(custom_methods) + 1}"
            seen_ids.add(cid.casefold())
            custom_methods.append(
                CustomPaymentMethodDef(
                    id=cid,
                    label_en=len_en or len_ar,
                    label_ar=len_ar,
                    requires_reference=bool(item.get("requires_reference", False)),
                    enabled=bool(item.get("enabled", True)),
                )
            )

    raw_brands = data.get("card_brands")
    card_brands = (
        _dedupe_clean_strings(raw_brands)
        if isinstance(raw_brands, list)
        else list(preset["card_brands"])  # type: ignore[arg-type]
    )
    if not card_brands:
        card_brands = list(preset["card_brands"])  # type: ignore[arg-type]

    raw_wallets = data.get("wallet_providers")
    wallet_providers = (
        _dedupe_clean_strings(raw_wallets)
        if isinstance(raw_wallets, list)
        else list(preset["wallet_providers"])  # type: ignore[arg-type]
    )
    if not customized and country == "EG":
        wallet_providers = [
            "e& money" if w.casefold() == "etisalat cash" else w for w in wallet_providers
        ]
    elif not customized and country in {"SA", "AE", "US"}:
        # Apple Pay moved to a first-class selectable payment method
        wallet_providers = [w for w in wallet_providers if w.casefold() != "apple pay"]
    if not wallet_providers:
        wallet_providers = list(preset["wallet_providers"])  # type: ignore[arg-type]

    raw_disabled = data.get("disabled_wallet_providers")
    valid_wallet_keys = {w.casefold() for w in wallet_providers}
    disabled_wallets = [
        d
        for d in _dedupe_clean_strings(raw_disabled if isinstance(raw_disabled, list) else [])
        if d.casefold() in valid_wallet_keys
    ]

    # Guarantee Cash or at least one active payment option always exists
    has_active_custom = any(c.enabled for c in custom_methods)
    if not methods and not has_active_custom:
        methods = [METHOD_CASH]

    instapay_instructions = " ".join(
        str(data.get("instapay_instructions") or "").strip().split()
    )[:180]

    return PaymentSettingsConfig(
        country=country,
        customized=customized,
        enabled_methods=methods,
        card_brands=card_brands,
        wallet_providers=wallet_providers,
        disabled_wallet_providers=disabled_wallets,
        custom_methods=custom_methods,
        require_card_reference=bool(data.get("require_card_reference", False)),
        require_apple_pay_reference=bool(data.get("require_apple_pay_reference", False)),
        require_instapay_reference=bool(data.get("require_instapay_reference", False)),
        require_ewallet_reference=bool(data.get("require_ewallet_reference", False)),
        instapay_instructions=instapay_instructions,
    )


def serialize_payment_settings(cfg: PaymentSettingsConfig) -> str:
    norm_country = normalize_country_code(cfg.country)
    methods: list[str] = []
    for m in cfg.enabled_methods or []:
        mu = str(m or "").strip().upper()
        if mu in STANDARD_PAYMENT_METHODS and mu not in methods:
            methods.append(mu)

    customs = []
    seen_ids: set[str] = set()
    for c in cfg.custom_methods or []:
        en = " ".join((c.label_en or "").strip().split())[:48]
        ar = " ".join((c.label_ar or "").strip().split())[:48]
        if not (en or ar):
            continue
        cid = re.sub(r"[^A-Za-z0-9_-]+", "_", (c.id or "").strip()).strip("_")[:32] or f"custom_{len(customs) + 1}"
        if cid.casefold() in seen_ids:
            cid = f"{cid}_{len(customs) + 1}"
        seen_ids.add(cid.casefold())
        customs.append(
            {
                "id": cid,
                "label_en": en or ar,
                "label_ar": ar,
                "requires_reference": bool(c.requires_reference),
                "enabled": bool(c.enabled),
            }
        )

    if not methods and not any(c["enabled"] for c in customs):
        methods = [METHOD_CASH]

    preset = COUNTRY_PRESETS[norm_country]
    card_brands = _dedupe_clean_strings(cfg.card_brands) or list(preset["card_brands"])  # type: ignore[arg-type]
    wallet_providers = _dedupe_clean_strings(cfg.wallet_providers) or list(preset["wallet_providers"])  # type: ignore[arg-type]
    valid_wallet_keys = {w.casefold() for w in wallet_providers}
    disabled_wallets = [
        d
        for d in _dedupe_clean_strings(cfg.disabled_wallet_providers or [])
        if d.casefold() in valid_wallet_keys
    ]
    instapay_instructions = " ".join(
        str(cfg.instapay_instructions or "").strip().split()
    )[:180]

    payload = {
        "country": norm_country,
        "customized": bool(cfg.customized),
        "enabled_methods": methods,
        "card_brands": card_brands,
        "wallet_providers": wallet_providers,
        "disabled_wallet_providers": disabled_wallets,
        "custom_methods": customs,
        "require_card_reference": bool(cfg.require_card_reference),
        "require_apple_pay_reference": bool(cfg.require_apple_pay_reference),
        "require_instapay_reference": bool(cfg.require_instapay_reference),
        "require_ewallet_reference": bool(cfg.require_ewallet_reference),
        "instapay_instructions": instapay_instructions,
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def apply_country_preset(
    current: PaymentSettingsConfig,
    new_country: str,
    *,
    force: bool = False,
) -> PaymentSettingsConfig:
    """Switch store country; only apply regional method defaults when uncustomized or forced."""
    norm = normalize_country_code(new_country)
    if current.customized and not force:
        return PaymentSettingsConfig(
            country=norm,
            customized=True,
            enabled_methods=list(current.enabled_methods),
            card_brands=list(current.card_brands),
            wallet_providers=list(current.wallet_providers),
            disabled_wallet_providers=list(current.disabled_wallet_providers),
            custom_methods=list(current.custom_methods),
            require_card_reference=current.require_card_reference,
            require_apple_pay_reference=current.require_apple_pay_reference,
            require_instapay_reference=current.require_instapay_reference,
            require_ewallet_reference=current.require_ewallet_reference,
            instapay_instructions=current.instapay_instructions,
        )

    preset = COUNTRY_PRESETS[norm]
    return PaymentSettingsConfig(
        country=norm,
        customized=False,
        enabled_methods=list(preset["enabled_methods"]),  # type: ignore[arg-type]
        card_brands=list(preset["card_brands"]),  # type: ignore[arg-type]
        wallet_providers=list(preset["wallet_providers"]),  # type: ignore[arg-type]
        disabled_wallet_providers=[],
        custom_methods=list(current.custom_methods),
        require_card_reference=current.require_card_reference,
        require_apple_pay_reference=current.require_apple_pay_reference,
        require_instapay_reference=current.require_instapay_reference,
        require_ewallet_reference=current.require_ewallet_reference,
        instapay_instructions=current.instapay_instructions,
    )


def validate_money_minor(value: int, *, field_label: str = "Amount") -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(tr("backend.err.price_non_neg"))
    return int(value)


def validate_external_reference(reference: str | None, *, required: bool = False) -> str:
    clean = " ".join((reference or "").strip().split())[:64]
    if required and not clean:
        raise ValueError(tr("backend.err.payment_ref_required"))
    if clean and _PAN_LIKE_RE.search(clean):
        raise ValueError(tr("backend.err.payment_pan_forbidden"))
    return clean


def validate_payment_payload(
    *,
    total_minor: int,
    payment_method: str,
    payment_amount_minor: int | None = None,
    cash_received_minor: int = 0,
    payment_provider: str = "",
    custom_method_label: str = "",
    external_reference: str = "",
    confirmed_external: bool = False,
    reference_required: bool = False,
) -> ValidatedPaymentPayload:
    if not isinstance(total_minor, int) or total_minor < 0:
        raise ValueError(tr("backend.err.price_non_neg"))

    method = (payment_method or "").strip().upper()
    if method not in ALL_PAYMENT_METHODS:
        raise ValueError(tr("backend.err.payment_invalid_method"))

    clean_provider = " ".join((payment_provider or "").strip().split())[:64]
    clean_custom_label = " ".join((custom_method_label or "").strip().split())[:64]

    if method == METHOD_CASH:
        if not isinstance(cash_received_minor, int) or cash_received_minor < 0:
            raise ValueError(tr("backend.err.cash_negative"))
        if cash_received_minor < total_minor:
            raise ValueError(tr("backend.err.cash_insufficient"))
        change_minor = cash_received_minor - total_minor
        return ValidatedPaymentPayload(
            payment_method=METHOD_CASH,
            payment_provider="",
            custom_method_label="",
            payment_amount_minor=total_minor,
            cash_received_minor=cash_received_minor,
            change_minor=change_minor,
            payment_status=STATUS_CASH_SETTLED,
            external_reference=validate_external_reference(external_reference, required=False),
            verification_source=VERIFICATION_CASH_DRAWER,
        )

    # Non-cash (Card / InstaPay / E-Wallet / Custom): strictly manual POS/external confirmation
    if not confirmed_external:
        raise ValueError(tr("backend.err.payment_confirm_required"))

    eff_amount = total_minor if payment_amount_minor is None else payment_amount_minor
    if not isinstance(eff_amount, int) or eff_amount < 0:
        raise ValueError(tr("backend.err.payment_amount_negative"))
    if eff_amount < total_minor:
        raise ValueError(tr("backend.err.payment_insufficient"))

    if method == METHOD_CUSTOM and not clean_custom_label:
        raise ValueError(tr("backend.err.payment_custom_label_req"))

    clean_ref = validate_external_reference(external_reference, required=reference_required)
    return ValidatedPaymentPayload(
        payment_method=method,
        payment_provider=clean_provider,
        custom_method_label=clean_custom_label,
        payment_amount_minor=total_minor,
        cash_received_minor=0,
        change_minor=0,
        payment_status=STATUS_CONFIRMED_EXTERNAL,
        external_reference=clean_ref,
        verification_source=VERIFICATION_MANUAL_CONFIRM,
    )

