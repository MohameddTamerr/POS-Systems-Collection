import pytest

from cafe_pos.money import (
    format_major_input,
    format_minor,
    parse_major_to_minor,
)


def test_parse_and_format_usd():
    minor_a = parse_major_to_minor("0.10", "USD")
    minor_b = parse_major_to_minor("0.20", "USD")
    assert minor_a + minor_b == 30
    assert format_minor(minor_a + minor_b, "USD") == "$0.30"
    assert format_major_input(1250, "USD") == "12.50"


def test_parse_and_format_jpy_zero_decimals():
    minor = parse_major_to_minor("450", "JPY")
    assert minor == 450
    assert format_minor(minor, "JPY") == "¥450"
    assert format_major_input(minor, "JPY") == "450"


def test_rejects_invalid_or_negative_amounts():
    with pytest.raises(ValueError):
        parse_major_to_minor("-1.50", "USD")
    with pytest.raises(ValueError):
        parse_major_to_minor("abc", "USD")
    with pytest.raises(ValueError):
        parse_major_to_minor("", "USD")

