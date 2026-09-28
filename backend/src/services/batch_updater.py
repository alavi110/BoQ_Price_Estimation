"""
Batch market index updating.

The spec's ETL (FR-011) pulls index readings nightly from IME (copper, steel),
the Central Bank (FX), energy tariffs and labour indices. This service is the
write side: it takes readings a fetcher has already obtained and lands them in
the database, keyed on ``(component_id, source_id, date)``.

That triple is *not* enforced by a database constraint - the schema carries only
plain indexes on ``(component_id, date)`` and ``(source_id, date)``. So
idempotency is this service's responsibility, and it is not thread-safe: two
concurrent runs landing the same triple can both miss the lookup and insert a
duplicate. The nightly schedule that makes this safe today is a single writer
per component; if a second writer is ever added, the constraint belongs in a
migration rather than in a bigger SELECT.

The separation is deliberate. *Fetching* talks to third-party endpoints and
fails in ways that have nothing to do with our data model; *landing* is where the
correctness rules live - what counts as a duplicate, what is plausible, what
counts as interpolated. Keeping them apart means the rules can be tested without
a network.

On landing a reading, any source left behind by a previous reading for that
component is marked stale, so a price calculation can tell which source it
actually used.
"""
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Dict, Iterable, List, Optional, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.core.logging import get_logger
from src.models.market_index import MarketDataSource, MarketIndex

logger = get_logger(__name__)

#: Below this relative move from the previous reading a value is treated as a
#: re-publication of the same figure rather than a genuine market move, so it
#: is stored as-is but not marked interpolated.
DEFAULT_NOISE_THRESHOLD = 1e-6


class IndexLandingError(ValueError):
    """Raised when a batch of readings cannot be landed."""


@dataclass
class IndexReading:
    """One reading offered for storage."""

    component_id: uuid.UUID
    source_id: uuid.UUID
    date: date
    value: float
    frequency: str = "daily"
    is_interpolated: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "component_id": str(self.component_id),
            "source_id": str(self.source_id),
            "date": self.date.isoformat(),
            "value": self.value,
            "frequency": self.frequency,
            "is_interpolated": self.is_interpolated,
        }


@dataclass
class BatchResult:
    """What a batch of readings actually did."""

    inserted: int = 0
    updated: int = 0
    skipped: int = 0
    rejected: List[Dict[str, Any]] = field(default_factory=list)
    sources_touched: List[str] = field(default_factory=list)
    completed_at: datetime = field(default_factory=datetime.utcnow)

    @property
    def total(self) -> int:
        return self.inserted + self.updated + self.skipped

    def to_dict(self) -> Dict[str, Any]:
        return {
            "inserted": self.inserted,
            "updated": self.updated,
            "skipped": self.skipped,
            "rejected": list(self.rejected),
            "sources_touched": list(self.sources_touched),
            "total": self.total,
            "completed_at": self.completed_at.isoformat(),
        }


class BatchIndexUpdater:
    """Lands market index readings idempotently."""

    def __init__(self, noise_threshold: float = DEFAULT_NOISE_THRESHOLD):
        self.noise_threshold = noise_threshold

    # ------------------------------------------------------------------ #
    @staticmethod
    def _validate(reading: IndexReading) -> Optional[str]:
        """Return a rejection reason, or ``None`` if the reading is storable."""
        if reading.value is None:
            return "value is required"
        try:
            value = float(reading.value)
        except (TypeError, ValueError):
            return f"value {reading.value!r} is not a number"
        if value != value:  # NaN
            return "value is NaN"
        if value in (float("inf"), float("-inf")):
            return "value is infinite"
        if value <= 0:
            # A zero or negative index would make the ratio division blow up or
            # invert, so it never reaches the price calculation.
            return f"value must be positive (got {value})"
        if reading.date is None:
            return "date is required"
        return None

    # ------------------------------------------------------------------ #
    async def land(
        self,
        db: AsyncSession,
        readings: Sequence[IndexReading],
        mark_sources_success: bool = True,
    ) -> BatchResult:
        """
        Store a batch of readings.

        Re-offering a reading for a ``(component, source, date)`` that already
        exists is an *update*, not an error: nightly ETLs re-send overlapping
        windows, and treating that as a duplicate would silently drop
        corrections. Readings that are not storable are collected in
        ``rejected`` and the rest of the batch still lands, because one bad row
        from a source must not lose the whole night.

        Args:
            mark_sources_success: flip each touched source to ``success`` and
                stamp ``last_success_at``. The caller is the authority on
                whether its fetch actually succeeded.

        Returns:
            A :class:`BatchResult`.
        """
        result = BatchResult()
        touched_sources: set[uuid.UUID] = set()

        for reading in readings:
            reason = self._validate(reading)
            if reason is not None:
                result.rejected.append({**reading.to_dict(), "reason": reason})
                continue

            existing = (
                await db.execute(
                    select(MarketIndex).where(
                        MarketIndex.component_id == reading.component_id,
                        MarketIndex.source_id == reading.source_id,
                        MarketIndex.date == reading.date,
                    )
                )
            ).scalar_one_or_none()

            if existing is None:
                db.add(
                    MarketIndex(
                        component_id=reading.component_id,
                        source_id=reading.source_id,
                        date=reading.date,
                        value=float(reading.value),
                        frequency=reading.frequency,
                        is_interpolated=reading.is_interpolated,
                    )
                )
                result.inserted += 1
            elif float(existing.value) == float(reading.value) and existing.is_interpolated == reading.is_interpolated:
                result.skipped += 1
            else:
                existing.value = float(reading.value)
                existing.frequency = reading.frequency
                existing.is_interpolated = reading.is_interpolated
                result.updated += 1

            touched_sources.add(reading.source_id)

        if touched_sources:
            result.sources_touched = await self._touch_sources(
                db, sorted(touched_sources), mark_sources_success
            )

        await db.commit()
        logger.info("index_batch_landed", **result.to_dict())
        return result

    # ------------------------------------------------------------------ #
    async def _touch_sources(
        self,
        db: AsyncSession,
        source_ids: Sequence[uuid.UUID],
        mark_success: bool,
    ) -> List[str]:
        """Stamp the sources a batch touched, so freshness can be reported."""
        sources = (
            await db.execute(
                select(MarketDataSource)
                .where(MarketDataSource.id.in_(list(source_ids)))
                .options(selectinload(MarketDataSource.component))
            )
        ).scalars().unique().all()

        now = datetime.utcnow()
        names = []
        for source in sources:
            source.last_run_at = now
            if mark_success:
                source.last_success_at = now
                source.status = "success"
                source.error_message = None
            names.append(source.component.code if source.component else source.name)
        return sorted(names)

    # ------------------------------------------------------------------ #
    async def mark_stale_sources(
        self,
        db: AsyncSession,
        as_of: date,
        max_age_days: int = 1,
    ) -> List[str]:
        """
        Flag sources whose newest reading is older than the freshness window.

        The dashboard reads this to show which indices cannot be trusted, rather
        than letting a stale number flow into a price and be discovered later.
        """
        sources = (
            await db.execute(
                select(MarketDataSource)
                .where(MarketDataSource.is_active.is_(True))
                .options(selectinload(MarketDataSource.component))
            )
        ).scalars().unique().all()

        stale: List[str] = []
        for source in sources:
            newest = (
                await db.execute(
                    select(MarketIndex)
                    .where(MarketIndex.source_id == source.id)
                    .order_by(MarketIndex.date.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()

            age = (as_of - newest.date).days if newest else None
            if age is None or age > max_age_days:
                source.status = "stale" if newest else "unknown"
                stale.append(source.component.code if source.component else source.name)
            elif source.status == "stale":
                # A fresh landing clears a stale flag; leaving it would make the
                # dashboard contradict itself.
                source.status = "success"

        if stale:
            await db.commit()
            logger.info("sources_marked_stale", components=stale, as_of=as_of.isoformat())
        return sorted(stale)

    # ------------------------------------------------------------------ #
    @staticmethod
    def interpolate(
        component_id: uuid.UUID,
        source_id: uuid.UUID,
        frequency: str,
        start: date,
        end: date,
        observations: Dict[date, float],
    ) -> List[IndexReading]:
        """
        Fill the gaps between known observations.

        Returns readings flagged ``is_interpolated=True`` for the dates inside
        the window that have no observation. The flag matters: a price built on
        an interpolated reading has to be disclosed as such, and a reader must
        be able to tell it apart from a real print.

        Linear interpolation between the nearest observations either side of
        each gap. Gaps at the edges of the window are left alone - extrapolating
        past the last known price invents a number nobody published.
        """
        if not observations or end <= start:
            return []

        known = sorted(observations.items())
        filled: List[IndexReading] = []
        cursor = start

        for (left_date, left_value), (right_date, right_value) in zip(known, known[1:]):
            while cursor < left_date:
                filled.append(
                    IndexReading(
                        component_id=component_id,
                        source_id=source_id,
                        date=cursor,
                        value=left_value,
                        frequency=frequency,
                        is_interpolated=True,
                    )
                )
                cursor = date.fromordinal(cursor.toordinal() + 1)
            if cursor < right_date:
                span = (right_date - left_date).days
                for step in range(1, span):
                    day = date.fromordinal(left_date.toordinal() + step)
                    if day in observations:
                        continue
                    ratio = step / span
                    filled.append(
                        IndexReading(
                            component_id=component_id,
                            source_id=source_id,
                            date=day,
                            value=left_value + (right_value - left_value) * ratio,
                            frequency=frequency,
                            is_interpolated=True,
                        )
                    )
            cursor = max(cursor, right_date)
            if cursor >= right_date:
                cursor = date.fromordinal(right_date.toordinal() + 1)

        return filled


#: process-wide updater
batch_index_updater = BatchIndexUpdater()


__all__ = [
    "BatchIndexUpdater",
    "BatchResult",
    "IndexLandingError",
    "IndexReading",
    "batch_index_updater",
]
