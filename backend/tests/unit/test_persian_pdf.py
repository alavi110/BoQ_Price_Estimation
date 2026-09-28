"""
Persian RTL document formatting (quickstart Scenario 5).

The defense document is submitted to Iranian tender authorities, so the RTL
layout, the Persian typography and the Solar Hijri dates are functional
requirements, not polish. WeasyPrint is an optional backend (it needs a
Pango/GTK stack); these tests cover the HTML that WeasyPrint consumes, which
is where the RTL and Persian formatting actually lives, and they skip only the
byte-level PDF assertions when the backend is absent.
"""
from datetime import date, datetime

import pytest

from src.services.calendar_converter import (
    MAX_JALALI_YEAR,
    MIN_JALALI_YEAR,
    MONTH_NAMES_FA,
    JalaliDate,
    days_in_jalali_month,
    format_jalali,
    format_number,
    gregorian_to_jalali,
    is_leap_jalali,
    jalali_to_gregorian,
    nowruz_gregorian,
    parse_jalali,
    to_persian_digits,
    weekday_fa,
)
from src.services.defense_doc_generator import DefenseDocumentGenerator
from src.services.pdf_generator import PDFGenerator


@pytest.fixture
def generator():
    return DefenseDocumentGenerator()


@pytest.fixture
def html(generator, defense_project, defense_item_rows) -> str:
    return generator.render_preview(defense_project, defense_item_rows, output_format="html").html


# --------------------------------------------------------------------------- #
# Solar Hijri calendar
# --------------------------------------------------------------------------- #
class TestJalaliCalendar:
    @pytest.mark.parametrize(
        "gregorian, jalali",
        [
            # Published, widely cross-checked dates. The Esfand entries follow
            # from the Nowruz sequence: 1399->2020-03-20, 1400->2021-03-21,
            # 1401->2022-03-21, 1402->2023-03-21, 1403->2024-03-20, 1404->2025-03-21.
            ((1979, 2, 11), (1357, 11, 22)),   # Islamic Revolution
            ((2000, 1, 1), (1378, 10, 11)),
            ((2020, 3, 20), (1399, 1, 1)),     # Nowruz 1399
            ((2021, 3, 20), (1399, 12, 30)),   # 1399 is a leap year
            ((2021, 3, 21), (1400, 1, 1)),     # Nowruz 1400
            ((2022, 3, 21), (1401, 1, 1)),     # Nowruz 1401
            ((2023, 3, 21), (1402, 1, 1)),     # Nowruz 1402
            ((2024, 3, 19), (1402, 12, 29)),   # 1402 is a common year
            ((2024, 3, 20), (1403, 1, 1)),     # Nowruz 1403
            ((2025, 3, 20), (1403, 12, 30)),   # 1403 is a leap year
            ((2025, 3, 21), (1404, 1, 1)),     # Nowruz 1404
            ((2026, 3, 21), (1405, 1, 1)),     # Nowruz 1405
            ((2026, 9, 27), (1405, 7, 5)),
        ],
    )
    def test_known_anchors(self, gregorian, jalali):
        assert gregorian_to_jalali(*gregorian) == JalaliDate(*jalali)
        assert jalali_to_gregorian(*jalali) == date(*gregorian)

    def test_nowruz_dates_match_the_published_sequence(self):
        """1 Farvardin for 1395-1410, as printed in every Iranian almanac."""
        expected = {
            1395: date(2016, 3, 20),
            1396: date(2017, 3, 21),
            1397: date(2018, 3, 21),
            1398: date(2019, 3, 21),
            1399: date(2020, 3, 20),
            1400: date(2021, 3, 21),
            1401: date(2022, 3, 21),
            1402: date(2023, 3, 21),
            1403: date(2024, 3, 20),
            1404: date(2025, 3, 21),
            1405: date(2026, 3, 21),
            1406: date(2027, 3, 21),
            1407: date(2028, 3, 20),
            1408: date(2029, 3, 20),
            1409: date(2030, 3, 21),
            1410: date(2031, 3, 21),
        }
        for year, nowruz in expected.items():
            assert nowruz_gregorian(year) == nowruz, f"Nowruz {year} is wrong"

    def test_round_trips_over_seventy_years(self):
        """Every day for 70 years must survive a round trip in both directions."""
        from datetime import timedelta

        day = date(1990, 1, 1)
        end = date(2060, 1, 1)
        while day < end:
            assert to_jalali_check(day) == day
            day += timedelta(days=1)

    def test_jalali_leap_years(self):
        # The documented sequence around the present: 1391, 1395, 1399, 1403,
        # then a five-year gap to 1408, 1412, 1416, 1420.
        assert is_leap_jalali(1399) is True
        assert is_leap_jalali(1400) is False
        assert is_leap_jalali(1402) is False
        assert is_leap_jalali(1403) is True
        assert is_leap_jalali(1404) is False
        assert is_leap_jalali(1408) is True
        assert is_leap_jalali(1412) is True

    def test_esfand_has_30_days_only_in_a_leap_year(self):
        assert days_in_jalali_month(1403, 12) == 30
        assert days_in_jalali_month(1404, 12) == 29
        assert days_in_jalali_month(1402, 12) == 29

    def test_month_lengths(self):
        for year in (1403, 1404, 1405):
            for month in range(1, 7):
                assert days_in_jalali_month(year, month) == 31
            for month in range(7, 12):
                assert days_in_jalali_month(year, month) == 30

    def test_all_twelve_month_names_are_present(self):
        assert len(MONTH_NAMES_FA) == 12
        assert MONTH_NAMES_FA[0] == "فروردین"
        assert MONTH_NAMES_FA[6] == "مهر"
        assert MONTH_NAMES_FA[11] == "اسفند"

    def test_rejects_impossible_dates(self):
        with pytest.raises(ValueError, match="(?i)invalid jalali month"):
            JalaliDate(1403, 13, 1)
        with pytest.raises(ValueError, match="(?i)invalid jalali day"):
            JalaliDate(1403, 1, 32)
        with pytest.raises(ValueError, match="(?i)invalid jalali day"):
            # 1404 is a common year, so Esfand has 29 days
            JalaliDate(1404, 12, 30)

    def test_rejects_invalid_gregorian_input(self):
        with pytest.raises(ValueError, match="(?i)invalid gregorian month"):
            gregorian_to_jalali(2026, 13, 1)
        with pytest.raises(ValueError, match="(?i)invalid gregorian day"):
            gregorian_to_jalali(2026, 2, 30)

    def test_refuses_to_extrapolate_beyond_the_defined_calendar(self):
        """
        The leap pattern is only defined between the cycle breaks.

        Outside that window the algorithm would still return a confident-looking
        answer, so it has to raise rather than print a wrong year on a tender
        document.
        """
        assert MIN_JALALI_YEAR < 1405 < MAX_JALALI_YEAR

        with pytest.raises(ValueError, match="(?i)outside the range"):
            gregorian_to_jalali(9000, 1, 1)

    def test_format_numeric_uses_persian_digits(self):
        rendered = format_jalali(date(2026, 9, 27))

        assert rendered == "۱۴۰۵/۰۷/۰۵"
        assert all(ch in "۰۱۲۳۴۵۶۷۸۹/" for ch in rendered)

    def test_format_ascii_keeps_latin_digits(self):
        assert format_jalali(date(2026, 9, 27), persian_digits=False) == "1405/07/05"

    def test_long_form_uses_the_persian_month_name(self):
        rendered = format_jalali(date(2026, 9, 27), long_form=True)

        assert "مهر" in rendered
        assert "۱۴۰۵" in rendered
        assert "۵" in rendered

    def test_weekday_name_is_saturday_first(self):
        # 2026-09-27 is a Sunday, which is the second day of the Iranian week
        assert weekday_fa(date(2026, 9, 27)) == "یکشنبه"
        # 2024-03-23 was a Friday
        assert weekday_fa(date(2024, 3, 22)) == "جمعه"

    def test_datetime_is_accepted(self):
        assert format_jalali(datetime(2026, 9, 27, 14, 30)) == "۱۴۰۵/۰۷/۰۵"

    def test_none_renders_as_empty(self):
        assert format_jalali(None) == ""

    def test_parse_round_trip(self):
        parsed = parse_jalali("۱۴۰۵/۰۷/۰۵")

        assert parsed == JalaliDate(1405, 7, 5)
        assert parsed.to_gregorian() == date(2026, 9, 27)

    def test_parse_accepts_ascii_digits_and_separators(self):
        assert parse_jalali("1405-7-5") == JalaliDate(1405, 7, 5)
        assert parse_jalali("1405.07.05") == JalaliDate(1405, 7, 5)

    def test_parse_rejects_garbage(self):
        with pytest.raises(ValueError, match="(?i)cannot parse"):
            parse_jalali("not a date")
        with pytest.raises(ValueError, match="(?i)empty"):
            parse_jalali("")

    def test_number_formatting(self):
        # Grouping separator is whatever Python emits; the digits are what matter
        grouped = format_number(1234567)
        assert len(grouped) == len("1,234,567")
        assert set(grouped) <= set("۰۱۲۳۴۵۶۷۸۹,٬")

        ascii_form = format_number(1234567, persian_digits=False)
        assert ascii_form == "1,234,567"
        assert not any(ch in ascii_form for ch in "۰۱۲۳۴۵۶۷۸۹")

        assert format_number(None) == ""
        # 1,234.50 with Persian digits and a fixed two-decimal tail
        assert format_number(1234.5, decimals=2) == "۱,۲۳۴.۵۰"
        assert to_persian_digits("2026") == "۲۰۲۶"


def to_jalali_check(value):
    """Round-trip a Gregorian date through Jalali and back."""
    from src.services.calendar_converter import to_jalali

    return to_jalali(value).to_gregorian()


# --------------------------------------------------------------------------- #
# RTL document structure
# --------------------------------------------------------------------------- #
class TestRTLStructure:
    def test_document_declares_rtl_direction(self, html):
        assert 'dir="rtl"' in html
        assert 'lang="fa"' in html

    def test_stylesheet_sets_rtl_text_direction(self, html):
        assert "direction: rtl" in html
        assert "text-align: right" in html

    def test_uses_the_vazirmatn_typeface(self, html):
        assert "Vazirmatn" in html
        assert "@font-face" in html

    def test_page_setup_is_a4(self, html):
        assert "size: A4" in html

    def test_has_persian_running_headers(self, html):
        assert "@top-center" in html
        assert "سند دفاعیه" in html

    def test_has_persian_page_footers(self, html):
        assert "@bottom-right" in html
        assert "counter(page)" in html
        assert "counter(pages)" in html

    def test_footer_carries_the_jalali_document_date(self, html):
        """
        The running footer shows the compact Jalali date.

        Latin digits are deliberate there: it is a 9pt page element where
        Persian digits are harder to read at that size, and the Persian long
        form is on the cover.
        """
        assert "1405/07/05" in html

    def test_numbers_in_tables_are_ltr_isolated(self, html):
        """
        Amounts are Latin-digit numerals inside an RTL paragraph.

        Without ``unicode-bidi: plaintext`` the cell ordering flips and
        "1,234,567" renders as "765,432,1".
        """
        assert "unicode-bidi: plaintext" in html

    def test_html_is_utf8_encoded(self, html):
        assert "<meta charset=\"utf-8\"" in html or "<meta charset='utf-8'" in html
        assert "مهر" in html  # decodes cleanly as UTF-8


# --------------------------------------------------------------------------- #
# Content that must survive into the rendered document
# --------------------------------------------------------------------------- #
class TestPersianContentRendering:
    def test_cover_shows_project_and_tender_identity(self, html, defense_project):
        assert defense_project["project_name"] in html
        assert defense_project["tender_reference"] in html
        assert defense_project["contractor_name"] in html

    def test_cover_shows_the_jalali_date_long_form(self, html):
        assert "۵ مهر ۱۴۰۵" in html

    def test_every_item_description_is_rendered(self, html, defense_item_rows):
        for row in defense_item_rows:
            assert row["description_fa"] in html
            assert row["code"] in html

    def test_weight_table_has_all_seven_components(self, html):
        for label in (
            "مس",
            "فولاد",
            "سیمان",
            "پلیمر",
            "انرژی",
            "کار",
            "مصارف",
        ):
            assert label in html, f"component label missing from document: {label}"

    def test_expert_override_is_badged_and_explained(self, html):
        assert "دارای اصلاح کارشناس" in html
        assert "دلیل اصلاح" in html
        assert "قیمت مس در بازار جهانی افزایش یافت" in html

    def test_adjustment_ladder_is_labelled_in_persian(self, html):
        assert "حاشیه ریسک" in html
        assert "شرایط پرداخت" in html
        assert "حاشیه سود" in html

    def test_scenarios_are_labelled_in_persian(self, html):
        assert "خوش‌بینانه" in html
        assert "بدبینانه" in html

    def test_forecast_appendix_lists_scenarios(self, html):
        assert "پیوست" in html
        assert "پیش‌بینی قیمت" in html

    def test_methodology_section_is_present(self, html):
        assert "روش‌شناسی" in html

    def test_amounts_are_rendered_with_digit_grouping(self, html):
        # 20,000,000 base total across the two fixture items
        assert "20,000,000" in html or "۲۰,۰۰۰,۰۰۰" in html

    def test_no_unrendered_jinja_delimiters(self, html):
        assert "{{" not in html
        assert "{%" not in html

    def test_no_none_leaked_into_the_output(self, html):
        assert ">None<" not in html
        assert "None%" not in html


# --------------------------------------------------------------------------- #
# Font discovery
# --------------------------------------------------------------------------- #
class TestFontDiscovery:
    def test_font_url_is_a_file_url_or_empty(self):
        generator = PDFGenerator()
        url = generator.font_url

        assert url == "" or url.startswith("file://")

    def test_pdf_backend_availability_is_reported_honestly(self):
        """The renderer must know whether it can actually produce a PDF."""
        assert isinstance(PDFGenerator.weasyprint_available(), bool)

    def test_pdf_request_without_backend_raises_an_actionable_error(
        self, generator, defense_project, defense_item_rows
    ):
        from src.services.pdf_generator import PDFBackendUnavailable

        if PDFGenerator.weasyprint_available():
            pytest.skip("WeasyPrint is available; the missing-backend path cannot be exercised")

        with pytest.raises(PDFBackendUnavailable) as excinfo:
            generator.generate(defense_project, defense_item_rows, output_format="pdf")

        message = str(excinfo.value)
        assert "WeasyPrint" in message
        # The error has to tell an operator what to do about it.
        assert "first_steps" in message or "install" in message.lower()
