"""Exact day-number primitives for the Birashk/Borkowski Jalali algorithm."""
from dataclasses import dataclass
from datetime import date
from typing import Tuple

#: Jalali years at which the 2820-year cycle's intercalation pattern shifts.
#: The algorithm is only defined between the first and last of these; outside
#: that range the leap pattern is an extrapolation and we refuse to guess.
CYCLE_BREAKS: Tuple[int, ...] = (
    -61, 9, 38, 199, 426, 686, 756, 818, 1111, 1181,
    1210, 1635, 2060, 2097, 2192, 2262, 2324, 2394, 2456, 3178,
)

_G_DAYS_IN_MONTH = (31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)
_G_CUMULATIVE_DAYS = (0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334)


def is_leap_gregorian(year: int) -> bool:
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)


def _div(numerator: int, denominator: int) -> int:
    """Integer division truncated toward zero, as the reference algorithm uses."""
    quotient = abs(numerator) // abs(denominator)
    return -quotient if (numerator < 0) != (denominator < 0) else quotient


def _mod(numerator: int, denominator: int) -> int:
    """Remainder with the sign of the dividend, matching the reference."""
    return numerator - _div(numerator, denominator) * denominator


def gregorian_to_jdn(gy: int, gm: int, gd: int) -> int:
    """
    Gregorian date -> day number counted from 1 March 622 CE.

    This is the Jalaali epoch: 1 Farvardin 1 AH falls in March 623 of the
    proleptic Gregorian calendar, which is where this day count starts. The
    month is shifted by six so that March becomes the first month of the year,
    which is what makes the March-based leap rule fall out naturally.
    """
    month_shift = _div(gm - 8, 6)
    shifted_year = gy + 100100 + month_shift

    day_number = (
        _div(shifted_year * 1461, 4)
        + _div(153 * _mod(gm + 9, 12) + 2, 5)
        + gd
        - 34840408
    )
    day_number -= _div(_div(shifted_year, 100) * 3, 4) - 752
    return day_number


def jdn_to_gregorian(jdn: int) -> Tuple[int, int, int]:
    """Day number counted from 1 March 622 -> Gregorian ``(year, month, day)``."""
    j = 4 * jdn + 139361631
    j += _div(_div(4 * jdn + 183187720, 146097) * 3, 4) * 4 - 3908
    i = _div(_mod(j, 1461), 4) * 5 + 308
    gd = _div(_mod(i, 153), 5) + 1
    gm = _mod(_div(i, 153), 12) + 1
    gy = _div(j, 1461) - 100100 + _div(8 - gm, 6)
    return gy, gm, gd


@dataclass(frozen=True)
class _JalaliCal:
    """The leap flag and Nowruz Gregorian date for one Jalali year."""

    leap: int
    gy: int
    march: int


def jalali_cal(jy: int, without_leap: bool = False) -> _JalaliCal:
    """
    Compute the leap flag and the Gregorian date of 1 Farvardin for Jalali year *jy*.

    This is the exact 2820-year-cycle algorithm from Borkowski, "The Persian
    calendar for 3000 years". The commonly cited 33-year approximation is not
    used: it misidentifies cycle position 17 as a leap year, which would make
    1403 a 366-day year when it was in fact 365 days long.
    """
    breaks = CYCLE_BREAKS
    last = len(breaks) - 1
    gy = jy + 621
    leap_j = -14
    jp = breaks[0]
    jump = 0

    if jy < jp or jy >= breaks[last]:
        raise ValueError(
            f"Jalali year {jy} is outside the range the calendar is defined for "
            f"({breaks[0]} to {breaks[last] - 1})"
        )

    for i in range(1, last + 1):
        jm = breaks[i]
        jump = jm - jp
        if jy < jm:
            break
        leap_j = leap_j + _div(jump, 33) * 8 + _div(_mod(jump, 33), 4)
        jp = jm

    n = jy - jp
    leap_j += _div(n, 33) * 8 + _div(_mod(n, 33) + 3, 4)
    if _mod(jump, 33) == 4 and jump - n == 4:
        leap_j += 1

    leap_g = _div(gy, 4) - _div((_div(gy, 100) + 1) * 3, 4) - 150
    march = 20 + leap_j - leap_g

    leap = 0
    if not without_leap:
        if jump - n < 6:
            n = n - jump + _div(jump + 4, 33) * 33
        # Position within the current four-year group; 0 is the leap year and
        # 4 the exceptional five-year gap between groups.
        leap = _mod(_mod(n + 1, 33) - 1, 4)
        if leap == -1:
            leap = 4

    return _JalaliCal(leap=leap, gy=gy, march=march)


def is_leap_jalali(year: int) -> bool:
    """
    True when Esfand of *year* has 30 days.

    ``jalali_cal`` returns a 0-3 position within the current four-year group,
    where **0 marks the leap year** and 4 is the exceptional five-year gap.
    """
    return jalali_cal(year).leap == 0


def days_in_jalali_month(year: int, month: int) -> int:
    if not 1 <= month <= 12:
        raise ValueError(f"Invalid Jalali month: {month}")
    if month <= 6:
        return 31
    if month <= 11:
        return 30
    return 30 if is_leap_jalali(year) else 29


def jalali_to_jdn(jy: int, jm: int, jd: int) -> int:
    """Jalali date -> day number counted from 1 March 622."""
    cal = jalali_cal(jy, without_leap=True)
    base = gregorian_to_jdn(cal.gy, 3, cal.march)
    return base + (jm - 1) * 31 - _div(jm, 7) * (jm - 7) + jd - 1


def jdn_to_jalali(jdn: int) -> Tuple[int, int, int]:
    """Day number counted from 1 March 622 -> Jalali ``(year, month, day)``."""
    gy, _, _ = jdn_to_gregorian(jdn)
    jy = gy - 621
    march = jalali_cal(jy).march
    day_of_year = jdn - gregorian_to_jdn(gy, 3, march)

    if day_of_year >= 0:
        if day_of_year <= 185:
            # Farvardin..Shahrivar: six 31-day months
            return jy, 1 + _div(day_of_year, 31), _mod(day_of_year, 31) + 1
        day_of_year -= 186
    else:
        # Before Nowruz, so we are in the tail of the previous Jalali year.
        previous = jy - 1
        day_of_year += 180 if is_leap_jalali(previous) else 179
        jy = previous

    # Mehr..Esfand: six 30-day months
    return jy, 7 + _div(day_of_year, 30), _mod(day_of_year, 30) + 1


def gregorian_to_ordinal(year: int, month: int, day: int) -> int:
    """Proleptic Gregorian -> :meth:`datetime.date.toordinal` value."""
    return date(year, month, day).toordinal()


def ordinal_to_gregorian(ordinal: int) -> date:
    """:meth:`datetime.date.toordinal` value -> proleptic Gregorian date."""
    return date.fromordinal(ordinal)
