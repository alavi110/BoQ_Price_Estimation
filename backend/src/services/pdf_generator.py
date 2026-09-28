"""
Defense document rendering.

Two outputs, one content tree:

* **HTML** - rendered by Jinja2 with an RTL stylesheet, a Vazirmatn
  ``@font-face`` and Persian page headers/footers. Always available; this is
  also the intermediate WeasyPrint consumes.
* **PDF** - produced by WeasyPrint from that same HTML. WeasyPrint is an
  optional dependency: it needs the Pango/GTK native stack, which is not
  available on every platform (notably stock Windows). When it is missing,
  :class:`PDFBackendUnavailable` is raised with the install instructions
  rather than silently emitting a Latin-only PDF that would misrepresent
  Persian text.
"""
import os
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Dict, Optional

from jinja2 import Environment, FileSystemLoader, select_autoescape

from src.core.logging import get_logger
from src.services.calendar_converter import (
    format_jalali,
    format_number,
    to_jalali,
    to_persian_digits,
)
from src.services.document_content import DocumentContent
from src.services.weight_history import SOURCE_LABELS_FA

logger = get_logger(__name__)

TEMPLATE_DIR = Path(__file__).parent / "templates"
DEFAULT_TEMPLATE = "defense_document_fa.html.j2"

#: Where a bundled Vazirmatn webfont is looked up, relative to the backend root
FONT_DIR_CANDIDATES = (
    Path("assets/fonts"),
    Path(__file__).resolve().parents[2] / "assets" / "fonts",
)

SCENARIO_LABELS_FA = {
    "optimistic": "خوش‌بینانه",
    "base": "پایه",
    "pessimistic": "بدبینانه",
}

#: Most filesystems cap a single filename at 255 bytes.
MAX_FILENAME_BYTES = 255

#: Device names Windows refuses to use as a filename stem.
WINDOWS_RESERVED_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{i}" for i in range(1, 10)}
    | {f"LPT{i}" for i in range(1, 10)}
)


class PDFBackendUnavailable(RuntimeError):
    """Raised when PDF output is requested but WeasyPrint cannot be loaded."""


@dataclass
class RenderResult:
    """A rendered document plus the metadata the caller needs to store it."""

    html: str
    format: str
    filename: str
    report: Any = None
    #: Name to use on disk. Defaults to :attr:`filename`; the generator sets it
    #: to the opaque document id so the download URL can find the file without
    #: echoing a user-supplied document number into a path lookup.
    storage_name: Optional[str] = None

    @property
    def size(self) -> int:
        return len(self.html.encode("utf-8")) if self.format == "html" else len(self.html)

    def name_on_disk(self) -> str:
        return self.storage_name or self.filename


# --------------------------------------------------------------------------- #
# Jinja filters
# --------------------------------------------------------------------------- #
def _money(value: Any, persian_digits: bool = True) -> str:
    if value is None:
        return "-"
    try:
        return format_number(round(float(value), 0), persian_digits=persian_digits)
    except (TypeError, ValueError):
        return str(value)


def _percent_value(value: Any, persian_digits: bool = True) -> str:
    """A weight/ratio rendered as a percentage of 1.0 (0.7 -> ۷۰٪)."""
    if value is None:
        return "-"
    try:
        return format_number(round(float(value) * 100, 2), persian_digits=persian_digits) + "٪"
    except (TypeError, ValueError):
        return str(value)


def _percent(value: Any, persian_digits: bool = True) -> str:
    """A value that is already a percentage (12.5 -> ۱۲٫۵٪)."""
    if value is None:
        return "-"
    try:
        return format_number(round(float(value), 1), persian_digits=persian_digits) + "٪"
    except (TypeError, ValueError):
        return str(value)


def _number(value: Any, decimals: Optional[int] = None) -> str:
    """Format a number with thousands separators in Persian digits."""
    if value is None:
        return "-"
    return format_number(value, persian_digits=True, decimals=decimals)


def _fa_date(value: Any) -> str:
    """Render a date as ``۱۲ مرداد ۱۴۰۳``; accepts dates or ISO strings."""
    if not value:
        return "-"
    if isinstance(value, str):
        try:
            value = date.fromisoformat(value)
        except ValueError:
            return "-"
    return format_jalali(value, long_form=True) or "-"


def _date_fromiso(value: str) -> str:
    return _fa_date(value)


class PDFGenerator:
    """
    Render the defense document to HTML or PDF.

    Args:
        template_dir: directory holding the Jinja2 templates.
        template_name: template to render.
        font_path: optional path to a Vazirmatn ``.woff2``/``.ttf`` file to embed.
    """

    def __init__(
        self,
        template_dir: Optional[Path] = None,
        template_name: str = DEFAULT_TEMPLATE,
        font_path: Optional[Path] = None,
    ):
        self.template_dir = Path(template_dir or TEMPLATE_DIR)
        self.template_name = template_name
        self.font_path = Path(font_path) if font_path else self._discover_font()

        self.env = Environment(
            loader=FileSystemLoader(str(self.template_dir)),
            autoescape=select_autoescape(["html", "xml", "j2"]),
            trim_blocks=True,
            lstrip_blocks=True,
        )
        # Formatting helpers are exposed as both filters (``{{ v|fa_money }}``)
        # and globals (``{{ fa_money(v) }}``) so the template can use whichever
        # reads better at the call site.
        helpers = {
            "fa_money": _money,
            "fa_percent": _percent,
            "fa_percent_value": _percent_value,
            "fa_number": _number,
            "fa_date": _fa_date,
            "fa_date_iso": _date_fromiso,
            "persian_digits": to_persian_digits,
            "to_jalali": to_jalali,
        }
        self.env.filters.update(helpers)
        self.env.globals.update(helpers)
        self.logger = logger

    # ------------------------------------------------------------------ #
    @staticmethod
    def _discover_font() -> Optional[Path]:
        """Locate a bundled Vazirmatn webfont, if the project ships one."""
        for candidate in FONT_DIR_CANDIDATES:
            if not candidate.is_dir():
                continue
            for pattern in ("Vazirmatn*.woff2", "Vazirmatn*.ttf", "vazirmatn*.woff2"):
                for match in sorted(candidate.glob(pattern)):
                    return match
        return None

    @property
    def font_url(self) -> str:
        """``url()`` value for the ``@font-face`` rule."""
        if self.font_path is not None and self.font_path.is_file():
            return self.font_path.resolve().as_uri()
        # No bundled font: fall back to the system stack declared in the CSS.
        return ""

    @staticmethod
    def weasyprint_available() -> bool:
        """Whether the WeasyPrint backend can be imported."""
        try:
            import weasyprint  # noqa: F401
        except Exception:
            return False
        return True

    # ------------------------------------------------------------------ #
    def _template_context(
        self,
        content: DocumentContent,
        report: Any = None,
    ) -> Dict[str, Any]:
        from src.core.config import settings

        return {
            "content": content,
            "report": report,
            "scenario_labels": SCENARIO_LABELS_FA,
            "fusion_alpha": settings.WEIGHT_FUSION_ALPHA,
            "font_url": self.font_url,
            "cover_change_pct": content.cover.change_pct,
            "source_labels": SOURCE_LABELS_FA,
        }

    def render_html(self, content: DocumentContent, report: Any = None) -> str:
        """Render the document to an RTL HTML string."""
        template = self.env.get_template(self.template_name)
        return template.render(**self._template_context(content, report))

    def write_html(
        self,
        content: DocumentContent,
        path: Path,
        report: Any = None,
    ) -> Path:
        """Render and write the HTML document to disk."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.render_html(content, report), encoding="utf-8")
        self.logger.info("defense_html_written", path=str(path))
        return path

    # ------------------------------------------------------------------ #
    def html_to_pdf(self, html: str, base_url: Optional[str] = None) -> bytes:
        """
        Convert an HTML string to PDF bytes.

        Raises:
            PDFBackendUnavailable: WeasyPrint is not importable.
        """
        try:
            from weasyprint import HTML
        except Exception as exc:  # ImportError, or a broken native stack
            raise PDFBackendUnavailable(
                "WeasyPrint could not be loaded, so PDF export is unavailable. "
                "Install its native dependencies (Pango/cairo) - see "
                "https://doc.courtbouillon.org/weasyprint/stable/first_steps.html "
                "- or request format='html' instead. "
                f"Underlying error: {exc}"
            ) from exc

        return HTML(string=html, base_url=base_url).write_pdf()

    def generate_pdf(
        self,
        content: DocumentContent,
        report: Any = None,
        base_url: Optional[str] = None,
    ) -> bytes:
        """Render the document and return PDF bytes."""
        html = self.render_html(content, report)
        if base_url is None and self.font_path is not None:
            base_url = str(self.font_path.resolve().parent)
        return self.html_to_pdf(html, base_url=base_url)

    def write_pdf(
        self,
        content: DocumentContent,
        path: Path,
        report: Any = None,
    ) -> Path:
        """Render the document and write it as a PDF."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(self.generate_pdf(content, report))
        self.logger.info("defense_pdf_written", path=str(path))
        return path

    # ------------------------------------------------------------------ #
    def generate(
        self,
        content: DocumentContent,
        output_format: str = "pdf",
        report: Any = None,
    ) -> RenderResult:
        """
        Render in the requested format.

        Args:
            output_format: ``pdf`` or ``html``. ``None`` means "use the default"
                (PDF); an unrecognised or empty string is an error rather than a
                silent fallback, so a client bug surfaces here instead of as a
                confusing failure several layers down.

        Raises:
            ValueError: unknown format.
            PDFBackendUnavailable: PDF requested but WeasyPrint is missing.
        """
        fmt = "pdf" if output_format is None else str(output_format).strip().lower()
        if fmt == "html":
            return RenderResult(
                html=self.render_html(content, report),
                format="html",
                filename=self.default_filename(content, "html"),
                report=report,
            )
        if fmt == "pdf":
            return RenderResult(
                html=self.generate_pdf(content, report),
                format="pdf",
                filename=self.default_filename(content, "pdf"),
                report=report,
            )
        raise ValueError(
            f"Unsupported export format: {output_format!r} (expected 'pdf' or 'html')"
        )

    def write(
        self,
        content: DocumentContent,
        output_format: str,
        output_dir: Path,
        report: Any = None,
        storage_stem: Optional[str] = None,
    ) -> "tuple[Path, RenderResult]":
        """
        Render the document and write it into *output_dir*.

        Returns the written path and the render result. The directory is created
        if missing, and the filename is kept unique so re-exporting the same
        document does not clobber a previous submission.

        Args:
            storage_stem: overrides the on-disk stem. Callers that later look
                the file up by an opaque id pass it here, so the readable
                :attr:`RenderResult.filename` can stay the download name shown
                to the user without being part of the path.
        """
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        result = self.generate(content, output_format, report)
        if storage_stem:
            safe = "".join(
                ch if (ch.isalnum() or ch in "-_") else "_" for ch in storage_stem
            ).strip("_")
            extension = "pdf" if result.format == "pdf" else "html"
            result.storage_name = f"{safe or 'document'}_defense_doc.{extension}"

        target = output_dir / result.name_on_disk()
        if target.exists():
            target = _unique_path(target)

        if result.format == "html":
            target.write_text(result.html, encoding="utf-8")
        else:
            target.write_bytes(result.html)

        self.logger.info("defense_document_written", path=str(target), size=target.stat().st_size)
        return target, result

    @staticmethod
    def default_filename(content: DocumentContent, extension: str = "pdf") -> str:
        """
        Build a filesystem-safe filename from the project identity.

        Filenames come from user-supplied document numbers, so they are
        sanitised against path separators, reserved device names and the 255-byte
        filesystem limit. A rejected upload must not become a write error on the
        export path.
        """
        raw = (
            content.cover.document_number
            or content.cover.project_code
            or content.cover.project_name
            or "defense"
        )
        stem = "".join(
            # alnum() is Unicode-aware, so Persian characters survive intact.
            ch if (ch.isalnum() or ch in "-_") else "_"
            for ch in str(raw)
        )
        stem = re.sub(r"_{2,}", "_", stem).strip("_") or "defense"

        # Leave room for the suffix below the 255-byte filesystem limit.
        suffix = f"_defense_doc.{extension}"
        encoded = stem.encode("utf-8")
        if len(encoded) + len(suffix) > MAX_FILENAME_BYTES:
            stem = encoded[: MAX_FILENAME_BYTES - len(suffix)].decode("utf-8", "ignore")

        name = f"{stem}{suffix}"
        if Path(name).stem.upper() in WINDOWS_RESERVED_NAMES:
            # CON, PRN, AUX... never work as a filename on Windows.
            name = f"_{name}"
        return name


#: process-wide generator
pdf_generator = PDFGenerator()


def _unique_path(path: Path) -> Path:
    """
    Return a non-colliding sibling of *path*.

    Re-exporting a document must not overwrite the copy that was already
    submitted - a tender defence is a historical record.
    """
    stem, suffix, parent = path.stem, path.suffix, path.parent
    for counter in range(2, 1000):
        candidate = parent / f"{stem}_{counter}{suffix}"
        if not candidate.exists():
            return candidate
    raise OSError(f"Cannot find a free filename next to {path}")
