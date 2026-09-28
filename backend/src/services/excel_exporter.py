"""
Excel export of the recalculated BoQ.

The defense document is the submission; this workbook is the working document
an estimator actually reconciles against. It therefore optimises for
inspectability rather than presentation:

* RTL sheet layout with Persian headers, so it opens correctly in Excel fa-IR.
* Every derived price is broken into its inputs in adjacent columns, so a
  reviewer can check the arithmetic without opening a second file.
* Live formulas rather than pasted values, where the value is derivable from
  other cells in the same row - a reviewer who changes a weight sees the total
  move, which is how the document gets challenged and defended.
"""
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.core.config import settings
from src.core.logging import get_logger
from src.models.boq_item import BoQItem
from src.models.chapter import Chapter
from src.models.forecast import Forecast
from src.models.price_calculation import PriceCalculation
from src.models.project import Project
from src.models.weight import Weight

logger = get_logger(__name__)

#: How long an export stays downloadable.
EXPORT_TTL_HOURS = 24

HEADER_FILL = PatternFill("solid", fgColor="1F4E5F")
HEADER_FONT = Font(color="FFFFFF", bold=True, size=11)
OVERRIDE_FILL = PatternFill("solid", fgColor="FFF2CC")
TOTAL_FILL = PatternFill("solid", fgColor="E8F0F2")
TOTAL_FONT = Font(bold=True)
THIN = Side(style="thin", color="B0B0B0")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)

#: ``label_fa, label_en`` for each column, in sheet order.
PRICE_COLUMNS = [
    ("ردیف", "Code", 12),
    ("شرح", "Description", 46),
    ("واحد", "Unit", 10),
    ("مقدار", "Quantity", 12),
    ("قیمت پایه", "Base Price", 18),
    ("قیمت تعدیل‌شده", "Index-Adjusted", 20),
    ("قیمت نهایی", "Final Price", 18),
    ("مبلغ کل", "Total", 20),
    ("درصد تغییر", "Change %", 12),
    ("منبع", "Source", 12),
    ("دلیل اصلاح", "Override Reason", 40),
]

WEIGHT_COLUMNS = [
    ("کد مؤلفه", "Component", 16),
    ("نام مؤلفه", "Component Name", 22),
    ("وزن LLM", "LLM Weight", 12),
    ("وزن ML", "ML Weight", 12),
    ("وزن نهایی", "Final Weight", 12),
    ("شاخص پایه", "Index Base", 14),
    ("شاخص فعلی", "Index Current", 14),
    ("نسبت", "Ratio", 10),
    ("سهم از قیمت", "Contribution", 16),
    ("منبع", "Source", 12),
]

FORECAST_COLUMNS = [
    ("ردیف", "Item Code", 12),
    ("افق (ماه)", "Horizon (months)", 14),
    ("سناریو", "Scenario", 14),
    ("قیمت پیش‌بینی", "Predicted", 20),
    ("کران پایین", "Lower", 18),
    ("کران بالا", "Upper", 18),
    ("مدل", "Model", 12),
]

SCENARIO_LABELS_FA = {
    "optimistic": "خوش‌بینانه",
    "base": "پایه",
    "pessimistic": "بدبینانه",
}


class ExcelExportError(RuntimeError):
    """Raised when the workbook cannot be produced (nothing to export)."""


@dataclass
class ExcelExportResult:
    """Where the workbook landed and what it contains."""

    document_id: str
    path: Path
    filename: str
    item_count: int
    chapter_count: int
    sheet_names: List[str] = field(default_factory=list)
    size_bytes: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "document_id": self.document_id,
            "filename": self.filename,
            "item_count": self.item_count,
            "chapter_count": self.chapter_count,
            "sheet_names": list(self.sheet_names),
            "size_bytes": self.size_bytes,
        }


# --------------------------------------------------------------------------- #
# Styling helpers
# --------------------------------------------------------------------------- #
def _style_sheet(sheet, columns: Sequence[Any], freeze: str = "A2") -> None:
    """Right-align a sheet for RTL, size the columns and freeze the header."""
    sheet.sheet_view.rightToLeft = True
    sheet.freeze_panes = freeze

    for index, (_, _, width) in enumerate(columns, start=1):
        letter = get_column_letter(index)
        sheet.column_dimensions[letter].width = width

        header = sheet.cell(row=1, column=index)
        header.fill = HEADER_FILL
        header.font = HEADER_FONT
        header.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        header.border = BORDER

    sheet.row_dimensions[1].height = 30


def _finalise_totals(sheet, first_row: int, last_row: int, columns: Sequence[Any]) -> None:
    """Add a bold totals row carrying live ``SUM`` formulas."""
    if last_row < first_row:
        return

    total_row = last_row + 1
    label = sheet.cell(row=total_row, column=1, value="جمع کل")
    label.font = TOTAL_FONT
    label.fill = TOTAL_FILL

    for index in range(1, len(columns) + 1):
        cell = sheet.cell(row=total_row, column=index)
        cell.fill = TOTAL_FILL
        cell.font = TOTAL_FONT
        cell.border = BORDER

    # Sum the quantity, price and total columns by header name so the column
    # order can change without breaking the formula.
    for index, (label_fa, label_en, _) in enumerate(columns, start=1):
        if label_en in {"Quantity", "Base Price", "Index-Adjusted", "Final Price", "Total"}:
            letter = get_column_letter(index)
            sheet.cell(
                row=total_row,
                column=index,
                value=f"=SUM({letter}{first_row}:{letter}{last_row})",
            ).font = TOTAL_FONT


# --------------------------------------------------------------------------- #
# Sheets
# --------------------------------------------------------------------------- #
def _write_prices(
    workbook: Workbook,
    items: Sequence[BoQItem],
    current: Dict[uuid.UUID, PriceCalculation],
) -> None:
    sheet = workbook.create_sheet("قیمت ها")
    sheet.append([label_fa for label_fa, _, _ in PRICE_COLUMNS])

    for item in items:
        calculation = current.get(item.id)
        base = float(item.base_price or 0)
        final = float(calculation.final_price) if calculation is not None else base
        adjusted = (
            float(calculation.index_snapshot.get("index_adjusted_price"))
            if calculation is not None
            and isinstance(calculation.index_snapshot, dict)
            and calculation.index_snapshot.get("index_adjusted_price") is not None
            else final
        )
        overridden = any(w.source == "expert" for w in item.weights)
        reasons = "; ".join(
            w.override_reason for w in item.weights if w.source == "expert" and w.override_reason
        )

        row = sheet.max_row + 1
        sheet.append(
            [
                item.code,
                item.description_fa,
                item.unit,
                float(item.quantity or 0),
                base,
                adjusted,
                final,
                f"=D{row}*G{row}",  # quantity x final price, as a live formula
                f"=IF(E{row}=0,\"\",(G{row}-E{row})/E{row})",
                "کارشناس" if overridden else "سیستم",
                reasons,
            ]
        )
        if overridden:
            for index in range(1, len(PRICE_COLUMNS) + 1):
                sheet.cell(row=row, column=index).fill = OVERRIDE_FILL

    _style_sheet(sheet, PRICE_COLUMNS)
    _finalise_totals(sheet, 2, sheet.max_row, PRICE_COLUMNS)

    for index in (5, 6, 7, 8):
        for cell in sheet.iter_cols(
            min_col=index, max_col=index, min_row=2, max_row=sheet.max_row
        ):
            for item in cell:
                item.number_format = "#,##0"
    for cell in sheet.iter_cols(min_col=9, max_col=9, min_row=2, max_row=sheet.max_row):
        for item in cell:
            item.number_format = "0.0%"
    sheet.column_dimensions["B"].width = 46


def _write_weights(
    workbook: Workbook,
    items: Sequence[BoQItem],
    current: Dict[uuid.UUID, PriceCalculation],
) -> None:
    sheet = workbook.create_sheet("وزن ها")
    sheet.append([label_fa for label_fa, _, _ in WEIGHT_COLUMNS])

    for item in items:
        snapshot = _index_snapshot(current.get(item.id))
        for weight in sorted(
            item.weights,
            key=lambda w: (w.component.display_order if w.component else 0),
        ):
            component = weight.component
            row = sheet.max_row + 1

            base_index = _lookup_index(snapshot, "base", weight.component_id)
            current_index = _lookup_index(snapshot, "current", weight.component_id)
            ratio = (current_index / base_index) if base_index else None
            contribution = (
                float(weight.final_weight or 0) * ratio if ratio is not None else None
            )

            sheet.append(
                [
                    f"{item.code} | {component.code if component else ''}",
                    (component.name_fa if component else "") or "",
                    float(weight.llm_weight) if weight.llm_weight is not None else None,
                    float(weight.ml_weight) if weight.ml_weight is not None else None,
                    float(weight.final_weight or 0),
                    base_index,
                    current_index,
                    ratio,
                    contribution,
                    weight.source,
                ]
            )
            if weight.source == "expert":
                for index in range(1, len(WEIGHT_COLUMNS) + 1):
                    sheet.cell(row=row, column=index).fill = OVERRIDE_FILL

    _style_sheet(sheet, WEIGHT_COLUMNS)
    for index in (3, 4, 5):
        for cell in sheet.iter_cols(
            min_col=index, max_col=index, min_row=2, max_row=sheet.max_row
        ):
            for item in cell:
                item.number_format = "0.0%"
    for index in (6, 7, 8, 9):
        for cell in sheet.iter_cols(
            min_col=index, max_col=index, min_row=2, max_row=sheet.max_row
        ):
            for item in cell:
                item.number_format = "#,##0.000"


def _index_snapshot(calculation: Optional[PriceCalculation]) -> Dict[str, Any]:
    snapshot = calculation.index_snapshot if calculation is not None else None
    return snapshot if isinstance(snapshot, dict) else {}


def _lookup_index(
    snapshot: Dict[str, Any],
    which: str,
    component_id: uuid.UUID,
) -> Optional[float]:
    """
    Read a component's base or current index out of a stored snapshot.

    Snapshots have been written in two shapes across the pipeline - flat
    ``index_base: 100.5`` and per-component ``{"<uuid>": 100.5}`` - so both are
    accepted rather than assuming one.
    """
    value = snapshot.get(f"index_{which}")
    if isinstance(value, dict):
        value = value.get(str(component_id))
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _write_forecasts(workbook: Workbook, items: Sequence[BoQItem]) -> None:
    sheet = workbook.create_sheet("پیش بینی ها")
    sheet.append([label_fa for label_fa, _, _ in FORECAST_COLUMNS])

    for item in items:
        for forecast in item.forecasts:
            sheet.append(
                [
                    item.code,
                    forecast.horizon_months,
                    SCENARIO_LABELS_FA.get(forecast.scenario, forecast.scenario),
                    float(forecast.predicted_price),
                    float(forecast.lower_bound) if forecast.lower_bound is not None else None,
                    float(forecast.upper_bound) if forecast.upper_bound is not None else None,
                    forecast.model.model_type if forecast.model else "",
                ]
            )

    _style_sheet(sheet, FORECAST_COLUMNS)
    for index in (4, 5, 6):
        for cell in sheet.iter_cols(
            min_col=index, max_col=index, min_row=2, max_row=sheet.max_row
        ):
            for item in cell:
                item.number_format = "#,##0"


def _write_comparison(workbook: Workbook, items: Sequence[BoQItem]) -> None:
    """Base vs updated, with the movement broken out by driver."""
    columns = [
        ("ردیف", "Code", 12),
        ("شرح", "Description", 46),
        ("قیمت پایه", "Base Price", 20),
        ("قیمت نهایی", "Final Price", 20),
        ("اثر شاخص", "Index Effect", 20),
        ("اثر ضرایب", "Adjustment Effect", 20),
        ("تغییر مطلق", "Absolute Change", 20),
        ("درصد تغییر", "Change %", 12),
    ]
    sheet = workbook.create_sheet("مقایسه")
    sheet.append([label_fa for label_fa, _, _ in columns])

    for item in items:
        row = sheet.max_row + 1
        base = float(item.base_price or 0)
        final = float(item.price_calculations[0].final_price) if item.price_calculations else base
        snapshot = (
            item.price_calculations[0].index_snapshot
            if item.price_calculations
            else {}
        )
        adjusted = (
            float(snapshot.get("index_adjusted_price"))
            if isinstance(snapshot, dict) and snapshot.get("index_adjusted_price") is not None
            else final
        )
        sheet.append(
            [
                item.code,
                item.description_fa,
                base,
                final,
                f"=D{row}-C{row}-(F{row})",
                f"=F{row}-E{row}",
                f"=D{row}-C{row}",
                f"=IF(C{row}=0,\"\",(D{row}-C{row})/C{row})",
            ]
        )

    _style_sheet(sheet, columns)
    for index in (3, 4, 5, 6, 7):
        for cell in sheet.iter_cols(
            min_col=index, max_col=index, min_row=2, max_row=sheet.max_row
        ):
            for item in cell:
                item.number_format = "#,##0"


def _write_cover(
    workbook: Workbook,
    project: Project,
    item_count: int,
    chapter_count: int,
) -> None:
    sheet = workbook.create_sheet("خلاصه", 0)
    sheet.sheet_view.rightToLeft = True
    sheet.column_dimensions["A"].width = 24
    sheet.column_dimensions["B"].width = 44

    from src.services.calendar_converter import format_jalali

    rows = [
        ("نام پروژه", project.name),
        ("کد پروژه", project.client_name or str(project.id)[:8]),
        ("شماره مناقصه", project.description or "-"),
        ("کارفرما", project.client_name or "-"),
        ("تاریخ پایه", format_jalali(project.base_date, long_form=True)),
        ("تاریخ تهیه گزارش", format_jalali(date.today(), long_form=True)),
        ("تعداد ردیف", item_count),
        ("تعداد فصل", chapter_count),
        ("ضریب حاشیه ریسک", float(project.risk_buffer or 1.0)),
        ("ضریب شرایط پرداخت", float(project.payment_terms or 1.0)),
        ("ضریب حاشیه سود", float(project.profit_margin or 1.0)),
    ]

    sheet.append(["عنوان", "مقدار"])
    for label, value in rows:
        sheet.append([label, value])

    _style_sheet(sheet, [("", "", 24), ("", "", 44)])


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #
async def export_project_workbook(
    db: AsyncSession,
    project_id: uuid.UUID,
    include_weights: bool = True,
    include_forecasts: bool = True,
    include_comparison: bool = True,
    output_dir: Optional[Path] = None,
) -> ExcelExportResult:
    """
    Build the recalculated-BoQ workbook for a project and write it to disk.

    Args:
        include_weights: add the per-component weight sheet.
        include_forecasts: add the forecast sheet (the largest join).
        include_comparison: add the base-vs-updated driver breakdown.

    Raises:
        ExcelExportError: unknown project, or the project has no BoQ items.
    """
    project = await db.get(Project, project_id)
    if project is None:
        raise ExcelExportError(f"Project {project_id} not found")

    options = [
        selectinload(BoQItem.chapter),
        selectinload(BoQItem.weights).selectinload(Weight.component),
        selectinload(BoQItem.price_calculations),
    ]
    if include_weights or include_comparison:
        options.append(selectinload(BoQItem.price_calculations))
    if include_forecasts:
        options.append(selectinload(BoQItem.forecasts).selectinload(Forecast.model))

    items = (
        (
            await db.execute(
                select(BoQItem)
                .where(BoQItem.project_id == project_id)
                .options(*options)
                .order_by(BoQItem.chapter_id, BoQItem.row_number, BoQItem.code)
            )
        )
        .scalars()
        .unique()
        .all()
    )
    if not items:
        raise ExcelExportError(f"No BoQ items found in project {project_id}")

    current: Dict[uuid.UUID, PriceCalculation] = {}
    for item in items:
        live = [c for c in item.price_calculations if c.is_current]
        chosen = live[0] if live else (item.price_calculations[0] if item.price_calculations else None)
        if chosen is not None:
            current[item.id] = chosen

    workbook = Workbook()
    # The default sheet has no styling; replace it with a cover page.
    workbook.remove(workbook.active)

    _write_cover(
        workbook, project, item_count=len(items), chapter_count=len({i.chapter_id for i in items})
    )
    _write_prices(workbook, items, current)
    if include_weights:
        _write_weights(workbook, items, current)
    if include_forecasts:
        _write_forecasts(workbook, items)
    if include_comparison:
        _write_comparison(workbook, items)

    document_id = str(uuid.uuid4())
    stem = "".join(
        ch for ch in (project.client_name or str(project.id)[:8]) if ch.isalnum() or ch in "-_"
    ).strip("_") or "boq"
    filename = f"{stem}_boq_{document_id[:8]}.xlsx"

    target_dir = Path(output_dir or settings.EXPORT_STORAGE_PATH)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / filename
    workbook.save(target)

    result = ExcelExportResult(
        document_id=document_id,
        path=target,
        filename=filename,
        item_count=len(items),
        chapter_count=len({i.chapter_id for i in items}),
        sheet_names=workbook.sheetnames,
        size_bytes=target.stat().st_size,
    )
    logger.info("excel_exported", project_id=str(project_id), **result.to_dict())
    return result
