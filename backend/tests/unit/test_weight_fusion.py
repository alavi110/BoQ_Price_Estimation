"""
Weight fusion tests (US2).

Fusion is the one place where the two attribution methods are combined, so it
is the place where a sign error, a missing normalisation or a swapped alpha
would be hardest to notice downstream: the result still looks like a perfectly
plausible weight vector.

Four invariants anchor this file, all of which the price formula depends on:

* the fused vector sums to 1.0. ``P = P_base * SUM(W_i * ratio_i)`` means a
  weight vector summing to 1.1 scales every price by 1.1, for no reason a
  reviewer could identify. Normalisation is therefore load-bearing, not tidying.
* alpha moves the result monotonically between the two inputs and never past
  either. A convex combination cannot overshoot; if it appears to, alpha is
  being applied to the wrong term.
* the canonical seven components are always all present, whatever the inputs
  omit, so the same description always produces the same component set.
* an expert override keeps the vector normalised by rescaling the *other*
  weights down. It is deliberately not surgical: see
  ``TestExpertOverride`` for why.

Expected values are written out longhand rather than copied from the code.
"""
import pytest

from src.core.config import settings
from src.services.confidence_scorer import ConfidenceScorer
from src.services.weight_attribution.fusion import (
    WeightFusion,
    fuse_weights,
)

CODES = list(WeightFusion.COMPONENTS)

#: The default: the trained model leads, the LLM refines.
ALPHA = settings.WEIGHT_FUSION_ALPHA


def vector(**overrides):
    """
    A seven-component weight vector summing to exactly 1.0.

    Overrides are *not* renormalised: several tests below need to hand the
    service a deliberately unbalanced vector and watch it correct for itself.
    Use :func:`balanced` whenever the test means to compare against an exact
    endpoint value.
    """
    base = {
        "copper": 0.30, "steel": 0.25, "cement": 0.10, "polymer": 0.10,
        "energy": 0.10, "labor": 0.10, "overhead": 0.05,
    }
    base.update(overrides)
    return base


def balanced(**overrides):
    """
    ``vector`` with the overrides applied and the whole thing renormalised.

    Use this to describe a *shape* - "copper is much heavier than steel" - when
    the test compares relatively.
    """
    return renormalise(vector(**overrides))


def composed(filler="labor", **fixed):
    """
    A vector summing to exactly 1.0 in which the named components hold the
    exact values given, the filler absorbs the remainder, and every other
    component is zero.

    Renormalising is no use when a test asserts an exact weight: spreading an
    unbalanced vector back over itself moves the very number under assertion.
    Zeroing the unnamed components and dumping the remainder on one unrelated
    one leaves both numbers under assertion untouched, so normalisation becomes
    a no-op the test can still check for.
    """
    weights = {code: 0.0 for code in CODES}
    weights.update(fixed)
    weights[filler] = 1.0 - sum(fixed.values())
    return weights


def renormalise(weights):
    """Rescale a dict to sum to 1.0, so overrides stay balanced."""
    total = sum(weights.values())
    return {k: v / total for k, v in weights.items()}


def ml_of(weights=None, r2=0.85):
    """
    ML weights plus the regressor's per-component R² breakdown.

    The breakdown is a *share* of the overall R², spread in proportion to the
    weights - which is what
    :meth:`MLRegressor.component_r2_scores` produces and therefore what the
    fusion is given in production. So it is built the same way here rather than
    as one flat figure per component: seven components each carrying the
    overall score would sum to ``7 * r2``, and a caller that wrote it that way
    would be describing a different model than the one being fitted.
    """
    weights = weights if weights is not None else vector()
    total = sum(weights.values()) or 1.0
    return weights, {code: r2 * share / total for code, share in weights.items()}


@pytest.fixture
def fusion():
    return WeightFusion()


# --------------------------------------------------------------------------- #
# The normalisation invariant
# --------------------------------------------------------------------------- #
class TestFusedWeights:
    def test_all_seven_canonical_components_are_present(self, fusion):
        result = fusion.fuse(vector(), *ml_of(), llm_certainty=0.9)
        assert set(result.final_weights) == set(CODES)
        assert len(CODES) == 7

    def test_fused_weights_sum_to_one(self, fusion):
        result = fusion.fuse(vector(), *ml_of(), llm_certainty=0.9)
        assert sum(result.final_weights.values()) == pytest.approx(1.0, abs=1e-12)

    def test_fused_weights_are_non_negative(self, fusion):
        result = fusion.fuse(vector(), *ml_of(), llm_certainty=0.9)
        assert all(w >= 0 for w in result.final_weights.values())

    def test_fusion_is_deterministic(self, fusion):
        first = fusion.fuse(vector(), *ml_of(), llm_certainty=0.9)
        second = fusion.fuse(vector(), *ml_of(), llm_certainty=0.9)
        assert first.final_weights == second.final_weights

    def test_identical_inputs_fuse_to_themselves(self, fusion):
        """No disagreement means the answer is the answer, unchanged."""
        result = fusion.fuse(vector(), *ml_of(), llm_certainty=0.9)
        assert result.final_weights["copper"] == pytest.approx(0.30)
        assert result.final_weights["overhead"] == pytest.approx(0.05)

    def test_an_unbalanced_input_is_normalised_rather_than_passed_through(self, fusion):
        """
        The regression this guards.

        Feeding a vector that sums to 1.05 straight through would inflate every
        downstream price by 5% - a number that looks like a market move but is
        pure bookkeeping.
        """
        heavy = renormalise(vector(copper=0.60, steel=0.05))
        heavy = {**heavy, "copper": heavy["copper"] * 1.5}  # no longer sums to 1
        assert abs(sum(heavy.values()) - 1.0) > 1e-3

        result = fusion.fuse(vector(), heavy, {c: 0.9 for c in CODES}, llm_certainty=0.9)

        assert sum(result.final_weights.values()) == pytest.approx(1.0, abs=1e-12)

    def test_an_all_zero_input_falls_back_to_a_uniform_split(self, fusion):
        """
        Better a disclosed uniform than a division by zero.

        Two empty attribution vectors mean the pipeline produced nothing, and
        an equal split at least produces a price whose error is visible.
        """
        result = fusion.fuse({}, {}, {}, llm_certainty=0.5)

        assert set(result.final_weights) == set(CODES)
        assert all(w == pytest.approx(1 / 7) for w in result.final_weights.values())
        assert sum(result.final_weights.values()) == pytest.approx(1.0, abs=1e-12)

    def test_a_negative_input_weight_is_absorbed_by_normalisation(self, fusion):
        """
        A negative attribution is nonsense, but the price it would produce is
        worse than nonsense: the normalisation is what stops it going negative.
        """
        result = fusion.fuse(vector(copper=-0.20), *ml_of(), llm_certainty=0.9)

        assert sum(result.final_weights.values()) == pytest.approx(1.0, abs=1e-12)
        assert all(w >= 0 for w in result.final_weights.values())


# --------------------------------------------------------------------------- #
# Alpha - the knob the formula hangs on
# --------------------------------------------------------------------------- #
class TestAlpha:
    def test_alpha_one_takes_the_ml_answer(self):
        fusion = WeightFusion(alpha=1.0)
        ml, r2 = ml_of(composed(copper=0.50, steel=0.10))
        llm = composed(copper=0.10, steel=0.50)

        result = fusion.fuse(llm, ml, r2, llm_certainty=1.0)

        assert result.final_weights["copper"] == pytest.approx(0.50)
        assert result.final_weights["steel"] == pytest.approx(0.10)

    def test_alpha_zero_takes_the_llm_answer(self):
        fusion = WeightFusion(alpha=0.0)
        ml, r2 = ml_of(composed(copper=0.50, steel=0.10))
        llm = composed(copper=0.10, steel=0.50)

        result = fusion.fuse(llm, ml, r2, llm_certainty=1.0)

        assert result.final_weights["copper"] == pytest.approx(0.10)
        assert result.final_weights["steel"] == pytest.approx(0.50)

    def test_alpha_half_is_the_midpoint(self):
        fusion = WeightFusion(alpha=0.5)
        ml, r2 = ml_of(composed(copper=0.50))
        llm = composed(copper=0.10)

        result = fusion.fuse(llm, ml, r2, llm_certainty=1.0)

        assert result.final_weights["copper"] == pytest.approx(0.30)

    def test_an_unbalanced_input_is_normalised_even_at_the_extremes(self):
        """
        alpha=1.0 does not mean "pass the ML vector through".

        With alpha at 1.0 the fused vector is the ML vector before
        normalisation, so an unbalanced ML answer would reach the price formula
        and scale it. The endpoint must still be renormalised.
        """
        fusion = WeightFusion(alpha=1.0)
        ml, r2 = ml_of(vector(copper=0.50, steel=0.10))  # sums to 1.05

        result = fusion.fuse(vector(), ml, r2, llm_certainty=1.0)

        assert sum(result.final_weights.values()) == pytest.approx(1.0, abs=1e-12)
        assert result.final_weights["copper"] == pytest.approx(0.50 / 1.05)

    def test_rising_alpha_moves_toward_the_ml_answer(self):
        """
        The documented behaviour: raising alpha shrinks the LLM's share.

        Sampled across the whole interval because a bug that swapped the terms
        would still be monotonic - just monotonic the wrong way, and only the
        endpoints would show it.
        """
        ml, r2 = ml_of(composed(copper=0.60, steel=0.05))
        llm = composed(copper=0.05, steel=0.55)

        previous = None
        for alpha in (0.0, 0.25, 0.5, 0.75, 1.0):
            copper = WeightFusion(alpha=alpha).fuse(
                llm, ml, r2, llm_certainty=1.0
            ).final_weights["copper"]
            if previous is not None:
                assert copper > previous, f"alpha={alpha} went backwards"
            previous = copper

    def test_the_result_never_leaves_the_range_of_its_inputs(self):
        """A convex combination cannot overshoot; if it does, alpha is wrong."""
        ml, r2 = ml_of(composed(copper=0.60))
        llm = composed(copper=0.05)

        for alpha in (0.0, 0.3, 0.7, 1.0):
            copper = WeightFusion(alpha=alpha).fuse(
                llm, ml, r2, llm_certainty=1.0
            ).final_weights["copper"]
            assert 0.05 - 1e-12 <= copper <= 0.60 + 1e-12

    def test_the_default_alpha_is_the_ml_led_one(self):
        """
        WEIGHT_FUSION_ALPHA = 0.7: the trained model leads the LLM.

        The 0.7 comes from configuration, so the test reads it from there too -
        if the setting is ever retuned, the assertion moves with it instead of
        pinning a value that is no longer the product's intent.
        """
        ml, r2 = ml_of(composed(copper=0.60))
        llm = composed(copper=0.05)

        result = fusion_default().fuse(llm, ml, r2, llm_certainty=1.0)

        expected = ALPHA * 0.60 + (1 - ALPHA) * 0.05
        assert result.final_weights["copper"] == pytest.approx(expected, abs=1e-12)
        # ML leads, so the answer sits nearer the ML figure than the LLM one.
        assert result.final_weights["copper"] > 0.325

    def test_the_method_name_records_the_alpha_used(self, fusion):
        """
        A defence document shows the method, and the method is only meaningful
        if it names the alpha that actually produced the weights.
        """
        assert "0.7" in fusion.fuse(vector(), *ml_of(), llm_certainty=0.9).fusion_method
        assert "1.0" in WeightFusion(alpha=1.0).fuse(
            vector(), *ml_of(), llm_certainty=0.9
        ).fusion_method


def fusion_default():
    return WeightFusion()


# --------------------------------------------------------------------------- #
# Confidence
# --------------------------------------------------------------------------- #
class TestConfidence:
    def test_agreement_raises_confidence(self, fusion):
        opposed = vector(copper=0.05, steel=0.05, cement=0.20, polymer=0.20,
                         energy=0.20, labor=0.20, overhead=0.10)

        agree = fusion.fuse(vector(), *ml_of(), llm_certainty=0.95)
        disagree = fusion.fuse(opposed, *ml_of(), llm_certainty=0.30)

        assert agree.confidence > disagree.confidence

    def test_a_poorly_fitting_model_lowers_confidence(self, fusion):
        good = fusion.fuse(vector(), *ml_of(r2=0.90), llm_certainty=0.9)
        bad = fusion.fuse(vector(), *ml_of(r2=0.10), llm_certainty=0.9)

        assert good.confidence > bad.confidence

    def test_confidence_stays_within_zero_and_one(self, fusion):
        for r2, certainty in ((-5.0, 0.0), (0.5, 0.5), (9.0, 1.0)):
            result = fusion.fuse(vector(), *ml_of(r2=r2), llm_certainty=certainty)
            assert 0.0 <= result.confidence <= 1.0

    def test_a_negative_r2_is_clamped_not_propagated(self, fusion):
        """
        A model that fits worse than the mean contributes nothing.

        Left unclamped, its negative R² would drag confidence below zero and the
        document would print a nonsense percentage. Note what it contributes
        *nothing*, as distinct from zeroing the answer: the LLM side still has
        its own evidence, so the result is the LLM's term alone. Clamping the
        blend instead would discard a usable second source along with the
        useless one.
        """
        result = fusion.fuse(vector(), *ml_of(r2=-0.8), llm_certainty=0.5)
        assert result.confidence == pytest.approx(0.3 * 0.5, abs=1e-12)

    def test_a_negative_r2_never_produces_a_negative_confidence(self, fusion):
        """
        The floor holds however bad the model is, and however certain the LLM.
        """
        for r2, certainty in ((-0.1, 0.0), (-5.0, 0.0), (-100.0, 0.9)):
            result = fusion.fuse(vector(), *ml_of(r2=r2), llm_certainty=certainty)
            assert result.confidence >= 0.0, (r2, certainty)

    def test_the_r2_breakdown_is_summed_not_averaged(self, fusion):
        """
        The per-component entries are shares, so the model's overall R² is their
        sum.

        Averaging instead would divide the score by the number of components: a
        perfect fit contributing 1.0 would register as 0.14 and a well-evidenced
        item would be presented as barely evidenced. Pinned here because the
        mistake is invisible in isolation - the confidence still looks like a
        number, just a wrong one.
        """
        weights = vector()
        _, breakdown = ml_of(weights=weights, r2=0.85)
        assert sum(breakdown.values()) == pytest.approx(0.85, abs=1e-12)

        result = fusion.fuse(vector(), weights, breakdown, llm_certainty=0.0)
        assert result.confidence == pytest.approx(0.7 * 0.85, abs=1e-12)

    def test_a_perfect_fit_is_not_divided_by_the_component_count(self, fusion):
        """
        The failure this guards against, stated as its own case: a model that
        explains the price perfectly must yield a near-certain score, not a
        fraction of one.
        """
        result = fusion.fuse(vector(), *ml_of(r2=1.0), llm_certainty=0.0)
        assert result.confidence == pytest.approx(0.7, abs=1e-12)

    def test_confidence_is_the_alpha_blend_of_the_two_signals(self, fusion):
        """
        Pinned longhand: 0.7 * 0.85 + 0.3 * 0.60 = 0.775.
        """
        result = fusion.fuse(vector(), *ml_of(r2=0.85), llm_certainty=0.60)
        assert result.confidence == pytest.approx(0.775, abs=1e-12)

    def test_the_r2_vector_is_carried_through_for_the_document(self, fusion):
        """
        FR-028: the document shows the model's own per-component fit.

        Carried through verbatim, so the figure in the document is the one the
        regressor reported rather than a restatement of it. Copper's entry is
        its share of the overall 0.77 in proportion to its weight, because that
        is the breakdown the regressor hands over.
        """
        weights = vector()
        _, breakdown = ml_of(weights=weights, r2=0.77)
        result = fusion.fuse(vector(), weights, breakdown, llm_certainty=0.9)

        assert result.ml_r2 == pytest.approx(breakdown)
        assert result.ml_r2["copper"] == pytest.approx(0.77 * 0.30)

    def test_the_llm_certainty_is_carried_through(self, fusion):
        result = fusion.fuse(vector(), *ml_of(), llm_certainty=0.42)
        assert result.llm_certainty == pytest.approx(0.42)

    def test_both_source_vectors_are_retained(self, fusion):
        """
        FR-028: the document shows LLM, ML and fused side by side.

        Keeping the losers matters - a reviewer who cannot see what the model
        said has no way to judge the answer it overrode.
        """
        llm = composed(copper=0.25)
        result = fusion.fuse(llm, *ml_of(), llm_certainty=0.8)

        assert result.llm_weights["copper"] == pytest.approx(0.25)
        assert result.ml_weights["copper"] == pytest.approx(0.30)
        assert result.final_weights["copper"] == pytest.approx(
            ALPHA * 0.30 + (1 - ALPHA) * 0.25
        )

    def test_the_bridging_vectors_are_completed_to_the_canonical_set(self, fusion):
        """
        A component only one side knew about is still recorded, at 0.0, so the
        document's two columns are the same width.
        """
        result = fusion.fuse(vector(), *ml_of(), llm_certainty=0.8)
        assert set(result.llm_weights) == set(CODES) == set(result.ml_weights)


# --------------------------------------------------------------------------- #
# Non-canonical components
# --------------------------------------------------------------------------- #
class TestCanonicalComponentSet:
    def test_a_component_outside_the_canonical_seven_is_dropped(self, fusion):
        """
        Weights are computed over a fixed component set.

        Silently admitting a component the price formula has no index for would
        produce a weight that moves nothing - or, once an index arrived, a
        price move nobody could trace.
        """
        result = fusion.fuse(vector(glass=0.10), *ml_of(), llm_certainty=0.8)

        assert set(result.final_weights) == set(CODES)
        assert "glass" not in result.final_weights

    def test_a_canonical_component_omitted_by_both_sides_still_appears(self, fusion):
        """
        The set is guaranteed, not emergent: a missing key reads as 0.0 rather
        than shifting every other component's share.
        """
        llm = vector()
        ml = vector()
        del llm["overhead"]
        del ml["overhead"]

        result = fusion.fuse(llm, ml, {c: 0.9 for c in CODES}, llm_certainty=0.8)

        assert "overhead" in result.final_weights
        assert result.final_weights["overhead"] == 0.0
        assert sum(result.final_weights.values()) == pytest.approx(1.0, abs=1e-12)


# --------------------------------------------------------------------------- #
# The alternative method
# --------------------------------------------------------------------------- #
class TestBayesianFusion:
    def test_it_also_produces_a_normalised_vector(self, fusion):
        result = fusion.fuse_bayesian(vector(), *ml_of(), llm_certainty=0.8)
        assert sum(result.final_weights.values()) == pytest.approx(1.0, abs=1e-12)
        assert set(result.final_weights) == set(CODES)

    def test_it_is_labelled_distinctly(self, fusion):
        """
        Two methods producing different weights must be distinguishable in the
        document, or a reviewer cannot tell which one was used.
        """
        result = fusion.fuse_bayesian(vector(), *ml_of(), llm_certainty=0.8)
        assert result.fusion_method == "bayesian"
        assert result.fusion_method != fusion.fuse(
            vector(), *ml_of(), llm_certainty=0.8
        ).fusion_method

    def test_a_certain_llm_pulls_the_result_toward_its_own_answer(self, fusion):
        """
        Precision weighting means certainty should matter, which is the whole
        reason to prefer this method.
        """
        confident = fusion.fuse_bayesian(
            vector(copper=0.55), *ml_of(vector(copper=0.20)), llm_certainty=0.95
        )
        hesitant = fusion.fuse_bayesian(
            vector(copper=0.55), *ml_of(vector(copper=0.20)), llm_certainty=0.05
        )

        assert confident.final_weights["copper"] > hesitant.final_weights["copper"]

    def test_an_explicit_prior_is_honoured(self, fusion):
        prior = {code: 1 / 7 for code in CODES}
        default = fusion.fuse_bayesian(vector(), *ml_of(), llm_certainty=0.8)
        tilted = fusion.fuse_bayesian(
            vector(), *ml_of(), llm_certainty=0.8, prior_weights=renormalise(
                vector(copper=0.60, steel=0.05)
            )
        )
        assert default.final_weights != tilted.final_weights
        assert prior  # the prior is a uniform default, not a requirement

    def test_no_prior_means_uniform(self, fusion):
        result = fusion.fuse_bayesian({}, {}, {}, llm_certainty=0.8)
        assert set(result.final_weights) == set(CODES)


# --------------------------------------------------------------------------- #
# Expert override - the method, not the route
# --------------------------------------------------------------------------- #
class TestExpertOverride:
    REASON = "قیمت مس در بازار جهانی افزایش چشمگیری داشت"

    def test_an_override_replaces_the_target_weight(self, fusion):
        result = fusion.expert_override(
            vector(), "copper", 0.45, reason=self.REASON
        )
        assert result.final_weights["copper"] == pytest.approx(0.45)

    def test_the_vector_still_sums_to_one_afterwards(self, fusion):
        """
        Why the override rescales rather than being surgical.

        If one weight moved and the rest did not, the vector would sum to
        1.0 + delta and every price computed from it would inflate by delta -
        an invisible mark-up that no reviewer would ever see. Rescaling the
        others is the only way an override can be honest.
        """
        result = fusion.expert_override(vector(), "copper", 0.45, reason=self.REASON)

        assert sum(result.final_weights.values()) == pytest.approx(1.0, abs=1e-12)

    def test_the_other_weights_keep_their_relative_shares(self, fusion):
        base = vector()
        result = fusion.expert_override(base, "copper", 0.45, reason=self.REASON)

        # copper's old share was 0.30, so the other 0.70 is squeezed into 0.55
        for code in CODES:
            if code == "copper":
                continue
            assert result.final_weights[code] == pytest.approx(
                base[code] * (0.55 / 0.70), abs=1e-12
            )

    def test_a_decrease_lifts_every_other_weight(self, fusion):
        """
        The mirror of the increase case.

        Taking copper from 0.30 to 0.20 frees 0.10 of mass, so the other six
        have to absorb it - otherwise the vector would sum to 0.90 and every
        price would quietly deflate by 10%.
        """
        result = fusion.expert_override(vector(), "copper", 0.20, reason=self.REASON)

        assert result.final_weights["copper"] == pytest.approx(0.20)
        assert sum(result.final_weights.values()) == pytest.approx(1.0, abs=1e-12)
        assert result.final_weights["steel"] > vector()["steel"]
        # 0.20 + 0.80 spread over the other six's original 0.70.
        assert result.final_weights["steel"] == pytest.approx(0.25 * 0.80 / 0.70)

    def test_an_override_to_zero_is_allowed_and_redistributed(self, fusion):
        """A component genuinely absent from this item is a legitimate answer."""
        result = fusion.expert_override(vector(), "copper", 0.0, reason=self.REASON)

        assert result.final_weights["copper"] == 0.0
        assert sum(result.final_weights.values()) == pytest.approx(1.0, abs=1e-12)

    def test_an_override_to_one_leaves_the_others_at_zero(self, fusion):
        result = fusion.expert_override(vector(), "copper", 1.0, reason=self.REASON)

        assert result.final_weights["copper"] == 1.0
        assert all(result.final_weights[c] == 0.0 for c in CODES if c != "copper")

    def test_a_vector_with_no_other_mass_is_split_evenly(self, fusion):
        """
        Nothing to scale proportionally, so the remainder is shared out rather
        than lost.
        """
        result = fusion.expert_override(
            {code: (1.0 if code == "copper" else 0.0) for code in CODES},
            "copper", 0.30, reason=self.REASON,
        )

        assert sum(result.final_weights.values()) == pytest.approx(1.0, abs=1e-12)
        assert all(
            result.final_weights[c] == pytest.approx(0.70 / 6)
            for c in CODES if c != "copper"
        )

    def test_an_override_is_labelled_and_certain(self, fusion):
        """An expert decision outranks both automated methods (FR-028)."""
        result = fusion.expert_override(vector(), "copper", 0.45, reason=self.REASON)

        assert result.fusion_method == "expert_override"
        assert result.confidence == 1.0

    def test_the_prior_vectors_are_not_claimed_by_the_override(self, fusion):
        """
        An override is a third thing entirely. Reporting the pre-override LLM or
        ML vector here would let the document imply the expert's number came
        from a model.
        """
        result = fusion.expert_override(vector(), "copper", 0.45, reason=self.REASON)

        assert result.llm_weights == {}
        assert result.ml_weights == {}
        assert result.ml_r2 == {}

    def test_an_unknown_component_is_rejected(self, fusion):
        with pytest.raises(ValueError, match="(?i)invalid component"):
            fusion.expert_override(
                vector(), "unobtainium", 0.10, reason=self.REASON
            )

    def test_a_weight_above_one_is_rejected(self, fusion):
        with pytest.raises(ValueError, match="(?i)between 0 and 1"):
            fusion.expert_override(vector(), "copper", 1.40, reason=self.REASON)

    def test_a_negative_weight_is_rejected(self, fusion):
        with pytest.raises(ValueError, match="(?i)between 0 and 1"):
            fusion.expert_override(vector(), "copper", -0.10, reason=self.REASON)

    def test_the_original_vector_is_not_mutated(self, fusion):
        """
        The service returns a new vector; the caller's stays intact.

        A caller that keeps a reference to the pre-override weights - the audit
        service does, to record what the expert replaced - would otherwise find
        the "previous" state had been overwritten, and the diff would be empty.
        """
        base = vector()
        before = dict(base)

        fusion.expert_override(base, "copper", 0.45, reason=self.REASON)

        assert base == before


# --------------------------------------------------------------------------- #
# The convenience wrapper
# --------------------------------------------------------------------------- #
class TestModuleHelper:
    def test_the_helper_matches_the_class(self, fusion):
        llm = vector()
        ml, r2 = ml_of()

        assert fuse_weights(llm, ml, r2, llm_certainty=0.8).final_weights == \
            fusion.fuse(llm, ml, r2, llm_certainty=0.8).final_weights

    def test_the_helper_honours_an_explicit_alpha(self, fusion):
        llm = composed(copper=0.10)
        ml, r2 = ml_of(composed(copper=0.50))

        result = fuse_weights(llm, ml, r2, llm_certainty=0.9, alpha=1.0)
        assert result.final_weights["copper"] == pytest.approx(0.50)

    def test_the_helper_defaults_to_the_configured_alpha(self, fusion):
        llm = vector(copper=0.10)
        ml, r2 = ml_of(vector(copper=0.50))

        assert fuse_weights(llm, ml, r2, llm_certainty=0.9).final_weights == \
            WeightFusion(alpha=ALPHA).fuse(llm, ml, r2, llm_certainty=0.9).final_weights


# --------------------------------------------------------------------------- #
# Confidence scoring - the service that decomposes the number
# --------------------------------------------------------------------------- #
class TestConfidenceScorer:
    @pytest.fixture
    def scorer(self):
        return ConfidenceScorer()

    def test_agreement_and_a_good_model_give_high_confidence(self, scorer):
        score = scorer.compute_confidence(
            vector(), vector(), {c: 0.92 for c in CODES}, 0.95, ALPHA
        )
        assert score.overall > 0.7

    def test_disagreement_lowers_confidence(self, scorer):
        opposed = vector(copper=0.05, steel=0.05, cement=0.20, polymer=0.20,
                         energy=0.20, labor=0.20, overhead=0.10)

        agree = scorer.compute_confidence(
            vector(), vector(), {c: 0.92 for c in CODES}, 0.95, ALPHA
        )
        clash = scorer.compute_confidence(
            opposed, vector(), {c: 0.92 for c in CODES}, 0.95, ALPHA
        )
        assert agree.overall > clash.overall

    def test_every_component_gets_its_own_score(self, scorer):
        """
        A single number cannot be acted on. The per-component scores are what
        tell an expert *which* material to look at.
        """
        score = scorer.compute_confidence(
            vector(), vector(), {c: 0.92 for c in CODES}, 0.95, ALPHA
        )
        assert set(score.component_scores) == set(CODES)

    def test_the_explanatory_factors_are_present(self, scorer):
        """A bare percentage cannot be reviewed; the factors are the reasoning."""
        score = scorer.compute_confidence(
            vector(), vector(), {c: 0.92 for c in CODES}, 0.95, ALPHA
        )
        assert score.factors
        assert any("r2" in str(name).lower() for name in score.factors)

    def test_the_overall_score_stays_in_the_unit_interval(self, scorer):
        for r2, certainty in ((-1.0, 0.0), (0.5, 0.5), (2.0, 1.0)):
            score = scorer.compute_confidence(
                vector(), vector(), {c: r2 for c in CODES}, certainty, ALPHA
            )
            assert 0.0 <= score.overall <= 1.0

    def test_the_level_label_matches_the_score(self, scorer):
        score = scorer.compute_confidence(
            vector(), vector(), {c: 0.92 for c in CODES}, 0.95, ALPHA
        )
        level = scorer.get_confidence_level(score.overall)

        assert level in ("high", "medium", "low", "very_low")
        if level == "high":
            assert score.overall >= 0.8
        elif level == "medium":
            assert 0.6 <= score.overall < 0.8
        elif level == "low":
            assert 0.4 <= score.overall < 0.6
        else:
            assert score.overall < 0.4

    def test_the_labels_cover_the_whole_range(self, scorer):
        """No score may fall between two labels, or the document shows nothing."""
        for value in (0.0, 0.39, 0.4, 0.59, 0.6, 0.79, 0.8, 1.0):
            assert scorer.get_confidence_level(value) is not None

    def test_the_labels_are_lowercase_to_match_the_other_producers(self, scorer):
        """
        The house convention, and the reason for it.

        ``forecast_service`` and ``confidence_calculator`` both emit lowercase
        and both feed the same document. A capitalised label here would render
        as nothing in a template that compares against 'high', with no error
        to point at it.
        """
        for value in (0.0, 0.5, 0.7, 0.95):
            assert scorer.get_confidence_level(value) == scorer.get_confidence_level(value).lower()

    def test_the_band_boundaries_are_exact(self, scorer):
        """The bands are inclusive at the bottom, so 0.8 is 'high' not 'medium'."""
        assert scorer.get_confidence_level(0.80) == "high"
        assert scorer.get_confidence_level(0.7999) == "medium"
        assert scorer.get_confidence_level(0.60) == "medium"
        assert scorer.get_confidence_level(0.5999) == "low"
        assert scorer.get_confidence_level(0.40) == "low"
        assert scorer.get_confidence_level(0.3999) == "very_low"
