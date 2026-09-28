"""
Persian (Jalali / Solar Hijri) calendar conversion.

Tender documents are submitted on the Iranian calendar, so every date printed
in the defense document must render as a Jalali date with Persian month names.

The conversion is the exact 2820-year-cycle algorithm from Borkowski
("The Persian calendar for 3000 years"), which lives in
:mod:`src.services._jalali_calendar`. The widely quoted 33-year approximation is
deliberately *not* used: it misidentifies cycle position 17 as a leap year,
which would make 1403 a 366-day year when it was 365 days long, and an
off-by-one-day document date in a tender submission is a real defect.

Verified anchors (all confirmed in both directions at import time):

    1979-02-11  <-> 1357-11-22   (Islamic Revolution)
    2020-03-20  <-> 1399-01-01   (Nowruz 1399)
    2024-03-20  <-> 1403-01-01   (Nowruz 1403)
    2026-09-27  <-> 1405-07-05
"""
import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional, Tuple, Union

from src.core.logging import get_logger
from src.services._jalali_calendar import (
    CYCLE_BREAKS,
    days_in_jalali_month,
    gregorian_to_jdn,
    is_leap_gregorian,
    is_leap_jalali,
    jalali_cal,
    jalali_to_jdn,
    jdn_to_gregorian,
    jdn_to_jalali,
)

logger = get_logger(__name__)

#: Persian (extended Arabic-Indic) digits
PERSIAN_DIGITS = "۰۱۲۳۴۵۶۷۸۹"

MONTH_NAMES_FA = (
    "فروردین",
    "اردیبهشت",
    "خرداد",
    "تیر",
    "مرداد",
    "شهریور",
    "مهر",
    "آبان",
    "آذر",
    "دی",
    "بهمن",
    "اسفند",
)

MONTH_NAMES_EN = (
    "Farvardin",
    "Ordibehesht",
    "Khordad",
    "Tir",
    "Mordad",
    "Shahrivar",
    "Mehr",
    "Aban",
    "Azar",
    "Dey",
    "Bahman",
    "Esfand",
)

#: Saturday-first, matching the Iranian week
WEEKDAYS_FA = (
    "شنبه",
    "یکشنبه",
    "دوشنبه",
    "سه‌شنبه",
    "چهارشنبه",
    "پنجشنبه",
    "جمعه",
)

#: First and last Jalali year for which the leap pattern is defined
MIN_JALALI_YEAR = CYCLE_BREAKS[0]
MAX_JALALI_YEAR = CYCLE_BREAKS[-1] - 1

_G_DAYS_IN_MONTH = (31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)

__all__ = [
    "JalaliDate",
    "MONTH_NAMES_EN",
    "MONTH_NAMES_FA",
    "PERSIAN_DIGITS",
    "WEEKDAYS_FA",
    "days_in_jalali_month",
    "format_jalali",
    "format_number",
    "gregorian_to_jalali",
    "is_leap_gregorian",
    "is_leap_jalali",
    "jalali_to_gregorian",
    "parse_jalali",
    "round_trip",
    "to_ascii_digits",
    "to_jalali",
    "to_persian_digits",
    "weekday_fa",
]


# --------------------------------------------------------------------------- #
# Value object
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class JalaliDate:
    """A date on the Solar Hijri calendar."""

    year: int
    month: int
    day: int

    def __post_init__(self) -> None:
        if not 1 <= self.month <= 12:
            raise ValueError(f"Invalid Jalali month: {self.month}")
        if not 1 <= self.day <= days_in_jalali_month(self.year, self.month):
            raise ValueError(f"Invalid Jalali day {self.day} for {self.year}/{self.month}")

    @property
    def month_name_fa(self) -> str:
        return MONTH_NAMES_FA[self.month - 1]

    @property
    def month_name_en(self) -> str:
        return MONTH_NAMES_EN[self.month - 1]

    def to_persian_digits(self) -> str:
        return to_persian_digits(f"{self.year:04d}/{self.month:02d}/{self.day:02d}")

    def to_gregorian(self) -> date:
        return jalali_to_gregorian(self.year, self.month, self.day)

    def __str__(self) -> str:
        return f"{self.day} {self.month_name_fa} {self.year}"


# --------------------------------------------------------------------------- #
# Core conversion
# --------------------------------------------------------------------------- #
def gregorian_to_jalali(year: int, month: int, day: int) -> JalaliDate:
    """Convert a Gregorian ``(year, month, day)`` to a :class:`JalaliDate`."""
    if not 1 <= month <= 12:
        raise ValueError(f"Invalid Gregorian month: {month}")
    max_day = 29 if (month == 2 and is_leap_gregorian(year)) else _G_DAYS_IN_MONTH[month - 1]
    if not 1 <= day <= max_day:
        raise ValueError(f"Invalid Gregorian day: {year}-{month}-{day}")

    return JalaliDate(*jdn_to_jalali(gregorian_to_jdn(year, month, day)))


def jalali_to_gregorian(year: int, month: int, day: int) -> date:
    """Convert a Solar Hijri date to a Gregorian :class:`datetime.date`."""
    if not 1 <= month <= 12:
        raise ValueError(f"Invalid Jalali month: {month}")
    if not 1 <= day <= days_in_jalali_month(year, month):
        raise ValueError(f"Invalid Jalali day {day} for {year}/{month}")

    return date(*jdn_to_gregorian(jalali_to_jdn(year, month, day)))


def nowruz_gregorian(jalali_year: int) -> date:
    """Gregorian date of 1 Farvardin for a Jalali year."""
    cal = jalali_cal(jalali_year)
    return date(cal.gy, 3, cal.march)


# --------------------------------------------------------------------------- #
# Public helpers
# --------------------------------------------------------------------------- #
def to_jalali(value: Union[date, datetime, None]) -> Optional[JalaliDate]:
    """Convert a Gregorian date/datetime to a :class:`JalaliDate`."""
    if value is None:
        return None
    if isinstance(value, datetime):
        value = value.date()
    if not isinstance(value, date):
        raise TypeError(f"Expected date or datetime, got {type(value).__name__}")
    return gregorian_to_jalali(value.year, value.month, value.day)


def to_persian_digits(text: str) -> str:
    """Swap ASCII digits for Persian ones."""
    return str(text).translate(str.maketrans("0123456789", PERSIAN_DIGITS))


def to_ascii_digits(text: str) -> str:
    """Swap Persian digits back to ASCII."""
    return str(text).translate(str.maketrans(PERSIAN_DIGITS, "0123456789"))


def weekday_fa(value: Union[date, datetime]) -> str:
    """Persian weekday name; the Iranian week starts on Saturday."""
    if isinstance(value, datetime):
        value = value.date()
    return WEEKDAYS_FA[(value.weekday() + 2) % 7]


def format_jalali(
    value: Union[date, datetime, None],
    persian_digits: bool = True,
    long_form: bool = False,
    with_weekday: bool = False,
) -> str:
    """
    Render a date as ``۱۴۰۳/۰۵/۱۲`` (default) or ``۱۲ مرداد ۱۴۰۳``.

    Args:
        value: Gregorian date to render; ``None`` renders as an empty string.
        persian_digits: use Persian digits (documents) or ASCII (logs/CSV).
        long_form: ``day month_name year`` instead of the numeric form.
        with_weekday: prefix the Persian weekday name.
    """
    jalali = to_jalali(value)
    if jalali is None:
        return ""

    if long_form:
        text = (
            f"{to_persian_digits(str(jalali.day))} {jalali.month_name_fa} "
            f"{to_persian_digits(str(jalali.year))}"
        )
    elif persian_digits:
        text = jalali.to_persian_digits()
    else:
        text = f"{jalali.year:04d}/{jalali.month:02d}/{jalali.day:02d}"

    if with_weekday:
        text = f"{weekday_fa(value)} {text}"
    return text


def format_number(
    value: Union[int, float, None],
    persian_digits: bool = True,
    decimals: Optional[int] = None,
) -> str:
    """Format a number with thousands separators, optionally in Persian digits."""
    if value is None:
        return ""
    if decimals is not None:
        text = f"{float(value):,.{decimals}f}"
    elif isinstance(value, float):
        text = f"{value:,}"
    else:
        text = f"{int(value):,}"
    return to_persian_digits(text) if persian_digits else text


def parse_jalali(text: str) -> JalaliDate:
    """Parse ``1403/05/12`` (Persian or ASCII digits) into a :class:`JalaliDate`."""
    if not text:
        raise ValueError("Empty Jalali date")
    normalised = to_ascii_digits(str(text))
    match = re.match(
        r"^\s*(\d{3,4})\s*[/\-.]?\s*(\d{1,2})\s*[/\-.]?\s*(\d{1,2})\s*$", normalised
    )
    if not match:
        raise ValueError(f"Cannot parse Jalali date: {text!r}")
    year, month, day = (int(group) for group in match.groups())
    return JalaliDate(year, month, day)


def round_trip(value: Union[date, datetime]) -> Tuple[JalaliDate, date]:
    """Convert to Jalali and back; used by tests to assert both directions agree."""
    jalali = to_jalali(value)
    return jalali, jalali.to_gregorian()


# Fail fast on a broken calendar. These are checked at import time because a
# silently wrong date would otherwise reach a submitted tender document.
_ANCHORS = (
    (date(1979, 2, 11), (1357, 11, 22)),
    (date(2000, 1, 1), (1378, 10, 11)),
    (date(2020, 3, 20), (1399, 1, 1)),
    (date(2021, 3, 20), (1399, 12, 30)),
    (date(2024, 3, 20), (1403, 1, 1)),
    (date(2025, 3, 20), (1403, 12, 30)),
    (date(2026, 9, 27), (1405, 7, 5)),
)
for _gregorian_anchor, _jalali_anchor in _ANCHORS:
    _converted = gregorian_to_jalali(
        _gregorian_anchor.year, _gregorian_anchor.month, _gregorian_anchor.day
    )
    if (_converted.year, _converted.month, _converted.day) != _jalali_anchor:
        raise RuntimeError(
            f"Jalali conversion is wrong: {_gregorian_anchor} -> {_converted} "
            f"(expected {_jalali_anchor})"
        )
    if jalali_to_gregorian(*_jalali_anchor) != _gregorian_anchor:
        raise RuntimeError(
            f"Gregorian conversion is wrong: {_jalali_anchor} -> "
            f"{jalali_to_gregorian(*_jalali_anchor)} (expected {_gregorian_anchor})"
        )
del _gregorian_anchor, _jalali_anchor, _converted
