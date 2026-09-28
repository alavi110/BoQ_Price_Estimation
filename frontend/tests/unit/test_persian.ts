/**
 * Persian formatting and the Jalali calendar (T106).
 *
 * The Jalali tests in here are a *cross-implementation* contract, not a set of
 * expectations about what a calendar ought to do. Every date in
 * `BACKEND_CONVERSIONS` was produced by running
 * `backend/src/services/_jalali_calendar.py` - the module that generates the
 * defence document's dates - and is pinned here so the two implementations
 * cannot drift apart unnoticed.
 *
 * That matters more than it might look. The dashboard previews a document the
 * backend generates, and a document that says ۳۰ خرداد while the screen it was
 * approved on said ۳۱ خرداد is a document nobody trusts and a tender that gets
 * queried. Two independent implementations of a calendar will agree on almost
 * every date and disagree on the handful that matter, which is exactly the
 * failure mode an unpinned mirror invites.
 */
import { describe, it, expect } from 'vitest';

import {
  ABSENT,
  DECIMAL_SEPARATOR,
  MINUS_SIGN,
  PERCENT_SIGN,
  PLUS_SIGN,
  THOUSANDS_SEPARATOR,
  dayNumberToJalali,
  daysInJalaliMonth,
  formatChange,
  formatDualDate,
  formatJalali,
  formatJalaliNumeric,
  formatPercent,
  formatRial,
  gregorianToDayNumber,
  isLeapJalali,
  jalaliToDayNumber,
  parseIsoDate,
  toJalali,
  toPersianNumber,
} from '@/lib/persian';

/**
 * Produced by `backend/src/services/_jalali_calendar.py`:
 *
 *   jdn = gregorian_to_jdn(y, m, d);  jdn_to_jalali(jdn)
 *
 * The dates are chosen at the boundaries where a leap rule goes wrong rather than
 * in the middle of a month, because the middle of a month is right under any
 * implementation.
 */
const BACKEND_CONVERSIONS: ReadonlyArray<readonly [string, readonly [number, number, number]]> = [
  // 1403 is a leap year, so its Esfand has 30 days and 20 March 2025 is the last
  // day of it. The 33-year approximation gets this backwards - it treats 1403 as
  // common and 1404 as leap - which shifts every subsequent date by a day. That
  // is the single fact this table exists to pin.
  ['2025-03-20', [1403, 12, 30]],
  ['2025-03-21', [1404, 1, 1]],
  ['2024-03-19', [1402, 12, 29]],
  ['2024-03-20', [1403, 1, 1]],
  // 1404 is *not* a leap year: Esfand ends on 20 March 2026.
  ['2026-03-20', [1404, 12, 29]],
  ['2026-03-21', [1405, 1, 1]],
  ['2026-03-22', [1405, 1, 2]],
  ['2027-03-21', [1406, 1, 1]],
  // Ordinary dates, to catch a consistent off-by-one that the boundary cases
  // alone might not reveal.
  ['2024-12-30', [1403, 10, 10]],
  ['2025-12-29', [1404, 10, 8]],
  ['2026-09-28', [1405, 7, 6]],
  ['2026-01-01', [1404, 10, 11]],
  ['2027-01-01', [1405, 10, 11]],
];

describe('the Jalali calendar agrees with the backend', () => {
  it.each(BACKEND_CONVERSIONS)('converts %s the way the defence document will', (iso, expected) => {
    const { jy, jm, jd } = toJalali(iso);

    expect([jy, jm, jd]).toEqual([...expected]);
  });

  it.each(BACKEND_CONVERSIONS)('round-trips %s through the Jalali calendar', (iso, expected) => {
    // The forward direction being right for a hand-picked date does not mean the
    // inverse is. This asserts the two agree with each other, which is what a
    // date *range* in the forecast horizon needs - a forecast is built by adding
    // months to a base date, so it goes Jalali-first and Gregorian-second.
    const { jy, jm, jd } = toJalali(iso);

    expect([jy, jm, jd]).toEqual([...expected]);
    expect(jalaliToDayNumber(jy, jm, jd)).toBe(gregorianToDayNumber(parseIsoDate(iso)));
    // And back the other way, through the inverse the month arithmetic uses.
    expect(dayNumberToJalali(gregorianToDayNumber(parseIsoDate(iso)))).toEqual({
      jy,
      jm,
      jd,
    });
  });

  it('agrees with the backend on which years are leap', () => {
    // From `is_leap_jalali` in the backend: 1399, 1403 and 1408 leap; 1400, 1404
    // and 1409 do not. 1400 is the interesting one - it is the year the 33-year
    // cycle desynchronised from, and it was a 29-day Esfand.
    expect([1399, 1400, 1403, 1404, 1408, 1409].map(isLeapJalali)).toEqual([
      true,
      false,
      true,
      false,
      true,
      false,
    ]);
  });

  it('agrees with the backend on the length of Esfand', () => {
    // 30 days in a leap year, 29 otherwise - from `days_in_jalali_month` in the
    // backend. Esfand is where an off-by-one turns into a date that does not
    // exist, so it is the month worth checking.
    expect(daysInJalaliMonth(1399, 12)).toBe(30);
    expect(daysInJalaliMonth(1400, 12)).toBe(29);
    expect(daysInJalaliMonth(1403, 12)).toBe(30);
    expect(daysInJalaliMonth(1404, 12)).toBe(29);
  });

  it('gives every month but Esfand a fixed length', () => {
    // Farvardin through Shahrivar have 31, Mehr through Bahman 30. A month-length
    // bug outside Esfand would shift a date by a whole month, so these are
    // asserted across the range rather than at the boundaries only.
    const lengths = [1, 2, 3, 4, 5, 6].map((month) => daysInJalaliMonth(1405, month));
    expect(lengths).toEqual([31, 31, 31, 31, 31, 31]);
    expect([7, 8, 9, 10, 11].map((month) => daysInJalaliMonth(1405, month))).toEqual([
      30, 30, 30, 30, 30,
    ]);
  });

  it('refuses a month that does not exist, rather than wrapping', () => {
    expect(() => daysInJalaliMonth(1405, 0)).toThrow(RangeError);
    expect(() => daysInJalaliMonth(1405, 13)).toThrow(RangeError);
  });

  it('refuses a year outside the cycle the algorithm is defined for', () => {
    // The 2820-year cycle has known intercalation breaks. A year beyond the last
    // one has no defined leap pattern, and returning a plausible-looking date for
    // it would be worse than refusing: a tender dated in the 23rd century is not
    // a thing this system has to survive, and a wrong Farvardin is invisible.
    expect(() => toJalali('4000-01-01')).toThrow(RangeError);
  });
});

describe('ISO dates are calendar labels, not instants', () => {
  it('takes the day as written, without a timezone shift', () => {
    // `new Date('2026-03-21')` is UTC midnight by the spec, so reading the local
    // day from it in any negative-offset zone returns 20 March - and 20 March 2026
    // is the last day of 1404, not the first of 1405. An index whose base date
    // slips by a day across Nowruz has a different ratio, and therefore a
    // different price.
    expect(parseIsoDate('2026-03-21')).toEqual({ year: 2026, month: 3, day: 21 });
    expect(toJalali('2026-03-21')).toEqual({ jy: 1405, jm: 1, jd: 1 });
  });

  it('rejects a string that is not a date', () => {
    expect(() => parseIsoDate('21/03/2026')).toThrow(TypeError);
    expect(() => parseIsoDate('')).toThrow(TypeError);
  });

  it('ignores a trailing time, because the calendar day is what matters', () => {
    expect(parseIsoDate('2026-09-28T23:59:59Z')).toEqual({ year: 2026, month: 9, day: 28 });
  });
});

describe('Persian numbers', () => {
  it('groups thousands the way Iranian documents do', () => {
    // A nine-figure rial amount is the norm here, not the exception, and
    // `1234567890` is not a number anyone can check against a contract.
    expect(toPersianNumber(1234567890)).toBe(
      `۱${THOUSANDS_SEPARATOR}۲۳۴${THOUSANDS_SEPARATOR}۵۶۷${THOUSANDS_SEPARATOR}۸۹۰`
    );
    expect(toPersianNumber(1000)).toBe(`۱${THOUSANDS_SEPARATOR}۰۰۰`);
    expect(toPersianNumber(100)).toBe('۱۰۰');
  });

  it('uses the Persian decimal separator', () => {
    expect(toPersianNumber(36.8)).toBe(`۳۶${DECIMAL_SEPARATOR}۸`);
  });

  it('leaves a sign as a mathematical minus, not a hyphen', () => {
    // In an RTL run an ASCII hyphen is reordered to the far side of the digits, so
    // "-10" renders as "10-" and a fall reads as a rise.
    expect(toPersianNumber(-1234)).toBe(`${MINUS_SIGN}۱${THOUSANDS_SEPARATOR}۲۳۴`);
  });
});

describe('rial amounts', () => {
  it('rounds to the rial and shows no decimals', () => {
    // A `.00` on a tender figure invites the reader to think a fractional amount
    // is in play, and rial amounts are integers in practice.
    expect(formatRial(1710625.4)).toBe(`۱${THOUSANDS_SEPARATOR}۷۱۰${THOUSANDS_SEPARATOR}۶۲۵`);
    expect(formatRial(0)).toBe('۰');
  });

  it('says so when a value is absent rather than showing a zero', () => {
    // A free-supply item has no price. Rendering that as ۰ would be a claim that
    // it is free, which is a different statement about the tender.
    expect(formatRial(null)).toBe(ABSENT);
    expect(formatRial(undefined)).toBe(ABSENT);
    expect(formatRial(Number.NaN)).toBe(ABSENT);
    expect(formatRial(Number.POSITIVE_INFINITY)).toBe(ABSENT);
  });
});

describe('percentages', () => {
  it('shows a weight as a whole percentage by default', () => {
    // "35%" is what goes in the document. "35.00%" is noise on a headline figure.
    expect(formatPercent(0.35)).toBe(`۳۵${PERCENT_SIGN}`);
    expect(formatPercent(0.35, 1)).toBe(`۳۵${DECIMAL_SEPARATOR}۰${PERCENT_SIGN}`);
  });

  it('distinguishes a missing percentage from a zero one', () => {
    // "The parser declined to answer" and "the answer is zero" are different
    // claims, and the defence document has to keep them apart.
    expect(formatPercent(null)).toBe(ABSENT);
    expect(formatPercent(0)).toBe(`۰${PERCENT_SIGN}`);
  });
});

describe('percentage changes', () => {
  it('always signs the number, so a rise is distinguishable from a smaller rise', () => {
    expect(formatChange(1000, 1375)).toBe(`+۳۷${DECIMAL_SEPARATOR}۵${PERCENT_SIGN}`);
    expect(formatChange(1000, 900)).toBe(`${MINUS_SIGN}۱۰${DECIMAL_SEPARATOR}۰${PERCENT_SIGN}`);
  });

  it('gives one decimal place, because whole percents turn a real move into "0%"', () => {
    // A 0.4% movement rounds to 0% at zero decimals, and "0%" reads as "the price
    // did not move" - a claim the reader has no way to disprove from the table.
    expect(formatChange(1000, 1004)).toBe(`+۰${DECIMAL_SEPARATOR}۴${PERCENT_SIGN}`);
  });

  it('refuses to divide by a zero base, rather than reporting Infinity or 0%', () => {
    // A legitimately free-supply item has a base price of zero. The honest answer
    // is that there is no percentage, and "0%" would say the price did not
    // change - which is a different claim about a quantity that does not exist.
    expect(formatChange(0, 500)).toBe(ABSENT);
    expect(formatChange(0, 0)).toBe(ABSENT);
  });

  it('uses the magnitude of the base, so a fall from a negative base reads as a rise', () => {
    // A credit or a reversal line can carry a negative base price. Dividing by the
    // signed value would report a fall as a rise. Still one decimal place: this
    // shares `formatChange`'s formatting, and a second convention for the same
    // figure depending on the sign of the base is not a convention.
    expect(formatChange(-1000, -500)).toBe(`+۵۰${DECIMAL_SEPARATOR}۰${PERCENT_SIGN}`);
  });

  it('says so when either end is absent', () => {
    expect(formatChange(null, 100)).toBe(ABSENT);
    expect(formatChange(100, undefined)).toBe(ABSENT);
  });
});

describe('dates as text', () => {
  it('names the month rather than numbering it', () => {
    // "۶" is ambiguous between a Gregorian June and a Jalali Shahrivar on a
    // document carrying both; "شهریور" is not.
    expect(formatJalali('2026-09-28')).toBe('۶ مهر ۱۴۰۵');
  });

  it('puts the Esfand 30th on the right Gregorian day', () => {
    // 1403 is leap, so 1403-12-30 exists and is 20 March 2025. Under the
    // 33-year approximation this same date is 1403-12-29, and the tender's own
    // New Year lands on the wrong day.
    expect(formatJalali('2025-03-20')).toBe('۳۰ اسفند ۱۴۰۳');
  });

  it('renders the numeric date form the defence document uses by default', () => {
    // `format_jalali` in the backend returns `۱۴۰۳/۰۵/۱۲` unless `long_form=True`,
    // and a column that has to line up under a date in the document gets this one.
    // The order is year/month/day - the reverse of the ISO string it was given, so
    // it is a conversion and not a digit swap.
    expect(formatJalaliNumeric('2026-09-28')).toBe('۱۴۰۵/۰۷/۰۶');
    expect(formatJalaliNumeric('2025-03-20')).toBe('۱۴۰۳/۱۲/۳۰');
  });

  it('zero-pads month and day, so a column of dates stays aligned', () => {
    // `۱۴۰۵/۷/۱` next to `۱۴۰۵/۰۶/۱۰` is unreadable, and an unpadded day is
    // ambiguous between 1 and 1-something when it sits in a table. Both sides of
    // the comparison are from `format_jalali` in the backend.
    expect(formatJalaliNumeric('2026-09-01')).toBe('۱۴۰۵/۰۶/۱۰');
    expect(formatJalaliNumeric('2026-09-22')).toBe('۱۴۰۵/۰۶/۳۱');
  });

  it('agrees with the backend on the named form, not just the numeric one', () => {
    // `format_jalali(..., long_form=True)` in the backend. Checked at a month
    // boundary because that is where a month-length error surfaces, and Shahrivar
    // is a 31-day month so 31 Shahrivar is the last day before Mehr.
    expect(formatJalali('2026-09-01')).toBe('۱۰ شهریور ۱۴۰۵');
    expect(formatJalali('2026-09-22')).toBe('۳۱ شهریور ۱۴۰۵');
    expect(formatJalali('2024-03-20')).toBe('۱ فروردین ۱۴۰۳');
  });

  it('shows both calendars when a column has to carry both', () => {
    const both = formatDualDate('2026-09-28');

    expect(both.jalali).toBe('۶ مهر ۱۴۰۵');
    // Slashes, not the decimal separator and not ASCII hyphens: the date
    // formatters and the number formatter are for different units, and routing a
    // date through the number formatter puts a thousands separator inside the
    // year (`۲٬۰۲۶-۰۹-۲۸`).
    expect(both.gregorian).toBe('۲۰۲۶/۰۹/۲۸');
  });

  it('prefixes the weekday only when asked', () => {
    expect(formatJalali('2026-09-28')).not.toMatch(/یکشنبه|دوشنبه/);
    expect(formatJalali('2026-09-28', true)).toMatch(
      /یکشنبه|دوشنبه|سه‌شنبه|چهارشنبه|پنجشنبه|جمعه|شنبه/
    );
  });
});
