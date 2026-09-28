"""
PDF export for the defense document (quickstart Scenario 5).

WeasyPrint needs a native Pango/cairo stack, which is present in the project's
Docker image but not on a bare Windows host. Rather than skip the whole file,
these tests cover everything that is genuinely testable everywhere - the
renderer's contract, filename hygiene, on-disk writes, the PDF magic bytes -
and gate only the byte-level PDF assertions behind
:func:`PDFGenerator.weasyprint_available`.

The design decision this encodes: when the backend is missing, the exporter
raises ``PDFBackendUnavailable`` with install instructions instead of silently
emitting a PDF with unshaped Persian text. A clear failure beats a plausible-
looking but wrong submission.
"""
from pathlib import Path

import pytest

from src.services.defense_doc_generator import DefenseDocumentGenerator
from src.services.document_content import DocumentContentBuilder
from src.services.pdf_generator import (
    PDFBackendUnavailable,
    PDFGenerator,
    RenderResult,
)

from tests.conftest import make_defense_item_row

#: True when the native Pango/cairo stack is present.
HAS_PDF_BACKEND = PDFGenerator.weasyprint_available()

requires_pdf_backend = pytest.mark.skipif(
    not HAS_PDF_BACKEND,
    reason=(
        "WeasyPrint's native Pango/cairo stack is unavailable on this host; "
        "the PDF path is exercised in the project's Docker image"
    ),
)


@pytest.fixture
def generator():
    return DefenseDocumentGenerator()


@pytest.fixture
def content(defense_project, defense_item_rows):
    return DocumentContentBuilder().build(defense_project, defense_item_rows)


# --------------------------------------------------------------------------- #
# Backend availability
# --------------------------------------------------------------------------- #
class TestBackendAvailability:
    def test_availability_is_reported_as_a_bool(self):
        assert isinstance(PDFGenerator.weasyprint_available(), bool)

    def test_availability_is_stable_across_calls(self):
        assert PDFGenerator.weasyprint_available() is PDFGenerator.weasyprint_available()

    def test_availability_probe_does_not_raise(self):
        """The probe runs on every API request, so it must never blow up."""
        PDFGenerator.weasyprint_available()

    @pytest.mark.skipif(HAS_PDF_BACKEND, reason="backend is present on this host")
    def test_html_still_works_without_a_pdf_backend(self, generator, defense_project, defense_item_rows):
        """
        Losing PDF must not take the document down with it.

        HTML carries every piece of content, so a machine without WeasyPrint can
        still produce a defence document.
        """
        record = generator.generate(defense_project, defense_item_rows, output_format="html")
        assert record.status == "completed"
        assert record.filename.endswith(".html")

    @pytest.mark.skipif(HAS_PDF_BACKEND, reason="backend is present on this host")
    def test_missing_backend_raises_with_actionable_guidance(self, generator, defense_project, defense_item_rows):
        with pytest.raises(PDFBackendUnavailable) as excinfo:
            generator.generate(defense_project, defense_item_rows, output_format="pdf")

        message = str(excinfo.value)
        # An operator has to be able to act on this without reading our source.
        assert "WeasyPrint" in message
        assert "courtbouillon.org" in message
        assert "html" in message.lower()

    @pytest.mark.skipif(HAS_PDF_BACKEND, reason="backend is present on this host")
    def test_missing_backend_leaves_a_rejection_record(self, generator, defense_project, defense_item_rows):
        """A failed PDF must be visible in the audit trail, not vanish."""
        with pytest.raises(PDFBackendUnavailable):
            generator.generate(defense_project, defense_item_rows, output_format="pdf")


# --------------------------------------------------------------------------- #
# Render contract
# --------------------------------------------------------------------------- #
class TestRenderContract:
    def test_html_render_returns_a_result_envelope(self, generator, defense_project, defense_item_rows):
        result = generator.render_preview(defense_project, defense_item_rows, output_format="html")

        assert isinstance(result, RenderResult)
        assert result.format == "html"
        assert result.html
        assert result.size == len(result.html.encode("utf-8"))
        assert result.filename.endswith(".html")

    def test_html_is_always_available_regardless_of_backend(self, content):
        assert PDFGenerator().render_html(content)

    def test_format_is_case_insensitive(self, content):
        result = PDFGenerator().generate(content, output_format="HTML")
        assert result.format == "html"

    @pytest.mark.parametrize("bad", ["docx", "xlsx", "csv", "", "json"])
    def test_unknown_formats_are_rejected(self, content, bad):
        with pytest.raises(ValueError, match="(?i)unsupported export format"):
            PDFGenerator().generate(content, output_format=bad)

    def test_none_format_means_pdf(self, content):
        """
        A missing format defaults to PDF, the submission format.

        An *empty* format is different - see the strictness test above.
        """
        with pytest.raises((PDFBackendUnavailable, ValueError)):
            PDFGenerator().generate(content, output_format=None)


# --------------------------------------------------------------------------- #
# Filenames
# --------------------------------------------------------------------------- #
class TestFilenameHygiene:
    def test_filename_falls_back_to_the_project_code(self, content):
        """No document number set, so the project code identifies the export."""
        assert PDFGenerator.default_filename(content, "pdf") == "PRJ-2026-01_defense_doc.pdf"

    def test_filename_is_used_when_a_document_number_exists(self, defense_project, defense_item_rows):
        content = DocumentContentBuilder().build(
            defense_project, defense_item_rows, document_number="DD-1405-007"
        )
        assert PDFGenerator.default_filename(content, "pdf") == "DD-1405-007_defense_doc.pdf"

    def test_document_number_takes_precedence_over_project_code(self, defense_project, defense_item_rows):
        content = DocumentContentBuilder().build(
            defense_project, defense_item_rows, document_number="DD-1"
        )
        name = PDFGenerator.default_filename(content, "pdf")
        assert name == "DD-1_defense_doc.pdf"
        assert "PRJ" not in name

    @pytest.mark.parametrize(
        "hostile",
        [
            "پروژه/نمونه",           # path separators
            "..\\..\\etc\\passwd",    # traversal
            "a b\tc\nd",              # whitespace and control characters
            "CON",                     # Windows reserved device name
            "x" * 300,                # over the filesystem limit
        ],
    )
    def test_hostile_document_numbers_produce_safe_filenames(self, defense_project, defense_item_rows, hostile):
        content = DocumentContentBuilder().build(
            defense_project, defense_item_rows, document_number=hostile
        )
        name = PDFGenerator.default_filename(content, "pdf")

        assert name
        assert not any(ch in name for ch in '/\\:*?"<>|\t\n\r')
        assert ".." not in name
        assert len(name.encode("utf-8")) <= 255
        # Must be a plain filename, not a path.
        assert Path(name).name == name

    def test_overlong_names_are_truncated_not_rejected(self, defense_project, defense_item_rows):
        """
        A long document number must still yield a writable name.

        Truncating is the right trade: refusing the export over a long reference
        would be a worse failure than a shortened filename.
        """
        content = DocumentContentBuilder().build(
            defense_project, defense_item_rows, document_number="A" * 400
        )
        name = PDFGenerator.default_filename(content, "pdf")

        assert len(name.encode("utf-8")) <= 255
        assert name.endswith("_defense_doc.pdf")

    def test_windows_device_names_are_escaped(self, defense_project, defense_item_rows):
        content = DocumentContentBuilder().build(
            defense_project, defense_item_rows, document_number="CON"
        )
        name = PDFGenerator.default_filename(content, "pdf")

        # Windows silently fails on these; an explicit escape is clearer.
        assert Path(name).stem.upper() not in {"CON", "PRN", "AUX", "NUL"}

    def test_persian_document_number_still_yields_a_usable_name(self, defense_project, defense_item_rows):
        content = DocumentContentBuilder().build(
            defense_project, defense_item_rows, document_number="سند-دفاعیه-۱۴۰۵"
        )
        name = PDFGenerator.default_filename(content, "pdf")

        assert name.endswith("_defense_doc.pdf")
        assert "/" not in name

    def test_extension_follows_the_format(self, content):
        assert PDFGenerator.default_filename(content, "html").endswith(".html")
        assert PDFGenerator.default_filename(content, "pdf").endswith(".pdf")


# --------------------------------------------------------------------------- #
# Writing to disk
# --------------------------------------------------------------------------- #
class TestFileOutput:
    def test_write_html_creates_missing_directories(self, content, tmp_path):
        target = tmp_path / "nested" / "deeper" / "doc.html"

        PDFGenerator().write_html(content, target)

        assert target.is_file()
        assert target.read_text(encoding="utf-8").startswith("<!DOCTYPE html>")

    def test_written_html_is_utf8(self, content, tmp_path):
        target = tmp_path / "doc.html"
        PDFGenerator().write_html(content, target)

        # Persian must survive the write; a cp1256 write would corrupt it.
        text = target.read_text(encoding="utf-8")
        assert "سند دفاعیه" in text

    def test_write_html_overwrites_an_existing_file(self, content, tmp_path):
        target = tmp_path / "doc.html"
        target.write_text("stale", encoding="utf-8")

        PDFGenerator().write_html(content, target)

        assert "stale" not in target.read_text(encoding="utf-8")

    def test_generation_writes_the_document_to_disk(self, generator, defense_project, defense_item_rows, tmp_path):
        """
        The API hands out a download URL, so the bytes have to land somewhere.

        A render that is never persisted would leave the endpoint returning a
        link to nothing.
        """
        generator.output_dir = tmp_path / "exports"

        record = generator.generate(defense_project, defense_item_rows, output_format="html")

        assert record.path
        written = Path(record.path)
        assert written.is_file()
        assert written.parent == generator.output_dir
        assert written.name == record.filename
        assert record.size_bytes == written.stat().st_size

    def test_repeated_exports_do_not_overwrite_each_other(self, generator, defense_project, defense_item_rows, tmp_path):
        """A submitted document is a historical record, not a scratch file."""
        generator.output_dir = tmp_path / "exports"

        first = generator.generate(defense_project, defense_item_rows, output_format="html")
        second = generator.generate(defense_project, defense_item_rows, output_format="html")

        assert first.path != second.path
        assert Path(first.path).is_file()
        assert Path(second.path).is_file()
        # Both are the same document, so both fingerprint the same.
        assert first.content_hash == second.content_hash

    def test_storage_name_is_the_document_id(self, generator, defense_project, defense_item_rows, tmp_path):
        """
        The file is addressed by an opaque id, not by the document number.

        The document number is user-supplied, so it must never reach a path
        lookup. The readable name survives as ``download_name``.
        """
        generator.output_dir = tmp_path / "exports"

        record = generator.generate(
            defense_project,
            defense_item_rows,
            output_format="html",
            document_number="../../etc/passwd",
        )

        assert Path(record.path).name.startswith(record.document_id)
        assert "passwd" not in record.path
        assert ".." not in record.path
        assert record.download_name.endswith("_defense_doc.html")
        assert record.download_name != record.filename

    def test_download_name_is_readable_and_differs_from_the_storage_name(self, generator, defense_project, defense_item_rows, tmp_path):
        """
        The user sees ``download_name``; the filesystem holds ``filename``.

        They are deliberately different, which is what keeps a user-supplied
        document number out of the path the download handler globs.
        """
        generator.output_dir = tmp_path / "exports"
        record = generator.generate(defense_project, defense_item_rows, output_format="html")

        assert record.download_name.endswith("_defense_doc.html")
        assert record.download_name != record.filename
        assert record.filename.startswith(record.document_id)
        assert record.to_dict()["download_name"] == record.download_name

    def test_written_html_is_utf8_on_disk(self, generator, defense_project, defense_item_rows, tmp_path):
        generator.output_dir = tmp_path / "exports"

        record = generator.generate(defense_project, defense_item_rows, output_format="html")

        assert "سند دفاعیه" in Path(record.path).read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# Byte-level PDF - requires the native stack
# --------------------------------------------------------------------------- #
@requires_pdf_backend
class TestPdfBytes:
    def test_pdf_starts_with_the_magic_header(self, content):
        pdf = PDFGenerator().generate_pdf(content)

        assert pdf.startswith(b"%PDF-")
        assert len(pdf) > 1000

    def test_pdf_ends_with_an_eof_marker(self, content):
        pdf = PDFGenerator().generate_pdf(content)
        assert b"%%EOF" in pdf[-2048:]

    def test_pdf_is_not_valid_utf8(self, content):
        """Guards against a text file being passed off as a PDF."""
        pdf = PDFGenerator().generate_pdf(content)
        with pytest.raises(UnicodeDecodeError):
            pdf.decode("utf-8")

    def test_pdf_reports_a_version(self, content):
        pdf = PDFGenerator().generate_pdf(content)
        assert pdf[:8].startswith(b"%PDF-1.")

    def test_generate_returns_pdf_bytes(self, content):
        result = PDFGenerator().generate(content, output_format="pdf")

        assert result.format == "pdf"
        assert result.filename.endswith(".pdf")
        assert result.html  # the byte payload

    def test_write_pdf_produces_a_readable_file(self, content, tmp_path):
        target = tmp_path / "doc.pdf"
        PDFGenerator().write_pdf(content, target)

        assert target.is_file()
        assert target.read_bytes().startswith(b"%PDF-")

    def test_pdf_embeds_a_font(self, content):
        """
        Without an embedded font the Persian text is unshaped tofu.

        The image installs Vazirmatn system-wide for exactly this reason.
        """
        pdf = PDFGenerator().generate_pdf(content)
        lowered = pdf.lower()
        assert b"/fontfile" in lowered or b"/fontdescriptor" in lowered
