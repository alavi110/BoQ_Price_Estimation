"""
Weight analysis orchestration.

Three sources of a weight, one number out:

* the LLM reads the Persian description and proposes a cost mix;
* the regressor reads the item's own price history against the market indices
  and proposes a cost mix;
* :class:`~src.services.weight_attribution.fusion.WeightFusion` combines them
  and scores how much the combination deserves to be trusted.

This service is the only place that runs all three, writes the result, and
reports what happened. Keeping the orchestration out of the route layer means
the same sequence can be driven by a Celery task or a script without being
reimplemented, and it means the route has no business logic to get wrong.

Two decisions worth stating:

**A failed side degrades, it does not abort the run.** An item whose history is
too short for the regressor still gets LLM weights; an item the LLM could not
classify still gets ML weights. Each failure is recorded against the item and
the run's status says ``partial``. A caller that cannot tell 49 analyses from
50 will treat a degraded item as a good one, so the report has to distinguish
them rather than returning a list of the successes.

**The ML side is fitted per item, not per project.** The regression target is an
individual item's price, so a project's pooled series would describe a
hypothetical average item rather than any item actually being priced. An item
with no usable history therefore contributes nothing to the ML side, and its
confidence reflects that.
"""
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from uuid import UUID, uuid4

import pandas as pd
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import settings
from src.core.logging import get_logger
from src.models.boq_item import BoQItem
from src.models.component import Component
from src.models.market_index import MarketIndex
from src.models.price_calculation import PriceCalculation
from src.models.weight import Weight
from src.schemas.weight import ComponentWeight, WeightBreakdown
from src.services.confidence_scorer import ConfidenceScorer
from src.services.weight_attribution.fusion import WeightFusion
from src.services.weight_attribution.llm_parser import LLMParser
from src.services.weight_attribution.ml_regressor import (
    InsufficientHistory,
    MLRegressor,
)
from src.services.weight_audit import weight_audit_service

logger = get_logger(__name__)

#: The seven components, in the order the price formula and the defence
#: document both use. Sourced from the fusion service so there is one
#: definition, not three.
COMPONENTS = list(WeightFusion.COMPONENTS)

#: Reported when an item's weights cannot be read. Distinct from ``None``,
#: which would mean "this item has no copper" - a reading the table would render
#: as a blank cell indistinguishable from a missing row.
NO_CATEGORY_FA = "نامشخص"


@dataclass
class AnalysisFailure:
    """One item that could not be fully attributed."""

    item_id: str
    code: str
    reason: str

    def to_dict(self) -> Dict[str, Any]:
        return {"item_id": self.item_id, "code": self.code, "reason": self.reason}


@dataclass
class ItemAnalysis:
    """The outcome for a single item."""

    item_id: str
    code: str
    description_fa: str
    category: str
    weights: List[ComponentWeight] = field(default_factory=list)
    fusion_confidence: float = 0.0
    llm_confidence: Optional[float] = None
    ml_trained: bool = False
    notes: List[str] = field(default_factory=list)

    @property
    def total_final_weight(self) -> float:
        return sum(w.final_weight for w in self.weights)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "item_id": self.item_id,
            "code": self.code,
            "description_fa": self.description_fa,
            "category": self.category,
            "llm_confidence": self.llm_confidence,
            "fusion_confidence": self.fusion_confidence,
            "ml_trained": self.ml_trained,
            "weights": [w.model_dump(mode="json") for w in self.weights],
            "total_final_weight": self.total_final_weight,
            "notes": list(self.notes),
        }


@dataclass
class WeightAnalysisReport:
    """What a run did, including what it could not do."""

    job_id: str
    project_id: str
    boq_job_id: Optional[str] = None
    status: str = "completed"
    items: List[ItemAnalysis] = field(default_factory=list)
    failures: List[AnalysisFailure] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    skipped: int = 0
    fusion_alpha: float = settings.WEIGHT_FUSION_ALPHA

    @property
    def item_count(self) -> int:
        return len(self.items)

    def summary(self) -> Dict[str, Any]:
        sources: Dict[str, int] = {}
        for item in self.items:
            for weight in item.weights:
                sources[weight.source] = sources.get(weight.source, 0) + 1

        return {
            "job_id": self.job_id,
            "project_id": self.project_id,
            "boq_job_id": self.boq_job_id,
            "status": self.status,
            "item_count": self.item_count,
            "skipped": self.skipped,
            "failed_count": len(self.failures),
            "ml_trained_count": sum(1 for item in self.items if item.ml_trained),
            "sources": sources,
            "fusion_alpha": self.fusion_alpha,
            # An item whose weights do not total 1.0 inflates or deflates every
            # price computed from it, so the count is surfaced rather than left
            # for a reader to notice.
            "items_with_invalid_weight_sum": [
                item.item_id
                for item in self.items
                if abs(item.total_final_weight - 1.0) > 1e-3
            ],
        }

    def to_dict(self) -> Dict[str, Any]:
        return {
            "summary": self.summary(),
            "items": [item.to_dict() for item in self.items],
            "failures": [failure.to_dict() for failure in self.failures],
            "warnings": list(self.warnings),
        }


class WeightAnalysisService:
    """
    Run, record and read back weight attributions.

    Collaborators are injectable so the sequence can be exercised against stubs
    - the LLM side in particular, since a real call needs a provider and a key,
    and a test suite that needs both is a test suite that does not run.
    """

    def __init__(
        self,
        fusion: Optional[WeightFusion] = None,
        llm_parser: Optional[LLMParser] = None,
        confidence_scorer: Optional[ConfidenceScorer] = None,
    ):
        self.fusion = fusion or WeightFusion()
        self.llm_parser = llm_parser or LLMParser()
        self.confidence = confidence_scorer or ConfidenceScorer()
        self.logger = logger
        #: Runs by weight-analysis job id, and by the BoQ job id they were
        #: requested with, so a client holding either address finds the same
        #: run. Process-local by design, matching the price audit service: a
        #: registry that outlived the process would need the results persisted
        #: per run, and the weights themselves are already in the database.
        self._runs: Dict[str, WeightAnalysisReport] = {}
        self._by_boq_job: Dict[str, str] = {}

    # ------------------------------------------------------------------ #
    # Catalogue and data loading
    # ------------------------------------------------------------------ #
    async def _components(self, db: AsyncSession) -> Dict[str, Component]:
        """
        The canonical seven, by code.

        A component missing from the catalogue is reported rather than created:
        inventing a row would give the price formula an index to look up that no
        ETL source will ever populate, and the resulting weight would be a
        permanent lie.
        """
        result = await db.execute(select(Component))
        by_code = {component.code: component for component in result.scalars().all()}
        missing = [code for code in COMPONENTS if code not in by_code]
        if missing:
            raise ValueError(
                f"The component catalogue is missing {missing}. Seed the seven "
                f"cost components before running weight analysis."
            )
        return {code: by_code[code] for code in COMPONENTS}

    async def _market_frame(self, db: AsyncSession) -> pd.DataFrame:
        """Every index reading, long form, for the regressor to pivot."""
        result = await db.execute(
            select(MarketIndex, Component.code).join(
                Component, MarketIndex.component_id == Component.id
            )
        )
        return pd.DataFrame(
            [
                {
                    "date": row.MarketIndex.date,
                    "component": row.code,
                    "value": float(row.MarketIndex.value),
                }
                for row in result.all()
            ]
        )

    async def _price_frame(self, db: AsyncSession) -> Dict[UUID, pd.DataFrame]:
        """
        Each item's own price history.

        Keyed by item, because the regression target is an individual item's
        price. A project-level pool would describe an average item that is not
        in the BoQ.
        """
        result = await db.execute(select(PriceCalculation))
        by_item: Dict[UUID, List[Dict[str, Any]]] = {}
        for calculation in result.scalars().all():
            by_item.setdefault(calculation.boq_item_id, []).append(
                {
                    "date": calculation.calculated_at,
                    "price": float(calculation.final_price),
                }
            )
        return {
            item_id: pd.DataFrame(rows)
            for item_id, rows in by_item.items()
            if rows
        }

    async def _target_items(
        self,
        db: AsyncSession,
        project_id: UUID,
        item_ids: Optional[List[UUID]],
    ) -> List[BoQItem]:
        query = select(BoQItem).where(BoQItem.project_id == project_id)
        if item_ids:
            query = query.where(BoQItem.id.in_(item_ids))
        result = await db.execute(query.order_by(BoQItem.row_number, BoQItem.code))
        return list(result.scalars().all())

    async def _already_attributed(
        self, db: AsyncSession, item_ids: List[UUID]
    ) -> Dict[UUID, int]:
        """How many of the seven each item already has, for the skip check."""
        if not item_ids:
            return {}
        result = await db.execute(
            select(Weight.boq_item_id, Weight.component_id).where(
                Weight.boq_item_id.in_(item_ids)
            )
        )
        counts: Dict[UUID, int] = {}
        for row in result.all():
            counts[row.boq_item_id] = counts.get(row.boq_item_id, 0) + 1
        return counts

    # ------------------------------------------------------------------ #
    # The run
    # ------------------------------------------------------------------ #
    async def analyze_project(
        self,
        db: AsyncSession,
        project_id: UUID,
        item_ids: Optional[List[UUID]] = None,
        force_reanalyze: bool = False,
        user_id: Optional[UUID] = None,
        boq_job_id: Optional[UUID] = None,
        job_id: Optional[UUID] = None,
    ) -> WeightAnalysisReport:
        """
        Attribute every requested item and persist the result.

        Args:
            force_reanalyze: re-attribute items that already have a full set of
                weights. Off by default so a second call is cheap and
                idempotent - and so an expert's overrides are not silently
                reverted by a re-run, which is the failure mode a re-analysis
                most needs to avoid.
        """
        run_id = str(job_id or uuid4())
        report = WeightAnalysisReport(
            job_id=run_id,
            project_id=str(project_id),
            boq_job_id=str(boq_job_id) if boq_job_id else None,
        )

        try:
            components = await self._components(db)
        except ValueError as error:
            report.status = "failed"
            report.warnings.append(str(error))
            self._remember(report)
            return report

        items = await self._target_items(db, project_id, item_ids)
        if not items:
            report.status = "failed"
            report.warnings.append(
                f"No BoQ items found for project {project_id}."
            )
            self._remember(report)
            return report

        market = await self._market_frame(db)
        history = await self._price_frame(db)
        attributed = await self._already_attributed(
            db, [item.id for item in items]
        )

        for item in items:
            if not force_reanalyze and attributed.get(item.id, 0) >= len(COMPONENTS):
                report.skipped += 1
                continue

            try:
                analysis = await self._analyze_item(
                    db=db,
                    item=item,
                    components=components,
                    market=market,
                    history=history.get(item.id),
                    user_id=user_id,
                )
            except Exception as error:  # noqa: BLE001 - one item must not sink the run
                self.logger.error(
                    "weight_analysis_item_failed",
                    item_id=str(item.id),
                    code=item.code,
                    error=str(error),
                )
                report.failures.append(
                    AnalysisFailure(
                        item_id=str(item.id),
                        code=item.code,
                        reason=str(error),
                    )
                )
                continue

            report.items.append(analysis)

        report.status = self._status_for(report)
        if market.empty:
            report.warnings.append(
                "No market index readings are loaded, so the ML side was "
                "skipped for every item and the weights are LLM-only."
            )

        self._remember(report)
        self.logger.info(
            "weight_analysis_completed",
            job_id=run_id,
            project_id=str(project_id),
            status=report.status,
            items=report.item_count,
            skipped=report.skipped,
            failed=len(report.failures),
        )
        return report

    @staticmethod
    def _status_for(report: WeightAnalysisReport) -> str:
        """
        ``completed``, ``partial`` or ``failed``.

        Partial is a distinct outcome rather than a success with warnings: a
        caller that cannot tell 49 analyses from 50 will treat the degraded item
        as a good one, and it is the degraded one whose weights reach a tender
        price.
        """
        if not report.items and report.failures:
            return "failed"
        if report.failures:
            return "partial"
        return "completed"

    async def _analyze_item(
        self,
        db: AsyncSession,
        item: BoQItem,
        components: Dict[str, Component],
        market: pd.DataFrame,
        history: Optional[pd.DataFrame],
        user_id: Optional[UUID],
    ) -> ItemAnalysis:
        """One item: LLM, regressor, fusion, persisted."""
        notes: List[str] = []

        llm_result = await self.llm_parser.analyze_item(
            item_code=item.code,
            description_fa=item.description_fa,
            description_en=item.description_en,
            unit=item.unit,
        )
        if llm_result.model_used == "fallback":
            # Load-bearing. The fallback's 0.3 confidence already tells the
            # fusion to lean on the ML side; the note is what tells the
            # reviewer why the LLM column is weak.
            notes.append(
                "The language model did not answer; weights came from keyword "
                "matching."
            )

        ml_weights: Dict[str, float] = {}
        ml_r2: float = 0.0
        ml_trained = False
        component_r2: Dict[str, float] = {}
        regressor: Optional[MLRegressor] = None

        if history is not None and not history.empty and not market.empty:
            regressor = MLRegressor()
            try:
                X, y = regressor.prepare_features(market, history)
                ml_weights = regressor.train(X, y, COMPONENTS)
                ml_r2 = float(regressor.r2_score)
                ml_trained = bool(regressor.is_trained)
                if ml_trained:
                    # A per-component R2 is the share of the overall fit each
                    # component accounts for, so it is what a reviewer reads
                    # against a component they think is over- or under-weighted.
                    # Taken from the regressor just fitted, not from a second
                    # fit of the same data.
                    component_r2 = regressor.component_r2_scores()
            except InsufficientHistory as error:
                notes.append(
                    f"ML weights skipped: {error} The item's price history does "
                    f"not overlap the market indices for long enough to fit."
                )
                regressor = None
            except ValueError as error:
                notes.append(f"ML weights skipped: {error}")
                regressor = None
        else:
            notes.append(
                "ML weights skipped: the item has no recorded price history."
            )

        result = self.fusion.fuse(
            llm_weights=llm_result.component_weights,
            ml_weights=ml_weights,
            # The per-component breakdown, not the overall scalar: the fusion
            # needs the shares, and they are what sums back to the overall R².
            ml_r2=component_r2,
            llm_certainty=llm_result.confidence,
        )

        source = "fusion" if ml_trained else "llm"
        weights: List[ComponentWeight] = []
        for code in COMPONENTS:
            component = components[code]
            weights.append(
                ComponentWeight(
                    component_id=component.id,
                    component_code=code,
                    component_name_fa=component.name_fa,
                    llm_weight=self._share(llm_result.component_weights, code),
                    # `or None` so an absent ML side is distinguishable from a
                    # zero share. An item with no steel has 0.0; an item with no
                    # usable history has no ML figure at all, and the document
                    # shows those differently.
                    ml_weight=self._share(ml_weights, code) or None,
                    final_weight=self._share(result.final_weights, code),
                    source=source,
                    confidence=result.confidence,
                    ml_r2=self._share(component_r2, code) if component_r2 else None,
                    llm_certainty=llm_result.confidence,
                )
            )
            await weight_audit_service.record_weight_attribution(
                db=db,
                item_id=item.id,
                component_id=component.id,
                llm_weight=self._share(llm_result.component_weights, code),
                ml_weight=self._share(ml_weights, code) or None,
                final_weight=self._share(result.final_weights, code),
                source=source,
                confidence=result.confidence,
                ml_r2=self._share(component_r2, code) if component_r2 else None,
                llm_certainty=llm_result.confidence,
                user_id=user_id,
            )

        item.llm_confidence = llm_result.confidence
        await db.flush()

        return ItemAnalysis(
            item_id=str(item.id),
            code=item.code,
            description_fa=item.description_fa,
            category=llm_result.category or NO_CATEGORY_FA,
            weights=weights,
            fusion_confidence=result.confidence,
            llm_confidence=llm_result.confidence,
            ml_trained=ml_trained,
            notes=notes,
        )

    @staticmethod
    def _share(weights: Dict[str, float], code: str) -> float:
        """
        A component's share, as a real 0.0 when it has none.

        ``.get(code, 0.0)`` would do the same; going through an explicit helper
        keeps the intent visible at the call sites, where a bare 0.0 and a
        missing measurement are easy to confuse.
        """
        return float(weights.get(code, 0.0))

    def _remember(self, report: WeightAnalysisReport) -> None:
        self._runs[report.job_id] = report
        if report.boq_job_id:
            self._by_boq_job[report.boq_job_id] = report.job_id

    def run_for(self, job_id: str) -> Optional[WeightAnalysisReport]:
        """
        The run addressed by either id.

        A client holds the BoQ job id from the upload and the analysis job id
        from this endpoint's response, and the quickstart polls with one and
        reads with the other. Resolving both means neither call has to know
        which is which.
        """
        if job_id in self._runs:
            return self._runs[job_id]
        aliased = self._by_boq_job.get(job_id)
        return self._runs.get(aliased) if aliased else None

    # ------------------------------------------------------------------ #
    # Reading back what was persisted
    # ------------------------------------------------------------------ #
    async def load_breakdowns(
        self,
        db: AsyncSession,
        project_id: UUID,
        item_ids: Optional[List[UUID]] = None,
    ) -> List[WeightBreakdown]:
        """
        The stored weights, per item.

        Reads the database rather than the last run's in-memory result, so the
        numbers a reviewer sees are the numbers that were actually recorded -
        including any expert override applied since.
        """
        query = select(BoQItem).where(BoQItem.project_id == project_id)
        if item_ids:
            query = query.where(BoQItem.id.in_(item_ids))
        items = list((await db.execute(query.order_by(BoQItem.code))).scalars().all())

        if not items:
            return []

        rows = await db.execute(
            select(Weight, Component)
            .join(Component, Weight.component_id == Component.id)
            .where(Weight.boq_item_id.in_([item.id for item in items]))
        )
        grouped: Dict[UUID, List[Any]] = {}
        for weight, component in rows.all():
            grouped.setdefault(weight.boq_item_id, []).append((weight, component))

        breakdowns: List[WeightBreakdown] = []
        for item in items:
            entries = grouped.get(item.id, [])
            # Ordered by the canonical component order rather than by whatever
            # order the database returned, so the table's rows line up with the
            # price formula's terms from one render to the next.
            entries.sort(
                key=lambda pair: COMPONENTS.index(pair[1].code)
                if pair[1].code in COMPONENTS
                else len(COMPONENTS)
            )

            weights = [
                ComponentWeight(
                    component_id=component.id,
                    component_code=component.code,
                    component_name_fa=component.name_fa,
                    llm_weight=float(weight.llm_weight) if weight.llm_weight is not None else None,
                    ml_weight=float(weight.ml_weight) if weight.ml_weight is not None else None,
                    final_weight=float(weight.final_weight),
                    source=weight.source,
                    confidence=float(weight.confidence) if weight.confidence is not None else None,
                    ml_r2=float(weight.ml_r2) if weight.ml_r2 is not None else None,
                    llm_certainty=float(weight.llm_certainty) if weight.llm_certainty is not None else None,
                    overridden_by=weight.overridden_by,
                    overridden_at=weight.overridden_at,
                    override_reason=weight.override_reason,
                )
                for weight, component in entries
            ]

            confidences = [
                float(weight.confidence)
                for weight, _ in entries
                if weight.confidence is not None
            ]
            breakdowns.append(
                WeightBreakdown(
                    item_id=item.id,
                    code=item.code,
                    description_fa=item.description_fa,
                    category=item.category or NO_CATEGORY_FA,
                    llm_confidence=float(item.llm_confidence)
                    if item.llm_confidence is not None
                    else None,
                    weights=weights,
                    # The run's own score, not an average of the rows: the rows
                    # all carry the same figure, and averaging them would make
                    # a seven-component item look better-evidenced than a
                    # one-component one for no reason.
                    fusion_confidence=(
                        sum(confidences) / len(confidences) if confidences else 0.0
                    ),
                    updated_at=max(
                        (weight.updated_at for weight, _ in entries),
                        default=item.updated_at,
                    ),
                )
            )

        return breakdowns


#: process-wide service
weight_analysis_service = WeightAnalysisService()
