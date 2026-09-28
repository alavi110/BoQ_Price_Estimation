"""
Market index resolution.

The published formula needs two readings per component:

* **base** - the value in force on the project's base date, which is what
  ``P_base`` in the BoQ was struck against;
* **current** - the newest value we hold.

Getting either wrong silently produces a plausible but wrong price, so the
resolution rules are stated here rather than left implicit in a query:

* The base is the last reading **on or before** the base date. Using the first
  reading *after* it would shift the whole tender.
* If the project predates our data, the earliest reading we hold is used and
  flagged - a gap is disclosed, not hidden.
* Where a component has several sources, the most recently successful one wins,
  because that is the one the freshness check vouched for.
"""
import uuid
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.core.logging import get_logger
from src.models.component import Component
from src.models.market_index import MarketIndex
from src.services.index_validator import (
    IndexSeverity,
    IndexValidationResult,
    IndexValidator,
    index_validator,
)

logger = get_logger(__name__)


@dataclass
class ResolvedIndex:
    """One component's base and current index, with provenance."""

    component_id: uuid.UUID
    component_code: str
    component_name_fa: str = ""
    base_value: Optional[float] = None
    current_value: Optional[float] = None
    base_date: Optional[date] = None
    current_date: Optional[date] = None
    base_source: Optional[str] = None
    current_source: Optional[str] = None
    base_source_url: Optional[str] = None
    current_source_url: Optional[str] = None
    is_interpolated: bool = False
    notes: List[str] = field(default_factory=list)

    @property
    def ratio(self) -> Optional[float]:
        """``current / base``, or ``None`` when either side is missing."""
        if self.base_value in (None, 0) or self.current_value is None:
            return None
        return self.current_value / self.base_value

    @property
    def is_complete(self) -> bool:
        return self.ratio is not None

    def to_snapshot(self) -> Dict[str, Any]:
        """
        The JSON stored on a ``PriceCalculation`` row.

        This is the audit evidence: a reader of the tender defence has to be
        able to reproduce the number months later, so the readings, their dates
        and their sources all travel with the price.
        """
        return {
            "index_base": self.base_value,
            "index_current": self.current_value,
            "index_ratio": self.ratio,
            "index_base_date": self.base_date.isoformat() if self.base_date else None,
            "index_current_date": self.current_date.isoformat() if self.current_date else None,
            "index_source": self.current_source,
            "index_source_url": self.current_source_url,
            "is_interpolated": self.is_interpolated,
            "notes": list(self.notes),
        }

    def to_dict(self) -> Dict[str, Any]:
        data = self.to_snapshot()
        data.update(
            {
                "component_id": str(self.component_id),
                "component_code": self.component_code,
                "component_name_fa": self.component_name_fa,
            }
        )
        return data


class MarketIndexService:
    """
    Reads market indices out of the database for a price calculation.

    Purely a reader: it does not fetch, interpolate or write. Batch refresh is
    :mod:`src.services.batch_updater`'s job, which keeps the read path honest -
    a price can only ever use data that was actually persisted.
    """

    def __init__(self, validator: Optional[IndexValidator] = None):
        self.validator = validator or index_validator

    # ------------------------------------------------------------------ #
    async def load_series(
        self,
        db: AsyncSession,
        component_ids: Sequence[uuid.UUID],
    ) -> Dict[uuid.UUID, List[MarketIndex]]:
        """
        Load every reading we hold for the given components, oldest first.

        ``component`` and ``source`` are eager-loaded: they are dereferenced
        below, and a lazy load inside an async request raises MissingGreenlet.
        """
        ids = [cid for cid in component_ids if cid]
        if not ids:
            return {}

        rows = (
            await db.execute(
                select(MarketIndex)
                .where(MarketIndex.component_id.in_(ids))
                .options(
                    selectinload(MarketIndex.component),
                    selectinload(MarketIndex.source),
                )
                .order_by(MarketIndex.component_id, MarketIndex.date)
            )
        ).scalars().unique().all()

        series: Dict[uuid.UUID, List[MarketIndex]] = {}
        for row in rows:
            series.setdefault(row.component_id, []).append(row)
        return series

    # ------------------------------------------------------------------ #
    @staticmethod
    def _pick_source(series: Sequence[MarketIndex]) -> Optional[MarketIndex]:
        """
        The reading to treat as authoritative for a component.

        Prefers the most recent date; on a tie, the source whose last run
        succeeded, since that is the one a reader can trust.
        """
        if not series:
            return None

        def rank(row: MarketIndex) -> tuple:
            succeeded = (
                getattr(row.source, "status", None) == "success"
            )
            return (row.date, succeeded, row.id)

        return max(series, key=rank)

    @staticmethod
    def _pick_base(
        series: Sequence[MarketIndex],
        base_date: date,
        component_code: str,
    ) -> Optional[MarketIndex]:
        """
        The reading in force on *base_date*.

        Falls back to the earliest reading we hold when the project predates our
        data, and says so - a disclosed gap beats a silent substitution.
        """
        if not series:
            return None

        on_or_before = [row for row in series if row.date <= base_date]
        if on_or_before:
            return on_or_before[-1]
        return series[0]

    # ------------------------------------------------------------------ #
    def resolve(
        self,
        component_id: uuid.UUID,
        series: Sequence[MarketIndex],
        base_date: date,
        component_code: str = "",
        component_name_fa: str = "",
    ) -> ResolvedIndex:
        """
        Turn one component's reading series into a base/current pair.

        ``component_code`` is accepted separately because a component with *no*
        readings is exactly the case an operator needs to read about, and an
        error naming a bare UUID tells them nothing. Callers that know the code
        should pass it; otherwise it is recovered from the newest reading.
        """
        current = self._pick_source(series)
        if current is None:
            return ResolvedIndex(
                component_id=component_id,
                component_code=component_code,
                component_name_fa=component_name_fa,
                notes=["No market index readings exist for this component."],
            )

        component = current.component
        base = self._pick_base(series, base_date, component.code if component else "")

        resolved = ResolvedIndex(
            component_id=component_id,
            component_code=component_code or (component.code if component else ""),
            component_name_fa=component_name_fa
            or ((component.name_fa if component else "") or ""),
            base_value=float(base.value) if base is not None else None,
            current_value=float(current.value),
            base_date=base.date if base is not None else None,
            current_date=current.date,
            base_source=getattr(base.source, "name", None) if base is not None else None,
            current_source=getattr(current.source, "name", None),
            base_source_url=getattr(base.source, "api_endpoint", None) if base is not None else None,
            current_source_url=getattr(current.source, "api_endpoint", None),
            is_interpolated=bool(current.is_interpolated or (base is not None and base.is_interpolated)),
        )

        if base is None:
            resolved.notes.append(
                f"No reading on or before {base_date.isoformat()}; the base is missing."
            )
        elif base.date > base_date:
            resolved.notes.append(
                f"No reading on or before {base_date.isoformat()}; used the earliest "
                f"available ({base.date.isoformat()}). The base price was struck "
                "against a different date."
            )
        if len(series) == 1 and resolved.base_value is not None:
            resolved.notes.append(
                "Only one reading exists, so the index ratio is necessarily 1.0 - "
                "the price cannot reflect any market movement."
            )
        return resolved

    # ------------------------------------------------------------------ #
    async def resolve_for_components(
        self,
        db: AsyncSession,
        component_ids: Sequence[uuid.UUID],
        base_date: date,
    ) -> Dict[uuid.UUID, ResolvedIndex]:
        """Resolve the base/current pair for each of the given components."""
        series = await self.load_series(db, component_ids)

        # Look the codes up separately so a component with no readings at all
        # still reports as "copper" rather than a UUID.
        names = {
            component.id: component
            for component in (await db.execute(
                select(Component).where(Component.id.in_(list(component_ids)))
            )).scalars().all()
        } if component_ids else {}

        return {
            component_id: self.resolve(
                component_id,
                series.get(component_id, []),
                base_date,
                component_code=names[component_id].code if component_id in names else "",
                component_name_fa=names[component_id].name_fa if component_id in names else "",
            )
            for component_id in component_ids
        }

    # ------------------------------------------------------------------ #
    def validate(
        self,
        resolved: Dict[uuid.UUID, ResolvedIndex],
        weights: Optional[Dict[uuid.UUID, float]] = None,
        as_of: Optional[date] = None,
    ) -> Dict[uuid.UUID, IndexValidationResult]:
        """
        Run the validator over resolved pairs.

        Args:
            weights: component -> weight, so a zero-weight component is demoted
                from blocking to informational.
        """
        pairs = {
            index.component_id: {
                "base": index.base_value,
                "current": index.current_value,
                "base_date": index.base_date,
                "current_date": index.current_date,
                "weight": (weights or {}).get(index.component_id),
            }
            for index in resolved.values()
        }
        # validate_all keys by code; remap back onto component ids.
        by_code = self.validator.validate_all(
            {
                index.component_code or f"component:{cid}": pairs[cid]
                for cid, index in resolved.items()
            },
            as_of=as_of,
        )
        results: Dict[uuid.UUID, IndexValidationResult] = {}
        for cid, index in resolved.items():
            key = index.component_code or f"component:{cid}"
            result = by_code[key]
            results[cid] = IndexValidationResult(
                component_code=index.component_code or key,
                is_valid=result.is_valid,
                base_value=result.base_value,
                current_value=result.current_value,
                base_date=result.base_date,
                current_date=result.current_date,
                ratio=result.ratio,
                issues=result.issues,
            )
        return results

    # ------------------------------------------------------------------ #
    def freshness(
        self,
        resolved: Dict[uuid.UUID, ResolvedIndex],
        as_of: Optional[date] = None,
    ) -> List[Dict[str, Any]]:
        """A per-component freshness report, for the API and the document."""
        today = as_of or date.today()
        report = []
        for index in resolved.values():
            age = (today - index.current_date).days if index.current_date else None
            if age is None:
                status = "unknown"
            elif age <= self.validator.max_staleness_days:
                status = "success"
            else:
                status = "stale"
            report.append(
                {
                    "component_code": index.component_code,
                    "component_name_fa": index.component_name_fa,
                    "current_value": index.current_value,
                    "current_date": index.current_date.isoformat() if index.current_date else None,
                    "age_days": age,
                    "status": status,
                    "source": index.current_source,
                    "source_url": index.current_source_url,
                    "notes": list(index.notes),
                }
            )
        return sorted(report, key=lambda row: row["component_code"])


#: process-wide reader
market_index_service = MarketIndexService()


__all__ = [
    "MarketIndexService",
    "ResolvedIndex",
    "market_index_service",
]
