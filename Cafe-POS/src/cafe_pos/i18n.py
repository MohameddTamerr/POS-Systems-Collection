from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from functools import lru_cache
from PySide6.QtCore import QCoreApplication, QLocale, QTranslator, Qt

from .bilingual import (
    CATEGORY_GLOSSARY_AR_TO_EN,
    CATEGORY_GLOSSARY_EN_TO_AR,
    detect_primary_script,
)
from cafe_pos.paths import get_translations_dir

DEFAULT_LANGUAGE = "en"
SUPPORTED_LANGUAGES = ("en", "ar-EG")
RECEIPT_LANGUAGE_OPTIONS = ("follow", "en", "ar-EG")

_LANGUAGE_FILE_MAP = {
    "en": "en.json",
    "ar-EG": "ar_EG.json",
}

_CANONICAL_CATEGORY_KEYS = {
    "all": "category.all",
    "espresso": "category.espresso",
    "cold drinks": "category.cold_drinks",
    "tea & matcha": "category.tea_matcha",
    "bakery": "category.bakery",
    "kitchen": "category.kitchen",
    "retail": "category.retail",
    "other": "category.other",
}

_AR_WEEKDAYS = (
    "الاثنين",
    "الثلاثاء",
    "الأربعاء",
    "الخميس",
    "الجمعة",
    "السبت",
    "الأحد",
)
_EN_WEEKDAYS = (
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
)
_AR_MONTHS_SHORT = (
    "",
    "يناير",
    "فبراير",
    "مارس",
    "أبريل",
    "مايو",
    "يونيو",
    "يوليو",
    "أغسطس",
    "سبتمبر",
    "أكتوبر",
    "نوفمبر",
    "ديسمبر",
)
_EN_MONTHS_SHORT = (
    "",
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)

_DIGIT_TRANS = str.maketrans(
    {
        "٠": "0",
        "١": "1",
        "٢": "2",
        "٣": "3",
        "٤": "4",
        "٥": "5",
        "٦": "6",
        "٧": "7",
        "٨": "8",
        "٩": "9",
        "۰": "0",
        "۱": "1",
        "۲": "2",
        "۳": "3",
        "۴": "4",
        "۵": "5",
        "۶": "6",
        "۷": "7",
        "۸": "8",
        "۹": "9",
        "٫": ".",
        "٬": "",
        "\u200e": "",
        "\u200f": "",
        "\u2066": "",
        "\u2067": "",
        "\u2068": "",
        "\u2069": "",
    }
)

_LOCK = threading.RLock()
_CURRENT_LANGUAGE = DEFAULT_LANGUAGE

_QT_STD_AR_MAP: dict[str, str] = {
    "OK": "موافق",
    "&OK": "موافق",
    "Cancel": "إلغاء",
    "&Cancel": "إلغاء",
    "Yes": "نعم",
    "&Yes": "نعم",
    "No": "لا",
    "&No": "لا",
    "Save": "حفظ",
    "&Save": "حفظ",
    "Discard": "تجاهل",
    "Close": "إغلاق",
    "&Close": "إغلاق",
    "Apply": "تطبيق",
    "Reset": "إعادة ضبط",
    "Open": "فتح",
    "Save All": "حفظ الكل",
    "Yes to All": "نعم للكل",
    "No to All": "لا للكل",
    "Retry": "إعادة المحاولة",
    "Ignore": "تجاهل",
    "Abort": "إيقاف",
    "Help": "مساعدة",
}


class _QtArabicButtonTranslator(QTranslator):
    """Lightweight offline QTranslator for Qt's standard QMessageBox / QDialogButtonBox buttons."""

    def isEmpty(self) -> bool:  # noqa: N802
        return False

    def translate(  # noqa: N802
        self,
        context: str,
        sourceText: str,
        disambiguation: str | None = None,
        n: int = -1,
    ) -> str:
        if context in ("QPlatformTheme", "QDialogButtonBox", "QMessageBox"):
            if sourceText in _QT_STD_AR_MAP:
                return _QT_STD_AR_MAP[sourceText]
            stripped = (sourceText or "").replace("&", "").strip()
            if stripped in _QT_STD_AR_MAP:
                return _QT_STD_AR_MAP[stripped]
        return ""


_QT_AR_TRANSLATOR: _QtArabicButtonTranslator | None = None


def _sync_qt_translator(lang: str) -> None:
    global _QT_AR_TRANSLATOR
    app = QCoreApplication.instance()
    if app is None:
        return
    if _QT_AR_TRANSLATOR is not None:
        app.removeTranslator(_QT_AR_TRANSLATOR)
    if lang == "ar-EG":
        if _QT_AR_TRANSLATOR is None:
            _QT_AR_TRANSLATOR = _QtArabicButtonTranslator(app)
        app.installTranslator(_QT_AR_TRANSLATOR)


def normalize_language(
    lang: str | None,
    *,
    allow_empty: bool = False,
    allow_follow: bool = False,
) -> str:
    raw = (lang or "").strip()
    if not raw:
        if allow_empty:
            return ""
        if allow_follow:
            return "follow"
        return DEFAULT_LANGUAGE

    low = raw.lower().replace("_", "-")
    if low == "follow" and allow_follow:
        return "follow"
    if low in {"ar", "ar-eg", "arabic", "egyptian arabic"}:
        return "ar-EG"
    if low in {"en", "en-us", "en-gb", "english"}:
        return "en"
    if allow_empty:
        return ""
    if allow_follow:
        return "follow"
    return DEFAULT_LANGUAGE


def get_language() -> str:
    with _LOCK:
        return _CURRENT_LANGUAGE


def set_language(lang: str | None) -> str:
    global _CURRENT_LANGUAGE
    norm = normalize_language(lang)
    with _LOCK:
        _CURRENT_LANGUAGE = norm
    _sync_qt_translator(norm)
    return norm


def is_rtl(lang: str | None = None) -> bool:
    eff = normalize_language(lang) if lang is not None else get_language()
    return eff == "ar-EG"


def layout_direction(lang: str | None = None) -> Qt.LayoutDirection:
    return Qt.RightToLeft if is_rtl(lang) else Qt.LeftToRight


def qt_locale(lang: str | None = None) -> QLocale:
    eff = normalize_language(lang) if lang is not None else get_language()
    if eff == "ar-EG":
        return QLocale(QLocale.Language.Arabic, QLocale.Country.Egypt)
    return QLocale(QLocale.Language.English, QLocale.Country.UnitedStates)


def resolve_receipt_language(
    receipt_lang: str | None, ui_lang: str | None = None
) -> str:
    norm = normalize_language(receipt_lang, allow_follow=True)
    if norm == "follow":
        return normalize_language(ui_lang) if ui_lang is not None else get_language()
    return norm


@lru_cache(maxsize=4)
def load_catalog(lang: str) -> dict[str, str]:
    norm = normalize_language(lang)
    fname = _LANGUAGE_FILE_MAP.get(norm, "en.json")
    path = get_translations_dir() / fname
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return {}
    return {str(k): str(v) for k, v in data.items()}


def tr(key: str, lang: str | None = None, **kwargs: object) -> str:
    eff = normalize_language(lang) if lang is not None else get_language()
    catalog = load_catalog(eff)
    template = catalog.get(key)
    if template is None and eff != DEFAULT_LANGUAGE:
        template = load_catalog(DEFAULT_LANGUAGE).get(key)
    if template is None:
        return key
    if kwargs:
        try:
            return template.format(**kwargs)
        except Exception:
            return template
    return template


def bidi_ltr(text: object) -> str:
    """Wrap LTR spans (numbers, codes, timestamps, URLs) safely inside RTL sentences."""
    s = str(text)
    if not s:
        return ""
    return f"\u2066{s}\u2069"


def bidi_for_lang(text: object, lang: str | None = None) -> str:
    eff = normalize_language(lang) if lang is not None else get_language()
    if eff == "ar-EG":
        return bidi_ltr(text)
    return str(text)


def normalize_digits(text: str) -> str:
    """Convert Arabic-Indic numerals & Arabic decimal separators to ASCII safely."""
    return (text or "").translate(_DIGIT_TRANS)


_EN_PLURALS = {
    "item": ("{n} item", "{n} items"),
    "order": ("{n} order", "{n} orders"),
    "unit": ("{n} unit", "{n} units"),
    "user": ("{n} user", "{n} users"),
    "event": ("{n} event", "{n} events"),
    "photo": ("{n} photo", "{n} photos"),
    "archive": ("{n} archive", "{n} archives"),
    "cashier": ("{n} cashier", "{n} cashiers"),
    "product": ("{n} product", "{n} products"),
    "image": ("{n} image", "{n} images"),
    "category": ("{n} category", "{n} categories"),
    "receipt": ("{n} receipt", "{n} receipts"),
    "group": ("{n} group", "{n} groups"),
    "option": ("{n} option", "{n} options"),
    "held": ("{n} held", "{n} held"),
}

_AR_PLURALS = {
    # singular_one, dual_two, few_3_to_10, many_rest, zero_text
    "item": ("صنف واحد", "صنفان", "{n} أصناف", "{n} صنف", "0 أصناف"),
    "order": ("طلب واحد", "طلبان", "{n} طلبات", "{n} طلب", "0 طلبات"),
    "unit": ("وحدة واحدة", "وحدتان", "{n} وحدات", "{n} وحدة", "0 وحدات"),
    "user": ("مستخدم واحد", "مستخدمان", "{n} مستخدمين", "{n} مستخدم", "0 مستخدمين"),
    "event": ("حدث واحد", "حدثان", "{n} أحداث", "{n} حدث", "0 أحداث"),
    "photo": ("صورة واحدة", "صورتان", "{n} صور", "{n} صورة", "0 صور"),
    "archive": ("نسخة واحدة", "نسختان", "{n} نسخ", "{n} نسخة", "0 نسخ"),
    "cashier": ("كاشير واحد", "كاشيران", "{n} كاشيرات", "{n} كاشير", "0 كاشيرات"),
    "product": ("منتج واحد", "منتجان", "{n} منتجات", "{n} منتج", "0 منتجات"),
    "image": ("صورة واحدة", "صورتان", "{n} صور", "{n} صورة", "0 صور"),
    "category": ("تصنيف واحد", "تصنيفان", "{n} تصنيفات", "{n} تصنيف", "0 تصنيفات"),
    "receipt": ("إيصال واحد", "إيصالان", "{n} إيصالات", "{n} إيصال", "0 إيصالات"),
    "group": ("مجموعة واحدة", "مجموعتان", "{n} مجموعات", "{n} مجموعة", "0 مجموعات"),
    "option": ("خيار واحد", "خياران", "{n} خيارات", "{n} خيار", "0 خيارات"),
    "held": ("طلب معلق واحد", "طلبان معلقان", "{n} طلبات معلقة", "{n} طلب معلق", "0 طلبات معلقة"),
}


def tr_count(kind: str, count: int, lang: str | None = None) -> str:
    eff = normalize_language(lang) if lang is not None else get_language()
    n = max(0, int(count))
    if eff == "ar-EG":
        forms = _AR_PLURALS.get(kind)
        if forms is None:
            return str(n)
        one, two, few, many, zero = forms
        if n == 0:
            return zero
        if n == 1:
            return one
        if n == 2:
            return two
        mod100 = n % 100
        if 3 <= mod100 <= 10:
            return few.format(n=n)
        return many.format(n=n)

    en_forms = _EN_PLURALS.get(kind)
    if en_forms is None:
        return str(n)
    singular, plural = en_forms
    return (singular if n == 1 else plural).format(n=n)


def tr_category(category: str, lang: str | None = None) -> str:
    clean = (category or "").strip()
    if not clean:
        return tr("category.other", lang=lang)
    eff = normalize_language(lang) if lang is not None else get_language()
    key = _CANONICAL_CATEGORY_KEYS.get(clean.lower())
    if key:
        return tr(key, lang=eff)
    if eff == "ar-EG" and detect_primary_script(clean) != "ar":
        ar_hit = CATEGORY_GLOSSARY_EN_TO_AR.get(clean.casefold())
        if ar_hit:
            return ar_hit
    elif eff == "en" and detect_primary_script(clean) == "ar":
        en_hit = CATEGORY_GLOSSARY_AR_TO_EN.get(clean)
        if en_hit:
            return en_hit
    return clean


def tr_category_record(cat: object, lang: str | None = None) -> str:
    """Resolve a CategoryRecord cleanly in active language with glossary fallback."""
    eff = normalize_language(lang) if lang is not None else get_language()
    name_en = (getattr(cat, "name_en", "") or "").strip()
    name_ar = (getattr(cat, "name_ar", "") or "").strip()
    if eff == "ar-EG":
        if name_ar:
            return name_ar
        return tr_category(name_en, lang="ar-EG")
    if name_en:
        return name_en
    return tr_category(name_ar, lang="en")


def tr_country(code: str, lang: str | None = None) -> str:
    norm = (code or "US").strip().upper()
    key = f"country.{norm}"
    translated = tr(key, lang=lang)
    return norm if translated == key else translated


def tr_payment_method(
    method: str,
    *,
    custom_label: str = "",
    provider: str = "",
    lang: str | None = None,
) -> str:
    norm = (method or "CASH").strip().upper()
    if norm == "CARD":
        base = tr("pay.method.card", lang=lang)
    elif norm == "APPLE_PAY":
        base = tr("pay.method.apple_pay", lang=lang)
    elif norm == "INSTAPAY":
        base = tr("pay.method.instapay", lang=lang)
    elif norm == "EWALLET":
        base = tr("pay.method.ewallet", lang=lang)
    elif norm == "CUSTOM":
        base = (custom_label or "").strip() or tr("pay.method.custom", lang=lang)
    else:
        base = tr("pay.method.cash", lang=lang)
    clean_prov = (provider or "").strip()
    if clean_prov:
        return f"{base} ({clean_prov})"
    return base


def tr_payment_status(status: str, lang: str | None = None) -> str:
    norm = (status or "cash_settled").strip().lower()
    if norm == "confirmed_external":
        return tr("pay.status.confirmed_external", lang=lang)
    return tr("pay.status.cash_settled", lang=lang)


def tr_order_type(order_type: str, lang: str | None = None) -> str:
    norm = (order_type or "DINE_IN").strip().upper()
    if norm == "TAKEAWAY":
        return tr("order_type.takeaway", lang=lang)
    if norm == "DELIVERY":
        return tr("order_type.delivery", lang=lang)
    return tr("order_type.dine_in", lang=lang)


def format_order_type_summary(
    order_type: str,
    *,
    table_number: str = "",
    customer_name: str = "",
    lang: str | None = None,
) -> str:
    norm = (order_type or "DINE_IN").strip().upper()
    base = tr_order_type(norm, lang=lang)
    tbl = (table_number or "").strip()
    cust = (customer_name or "").strip()
    if norm == "DINE_IN" and tbl:
        return f"{base} · {tr('order_type.table_short', table=tbl, lang=lang)}"
    if norm == "DELIVERY" and cust:
        return f"{base} · {cust}"
    return base


def canonical_category_or_raw(text: str) -> str:
    """Resolve localized or English category text back to canonical English name if known."""
    clean = (text or "").strip()
    if not clean:
        return ""
    for lang in SUPPORTED_LANGUAGES:
        for canon_lower, key in _CANONICAL_CATEGORY_KEYS.items():
            if clean == tr(key, lang=lang):
                # Title-case canonical string match
                for c in (
                    "All",
                    "Espresso",
                    "Cold Drinks",
                    "Tea & Matcha",
                    "Bakery",
                    "Kitchen",
                    "Retail",
                    "Other",
                ):
                    if c.lower() == canon_lower:
                        return c
    return clean


def tr_role(role: str, lang: str | None = None) -> str:
    norm = (role or "").strip().upper()
    if norm == "ADMIN":
        return tr("role.admin", lang=lang)
    return tr("role.cashier", lang=lang)


def tr_role_long(role: str, lang: str | None = None) -> str:
    norm = (role or "").strip().upper()
    if norm == "ADMIN":
        return tr("role.admin_long", lang=lang)
    return tr("role.cashier_long", lang=lang)


def tr_audit_action(action: str, lang: str | None = None) -> str:
    clean = (action or "").strip().upper()
    key = f"audit.{clean}"
    translated = tr(key, lang=lang)
    return clean if translated == key else translated


def format_header_date(dt: datetime | None = None, lang: str | None = None) -> str:
    now = dt or datetime.now()
    eff = normalize_language(lang) if lang is not None else get_language()
    wd = now.weekday()
    if eff == "ar-EG":
        return f"{_AR_WEEKDAYS[wd]}، {now.day} {_AR_MONTHS_SHORT[now.month]}"
    return f"{_EN_WEEKDAYS[wd]}, {now.day:02d} {_EN_MONTHS_SHORT[now.month]}"


def format_timestamp_display(
    iso_utc: str,
    lang: str | None = None,
    *,
    include_seconds: bool = False,
) -> str:
    """Format stored ISO-8601 UTC string cleanly in local time without bidi scrambling."""
    raw = (iso_utc or "").strip()
    if not raw:
        return tr("common.never", lang=lang)
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        local_dt = dt.astimezone()
    except ValueError:
        return bidi_for_lang(raw, lang=lang)

    eff = normalize_language(lang) if lang is not None else get_language()
    date_part = local_dt.strftime("%Y-%m-%d")
    hour12 = local_dt.hour % 12 or 12
    sec_part = f":{local_dt.second:02d}" if include_seconds else ""
    time_digits = f"{hour12:02d}:{local_dt.minute:02d}{sec_part}"

    if eff == "ar-EG":
        period = "م" if local_dt.hour >= 12 else "ص"
        return f"{bidi_ltr(date_part)}  {bidi_ltr(time_digits)} {period}"
    period_en = "PM" if local_dt.hour >= 12 else "AM"
    return f"{date_part}  {time_digits} {period_en}"


def format_chart_bucket_label(
    date_key: str,
    fallback_label: str = "",
    lang: str | None = None,
) -> str:
    eff = normalize_language(lang) if lang is not None else get_language()
    key = (date_key or "").strip()
    try:
        if len(key) == 5 and key[2] == ":":
            # Hourly bucket HH:00
            dt = datetime.strptime(key, "%H:%M")
            h12 = dt.hour % 12 or 12
            if eff == "ar-EG":
                suf = "م" if dt.hour >= 12 else "ص"
                return f"{h12} {suf}"
            suf_en = "PM" if dt.hour >= 12 else "AM"
            return f"{h12} {suf_en}"
        if len(key) == 16 and " " in key:
            # Hourly bucket YYYY-MM-DD HH:00
            dt = datetime.strptime(key, "%Y-%m-%d %H:%M")
            h12 = dt.hour % 12 or 12
            if eff == "ar-EG":
                suf = "م" if dt.hour >= 12 else "ص"
                return f"{h12} {suf}"
            suf_en = "PM" if dt.hour >= 12 else "AM"
            return f"{h12} {suf_en}"
        if len(key) == 10:
            # Daily bucket YYYY-MM-DD
            dt = datetime.strptime(key, "%Y-%m-%d")
            if eff == "ar-EG":
                return f"{dt.day} {_AR_MONTHS_SHORT[dt.month]}"
            return f"{_EN_MONTHS_SHORT[dt.month]} {dt.day:02d}"
        if len(key) == 7:
            # Monthly bucket YYYY-MM
            dt = datetime.strptime(key, "%Y-%m")
            if eff == "ar-EG":
                return f"{_AR_MONTHS_SHORT[dt.month]} {dt.year}"
            return f"{_EN_MONTHS_SHORT[dt.month]} {dt.year}"
    except ValueError:
        pass
    return fallback_label or key
