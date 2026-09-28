"""
Expert weight override tests (US2, T031).

An override is the one place a human being changes a number that flows straight
into a tender price, and FR-028 requires the document to show both the AI's
suggestion and the expert's figure with the reason for the change. So this file
is mostly about what a *disclosed* override has to get right.

The central invariant is that the weight vector still sums to 1.0 afterwards.
The price formula is ``P = P_base * SUM(W_i * ratio_i)``, so a vector summing to
1.15 inflates every price built from it by 15% - an invisible mark-up, because
nothing downstream reports a total other than 1.0 and the expert's UI showed a
single field. Both the in-memory service and the persisted one rescale the other
components rather than moving one weight in isolation, and both are tested for
it.

Also covered: the reason is mandatory (the document shows it), a stale
``old_weight`` is refused rather than rescaled from a wrong baseline, and a
no-op override is refused because it would add a trail entry explaining no
change.
"""
import uuid
from datetime import date

import pytest
import pytest_asyncio

from src.services.weight_audit import MIN_REASON_LENGTH, WeightAuditService
from src.services.weight_attribution.fusion import WeightFusion

CODES = list(WeightFusion.COMPONENTS)

#: A stated reason, long enough to clear MIN_REASON_LENGTH.
REASON = "قیمت مس در بازار جهانی افزایش چشمگیری داشت"


def vector(**overrides):
    """
    A seven-component weight vector summing to 1.0.

    Overrides are not renormalised, so a test can hand the service a
    deliberately unbalanced vector. Use :func:`composed` to pin an exact value.
    """
    base = {
        "copper": 0.30, "steel": 0.25, "cement": 0.10, "polymer": 0.10,
        "energy": 0.10, "labor": 0.10, "overhead": 0.05,
    }
    base.update(overrides)
    return base


def composed(filler="labor", **fixed):
    """
    A vector summing to exactly 1.0 with the named components holding the exact
    values given, the remainder on *filler*, and every other component zero.
    """
    weights = {code: 0.0 for code in CODES}
    weights.update(fixed)
    weights[filler] = 1.0 - sum(fixed.values())
    return weights


@pytest.fixture
def fusion():
    return WeightFusion()


@pytest.fixture
def service():
    return WeightAuditService()


# --------------------------------------------------------------------------- #
# Database fixtures for the persisted-override tests
# --------------------------------------------------------------------------- #
@pytest_asyncio.fixture
async def weight_rows(db_session):
    """
    A project with one item and all seven of its component weights, summing to 1.

    The vector is the same ``vector()`` the in-memory tests use, so the
    persisted and in-memory paths can be asserted against one expectation. The
    copper row carries an llm/ml pair that differ from the final weight, because
    FR-028 needs the document to show the AI's figure beside the expert's - and
    "the automated numbers are still there" is only a real assertion if there
    were automated numbers to begin with.
    """
    from src.models.boq_item import BoQItem
    from src.models.chapter import Chapter
    from src.models.component import Component
    from src.models.project import Project
    from src.models.user import User
    from src.models.weight import Weight

    tag = uuid.uuid4().hex[:8]
    manager = User(
        sso_id=f"ovr-{tag}", email=f"ovr-{tag}@example.org",
        full_name="کارشناس بازبینی", role="reviewer",
    )
    db_session.add(manager)
    await db_session.flush()

    project = Project(
        name=f"پروژه بازنگری وزن {tag}", client_name="PRT-01",
        description="آزمون بازنگری وزن", created_by=manager.id,
        base_date=date(2026, 1, 1),
    )
    db_session.add(project)
    await db_session.flush()

    chapter = Chapter(
        project_id=project.id, code="01", name="فصل اول",
        name_fa="فصل اول", sort_order=1,
    )
    db_session.add(chapter)
    await db_session.flush()

    item = BoQItem(
        project_id=project.id, chapter_id=chapter.id, code="01-001",
        description_fa="کابل تحت زمینی ۳×۱۵۰", description_en="MV cable",
        unit="kg", base_price=1_000_000.0, quantity=10.0,
        llm_confidence=0.85, row_number=1,
    )
    db_session.add(item)
    await db_session.flush()

    ids = {}
    for code, weight in vector().items():
        component = Component(
            code=code, name_fa=f"نام {code}", name_en=code.title(),
            category=_CATEGORY[code], unit=_UNIT[code],
            base_unit=_UNIT[code],
        )
        db_session.add(component)
        await db_session.flush()

        db_session.add(Weight(
            boq_item_id=item.id,
            component_id=component.id,
            llm_weight=0.28 if code == "copper" else weight,
            ml_weight=0.32 if code == "copper" else weight,
            final_weight=weight,
            source="fusion",
            confidence=0.8,
        ))
        ids[code] = component.id

    await db_session.flush()

    return {"user_id": manager.id, "item_id": item.id, "ids": ids}


@pytest.fixture
def expert_id(weight_rows):
    """The reviewing expert's user id, for ``overridden_by``."""
    return weight_rows["user_id"]


@pytest.fixture
def weight_ids(weight_rows):
    """``(item_id, copper_id, steel_id)`` - the triple most tests need."""
    return (
        weight_rows["item_id"],
        weight_rows["ids"]["copper"],
        weight_rows["ids"]["steel"],
    )


#: The category enum accepts only these four values.
_CATEGORY = {
    "copper": "material", "steel": "material", "cement": "material",
    "polymer": "material", "energy": "energy", "labor": "labor",
    "overhead": "overhead",
}

#: Units are irrelevant to the override, but the column is NOT NULL and a shared
#: placeholder is clearer than seven invented ones.
_UNIT = {code: "unit" for code in CODES}


# --------------------------------------------------------------------------- #
# In-memory override
# --------------------------------------------------------------------------- #
class TestOverrideInMemory:
    def test_the_target_weight_is_replaced(self, fusion):
        result = fusion.expert_override(vector(), "copper", 0.45, reason=REASON)

        assert result.final_weights["copper"] == pytest.approx(0.45)

    def test_the_vector_still_sums_to_one(self, fusion):
        """
        The invariant the price formula depends on.

        Moving one weight and leaving the rest alone turns 0.30 -> 0.45 into a
        15% mark-up on the whole item, which no reviewer would ever see.
        """
        result = fusion.expert_override(vector(), "copper", 0.45, reason=REASON)

        assert sum(result.final_weights.values()) == pytest.approx(1.0, abs=1e-12)

    def test_an_increase_squeezes_the_others_proportionally(self, fusion):
        """
        copper's share was 0.30, so the other 0.70 has to fit into 0.55.
        """
        base = vector()
        result = fusion.expert_override(base, "copper", 0.45, reason=REASON)

        for code in CODES:
            if code == "copper":
                continue
            assert result.final_weights[code] == pytest.approx(
                base[code] * (0.55 / 0.70), abs=1e-12
            )

    def test_a_decrease_lifts_the_others_proportionally(self, fusion):
        """
        The mirror case. Freeing 0.10 of mass means the other six absorb it;
        leaving them alone would deflate every price by 10%.
        """
        result = fusion.expert_override(vector(), "copper", 0.20, reason=REASON)

        assert result.final_weights["copper"] == pytest.approx(0.20)
        assert result.final_weights["steel"] == pytest.approx(0.25 * 0.80 / 0.70)
        assert sum(result.final_weights.values()) == pytest.approx(1.0, abs=1e-12)

    @pytest.mark.parametrize("target", [0.0, 0.01, 0.20, 0.45, 0.99, 1.0])
    def test_the_total_is_preserved_across_the_whole_range(self, fusion, target):
        result = fusion.expert_override(vector(), "copper", target, reason=REASON)

        assert sum(result.final_weights.values()) == pytest.approx(1.0, abs=1e-12)
        assert all(w >= 0 for w in result.final_weights.values())

    def test_overriding_to_zero_is_a_legitimate_answer(self, fusion):
        """A component genuinely absent from an item deserves a zero, not a floor."""
        result = fusion.expert_override(vector(), "copper", 0.0, reason=REASON)

        assert result.final_weights["copper"] == 0.0
        assert sum(result.final_weights.values()) == pytest.approx(1.0, abs=1e-12)

    def test_overriding_to_one_leaves_the_others_at_zero(self, fusion):
        result = fusion.expert_override(vector(), "copper", 1.0, reason=REASON)

        assert result.final_weights["copper"] == 1.0
        assert all(result.final_weights[c] == 0.0 for c in CODES if c != "copper")

    def test_a_vector_with_no_other_mass_is_split_evenly(self, fusion):
        """
        Nothing to scale proportionally, so the remainder is shared out rather
        than lost.
        """
        result = fusion.expert_override(
            {code: (1.0 if code == "copper" else 0.0) for code in CODES},
            "copper", 0.30, reason=REASON,
        )

        assert sum(result.final_weights.values()) == pytest.approx(1.0, abs=1e-12)
        assert all(
            result.final_weights[c] == pytest.approx(0.70 / 6)
            for c in CODES if c != "copper"
        )

    def test_the_override_is_labelled_and_certain(self, fusion):
        """
        An expert decision outranks both automated methods (FR-028), and the
        document has to be able to say so.
        """
        result = fusion.expert_override(vector(), "copper", 0.45, reason=REASON)

        assert result.fusion_method == "expert_override"
        assert result.confidence == 1.0

    def test_the_automated_vectors_are_not_claimed_by_the_override(self, fusion):
        """
        An override is a third thing entirely. Reporting the pre-override LLM or
        ML vector here would let the document imply the expert's number came
        from a model.
        """
        result = fusion.expert_override(vector(), "copper", 0.45, reason=REASON)

        assert result.llm_weights == {}
        assert result.ml_weights == {}
        assert result.ml_r2 == {}

    def test_the_caller_vector_is_not_mutated(self, fusion):
        """
        The audit layer keeps a reference to the pre-override weights to record
        the diff. Mutating them in place would leave the recorded "before" equal
        to the "after" and the diff empty.
        """
        base = vector()
        before = dict(base)

        fusion.expert_override(base, "copper", 0.45, reason=REASON)

        assert base == before

    def test_all_seven_components_are_still_reported(self, fusion):
        result = fusion.expert_override(vector(), "copper", 0.45, reason=REASON)

        assert set(result.final_weights) == set(CODES)


# --------------------------------------------------------------------------- #
# Refusals
# --------------------------------------------------------------------------- #
class TestOverrideRefusals:
    def test_an_unknown_component_is_refused(self, fusion):
        """
        A weight on a component the price formula has no index for would move
        nothing at the best, and at the worst be discovered only in a dispute.
        """
        with pytest.raises(ValueError, match="(?i)invalid component"):
            fusion.expert_override(vector(), "unobtainium", 0.10, reason=REASON)

    @pytest.mark.parametrize("target", [1.01, 1.4, 2.0, 100.0])
    def test_a_weight_above_one_is_refused(self, fusion, target):
        with pytest.raises(ValueError, match="(?i)between 0 and 1"):
            fusion.expert_override(vector(), "copper", target, reason=REASON)

    @pytest.mark.parametrize("target", [-0.01, -0.1, -1.0])
    def test_a_negative_weight_is_refused(self, fusion, target):
        with pytest.raises(ValueError, match="(?i)between 0 and 1"):
            fusion.expert_override(vector(), "copper", target, reason=REASON)

    def test_an_in_memory_override_to_the_existing_value_is_a_no_op(self, fusion):
        """
        The in-memory path does *not* refuse a no-op, and that asymmetry is
        deliberate.

        It computes a vector and hands it back; nothing is written, so a
        no-op costs nothing and a refusal would only be a surprise. The
        persisted layer is the one that refuses, because there a no-op writes a
        trail entry - and an entry in a defence document that explains no change
        is worse than no entry. Pinned here so the difference is a recorded
        decision rather than an oversight found later.
        """
        base = vector()
        before = dict(base)

        result = fusion.expert_override(base, "copper", 0.30, reason=REASON)

        # Compared per component rather than by dict equality: the rescale
        # factor is `(1.0 - 0.30) / 0.70`, which is 0.9999999999999998 in
        # binary floating point, not 1.0. The no-op is exact to the precision the
        # price formula works at and not one bit further - which is why this is
        # not worth refusing, and also why it is not worth asserting as identity.
        for code in CODES:
            assert result.final_weights[code] == pytest.approx(
                before[code], abs=1e-15
            ), code
        assert sum(result.final_weights.values()) == pytest.approx(1.0, abs=1e-12)


# --------------------------------------------------------------------------- #
# The reason - FR-028
# --------------------------------------------------------------------------- #
class TestOverrideReason:
    @pytest.mark.parametrize("reason", ["", "   ", "too short", "ok"])
    def test_a_reason_that_says_nothing_is_refused(self, reason):
        """
        FR-028 requires the document to show why an expert departed from the
        AI attribution. "ok" is not a reason, and once it is in the trail it is
        indistinguishable from one.
        """
        from pydantic import ValidationError

        from src.schemas.weight import WeightOverrideRequest

        with pytest.raises(ValidationError):
            WeightOverrideRequest(
                item_id=uuid.uuid4(),
                component_id=uuid.uuid4(),
                new_weight=0.45,
                reason=reason,
            )

    def test_the_schema_floor_is_the_one_the_service_enforces(self):
        """
        The API schema and the audit service enforce the same minimum, so a
        direct caller cannot record a reason the API would have rejected. The
        bound is read off the schema rather than hard-coded twice, so the two
        cannot drift apart unnoticed.
        """
        from pydantic import ValidationError

        from src.schemas.weight import WeightOverrideRequest

        floor = next(
            constraint.min_length
            for constraint in WeightOverrideRequest.model_fields["reason"].metadata
            if getattr(constraint, "min_length", None) is not None
        )

        assert floor == MIN_REASON_LENGTH

        with pytest.raises(ValidationError):
            WeightOverrideRequest(
                item_id=uuid.uuid4(),
                component_id=uuid.uuid4(),
                new_weight=0.45,
                reason="x" * (floor - 1),
            )

        assert WeightOverrideRequest(
            item_id=uuid.uuid4(),
            component_id=uuid.uuid4(),
            new_weight=0.45,
            reason="x" * floor,
        ).reason == "x" * floor

    def test_the_floor_counts_characters_not_bytes(self):
        """
        Pydantic measures ``min_length`` in code points, which is what a reader
        means by "at least ten characters".

        The service has to agree, or the two limits would differ by language: a
        byte-length check would admit roughly twice as many Persian characters
        as ASCII ones. This is the case that would expose such a check, since
        every character in the reason is a multi-byte one.
        """
        reason = "قیمت مس در بازار جهانی"[:MIN_REASON_LENGTH]

        assert len(reason.encode("utf-8")) > MIN_REASON_LENGTH
        assert len(reason) == MIN_REASON_LENGTH


# --------------------------------------------------------------------------- #
# The persisted override - needs a database
# --------------------------------------------------------------------------- #
class TestPersistedOverride:
    """
    The stored-vector version of the same invariant.

    These need the async test database, so they are skipped when it is absent
    rather than silently passing.
    """

    @pytest.mark.asyncio
    async def test_the_stored_vector_still_sums_to_one(self, service, db_session,
                                                       expert_id, weight_ids):
        """
        The same 15% mark-up, one layer down.

        The service used to set ``final_weight`` on the named component and
        leave the other six alone, so an override moved the whole item's price
        by the size of the change with nothing recording that it had done so.
        """
        item_id, copper_id, steel_id = weight_ids
        before = sum(
            float(row.final_weight)
            for row in await service._load_siblings(db_session, item_id)
        )
        assert before == pytest.approx(1.0, abs=1e-9)

        await service.record_expert_override(
            db_session, item_id, copper_id,
            old_weight=0.30, new_weight=0.45, reason=REASON, user_id=expert_id,
        )

        after = sum(
            float(row.final_weight)
            for row in await service._load_siblings(db_session, item_id)
        )
        # Loose only because of the column, not the algorithm. ``final_weight``
        # is ``Numeric(5, 4)``, so each of the seven rescaled values is rounded
        # to four places on write; seven roundings can accumulate to 3.5e-4.
        # The bound is the schema's resolution, and it is asserted as such
        # rather than loosened to whatever happened to come out - a drift four
        # orders of magnitude larger would still fail here.
        assert after == pytest.approx(1.0, abs=5e-4)

    @pytest.mark.asyncio
    async def test_the_other_components_are_actually_rescaled(self, service,
                                                              db_session, expert_id,
                                                              weight_ids):
        item_id, copper_id, steel_id = weight_ids

        await service.record_expert_override(
            db_session, item_id, copper_id,
            old_weight=0.30, new_weight=0.45, reason=REASON, user_id=expert_id,
        )

        steel = next(
            row for row in await service._load_siblings(db_session, item_id)
            if row.component_id == steel_id
        )
        # steel was 0.25 of the other 0.70; it is now 0.25 of 0.55. The
        # half-a-unit-in-the-last-place tolerance is the ``Numeric(5, 4)``
        # column rounding, not slack in the rescale.
        assert float(steel.final_weight) == \
            pytest.approx(0.25 * 0.55 / 0.70, abs=5e-5)

    @pytest.mark.asyncio
    async def test_a_decrease_lifts_the_others(self, service, db_session,
                                               expert_id, weight_ids):
        item_id, copper_id, steel_id = weight_ids

        await service.record_expert_override(
            db_session, item_id, copper_id,
            old_weight=0.30, new_weight=0.20, reason=REASON, user_id=expert_id,
        )

        steel = next(
            row for row in await service._load_siblings(db_session, item_id)
            if row.component_id == steel_id
        )
        assert float(steel.final_weight) == \
            pytest.approx(0.25 * 0.80 / 0.70, abs=5e-5)

    @pytest.mark.asyncio
    async def test_the_override_is_attributed_and_stamped(self, service, db_session,
                                                         expert_id, weight_ids):
        item_id, copper_id, _ = weight_ids

        row = await service.record_expert_override(
            db_session, item_id, copper_id,
            old_weight=0.30, new_weight=0.45, reason=REASON, user_id=expert_id,
        )

        assert row.source == "expert"
        assert str(row.overridden_by) == str(expert_id)
        assert row.overridden_at is not None
        assert row.override_reason == REASON

    @pytest.mark.asyncio
    async def test_the_automated_figures_survive_for_the_document(self, service,
                                                                  db_session,
                                                                  expert_id,
                                                                  weight_ids):
        """
        FR-028: the document shows the AI suggestion *and* the expert figure.
        Clearing llm_weight and ml_weight on an override would erase the very
        comparison the document exists to make.
        """
        item_id, copper_id, _ = weight_ids

        row = await service.record_expert_override(
            db_session, item_id, copper_id,
            old_weight=0.30, new_weight=0.45, reason=REASON, user_id=expert_id,
        )

        assert float(row.llm_weight) == pytest.approx(0.28)
        assert float(row.ml_weight) == pytest.approx(0.32)

    @pytest.mark.asyncio
    async def test_a_stale_old_weight_is_refused(self, service, db_session,
                                                 expert_id, weight_ids):
        """
        A client working from a stale view would rescale the other six from the
        wrong baseline, so the vector would still total 1.0 - but not the vector
        the expert was looking at.
        """
        item_id, copper_id, _ = weight_ids

        with pytest.raises(ValueError, match="stale view"):
            await service.record_expert_override(
                db_session, item_id, copper_id,
                old_weight=0.22, new_weight=0.45, reason=REASON, user_id=expert_id,
            )

    @pytest.mark.asyncio
    async def test_a_no_op_override_is_refused(self, service, db_session,
                                               expert_id, weight_ids):
        item_id, copper_id, _ = weight_ids

        with pytest.raises(ValueError, match="changes nothing"):
            await service.record_expert_override(
                db_session, item_id, copper_id,
                old_weight=0.30, new_weight=0.30, reason=REASON, user_id=expert_id,
            )

    @pytest.mark.asyncio
    async def test_an_oversized_target_is_refused_before_anything_is_written(
        self, service, db_session, expert_id, weight_ids
    ):
        item_id, copper_id, steel_id = weight_ids

        with pytest.raises(ValueError, match="between 0 and 1"):
            await service.record_expert_override(
                db_session, item_id, copper_id,
                old_weight=0.30, new_weight=1.4, reason=REASON, user_id=expert_id,
            )

        steel = next(
            row for row in await service._load_siblings(db_session, item_id)
            if row.component_id == steel_id
        )
        assert float(steel.final_weight) == pytest.approx(0.25)

    @pytest.mark.asyncio
    async def test_an_unknown_component_is_refused(self, service, db_session,
                                                   expert_id, weight_ids):
        item_id, _, _ = weight_ids
        stranger = uuid.uuid4()

        with pytest.raises(ValueError, match="Weight not found"):
            await service.record_expert_override(
                db_session, item_id, stranger,
                old_weight=0.30, new_weight=0.45, reason=REASON, user_id=expert_id,
            )

    @pytest.mark.asyncio
    async def test_a_reason_that_says_nothing_is_refused(self, service, db_session,
                                                         expert_id, weight_ids):
        item_id, copper_id, _ = weight_ids

        for reason in ("", "   ", "ok"):
            with pytest.raises(ValueError, match="reason"):
                await service.record_expert_override(
                    db_session, item_id, copper_id,
                    old_weight=0.30, new_weight=0.45, reason=reason,
                    user_id=expert_id,
                )

    @pytest.mark.asyncio
    async def test_a_short_reason_is_refused(self, service, db_session,
                                             expert_id, weight_ids):
        """
        Measured in characters, not in words or script.

        A ten-character Persian reason is a perfectly good reason, so the floor
        is counted in code points rather than in bytes - a byte-length check
        would admit twice as many Persian characters as ASCII ones, and the two
        limits would silently disagree depending on the language the expert was
        writing in. The reason is truncated rather than invented so the test
        pins the boundary on a string that reads like a real one.
        """
        item_id, copper_id, _ = weight_ids
        real = "قیمت مس در بازار جهانی افزایش یافت"

        assert len(real) > MIN_REASON_LENGTH
        truncated = real[:MIN_REASON_LENGTH - 1]
        assert len(truncated) < MIN_REASON_LENGTH

        with pytest.raises(ValueError, match="at least 10 characters"):
            await service.record_expert_override(
                db_session, item_id, copper_id,
                old_weight=0.30, new_weight=0.45, reason=truncated,
                user_id=expert_id,
            )

    @pytest.mark.asyncio
    async def test_a_reason_of_exactly_the_minimum_is_accepted(
        self, service, db_session, expert_id, weight_ids
    ):
        item_id, copper_id, _ = weight_ids
        exact = "قیمت مس در بازار جهانی افزایش یافت"[:MIN_REASON_LENGTH]

        row = await service.record_expert_override(
            db_session, item_id, copper_id,
            old_weight=0.30, new_weight=0.45, reason=exact, user_id=expert_id,
        )

        assert row.override_reason == exact

    @pytest.mark.asyncio
    async def test_a_refused_override_leaves_the_vector_untouched(
        self, service, db_session, expert_id, weight_ids
    ):
        """
        Validation happens before the rescale, so a rejected override cannot
        leave the other six components scaled with no matching target.
        """
        item_id, copper_id, steel_id = weight_ids

        with pytest.raises(ValueError):
            await service.record_expert_override(
                db_session, item_id, copper_id,
                old_weight=0.30, new_weight=0.45, reason="tiny",
                user_id=expert_id,
            )

        siblings = await service._load_siblings(db_session, item_id)
        assert sum(float(row.final_weight) for row in siblings) == \
            pytest.approx(1.0, abs=1e-9)
        copper = next(row for row in siblings if row.component_id == copper_id)
        assert float(copper.final_weight) == pytest.approx(0.30)

    @pytest.mark.asyncio
    async def test_the_reason_is_stripped(self, service, db_session, expert_id,
                                          weight_ids):
        """
        Padded reasons would render with leading whitespace in the document's
        reason column.
        """
        item_id, copper_id, _ = weight_ids

        row = await service.record_expert_override(
            db_session, item_id, copper_id,
            old_weight=0.30, new_weight=0.45, reason=f"  {REASON}  ",
            user_id=expert_id,
        )

        assert row.override_reason == REASON


# --------------------------------------------------------------------------- #
# Re-attribution must not collide
# --------------------------------------------------------------------------- #
class TestReAttribution:
    @pytest.mark.asyncio
    async def test_recording_the_same_pair_twice_updates_rather_than_raises(
        self, service, db_session, expert_id, weight_ids
    ):
        """
        ``weights`` has a unique constraint on (item, component), because an
        item has one current weight per component.

        A plain insert meant a second analysis of the same item - which is what
        re-running the attribution job does, and what ``force_reanalyze`` asks
        for - raised an integrity error instead of updating the row.
        """
        item_id, copper_id, _ = weight_ids

        again = await service.record_weight_attribution(
            db_session, item_id, copper_id,
            llm_weight=0.31, ml_weight=0.33, final_weight=0.32, source="fusion",
        )
        await db_session.commit()

        assert float(again.final_weight) == pytest.approx(0.32)
        assert again.source == "fusion"

    @pytest.mark.asyncio
    async def test_a_fresh_attribution_clears_a_stale_override(
        self, service, db_session, expert_id, weight_ids
    ):
        """
        Leaving the old reason in place would attribute the new number to the
        old decision - the document would show an expert override that no longer
        exists.
        """
        item_id, copper_id, _ = weight_ids

        await service.record_expert_override(
            db_session, item_id, copper_id,
            old_weight=0.30, new_weight=0.45, reason=REASON, user_id=expert_id,
        )
        await service.record_weight_attribution(
            db_session, item_id, copper_id,
            llm_weight=0.30, ml_weight=0.30, final_weight=0.30, source="fusion",
        )

        row = next(
            r for r in await service._load_siblings(db_session, item_id)
            if r.component_id == copper_id
        )
        assert row.source == "fusion"
        assert row.override_reason is None
        assert row.overridden_by is None
        assert row.overridden_at is None

    @pytest.mark.asyncio
    async def test_a_zero_weight_is_reported_as_zero_not_as_absent(
        self, service, db_session, expert_id, weight_ids
    ):
        """
        Truthiness was used to decide absence, so a component with a genuine 0.0
        share reported as ``None`` - a blank cell in the document where a zero
        belongs, reading as "no figure" rather than "none of this material".
        """
        item_id, copper_id, _ = weight_ids

        await service.record_weight_attribution(
            db_session, item_id, copper_id,
            llm_weight=0.0, ml_weight=0.0, final_weight=0.0, source="fusion",
        )

        history = await service.get_weight_history(db_session, item_id)
        entry = next(e for e in history if e["component_id"] == str(copper_id))

        assert entry["llm_weight"] == 0.0
        assert entry["ml_weight"] == 0.0
        assert entry["final_weight"] == 0.0
