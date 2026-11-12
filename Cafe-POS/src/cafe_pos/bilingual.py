from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Callable
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

_AR_CHAR_RE = re.compile(r"[\u0600-\u06FF]")
_LATIN_CHAR_RE = re.compile(r"[A-Za-z]")
# Arabic diacritics / harakat & tatweel stripped safely for Unicode category key comparison
_AR_DIACRITICS_RE = re.compile(r"[\u0610-\u061A\u0640\u064B-\u065F\u0670\u06D6-\u06ED]")


@dataclass(frozen=True)
class TranslationSuggestion:
    source_text: str
    source_lang: str  # "en" | "ar"
    target_text: str
    target_lang: str  # "ar" | "en"
    confidence: str   # "confirmed" | "high" | "medium" | "none"
    source: str       # "user_cache" | "cache" | "offline_dict" | "online_api" | "none"


# Comprehensive offline EN <-> AR Café Category Glossary
CATEGORY_GLOSSARY_EN_TO_AR: dict[str, str] = {
    "all": "كل القائمة",
    "espresso": "إسبريسو",
    "cold drinks": "مشروبات باردة",
    "cold coffee": "قهوة باردة",
    "iced drinks": "مشروبات مثلجة",
    "hot drinks": "مشروبات ساخنة",
    "tea & matcha": "شاي وماتشا",
    "tea": "شاي",
    "matcha": "ماتشا",
    "coffee": "قهوة",
    "specialty coffee": "قهوة مختصة",
    "specialty": "مختصة",
    "bakery": "مخبوزات",
    "pastries": "معجنات ومخبوزات",
    "pastry": "معجنات",
    "kitchen": "المطبخ",
    "sandwiches": "ساندوتشات",
    "sandwich": "ساندوتش",
    "desserts": "حلويات",
    "dessert": "حلويات",
    "sweets": "حلويات",
    "milkshakes": "ميلك شيك",
    "milkshake": "ميلك شيك",
    "smoothies": "سموذي",
    "smoothie": "سموذي",
    "fresh juices": "عصائر طازجة",
    "juices": "عصائر",
    "juice": "عصير",
    "frappe": "فرابيه",
    "mojito": "موهيتو",
    "breakfast": "إفطار",
    "salads": "سلطات",
    "snacks": "سناكس",
    "retail": "منتجات البيع",
    "coffee beans": "حبوب قهوة",
    "water & soda": "مياه ومشروبات غازية",
    "other": "أخرى",
}

CATEGORY_GLOSSARY_AR_TO_EN: dict[str, str] = {
    "كل القائمة": "All",
    "إسبريسو": "Espresso",
    "اسبريسو": "Espresso",
    "مشروبات باردة": "Cold Drinks",
    "مشروبات بارده": "Cold Drinks",
    "قهوة باردة": "Cold Coffee",
    "مشروبات مثلجة": "Iced Drinks",
    "مشروبات ساخنة": "Hot Drinks",
    "شاي وماتشا": "Tea & Matcha",
    "شاي": "Tea",
    "ماتشا": "Matcha",
    "قهوة": "Coffee",
    "قهوه": "Coffee",
    "قهوة مختصة": "Specialty Coffee",
    "مختصة": "Specialty",
    "مخبوزات": "Bakery",
    "معجنات ومخبوزات": "Pastries",
    "معجنات": "Pastries",
    "المطبخ": "Kitchen",
    "مطبخ": "Kitchen",
    "ساندوتشات": "Sandwiches",
    "ساندويتشات": "Sandwiches",
    "ساندوتش": "Sandwiches",
    "حلويات": "Desserts",
    "حلى": "Desserts",
    "ميلك شيك": "Milkshakes",
    "سموذي": "Smoothies",
    "عصائر طازجة": "Fresh Juices",
    "عصائر": "Juices",
    "عصير": "Juice",
    "فرابيه": "Frappe",
    "موهيتو": "Mojito",
    "إفطار": "Breakfast",
    "افطار": "Breakfast",
    "فطار": "Breakfast",
    "سلطات": "Salads",
    "سناكس": "Snacks",
    "منتجات البيع": "Retail",
    "بيع بالتجزئة": "Retail",
    "حبوب قهوة": "Coffee Beans",
    "حبوب القهوة": "Coffee Beans",
    "أخرى": "Other",
    "اخرى": "Other",
}

# Offline EN -> AR Phrase Dictionary for Café Items
_PRODUCT_PHRASES_EN_TO_AR: dict[str, str] = {
    "double espresso": "دبل إسبريسو",
    "single espresso": "إسبريسو سنجل",
    "espresso": "إسبريسو",
    "flat white": "فلات وايت",
    "classic cappuccino": "كابتشينو كلاسيك",
    "cappuccino": "كابتشينو",
    "caramel latte": "لاتيه بالكراميل",
    "vanilla latte": "لاتيه بالفانيليا",
    "hazelnut latte": "لاتيه بالبندق",
    "pistachio latte": "لاتيه بالفستق",
    "spanish latte": "سبانيش لاتيه",
    "iced spanish latte": "سبانيش لاتيه مثلج",
    "iced americano": "أمريكانو مثلج",
    "americano": "أمريكانو",
    "iced vanilla latte": "لاتيه فانيليا مثلج",
    "iced caramel latte": "لاتيه كراميل مثلج",
    "iced latte": "لاتيه مثلج",
    "iced mocha": "موكا مثلجة",
    "iced matcha": "ماتشا مثلجة",
    "iced coffee": "قهوة مثلجة",
    "cold brew": "كولد برو",
    "ceremonial matcha": "ماتشا احتفالية",
    "matcha latte": "ماتشا لاتيه",
    "spiced chai latte": "شاي لاتيه بالتوابل",
    "chai latte": "شاي لاتيه",
    "butter croissant": "كرواسون بالزبدة",
    "chocolate croissant": "كرواسون بالشوكولاتة",
    "cheese croissant": "كرواسون بالجبنة",
    "chocolate muffin": "مافن الشوكولاتة",
    "blueberry muffin": "مافن التوت الأزرق",
    "turkey pesto panini": "بانيني ديك رومي بالبيستو",
    "club sandwich": "كلوب ساندوتش",
    "cheese sandwich": "ساندوتش جبنة",
    "chicken sandwich": "ساندوتش دجاج",
    "house blend beans (250g)": "حبوب القهوة الخاصة (250 جم)",
    "caramel macchiato": "كراميل ماكياتو",
    "hot chocolate": "هوت شوكليت",
    "turkish coffee": "قهوة تركي",
    "french coffee": "قهوة فرنسي",
    "arabic coffee": "قهوة عربية",
    "green tea": "شاي أخضر",
    "mint tea": "شاي بالنعناع",
    "hibiscus tea": "كركديه",
    "earl grey": "إيرل جراي",
    "chocolate milkshake": "ميلك شيك شوكولاتة",
    "strawberry milkshake": "ميلك شيك فراولة",
    "vanilla milkshake": "ميلك شيك فانيليا",
    "oreo milkshake": "ميلك شيك أوريو",
    "mango smoothie": "سموذي مانجو",
    "strawberry smoothie": "سموذي فراولة",
    "berry smoothie": "سموذي توت",
    "fresh orange juice": "عصير برتقال طازج",
    "orange juice": "عصير برتقال",
    "mango juice": "عصير مانجو",
    "lemon mint": "ليمون بالنعناع",
    "cheesecake": "تشيز كيك",
    "chocolate cake": "كيك شوكولاتة",
    "carrot cake": "كيك جزر",
    "brownie": "براوني",
    "chocolate brownie": "براوني شوكولاتة",
    "tiramisu": "تيراميسو",
    "cinnamon roll": "سينابون رول",
    "avocado toast": "توست أفوكادو",
    "mineral water": "مياه معدنية",
    "size": "الحجم",
    "milk type": "نوع الحليب",
    "extras": "الإضافات",
    "sugar level": "مستوى السكر",
    "small": "صغير",
    "medium": "وسط",
    "large": "كبير",
    "regular": "عادي",
    "oat milk": "حليب شوفان",
    "almond milk": "حليب لوز",
    "soy milk": "حليب صويا",
    "coconut milk": "حليب جوز الهند",
    "extra shot": "شوت إسبريسو إضافي",
    "vanilla syrup": "سيرب فانيليا",
    "caramel syrup": "سيرب كراميل",
    "caramel drizzle": "صوص كراميل",
    "whipped cream": "كريمة مخفوقة",
    "no sugar": "بدون سكر",
    "less sugar": "سكر خفيف",
    "medium sugar": "سكر وسط",
    "extra sugar": "سكر زيادة",
}

_PRODUCT_PHRASES_AR_TO_EN: dict[str, str] = {
    v: k.title() if "(" not in k else "House Blend Beans (250g)"
    for k, v in _PRODUCT_PHRASES_EN_TO_AR.items()
}
_PRODUCT_PHRASES_AR_TO_EN.update(
    {
        "دبل اسبريسو": "Double Espresso",
        "اسبريسو": "Espresso",
        "كابتشينو": "Cappuccino",
        "امريكانو مثلج": "Iced Americano",
        "لاتيه فانيليا مثلج": "Iced Vanilla Latte",
        "كرواسون زبدة": "Butter Croissant",
        "بانيني ديك رومي": "Turkey Panini",
        "ميلك شيك بالشوكولاتة": "Chocolate Milkshake",
        "سموذي مانجو": "Mango Smoothie",
        "عصير برتقال فريش": "Fresh Orange Juice",
        "قهوة تركية": "Turkish Coffee",
        "شاي اخضر": "Green Tea",
        "شاي نعناع": "Mint Tea",
        "ليمون نعناع": "Lemon Mint",
        "ساندوتش جبنة": "Cheese Sandwich",
        "ساندوتش فراخ": "Chicken Sandwich",
        "الحجم": "Size",
        "نوع الحليب": "Milk Type",
        "الإضافات": "Extras",
        "اضافات": "Extras",
        "مستوى السكر": "Sugar Level",
        "صغير": "Small",
        "وسط": "Medium",
        "كبير": "Large",
        "عادي": "Regular",
        "حليب شوفان": "Oat Milk",
        "حليب لوز": "Almond Milk",
        "حليب صويا": "Soy Milk",
        "شوت إسبريسو إضافي": "Extra Shot",
        "سيرب فانيليا": "Vanilla Syrup",
        "كريمة مخفوقة": "Whipped Cream",
        "بدون سكر": "No Sugar",
        "سكر خفيف": "Less Sugar",
        "سكر زيادة": "Extra Sugar",
    }
)

# Word-level bilingual café dictionary for compositional suggestions
_WORD_EN_TO_AR: dict[str, str] = {
    "iced": "مثلج",
    "hot": "ساخن",
    "cold": "بارد",
    "fresh": "طازج",
    "double": "دبل",
    "single": "سنجل",
    "classic": "كلاسيك",
    "special": "مخصوص",
    "specialty": "مختصة",
    "espresso": "إسبريسو",
    "latte": "لاتيه",
    "cappuccino": "كابتشينو",
    "americano": "أمريكانو",
    "mocha": "موكا",
    "macchiato": "ماكياتو",
    "cortado": "كورتادو",
    "frappe": "فرابيه",
    "mojito": "موهيتو",
    "milkshake": "ميلك شيك",
    "milkshakes": "ميلك شيك",
    "smoothie": "سموذي",
    "smoothies": "سموذي",
    "coffee": "قهوة",
    "tea": "شاي",
    "matcha": "ماتشا",
    "chai": "شاي",
    "juice": "عصير",
    "juices": "عصائر",
    "water": "مياه",
    "soda": "صودا",
    "caramel": "كراميل",
    "vanilla": "فانيليا",
    "chocolate": "شوكولاتة",
    "hazelnut": "بندق",
    "pistachio": "فستق",
    "cinnamon": "قرفة",
    "butter": "زبدة",
    "honey": "عسل",
    "cream": "كريمة",
    "milk": "حليب",
    "oat": "شوفان",
    "almond": "لوز",
    "mint": "نعناع",
    "lemon": "ليمون",
    "orange": "برتقال",
    "mango": "مانجو",
    "strawberry": "فراولة",
    "blueberry": "توت أزرق",
    "berry": "توت",
    "peach": "خوخ",
    "apple": "تفاح",
    "banana": "موز",
    "croissant": "كرواسون",
    "muffin": "مافن",
    "cake": "كيك",
    "cheesecake": "تشيز كيك",
    "brownie": "براوني",
    "cookie": "كوكيز",
    "donut": "دونات",
    "tart": "تارت",
    "panini": "بانيني",
    "sandwich": "ساندوتش",
    "sandwiches": "ساندوتشات",
    "toast": "توست",
    "salad": "سلطة",
    "cheese": "جبنة",
    "turkey": "ديك رومي",
    "chicken": "دجاج",
    "tuna": "تونة",
    "pesto": "بيستو",
    "avocado": "أفوكادو",
    "beans": "حبوب",
    "pastry": "معجنات",
    "pastries": "معجنات",
    "dessert": "حلويات",
    "desserts": "حلويات",
    "breakfast": "إفطار",
}

_WORD_AR_TO_EN: dict[str, str] = {
    "مثلج": "Iced",
    "مثلجة": "Iced",
    "مثلجه": "Iced",
    "ساخن": "Hot",
    "ساخنة": "Hot",
    "بارد": "Cold",
    "باردة": "Cold",
    "طازج": "Fresh",
    "طازجة": "Fresh",
    "فريش": "Fresh",
    "دبل": "Double",
    "سنجل": "Single",
    "كلاسيك": "Classic",
    "مخصوص": "Special",
    "مختصة": "Specialty",
    "إسبريسو": "Espresso",
    "اسبريسو": "Espresso",
    "لاتيه": "Latte",
    "كابتشينو": "Cappuccino",
    "أمريكانو": "Americano",
    "امريكانو": "Americano",
    "موكا": "Mocha",
    "ماكياتو": "Macchiato",
    "كورتادو": "Cortado",
    "فرابيه": "Frappe",
    "موهيتو": "Mojito",
    "قهوة": "Coffee",
    "قهوه": "Coffee",
    "شاي": "Tea",
    "ماتشا": "Matcha",
    "عصير": "Juice",
    "عصائر": "Juices",
    "مياه": "Water",
    "صودا": "Soda",
    "كراميل": "Caramel",
    "كاراميل": "Caramel",
    "فانيليا": "Vanilla",
    "شوكولاتة": "Chocolate",
    "شوكولاته": "Chocolate",
    "شوكولا": "Chocolate",
    "بندق": "Hazelnut",
    "فستق": "Pistachio",
    "قرفة": "Cinnamon",
    "زبدة": "Butter",
    "زبده": "Butter",
    "عسل": "Honey",
    "كريمة": "Cream",
    "حليب": "Milk",
    "لبن": "Milk",
    "شوفان": "Oat",
    "لوز": "Almond",
    "نعناع": "Mint",
    "ليمون": "Lemon",
    "برتقال": "Orange",
    "مانجو": "Mango",
    "فراولة": "Strawberry",
    "فراوله": "Strawberry",
    "توت": "Berry",
    "خوخ": "Peach",
    "تفاح": "Apple",
    "موز": "Banana",
    "كرواسون": "Croissant",
    "مافن": "Muffin",
    "كيك": "Cake",
    "براوني": "Brownie",
    "كوكيز": "Cookie",
    "دونات": "Donut",
    "تارت": "Tart",
    "بانيني": "Panini",
    "ساندوتش": "Sandwich",
    "ساندوتشات": "Sandwiches",
    "توست": "Toast",
    "سلطة": "Salad",
    "جبنة": "Cheese",
    "جبن": "Cheese",
    "تركي": "Turkish",
    "فرنسي": "French",
    "أخضر": "Green",
    "اخضر": "Green",
    "دجاج": "Chicken",
    "فراخ": "Chicken",
    "تونة": "Tuna",
    "بيستو": "Pesto",
    "أفوكادو": "Avocado",
    "حبوب": "Beans",
    "مخبوزات": "Bakery",
    "معجنات": "Pastries",
    "حلويات": "Desserts",
    "حلى": "Desserts",
    "إفطار": "Breakfast",
    "افطار": "Breakfast",
}


def normalize_category_key(text: str | None) -> str:
    """Unicode-aware key normalization preserving distinct Arabic words/phrases."""
    raw = unicodedata.normalize("NFKC", text or "")
    no_diacritics = _AR_DIACRITICS_RE.sub("", raw)
    collapsed = " ".join(no_diacritics.split())
    return collapsed.casefold()


def strip_arabic_diacritics(text: str | None) -> str:
    return _AR_DIACRITICS_RE.sub("", unicodedata.normalize("NFKC", text or "")).strip()


def detect_primary_script(text: str | None) -> str:
    clean = (text or "").strip()
    ar_count = len(_AR_CHAR_RE.findall(clean))
    en_count = len(_LATIN_CHAR_RE.findall(clean))
    if ar_count > 0 and ar_count >= en_count:
        return "ar"
    return "en"


def _compose_words(text: str, source_lang: str, *, is_category: bool) -> TranslationSuggestion | None:
    clean = " ".join((text or "").strip().split())
    tokens = [t for t in re.split(r"[\s·\-/,&]+", clean) if t]
    if not tokens or len(tokens) > 5:
        return None

    if source_lang == "en":
        cat_hit = CATEGORY_GLOSSARY_EN_TO_AR.get(clean.casefold()) if is_category else None
        if cat_hit:
            return TranslationSuggestion(clean, "en", cat_hit, "ar", "high", "offline_dict")
        exact_hit = _PRODUCT_PHRASES_EN_TO_AR.get(clean.casefold()) or CATEGORY_GLOSSARY_EN_TO_AR.get(clean.casefold())
        if exact_hit:
            return TranslationSuggestion(clean, "en", exact_hit, "ar", "high", "offline_dict")

        mapped: list[str] = []
        matched_count = 0
        for tok in tokens:
            w = _WORD_EN_TO_AR.get(tok.casefold())
            if w:
                mapped.append(w)
                matched_count += 1
        if matched_count == len(tokens) and mapped:
            # Reorder leading adjective -> Arabic post-modifier pattern (e.g. "Iced Latte" -> "لاتيه مثلج")
            if len(tokens) == 2 and tokens[0].casefold() in {"iced", "hot", "cold", "fresh", "green", "french", "turkish"}:
                ar_phrase = f"{mapped[1]} {mapped[0]}"
            else:
                ar_phrase = " ".join(mapped)
            return TranslationSuggestion(clean, "en", ar_phrase, "ar", "medium", "offline_dict")
        return None

    # Arabic -> English
    no_diac = strip_arabic_diacritics(clean)
    if is_category:
        cat_hit = CATEGORY_GLOSSARY_AR_TO_EN.get(clean) or CATEGORY_GLOSSARY_AR_TO_EN.get(no_diac)
        if cat_hit:
            return TranslationSuggestion(clean, "ar", cat_hit, "en", "high", "offline_dict")

    exact_ar = (
        _PRODUCT_PHRASES_AR_TO_EN.get(clean)
        or _PRODUCT_PHRASES_AR_TO_EN.get(no_diac)
        or CATEGORY_GLOSSARY_AR_TO_EN.get(clean)
        or CATEGORY_GLOSSARY_AR_TO_EN.get(no_diac)
    )
    if exact_ar:
        return TranslationSuggestion(clean, "ar", exact_ar, "en", "high", "offline_dict")

    mapped_en: list[str] = []
    matched_count = 0
    ar_tokens = [t.strip() for t in re.split(r"[\s·\-/,&]+", no_diac) if t.strip()]
    for t_clean in ar_tokens:
        w = _WORD_AR_TO_EN.get(t_clean)
        if not w:
            for pref in ("بال", "ال", "ب"):
                if t_clean.startswith(pref) and len(t_clean) > len(pref) + 1:
                    sub = t_clean[len(pref) :]
                    if sub in _WORD_AR_TO_EN:
                        w = _WORD_AR_TO_EN[sub]
                        break
        if w:
            mapped_en.append(w)
            matched_count += 1
    if ar_tokens and matched_count == len(ar_tokens) and mapped_en:
        # Arabic puts adjective second ("لاتيه مثلج" -> "Iced Latte")
        if len(mapped_en) == 2 and mapped_en[1].casefold() in {"iced", "hot", "cold", "fresh", "green", "french", "turkish"}:
            en_phrase = f"{mapped_en[1]} {mapped_en[0]}"
        else:
            en_phrase = " ".join(mapped_en)
        return TranslationSuggestion(clean, "ar", en_phrase, "en", "medium", "offline_dict")
    return None


def suggest_bilingual_name(
    text: str,
    *,
    is_category: bool = False,
    cache_lookup: Callable[[str, str, str], tuple[str, bool, str] | None] | None = None,
) -> TranslationSuggestion:
    """Suggest counterpart EN/AR label deterministically from user cache first, then offline glossary."""
    clean = " ".join((text or "").strip().split())
    if not clean:
        return TranslationSuggestion("", "en", "", "ar", "none", "none")

    src_lang = detect_primary_script(clean)
    dst_lang = "en" if src_lang == "ar" else "ar"

    if cache_lookup is not None:
        hit = cache_lookup(clean, src_lang, dst_lang)
        if hit and hit[0].strip():
            target_text, is_confirmed, cached_conf = hit
            return TranslationSuggestion(
                source_text=clean,
                source_lang=src_lang,
                target_text=target_text.strip(),
                target_lang=dst_lang,
                confidence="confirmed" if is_confirmed else (cached_conf or "medium"),
                source="user_cache" if is_confirmed else "cache",
            )

    dict_hit = _compose_words(clean, src_lang, is_category=is_category)
    if dict_hit is not None:
        return dict_hit

    return TranslationSuggestion(clean, src_lang, "", dst_lang, "none", "none")


def suggest_bilingual_pair(
    name: str,
    *,
    is_category: bool = False,
    cache_lookup: Callable[[str, str, str], tuple[str, bool, str] | None] | None = None,
) -> tuple[str, str, TranslationSuggestion]:
    """Return `(name_en, name_ar, suggestion)` resolved safely from single name input."""
    clean = " ".join((name or "").strip().split())
    if not clean:
        return "", "", TranslationSuggestion("", "en", "", "ar", "none", "none")

    sug = suggest_bilingual_name(clean, is_category=is_category, cache_lookup=cache_lookup)
    if sug.source_lang == "ar":
        return sug.target_text, clean, sug
    return clean, sug.target_text, sug


def translate_online_optional(
    text: str,
    source_lang: str,
    target_lang: str,
    *,
    enabled: bool = False,
    timeout_seconds: float = 3.5,
) -> TranslationSuggestion | None:
    """Optional HTTPS translation helper strictly disabled unless admin opted in."""
    if not enabled:
        return None
    clean = " ".join((text or "").strip().split())
    if not clean:
        return None
    src = "ar" if source_lang.startswith("ar") else "en"
    dst = "en" if target_lang.startswith("en") else "ar"
    if src == dst:
        return None

    params = urllib.parse.urlencode({"q": clean, "langpair": f"{src}|{dst}"})
    url = f"https://api.mymemory.translated.net/get?{params}"
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "CafePOS-Desktop/2.0", "Accept": "application/json"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=max(1.0, float(timeout_seconds))) as resp:
            payload = json.loads(resp.read(65536).decode("utf-8"))
        translated = " ".join(
            str((payload.get("responseData") or {}).get("translatedText") or "").strip().split()
        )
        if not translated or translated.casefold() == clean.casefold():
            return None
        return TranslationSuggestion(
            source_text=clean,
            source_lang=src,
            target_text=translated[:80],
            target_lang=dst,
            confidence="medium",
            source="online_api",
        )
    except (urllib.error.URLError, TimeoutError, OSError, ValueError, TypeError):
        return None
