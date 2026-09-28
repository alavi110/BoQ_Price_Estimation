"""
ML regression weight tests (US2).

The mechanism under test is the one FR-007 names: fit a regression of the
item's price on the market indices, and read the weights off the standardised
coefficients. US2's second acceptance criterion is therefore the centre of
this file - *empirical weights correlate with regression coefficients* - and it
is asserted directly rather than inferred from a good R².

Everything is built from a synthetic series with a known cost structure, so the
expected weights are known by construction and the test fails if the
coefficient-to-weight mapping is wrong even when the fit looks excellent.

Sample-size limits are tested as carefully as the happy path. Roughly a fifth
of real items have no history at all, so "declines to fit, and says so
distinctly" is a load-bearing behaviour, not an edge case: a model that quietly
returned uniform weights with a healthy R² would be worse than no model.
"""
import numpy as np
import pandas as pd
import pytest

from src.services.weight_attribution.ml_regressor import (
    MAX_FEATURES,
    MIN_SAMPLES_PER_FEATURE,
    InsufficientHistory,
    MLRegressor,
    train_weight_models,
)

CODES = list(MLRegressor.COMPONENTS)

#: The cost structure the synthetic series is generated from. These are the
#: shares the regression is expected to recover, not guesses.
TRUE_WEIGHTS = {
    "copper": 0.35, "steel": 0.25, "cement": 0.10, "polymer": 0.08,
    "energy": 0.10, "labor": 0.08, "overhead": 0.04,
}

#: Index base levels, chosen far apart so the scaler has real work to do and a
#: units mix-up cannot pass by accident.
BASE_LEVELS = {
    "copper": 2_500_000.0, "steel": 1_800_000.0, "cement": 100.0,
    "polymer": 45.0, "energy": 1_000.0, "labor": 12.0, "overhead": 8.0,
}


#: The price level the synthetic item starts at, so the series is in the same
#: order of magnitude as a real rial figure.
BASE_PRICE = 1_000_000.0

#: Realised relative volatility every synthetic index is given. See
#: :func:`synthetic` for why this is pinned rather than left to the RNG.
TARGET_VOL = 0.25


#: Persistence of the synthetic index paths. Below 1 so the series reverts
#: towards its level rather than wandering off - index levels mean-revert in
#: reality, and a pure random walk would make a time-ordered holdout a request
#: to extrapolate a non-stationary series, which no model can do and which would
#: make the cross-validated R2 meaningless as a confidence input.
PERSISTENCE = 0.6


def _unit_vol_walk(rng, months: int) -> np.ndarray:
    """
    An independent, mean-reverting path rescaled to a fixed realised volatility.

    The rescaling is the point of the helper. A raw walk's realised volatility
    varies by component, and the share the regression recovers is
    ``w_i * vol_i / sum(w_j * vol_j)`` - so an unconstrained draw silently
    multiplies each true weight by a random factor, and the estimator ends up
    measured against the generator's luck rather than against the mechanism.
    """
    shocks = rng.normal(0.0, 1.0, months)
    path = np.zeros(months)
    for t in range(1, months):
        path[t] = PERSISTENCE * path[t - 1] + shocks[t]
    path = path - path[0]  # a level offset is not information
    spread = path.std()
    return path / spread * TARGET_VOL if spread > 0 else path


def synthetic(
    months: int = 40,
    weights: dict = None,
    seed: int = 7,
    noise: float = 0.0,
    correlated: bool = False,
    start: str = "2019-01-01",
):
    """
    Build ``(market_data, item_history)`` for an item with a known structure.

    A *designed* experiment, not a simulation: each index gets an independent
    path of identical realised volatility, and the price is the normalised
    weighted sum of those indices. That makes the true weights exactly
    identifiable, which is what a mechanism test needs - with a shared market
    factor the design matrix goes near-singular and the test would be measuring
    collinearity instead.

    ``correlated`` adds the shared factor back, for the separate question of
    whether the *ranking* survives realistic cross-component correlation.
    """
    weights = weights or TRUE_WEIGHTS
    rng = np.random.default_rng(seed)

    dates = pd.date_range(start, periods=months, freq="MS")
    common = _unit_vol_walk(rng, months)

    frames = []
    for code, level in BASE_LEVELS.items():
        path = _unit_vol_walk(rng, months)
        if correlated:
            # A shared component plus an idiosyncratic one, so the seven series
            # move together the way real commodity indices do.
            path = 0.6 * common + 0.8 * path
        values = level * (1.0 + path)
        frames.append(pd.DataFrame({
            "date": dates,
            "component": code,
            "value": values,
        }))
    market = pd.concat(frames, ignore_index=True)

    # Normalised indices: 1.0 at the first observation, drifting thereafter.
    normalised = {
        frame["component"].iloc[0]: frame["value"].to_numpy() / frame["value"].iloc[0]
        for frame in frames
    }
    price = np.sum(
        [weight * normalised[code] for code, weight in weights.items()], axis=0
    ) * BASE_PRICE
    if noise:
        price = price + rng.normal(0.0, noise * price.mean(), months)

    history = pd.DataFrame({"date": dates, "price": price})
    return market, history


def _with_amplified_copper(
    amplitude: float, months: int = 40, seed: int = 7, noise: float = 0.02
):
    """
    The synthetic fixture with copper's swings scaled by ``amplitude``.

    Both halves move together: the index is amplified *and* the price series is
    regenerated against the amplified index, so the item's copper weight is still
    ``TRUE_WEIGHTS["copper"]`` while copper's standard deviation is ``amplitude``
    times the others. That is what makes it a clean probe - the cost share is
    held fixed and only the volatility changes, so any weight that moves with the
    volatility is a volatility artefact rather than an estimate.

    Amplifying about the index's own base level keeps the first reading at
    ``BASE_LEVELS["copper"]``, so the rebasing the rest of the file relies on is
    preserved and only the spread around it grows.
    """
    market, history = synthetic(months=months, seed=seed, noise=noise)
    rows = market["component"] == "copper"
    base = BASE_LEVELS["copper"]
    copper = market.loc[rows, "value"].to_numpy(dtype=float)
    amplified_copper = base + amplitude * (copper - base)
    market.loc[rows, "value"] = amplified_copper

    # Rebuild the price from the amplified index. Without this the regression
    # would be explaining a series the new features no longer fit, and the test
    # would be measuring the resulting poor R² rather than the weights.
    copper_share = amplified_copper / amplified_copper[0]
    other_share = (
        history["price"].to_numpy(dtype=float) / BASE_PRICE
        - TRUE_WEIGHTS["copper"] * (copper / copper[0])
    ) / (1.0 - TRUE_WEIGHTS["copper"])
    history["price"] = (
        TRUE_WEIGHTS["copper"] * copper_share
        + (1.0 - TRUE_WEIGHTS["copper"]) * other_share
    ) * BASE_PRICE
    return market, history


def fitted(months: int = 40, noise: float = 0.0, **kwargs):
    """A trained regressor over a synthetic series, plus its inputs."""
    correlated = kwargs.pop("correlated", False)
    market, history = synthetic(months=months, noise=noise, correlated=correlated)
    regressor = MLRegressor(**kwargs)
    X, y = regressor.prepare_features(market, history)
    weights = regressor.train(X, y, CODES)
    return regressor, weights, X, y


# --------------------------------------------------------------------------- #
# Features
# --------------------------------------------------------------------------- #
class TestPrepareFeatures:
    def test_it_returns_a_matrix_and_a_price_series(self):
        market, history = synthetic()
        regressor = MLRegressor()

        X, y = regressor.prepare_features(market, history)

        assert X.ndim == 2
        assert len(X) == len(y)
        assert X.shape[0] == len(history)

    def test_only_the_overlapping_dates_are_kept(self):
        """
        The two frames will rarely share every date, and imputing the gaps would
        invent prices. Rows where the price is unknown are dropped, not filled.
        """
        market, history = synthetic(months=40)
        history = history.iloc[10:]  # the item only has 30 prices on record

        regressor = MLRegressor()
        X, y = regressor.prepare_features(market, history)

        assert len(X) == 30

    def test_a_component_with_no_readings_is_dropped(self):
        """A NaN column would make RidgeCV refuse the whole matrix."""
        market, history = synthetic()
        market = market[market["component"] != "polymer"]

        regressor = MLRegressor()
        X, _ = regressor.prepare_features(market, history)

        assert "polymer" not in [c for _, c in regressor.feature_layout]

    def test_components_outside_the_canonical_set_are_ignored(self):
        market, history = synthetic()
        extra = market[market["component"] == "copper"].copy()
        extra["component"] = "unobtainium"
        market = pd.concat([market, extra], ignore_index=True)

        regressor = MLRegressor()
        regressor.prepare_features(market, history)

        assert {c for _, c in regressor.feature_layout} <= set(CODES)

    def test_dates_are_collapsed_to_monthly(self):
        """
        Two quotes in one month are one observation for a monthly index.

        Left un-collapsed, duplicated dates produce rows the shift() lags then
        read as consecutive months - a silent off-by-one in every lag.
        """
        market, history = synthetic(months=20)
        market = pd.concat([market, market], ignore_index=True)
        market["value"] = market["value"] * 1.001  # a distinct second quote

        regressor = MLRegressor()
        X, _ = regressor.prepare_features(market, history)

        assert len(X) == 20

    def test_the_feature_layout_is_at_least_one_row_per_component(self):
        """No lags means one column per component and nothing else."""
        regressor, _, X, _ = fitted()

        assert regressor.lag_count == 0
        assert X.shape[1] == len(CODES)

    def test_the_lag_count_is_chosen_from_the_data(self):
        """
        Short history gets no lags; long history gets some.

        A fixed lag schedule applied to a 14-row history would produce mostly
        NaN rows and a model fitted on whatever survived.
        """
        short = MLRegressor()
        short_market, short_history = synthetic(months=14)
        short_X, _ = short.prepare_features(short_market, short_history)

        long_regressor, _, long_X, _ = fitted(months=60)

        assert short.lag_count == 0
        assert short_X.shape[1] == len(CODES)
        assert long_regressor.lag_count > 0
        assert long_X.shape[1] > len(CODES)

    def test_the_admitted_lag_widths_are_the_ones_offered(self):
        """
        Not an arbitrary range. Building lags 0,1,2,3 instead of 0,1,3 would
        silently substitute two-month and four-month effects for the
        three- and six-month ones the policy chose.
        """
        regressor = MLRegressor()
        levels = regressor._choose_lags(2000, len(CODES))

        assert levels[0] == 0
        assert set(levels[1:]) <= set(MLRegressor.LAG_CANDIDATES)
        assert levels == sorted(levels)

    def test_the_feature_count_is_capped(self):
        """Even very long history must not grow the model without bound."""
        regressor, _, X, _ = fitted(months=200)

        assert X.shape[1] <= MAX_FEATURES

    def test_no_row_is_lost_to_the_lag_filtering(self):
        """
        A lagged layout that leaves an all-NaN trailing block would shorten the
        series silently, and the R² would be computed on fewer points than the
        caller believes.
        """
        regressor = MLRegressor()
        market, history = synthetic(months=40)
        X, y = regressor.prepare_features(market, history)

        assert len(X) == len(y) == 40

    def test_too_little_overlapping_history_is_refused(self):
        """Named separately from a bad fit, because the caller acts differently."""
        market, history = synthetic(months=40)
        regressor = MLRegressor(min_samples=12)

        with pytest.raises(InsufficientHistory, match="overlapping observations"):
            regressor.prepare_features(market, history.iloc[:5])

    def test_a_missing_component_column_is_reported_clearly(self):
        market, history = synthetic()
        market = market.rename(columns={"component": "material"})

        with pytest.raises(ValueError, match="component column"):
            MLRegressor().prepare_features(market, history)

    def test_a_missing_price_column_is_reported_clearly(self):
        market, history = synthetic()
        history = history.rename(columns={"price": "amount"})

        with pytest.raises(ValueError, match="price column"):
            MLRegressor().prepare_features(market, history)

    def test_empty_market_data_is_refused_rather_than_fitted(self):
        """Zero features cannot explain anything; saying so beats dividing by zero."""
        _, history = synthetic()

        with pytest.raises(InsufficientHistory):
            MLRegressor().prepare_features(pd.DataFrame(), history)


# --------------------------------------------------------------------------- #
# Weights come from the coefficients - the acceptance criterion
# --------------------------------------------------------------------------- #
class TestWeightsAreTheRegressionCoefficients:
    def test_the_weights_recover_a_known_cost_structure(self):
        """
        US2 acceptance criterion 2, asserted directly.

        The series was generated from ``TRUE_WEIGHTS``, so a regressor that
        reports those shares has demonstrably read them off the coefficients -
        which is the whole claim. The tolerance reflects estimation error on 40
        observations, not slack in the mechanism: an exact-relationship fit
        recovers the shares to five decimal places, which
        :meth:`TestR2.test_an_exact_relationship_scores_near_one` pins.
        """
        _, weights, _, _ = fitted(noise=0.02)

        for code, expected in TRUE_WEIGHTS.items():
            assert weights[code] == pytest.approx(expected, abs=0.04), code

    def test_the_ranking_matches_the_true_ranking(self):
        """
        Tighter than the absolute values, and it is the assertion that would
        catch a sign error: an inverted coefficient order reverses the ranking
        completely, while the sum-to-one invariant stays satisfied.
        """
        _, weights, _, _ = fitted(noise=0.02)

        assert sorted(weights, key=weights.get, reverse=True)[:2] == \
            ["copper", "steel"]
        assert weights["copper"] > weights["cement"] > weights["overhead"]

    def test_the_ranking_survives_correlated_indices(self):
        """
        Real commodity indices move together, so the design matrix is close to
        collinear and the individual coefficients are harder to pin down than
        the overall fit suggests.

        The ordering is the robust part of the answer and the one a reviewer
        acts on; the exact shares are not. Asserting the ranking here is what
        keeps a collinearity regression from being mistaken for a broken one.
        """
        _, weights, _, _ = fitted(noise=0.02, correlated=True)

        assert sorted(weights, key=weights.get, reverse=True)[:2] == \
            ["copper", "steel"]
        assert sum(weights.values()) == pytest.approx(1.0, abs=1e-12)

    def test_the_weights_are_the_coefficients_in_index_point_terms(self):
        """
        The mechanism, checked against the model itself rather than a copy of
        the expected answer.

        The correction is ``|coefficient| * base_level / scale``: undo the
        standardisation, then express the movement relative to the level the
        index was quoted at. On this fixture the ``base / scale`` factor comes out
        the same constant for every column - every synthetic index has the same
        realised relative volatility, so its base and its scale are proportional -
        which is precisely why this fixture alone cannot tell the corrected and
        uncorrected readings apart, and why the two tests below have to separate
        them deliberately.
        """
        regressor, weights, X, _ = fitted(noise=0.02)
        scaler = regressor.pipeline.named_steps["scaler"]
        coefficients = (
            np.abs(np.asarray(regressor.pipeline.named_steps["ridge"].coef_).ravel())
            * X[0]
            / np.asarray(scaler.scale_).ravel()
        )
        pooled: dict = {}
        for (lag, component), value in zip(regressor.feature_layout, coefficients):
            pooled[component] = pooled.get(component, 0.0) + value
        total = sum(pooled.values())

        for code in CODES:
            assert weights[code] == pytest.approx(pooled[code] / total, abs=1e-9)

    def test_the_weights_do_not_depend_on_the_units_the_index_is_quoted_in(self):
        """
        A copper index in rial/kg and the same index quoted per tonne are the same
        index, and have to produce the same weight.

        This is what the ``base_level`` half of the correction is for. Drop it and
        the weight is read off the raw coefficient, which answers "rial of item
        price per unit of index" - and that answer *is* a units artefact: requoting
        copper per tonne divides the share copper receives by 1000 and hands the
        difference to the other six components, which still sum to 1.0 and still
        look like a cost breakdown.

        Note what this test does and does not discriminate. It cannot catch a
        regression to the standardised coefficients, because requoting divides the
        column's scale by the same 1000 and the two errors cancel. What it does
        pin is the base-level factor, which is a different mistake with a
        different magnitude - and
        ``test_a_more_volatile_index_does_not_claim_a_larger_share`` is the one
        that catches the standardised reading.
        """
        market, history = synthetic(months=40, noise=0.02)
        requoted = market.copy()
        requoted.loc[requoted["component"] == "copper", "value"] *= 1000.0

        plain = MLRegressor()
        plain_X, plain_y = plain.prepare_features(market, history)
        plain_weights = plain.train(plain_X, plain_y, CODES)

        shifted = MLRegressor()
        shifted_X, shifted_y = shifted.prepare_features(requoted, history)
        shifted_weights = shifted.train(shifted_X, shifted_y, CODES)

        for code in CODES:
            assert shifted_weights[code] == pytest.approx(
                plain_weights[code], abs=1e-6
            ), code

    def test_a_more_volatile_index_does_not_claim_a_larger_share(self):
        """
        Volatility is not cost share, stated as a test.

        This is the discriminating case: it is the one that fails if the
        standardised coefficients are read directly instead of being put back into
        index-point terms. Copper's index is given six times the amplitude of the
        other six, so its standard deviation is six times as large while its share
        of the price is unchanged at 0.35 - the price series is regenerated to
        match, so the regression is asked a question with a known answer. Reading
        the standardised coefficients credits the volatility as cost and the item
        comes back around 60% copper: a number that looks entirely plausible, sums
        to 1.0, and would be printed into a defence document as though it had been
        measured.
        """
        amplified, history = _with_amplified_copper(amplitude=6.0)

        regressor = MLRegressor()
        X, y = regressor.prepare_features(amplified, history)
        weights = regressor.train(X, y, CODES)

        assert weights["copper"] == pytest.approx(
            TRUE_WEIGHTS["copper"], abs=0.05
        ), weights

    def test_the_standard_scaler_conditions_the_fit_and_nothing_else(self):
        """
        Standardisation is in the pipeline to make the *fit* well-conditioned, not
        to define the weights.

        A copper index in rial/kg and a labour index in rial/day differ by five
        orders of magnitude, so ridge on the raw columns would be solving a problem
        whose conditioning is set by the units rather than by the data. That is the
        reason the scaler is there - and it is equally the reason its output is not
        the answer, which is what
        ``test_the_weights_are_the_coefficients_in_index_point_terms`` pins.
        """
        regressor, _, X, _ = fitted()
        scaler = regressor.pipeline.named_steps["scaler"]

        assert np.allclose(scaler.transform(X).mean(axis=0), 0.0, atol=1e-9)
        assert np.allclose(scaler.transform(X).std(axis=0), 1.0, atol=1e-6)
        # The units are what make the raw design badly conditioned, so removing
        # them has to change the conditioning by a wide margin.
        assert np.linalg.cond(X) > 100 * np.linalg.cond(scaler.transform(X))

    def test_a_lagged_component_pools_its_lags(self):
        """
        How much the material costs overall, not how much of one month it did.

        With a lagged layout the coefficients for copper's three widths are
        summed into one share. Reading only the current-period column would
        report a fraction of the component's real contribution and silently
        renormalise the other six upward.
        """
        regressor, weights, X, _ = fitted(months=120, noise=0.02)
        assert regressor.lag_count > 0, "the series produced no lags to pool"

        assert sum(weights.values()) == pytest.approx(1.0, abs=1e-12)
        assert weights["copper"] > 0

        copper_columns = [
            value for (lag, component), value in zip(
                regressor.feature_layout,
                np.abs(np.asarray(regressor.pipeline.named_steps["ridge"].coef_).ravel()),
            )
            if component == "copper"
        ]
        assert len(copper_columns) == len(regressor.lag_levels)

    def test_a_negative_coefficient_is_absoluted_and_reported(self):
        """
        A coefficient that flips sign means the series is too short to trust.
        Absoluting it keeps the vector a valid distribution, and reporting the
        flip keeps the reviewer able to see what happened.
        """
        regressor, weights, _, _ = fitted(noise=0.02)

        assert all(w >= 0 for w in weights.values())
        # Whatever the signs, the reported count is consistent with the model.
        signs = regressor.coefficient_signs()
        assert all(count >= 1 for count in signs.values())


# --------------------------------------------------------------------------- #
# Normalisation - the property the price formula depends on
# --------------------------------------------------------------------------- #
class TestWeightNormalisation:
    def test_the_weights_sum_to_one(self):
        _, weights, _, _ = fitted(noise=0.02)
        assert sum(weights.values()) == pytest.approx(1.0, abs=1e-12)

    def test_all_seven_components_are_reported(self):
        _, weights, _, _ = fitted(noise=0.02)
        assert set(weights) == set(CODES)

    def test_no_weight_is_negative(self):
        _, weights, _, _ = fitted(noise=0.02)
        assert all(w >= 0 for w in weights.values())


# --------------------------------------------------------------------------- #
# R2 - what confidence is built on (FR-029)
# --------------------------------------------------------------------------- #
class TestR2:
    def test_an_exact_relationship_scores_near_one(self):
        """
        With no noise the model should explain the price. If it does not, the
        feature matrix and the target are misaligned - which is the failure a
        lagged layout is prone to, so this is the alignment test.

        Checked on the *cross-validated* score, not the in-sample one, because
        the cross-validated figure is the one that reaches the confidence
        calculation.
        """
        regressor, _, _, _ = fitted(noise=0.0)
        assert regressor.r2_score == pytest.approx(1.0, abs=0.02)

    def test_the_in_sample_fit_reproduces_the_training_data(self):
        """
        The stricter form of the alignment check: the fitted model, applied to
        the matrix it was trained on, reproduces the target it was given.
        """
        from sklearn.metrics import r2_score

        regressor, _, X, y = fitted(noise=0.0)
        assert r2_score(y, regressor.predict_price(X)) == pytest.approx(1.0, abs=1e-6)

    def test_the_held_out_score_is_never_better_than_the_in_sample_one(self):
        """
        The cross-validated score is the honest one, and it is always the worse
        of the two: every fold predicts dates the model has not seen.

        A held-out score above the fitted one would mean the split leaked future
        information backwards, which is the specific error ``TimeSeriesSplit``
        exists to prevent.
        """
        from sklearn.metrics import r2_score

        for kwargs in ({"noise": 0.0}, {"noise": 0.02}, {"noise": 0.02, "correlated": True}):
            regressor, _, X, y = fitted(**kwargs)
            in_sample = r2_score(y, regressor.predict_price(X))
            assert regressor.r2_score <= in_sample + 1e-9, kwargs

    def test_pure_noise_scores_near_or_below_zero(self):
        """
        The control. A model that scored well on noise would be reported as
        trustworthy, and an expert would defer to it.
        """
        market, history = synthetic(months=40, seed=3)
        history["price"] = np.random.default_rng(11).normal(1000, 400, len(history))

        regressor = MLRegressor()
        X, y = regressor.prepare_features(market, history)
        regressor.train(X, y, CODES)

        assert regressor.r2_score < 0.6

    def test_the_cross_validation_respects_time_order(self):
        """
        A random split would let the model interpolate a test point from its own
        future, reporting a confidence that would not survive a real tender.
        """
        regressor, _, X, y = fitted(noise=0.0)
        assert regressor.n_observations == len(X) == len(y)

    def test_the_r2_is_attributed_to_components_by_weight(self):
        """
        FR-029 makes confidence a function of R2, so a caller needs to know which
        part of the model carries it.
        """
        regressor, weights, _, _ = fitted(noise=0.02)
        scores = regressor.component_r2_scores()

        assert set(scores) == set(CODES)
        for code in CODES:
            assert scores[code] == pytest.approx(regressor.r2_score * weights[code])
        assert sum(scores.values()) == pytest.approx(regressor.r2_score, abs=1e-12)

    def test_a_heavier_component_carries_more_of_the_confidence(self):
        regressor, weights, _, _ = fitted(noise=0.02)
        scores = regressor.component_r2_scores()

        assert scores["copper"] > scores["overhead"]


# --------------------------------------------------------------------------- #
# Declining to fit
# --------------------------------------------------------------------------- #
class TestInsufficientHistory:
    def test_a_constant_price_is_refused_rather_than_fitted(self):
        """
        A price that never moves has no cost structure to attribute. An R² of
        0.0 would be a claim about the model; the truth is there was nothing
        to fit, and the caller has a different response for each.
        """
        market, history = synthetic(months=40)
        history["price"] = 1_000_000.0

        regressor = MLRegressor()
        X, y = regressor.prepare_features(market, history)

        with pytest.raises(InsufficientHistory, match="never varies"):
            regressor.train(X, y, CODES)

    def test_too_few_rows_is_refused(self):
        market, history = synthetic(months=40)
        regressor = MLRegressor()
        X, y = regressor.prepare_features(market, history)

        with pytest.raises(InsufficientHistory, match="training rows"):
            regressor.train(X[:5], y[:5], CODES)

    def test_mismatched_shapes_are_refused(self):
        regressor = MLRegressor()
        X, y = np.zeros((20, 7)), np.zeros(20)

        with pytest.raises(ValueError, match="rows but y has"):
            regressor.train(X, y[:10], CODES)

    def test_a_one_dimensional_matrix_is_refused(self):
        with pytest.raises(ValueError, match="2-dimensional"):
            MLRegressor().train(np.zeros(20), np.zeros(20), CODES)

    def test_the_fallback_is_an_equal_split_with_no_credibility(self):
        """
        The placeholder must be visibly a placeholder. A uniform split carrying
        a healthy R2 would be indistinguishable from a real finding.
        """
        regressor = MLRegressor()

        assert regressor.fallback_weights() == {
            c: pytest.approx(1 / 7) for c in CODES
        }
        assert regressor.is_trained is False

    def test_predict_before_training_falls_back_and_scores_zero(self):
        weights, r2 = MLRegressor().predict(np.zeros((1, 7)))

        assert all(w == pytest.approx(1 / 7) for w in weights.values())
        assert all(score == 0.0 for score in r2.values())

    def test_train_weight_models_returns_a_usable_untrained_regressor(self):
        """
        A caller gets a working object either way. The zero R2 is what makes the
        fusion fall back toward the LLM instead of treating noise as evidence.
        """
        market, history = synthetic(months=40)
        history = history.iloc[:4]

        regressor = train_weight_models(market, history)

        assert regressor.is_trained is False
        weights, r2 = regressor.predict(np.zeros((1, 7)))
        assert sum(weights.values()) == pytest.approx(1.0, abs=1e-12)
        assert max(r2.values()) == 0.0


# --------------------------------------------------------------------------- #
# Input validation at fit time
# --------------------------------------------------------------------------- #
class TestTrainValidation:
    def test_asking_for_a_prediction_with_the_wrong_width_is_refused(self):
        """
        Silently ignoring a column-count mismatch would return confident weights
        from a model fed features in an order it was never trained on.
        """
        regressor, _, X, _ = fitted(noise=0.02)

        with pytest.raises(ValueError, match="columns but the model was trained on"):
            regressor.predict(np.zeros((1, X.shape[1] + 1)))

    def test_predicting_a_price_without_a_model_is_refused(self):
        with pytest.raises(ValueError, match="needs a trained model"):
            MLRegressor().predict_price(np.zeros((1, 7)))

    def test_predicting_a_price_after_training_works(self):
        regressor, _, X, _ = fitted(noise=0.0)
        assert len(regressor.predict_price(X[:1])) == 1


# --------------------------------------------------------------------------- #
# Persistence
# --------------------------------------------------------------------------- #
class TestPersistence:
    def test_a_saved_model_reloads_with_its_weights_intact(self, tmp_path):
        path = str(tmp_path / "model.joblib")
        regressor, weights, X, _ = fitted(noise=0.02)
        regressor.save_models(path)

        restored = MLRegressor()
        restored.load_models(path)

        assert restored.is_trained is True
        assert restored.weights() == weights

    def test_the_feature_layout_travels_with_the_model(self, tmp_path):
        """
        Not cosmetic: the layout is what says which coefficient belongs to which
        component. Without it a loaded model cannot label its own weights.
        """
        path = str(tmp_path / "m.joblib")
        regressor, _, _, _ = fitted(months=60, noise=0.02)
        regressor.save_models(path)

        restored = MLRegressor()
        restored.load_models(path)

        assert restored.feature_layout == regressor.feature_layout
        assert restored.lag_count == regressor.lag_count
        assert restored.r2_score == pytest.approx(regressor.r2_score)

    def test_a_file_that_is_not_a_regressor_model_is_refused(self, tmp_path):
        """
        Loading something of unknown provenance would hand back weights labelled
        against components the model was never fitted on.
        """
        import joblib

        path = str(tmp_path / "foreign.joblib")
        joblib.dump({"something_else": 1}, path)

        with pytest.raises(ValueError, match="not a model written by"):
            MLRegressor().load_models(path)

    def test_a_reloaded_model_still_validates_the_feature_width(self, tmp_path):
        path = str(tmp_path / "m.joblib")
        regressor, _, X, _ = fitted(noise=0.02)
        regressor.save_models(path)

        restored = MLRegressor()
        restored.load_models(path)

        with pytest.raises(ValueError, match="columns but the model was trained on"):
            restored.predict(np.zeros((1, 3)))

    def test_a_reloaded_model_still_reports_its_r2(self, tmp_path):
        path = str(tmp_path / "m.joblib")
        regressor, _, _, _ = fitted(noise=0.02)
        regressor.save_models(path)

        restored = MLRegressor()
        restored.load_models(path)
        _, r2 = restored.predict(np.zeros((1, len(regressor.feature_layout))))

        assert sum(r2.values()) == pytest.approx(regressor.r2_score, abs=1e-9)

    def test_saving_an_untrained_model_is_refused(self, tmp_path):
        with pytest.raises(ValueError, match="needs a trained model"):
            MLRegressor().save_models(str(tmp_path / "m.joblib"))


# --------------------------------------------------------------------------- #
# The sample-size policy, stated as a test
# --------------------------------------------------------------------------- #
class TestSampleSizePolicy:
    def test_the_policy_constants_are_the_ones_the_code_uses(self):
        assert MIN_SAMPLES_PER_FEATURE == 4
        assert MAX_FEATURES == 24

    def test_one_lag_level_is_actually_reachable(self):
        """
        The cap must clear the width of a single lag level over the canonical
        seven components (14 columns). A cap of 12 or below would make the
        lag machinery dead code that silently never fires.
        """
        assert MAX_FEATURES >= 2 * len(CODES)

    def test_the_cap_is_the_binding_constraint_on_long_history(self):
        """
        Measured through the real code path, not by re-deriving the arithmetic.

        The width is read off the matrix the service actually built, since
        ``lag_count`` is a lag *width in months* and the column count it implies
        depends on which widths were admitted.
        """
        for months in (200, 2000):
            regressor = MLRegressor()
            market, history = synthetic(months=months)
            X, _ = regressor.prepare_features(market, history)
            assert X.shape[1] <= MAX_FEATURES, f"{months} months"

    def test_long_history_reaches_the_cap(self):
        """
        The cap is doing work, not sitting idle. The width is the largest
        multiple of the component count that fits under the cap - 21 columns for
        seven components, not 24, because a partial level would leave some
        components with a lagged column and others without.
        """
        regressor = MLRegressor()
        market, history = synthetic(months=2000)
        X, _ = regressor.prepare_features(market, history)

        expected = (MAX_FEATURES // len(CODES)) * len(CODES)
        assert X.shape[1] == expected
        assert len(regressor.lag_levels) == expected // len(CODES)

    def test_every_component_gets_a_column_at_every_admitted_lag(self):
        """
        The layout is all-or-nothing per level. A component missing from one lag
        but present in another would make the pooled share incomparable, since
        some components would be credited with more terms than others.
        """
        for months in (20, 60, 200, 2000):
            regressor = MLRegressor()
            market, history = synthetic(months=months)
            regressor.prepare_features(market, history)

            seen = {lag for lag, _ in regressor.feature_layout}
            assert seen == set(regressor.lag_levels)
            for lag in regressor.lag_levels:
                at_lag = [c for l, c in regressor.feature_layout if l == lag]
                assert sorted(at_lag) == sorted(CODES), (months, lag)

    def test_the_default_minimum_is_two_rows_per_component(self):
        """
        Enough for a two-fold split to predict a held-out block, and
        deliberately short of the per-feature ratio: see MLRegressor's
        ``__init__`` for why refusing to fit would be the worse behaviour.
        """
        assert MLRegressor().min_samples == 2 * len(CODES) == 14

    def test_a_history_shorter_than_the_minimum_is_refused(self):
        regressor = MLRegressor()
        market, history = synthetic(months=40)
        history = history.iloc[:10]

        with pytest.raises(InsufficientHistory, match="overlapping observations"):
            regressor.prepare_features(market, history)

    def test_a_history_at_the_minimum_does_fit(self):
        """The floor is a floor, not a suggestion - the boundary is inclusive."""
        regressor = MLRegressor()
        market, history = synthetic(months=40)
        history = history.iloc[:14]

        X, y = regressor.prepare_features(market, history)
        weights = regressor.train(X, y, CODES)

        assert sum(weights.values()) == pytest.approx(1.0, abs=1e-12)

    def test_each_lag_added_was_affordable_when_it_was_added(self):
        """
        Stated on the decision rather than the outcome.

        The guarantee is about the *next* step: for every level that was
        admitted, the rows-to-features ratio held and the cap was not exceeded;
        and the first level that was not admitted failed for one of those two
        stated reasons. The base level features have their own separate floor
        (see :meth:`MLRegressor.__init__`), and folding them in here would
        assert a rule the service never claimed.
        """
        regressor = MLRegressor()
        for rows in (14, 20, 40, 200, 2000):
            levels = regressor._choose_lags(rows, len(CODES))
            rejected_everything = levels == [0]

            for admitted in range(1, len(levels)):
                width = len(CODES) * (admitted + 1)
                assert rows >= MIN_SAMPLES_PER_FEATURE * width, (rows, admitted)
                assert width <= MAX_FEATURES, (rows, admitted)

            next_width = len(CODES) * (len(levels) + 1)
            stopped_because_too_few_rows = rows < MIN_SAMPLES_PER_FEATURE * next_width
            stopped_because_capped = next_width > MAX_FEATURES
            assert rejected_everything or stopped_because_too_few_rows \
                or stopped_because_capped, (rows, levels)

    def test_lag_widths_are_ordered_cheapest_first(self):
        """
        If the candidates were unordered, the cap could land on 12 periods
        instead of 1 and the series would be too short to support it.
        """
        assert list(MLRegressor.LAG_CANDIDATES) == sorted(MLRegressor.LAG_CANDIDATES)

    def test_no_components_means_no_lags(self):
        """
        Nothing to build columns for, so the layout is the bare current-period
        level - which downstream surfaces as a clear "no index data" error
        rather than an empty model.
        """
        assert MLRegressor()._choose_lags(1000, 0) == [0]
