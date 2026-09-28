"""
Exports API routes.

Two exports matter to a tender defence: the recalculated BoQ as a spreadsheet
(the working document) and the formal defense document (the submission). Both
hand back a download URL rather than streaming bytes, so a large export does
not hold a request open.

Error mapping is deliberate and is the contract the frontend codes against:

* **404** - the project or the requested scope does not exist.
* **413** - the requested scope is too large to render in one document.
* **422** - the data exists but fails the export validation checklist. The
  failing checks come back in the body, because the caller's next move is to fix
  them rather than retry.
* **503** - PDF was requested and WeasyPrint's native stack is absent. This is a
  deployment problem, so the response says so and points at the HTML fallback.
"""
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import settings
from src.core.database import get_db
from src.core.logging import get_logger
from src.core.security import estimator_required, get_current_user, manager_required
from src.schemas.defense_doc import (
    DefenseDocPreviewResponse,
    DefenseDocRequest,
    DefenseDocResponse,
    DefenseDocValidation,
    DefenseDocValidationCheck,
)
from src.schemas.exports import ExportExcelRequest, ExportExcelResponse
from src.services.defense_doc_data import (
    DefenseDocumentDataUnavailable,
    load_component_names,
    load_item_rows,
    load_project,
    project_to_cover_metadata,
)
from src.services.defense_doc_generator import (
    DefenseDocumentGenerator,
    DefenseDocumentRejected,
)
from src.services.export_validator import ValidationReport
from src.services.pdf_generator import PDFBackendUnavailable, PDFGenerator

router = APIRouter()
logger = get_logger(__name__)

#: How long a generated export stays downloadable.
EXPORT_TTL_HOURS = 24

#: A document larger than this would time out the request rather than produce a
#: usable file, so the caller is told to narrow the scope instead.
MAX_DEFENSE_DOC_ITEMS = 5000


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _expires_at() -> datetime:
    return datetime.utcnow() + timedelta(hours=EXPORT_TTL_HOURS)


def _validation_schema(
    report: Optional[ValidationReport],
) -> Optional[DefenseDocValidation]:
    """Serialise a validation report for the API response."""
    if report is None:
        return None
    return DefenseDocValidation(
        is_valid=report.is_valid,
        checks=[
            DefenseDocValidationCheck(
                code=check.code,
                label_fa=check.label_fa,
                passed=check.passed,
                severity=check.severity.value,
                detail=check.detail,
                count=check.count,
            )
            for check in report.checks
        ],
        blocking_errors=list(report.blocking_errors),
        warning_count=len(report.warnings),
        validated_at=report.validated_at,
    )


def _export_dir() -> Path:
    return Path(settings.EXPORT_STORAGE_PATH).resolve()


def _generator() -> DefenseDocumentGenerator:
    """A generator wired to the configured export directory."""
    return DefenseDocumentGenerator(output_dir=settings.EXPORT_STORAGE_PATH)


async def _load_context(
    db: AsyncSession,
    project_id: UUID,
    item_ids: Optional[List[UUID]] = None,
    chapter_ids: Optional[List[UUID]] = None,
    include_forecasts: bool = True,
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """
    Load the cover metadata and item rows for a document.

    Shared by the generate and preview endpoints so the two cannot drift apart
    in how they interpret "no data" - a preview that renders while generation
    404s would be worse than either behaviour alone.
    """
    try:
        project = await load_project(db, project_id)
    except DefenseDocumentDataUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc

    cover = project_to_cover_metadata(project)
    try:
        rows = await load_item_rows(
            db,
            project,
            item_ids=item_ids,
            chapter_ids=chapter_ids,
            include_forecasts=include_forecasts,
        )
    except DefenseDocumentDataUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    return cover, rows


async def _prepared_generator(db: AsyncSession) -> DefenseDocumentGenerator:
    """Generator with the project's own component naming applied."""
    generator = _generator()
    generator.builder.component_names = await load_component_names(db)
    return generator


# --------------------------------------------------------------------------- #
# Defense document (US6)
# --------------------------------------------------------------------------- #
@router.post(
    "/defense-doc",
    response_model=DefenseDocResponse,
    status_code=status.HTTP_200_OK,
    summary="Generate defense document (سند دفاعیه)",
)
async def export_defense_doc(
    request: DefenseDocRequest,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(manager_required),
):
    """
    Generate the formal defense document for a project or a subset of it.

    ``item_ids`` and ``chapter_ids`` intersect when both are supplied, so
    passing both narrows the scope rather than widening it.
    """
    cover, rows = await _load_context(
        db, request.project_id, request.item_ids, request.chapter_ids
    )

    if len(rows) > MAX_DEFENSE_DOC_ITEMS:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=(
                f"Requested {len(rows)} items; the defense document is limited to "
                f"{MAX_DEFENSE_DOC_ITEMS}. Narrow the request with item_ids or "
                "chapter_ids."
            ),
        )

    generator = await _prepared_generator(db)
    try:
        record = generator.generate(
            project=cover,
            item_rows=rows,
            output_format=request.format,
            job_id=str(request.job_id),
            project_id=str(request.project_id),
            document_number=request.document_number or "",
            skip_validation=not request.strict,
        )
    except DefenseDocumentRejected as exc:
        # The caller's next move is to fix the data, so hand back the whole
        # checklist rather than a bare error string.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "message": "Export validation failed",
                "blocking_errors": exc.report.blocking_errors,
                "failed_checks": [
                    {
                        "code": check.code,
                        "label_fa": check.label_fa,
                        "detail": check.detail,
                        "count": check.count,
                    }
                    for check in exc.report.checks
                    if not check.passed
                ],
            },
        ) from exc
    except PDFBackendUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "message": str(exc),
                "hint": (
                    "Request format='html', or run the API in the project's Docker "
                    "image where the PDF dependencies are installed."
                ),
                "pdf_available": PDFGenerator.weasyprint_available(),
            },
        ) from exc

    logger.info(
        "defense_doc_exported",
        project_id=str(request.project_id),
        document_id=record.document_id,
        items=record.item_count,
        output_format=record.output_format,
        user_id=str(getattr(current_user, "id", "")),
    )

    return DefenseDocResponse(
        document_id=record.document_id,
        download_url=f"/exports/defense-doc/{record.document_id}/download",
        expires_at=_expires_at(),
        filename=record.download_name or record.filename,
        format=request.format,
        item_count=record.item_count,
        chapter_count=record.chapter_count,
        base_total=record.base_total,
        final_total=record.final_total,
        content_hash=record.content_hash,
        validation=_validation_schema(record.report),
    )


@router.get(
    "/defense-doc/preview",
    response_model=DefenseDocPreviewResponse,
    summary="Preview the defense document inline",
)
async def preview_defense_doc(
    project_id: UUID = Query(..., description="Project to render"),
    format: str = Query("html", pattern="^(pdf|html)$"),
    item_ids: Optional[List[UUID]] = Query(None),
    chapter_ids: Optional[List[UUID]] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(estimator_required),
):
    """
    Render a preview without persisting an export or writing an audit record.

    Defaults to HTML: the dashboard embeds it directly, and it is the only
    format available on a host without WeasyPrint.
    """
    cover, rows = await _load_context(
        db, project_id, item_ids, chapter_ids, include_forecasts=False
    )

    generator = await _prepared_generator(db)
    try:
        result = generator.render_preview(cover, rows, output_format=format)
    except PDFBackendUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "message": str(exc),
                "pdf_available": PDFGenerator.weasyprint_available(),
            },
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    # A PDF preview is base64-wrapped so the response stays valid JSON.
    payload = result.html if isinstance(result.html, str) else result.html.decode("latin-1")
    return DefenseDocPreviewResponse(
        document_id="preview",
        format=result.format,
        filename=result.filename,
        size_bytes=result.size,
        content=payload,
        validation=_validation_schema(result.report),
    )


@router.get(
    "/defense-doc/{document_id}/download",
    summary="Download a previously generated defense document",
    response_class=FileResponse,
)
async def download_defense_doc(
    document_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(manager_required),
):
    """
    Serve a generated document from the export directory.

    Files are stored under the opaque document id, so this lookup never puts a
    user-supplied document number into a filesystem path. The id is still
    re-validated and the resolved path re-checked against the export directory,
    so a crafted id still cannot walk out of it.
    """
    export_dir = _export_dir()
    safe_id = "".join(ch for ch in str(document_id) if ch.isalnum() or ch in "-_")
    if not safe_id or safe_id != str(document_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Malformed document id"
        )

    # Prefer the row we recorded, so a stale file on disk cannot be served in
    # place of the document the user asked for.
    stored = await _recorded_download_name(db, document_id, export_dir, safe_id)

    matches = sorted(export_dir.glob(f"{safe_id}_defense_doc.*"))
    if not matches:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                "Document not found. It may have expired; regenerate it with "
                "POST /exports/defense-doc."
            ),
        )

    target = max(matches, key=lambda path: path.stat().st_mtime).resolve()
    if export_dir != target.parent and export_dir not in target.parents:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Malformed document id"
        )

    media_type = (
        "application/pdf" if target.suffix == ".pdf" else "text/html; charset=utf-8"
    )
    return FileResponse(
        path=target, media_type=media_type, filename=stored or target.name
    )


async def _recorded_download_name(
    db: AsyncSession,
    document_id: UUID,
    export_dir: Path,
    safe_id: str,
) -> Optional[str]:
    """The human-readable name recorded for a document, if we still have it."""
    from sqlalchemy import select

    from src.models.defense_document import DefenseDocument

    row = await db.get(DefenseDocument, document_id)
    if row is not None and row.pdf_path:
        candidate = Path(row.pdf_path).name
        if (export_dir / candidate).is_file():
            return candidate
    return None


# --------------------------------------------------------------------------- #
# Excel (quickstart Scenario 6)
# --------------------------------------------------------------------------- #
@router.post(
    "/excel",
    response_model=ExportExcelResponse,
    summary="Export updated BoQ to Excel",
)
async def export_excel(
    request: ExportExcelRequest,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(estimator_required),
):
    """
    Export the recalculated BoQ as a workbook.

    Requires only estimator rights, unlike the defense document: this is a
    working file, not a submission artefact.
    """
    from src.services.excel_exporter import ExcelExportError, export_project_workbook

    try:
        result = await export_project_workbook(
            db,
            project_id=request.project_id,
            include_weights=request.include_weights,
            include_forecasts=request.include_forecasts,
            include_comparison=request.include_comparison,
        )
    except ExcelExportError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc

    return ExportExcelResponse(
        download_url=f"/exports/excel/{result.document_id}/download",
        expires_at=_expires_at(),
        filename=result.filename,
    )


@router.get(
    "/excel/{document_id}/download",
    summary="Download a previously generated Excel export",
    response_class=FileResponse,
)
async def download_excel(
    document_id: UUID,
    current_user=Depends(estimator_required),
):
    """Serve a generated workbook from the export directory."""
    export_dir = _export_dir()
    safe_id = "".join(ch for ch in str(document_id) if ch.isalnum() or ch in "-_")
    if not safe_id or safe_id != str(document_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Malformed document id"
        )

    matches = sorted(export_dir.glob(f"*_{safe_id[:8]}.xlsx"))
    if not matches:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workbook not found; it may have expired. Re-run POST /exports/excel.",
        )

    target = matches[0].resolve()
    if export_dir != target.parent and export_dir not in target.parents:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Malformed document id"
        )

    return FileResponse(
        path=target,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=target.name,
    )


@router.get(
    "/defense-doc/validate",
    response_model=DefenseDocValidation,
    summary="Re-run the export checklist for a scope",
)
async def validate_defense_doc(
    project_id: UUID = Query(..., description="Project to check"),
    item_ids: Optional[List[UUID]] = Query(None),
    chapter_ids: Optional[List[UUID]] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(estimator_required),
):
    """
    Run the export checklist without rendering a document.

    Lets the dashboard warn about a submission that would be rejected *before*
    the user asks for it.
    """
    cover, rows = await _load_context(
        db, project_id, item_ids, chapter_ids, include_forecasts=False
    )
    generator = await _prepared_generator(db)
    report = generator.validate(cover, rows)

    schema = _validation_schema(report)
    assert schema is not None  # validate() always returns a report
    return schema
