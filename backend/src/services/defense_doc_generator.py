"""
Defense document generator (سند دفاعیه).

Ties together the content builder, the export validator and the renderer, and
records the generation event in the audit trail. A document is only produced
when validation passes, so a defective export never reaches a tender
submission.
"""
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from src.core.config import settings
from src.core.logging import audit_logger, get_logger
from src.services.document_content import (
    COMPONENT_ORDER,
    ChapterSummary,
    DocumentContent,
    DocumentContentBuilder,
)
from src.services.export_validator import ExportValidator, ValidationReport
from src.services.pdf_generator import PDFBackendUnavailable, PDFGenerator, RenderResult
from src.services.weight_history import ItemWeightHistory, WeightHistoryService

logger = get_logger(__name__)


class DefenseDocumentRejected(RuntimeError):
    """Raised when a document fails a blocking validation check."""

    def __init__(self, report: ValidationReport):
        self.report = report
        super().__init__(
            "Defense document rejected by export validation: "
            + "; ".join(report.blocking_errors)
        )


@dataclass
class GenerationRecord:
    """Bookkeeping for one generation run."""

    document_id: str
    project_id: str
    job_id: str
    created_at: datetime
    item_count: int = 0
    chapter_count: int = 0
    base_total: float = 0.0
    final_total: float = 0.0
    output_format: str = "pdf"
    filename: str = ""
    #: Name to show the user in Content-Disposition. The file itself is stored
    #: under an opaque document id, so a document number never reaches a path
    #: lookup.
    download_name: str = ""
    content_hash: str = ""
    status: str = "pending"
    error: Optional[str] = None
    warnings: List[str] = field(default_factory=list)
    content: Optional[DocumentContent] = None
    report: Optional[ValidationReport] = None
    #: Where the rendered document landed, once it has been written.
    path: Optional[str] = None
    #: Size of the written artefact in bytes.
    size_bytes: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "document_id": self.document_id,
            "project_id": self.project_id,
            "job_id": self.job_id,
            "created_at": self.created_at.isoformat(),
            "item_count": self.item_count,
            "chapter_count": self.chapter_count,
            "base_total": self.base_total,
            "final_total": self.final_total,
            "output_format": self.output_format,
            "filename": self.filename,
            "download_name": self.download_name or self.filename,
            "path": self.path,
            "size_bytes": self.size_bytes,
            "content_hash": self.content_hash,
            "status": self.status,
            "error": self.error,
            "warnings": list(self.warnings),
        }


class DefenseDocumentGenerator:
    """
    Build, validate and render the defense document.

    Args:
        builder: content builder (injected for tests).
        validator: export validator (injected for tests).
        renderer: HTML/PDF renderer (injected for tests).
        history_service: AI-vs-expert weight comparison service.
    """

    def __init__(
        self,
        builder: Optional[DocumentContentBuilder] = None,
        validator: Optional[ExportValidator] = None,
        renderer: Optional[PDFGenerator] = None,
        history_service: Optional[WeightHistoryService] = None,
        output_dir: Optional[Path] = None,
    ):
        self.builder = builder or DocumentContentBuilder()
        self.validator = validator or ExportValidator()
        self.renderer = renderer or PDFGenerator()
        self.history = history_service or WeightHistoryService()
        #: Where rendered documents are written. Configured so the API can hand
        #: out a download URL for whatever :meth:`generate` produced.
        self.output_dir = Path(output_dir or settings.EXPORT_STORAGE_PATH)
        self.logger = logger

    # ------------------------------------------------------------------ #
    def build_content(
        self,
        project: Dict[str, Any],
        item_rows: Iterable[Dict[str, Any]],
        document_number: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> DocumentContent:
        """Assemble the content tree without validating or rendering it."""
        return self.builder.build(
            project=project,
            item_rows=item_rows,
            document_number=document_number,
            metadata=metadata,
        )

    def enrich_with_weight_history(
        self,
        item_rows: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        Compute the project-wide AI-vs-expert override summary.

        Returns the merge summary plus the per-item history, so the caller can
        decide what to persist.
        """
        histories = [
            self.history.build_item_history(
                item_id=str(row.get("item_id") or row.get("id") or ""),
                weight_rows=row.get("weights") or [],
                item_code=str(row.get("code") or ""),
                description_fa=row.get("description_fa") or "",
            )
            for row in item_rows
        ]
        summary = self.history.merge_histories(histories)
        return {"summary": summary, "histories": histories}

    # ------------------------------------------------------------------ #
    def generate(
        self,
        project: Dict[str, Any],
        item_rows: List[Dict[str, Any]],
        output_format: str = "pdf",
        job_id: str = "",
        project_id: str = "",
        document_number: str = "",
        metadata: Optional[Dict[str, Any]] = None,
        skip_validation: bool = False,
    ) -> GenerationRecord:
        """
        Produce a defense document.

        Args:
            project: project metadata for the cover page.
            item_rows: per-item rows, optionally carrying ``weights``,
                ``adjustments`` and ``forecasts``.
            output_format: ``pdf`` or ``html``.
            job_id / project_id: recorded on the audit trail.
            document_number: identifier printed on the cover.
            skip_validation: bypass the export checklist (debug only).

        Raises:
            DefenseDocumentRejected: a blocking validation check failed.
            PDFBackendUnavailable: PDF requested but WeasyPrint is missing.
        """
        record = GenerationRecord(
            document_id=str(uuid.uuid4()),
            project_id=project_id or str(project.get("project_id") or ""),
            job_id=job_id or str(project.get("job_id") or ""),
            created_at=datetime.utcnow(),
            output_format=output_format,
        )

        try:
            content = self.build_content(
                project=project,
                item_rows=item_rows,
                document_number=document_number or record.document_id[:8],
                metadata=metadata,
            )
            record.content = content
            record.item_count = content.item_count
            record.chapter_count = len(content.chapters)
            record.base_total = content.cover.base_total
            record.final_total = content.cover.final_total

            # Validation gates the render
            report = self.validator.validate(content)
            record.report = report
            record.content_hash = content.fingerprint()
            record.warnings = [f"{c.code}: {c.detail or c.label_fa}" for c in report.warnings]

            if not report.is_valid:
                if not skip_validation:
                    record.status = "rejected"
                    self._audit(record, action="defense_document_rejected")
                    raise DefenseDocumentRejected(report)
                # A bypassed export must never be silent: record exactly which
                # blocking checks were skipped so the draft is not mistaken
                # for a submission-ready document.
                record.warnings.append("VALIDATION SKIPPED (draft only)")
                record.warnings.extend(report.blocking_errors)

            result_path, result = self.renderer.write(
                content, output_format, self.output_dir, report, storage_stem=record.document_id
            )
            record.filename = result_path.name
            record.download_name = result.filename
            record.path = str(result_path)
            record.size_bytes = result_path.stat().st_size
            record.status = "completed"
            self._audit(record, action="defense_document_generated")
            return record

        except DefenseDocumentRejected:
            raise
        except PDFBackendUnavailable as exc:
            record.status = "failed"
            record.error = str(exc)
            self._audit(record, action="defense_document_failed")
            raise
        except Exception as exc:
            record.status = "failed"
            record.error = str(exc)
            self.logger.error("defense_document_failed", error=str(exc), exc_info=True)
            self._audit(record, action="defense_document_failed")
            raise

    # ------------------------------------------------------------------ #
    def render_preview(
        self,
        project: Dict[str, Any],
        item_rows: List[Dict[str, Any]],
        output_format: str = "html",
        report: Optional[ValidationReport] = None,
    ) -> RenderResult:
        """
        Render without persisting or auditing - used by the dashboard preview.
        """
        content = self.build_content(project=project, item_rows=item_rows)
        validation = report if report is not None else self.validator.validate(content)
        return self.renderer.generate(content, output_format, validation)

    def validate(
        self,
        project: Dict[str, Any],
        item_rows: List[Dict[str, Any]],
    ) -> ValidationReport:
        """Run the export checklist without rendering anything."""
        return self.validator.validate(self.build_content(project, item_rows))

    # ------------------------------------------------------------------ #
    @staticmethod
    def _audit(record: GenerationRecord, action: str) -> None:
        try:
            audit_logger.log_change(
                entity_type="defense_document",
                entity_id=record.document_id,
                field_name="status",
                old_value=None,
                new_value=record.status,
                user_id="system",
                action=action,
            )
        except Exception as exc:  # auditing must never break generation
            logger.warning("defense_document_audit_failed", error=str(exc))


#: process-wide generator
defense_document_generator = DefenseDocumentGenerator()