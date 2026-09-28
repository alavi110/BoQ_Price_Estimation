"""
ML Regressor for Weight Attribution.

What this service actually does
------------------------------
An item's price is driven by the prices of the materials in it. So the natural
regression is the item's own price against the market indices, one column per
component::

    P_item(t) = b0 + b_copper * Index_copper(t) + b_steel * Index_steel(t) + ...

and the coefficients, put back into index-point terms and normalised, *are* the
empirical weights. A component with a large share of the cost produces a large
coefficient; one that barely appears produces a small one. That is the mechanism
behind the spec's "empirical weights correlate with regression coefficients"
(US2 acceptance criterion 2, FR-007).

Putting the coefficients back into index-point terms is not optional bookkeeping
- see :meth:`MLRegressor._weights_from_coefficients`. The regression is fitted
on standardised columns because that is what makes the fit well-conditioned, but
a standardised coefficient answers "how much does the price move per *standard
deviation* of this index", and that is a different quantity from "how much of
this price is this component". The two coincide only when every index happens to
have the same volatility, and the difference is not subtle: on a fixture where
copper is 35% of the cost and labour 10%, reading the standardised coefficients
reports copper at 18% and labour at 18% - it hands the volatile material the
share and takes it from the labour, which is the opposite of what the BoQ says.

The distinction that matters: the target is the *price*, and the features are
the *indices*. The reverse - one regressor per component, each predicting that
component's own series - is the shape this module used to have, and it cannot
produce a weight at all, however well it scores. A weight is a share of
something, so something has to be the thing being explained.

Sample size, and why the feature set is small
---------------------------------------------
Only about a fifth of items have any price history at all (US2 assumptions), and
a tender BoQ item's price history is short - a handful of contracts over a few
years. With 20-40 usable observations, 70 engineered features would fit noise
perfectly and generalise to nothing, and the R² that decided expert trust
(FR-029) would measure memorisation.

So the feature set is deliberately narrow: each index at the quoted date, plus a
small number of lags *only when there is enough history to support them*
(``min_samples_per_feature``). The lag count is chosen from the data rather than
fixed, and the choice is logged, because silently fitting 28 features to 12 rows
is the exact failure this avoids.

The intercept is deliberately excluded from the weights: it captures a fixed
price level, not a cost share, and folding it in would make an item look
copper-heavy because it carries a mobilisation charge.
"""
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

import joblib
import numpy as np
import pandas as pd
from sklearn.linear_model import RidgeCV
from sklearn.metrics import r2_score
from sklearn.model_selection import TimeSeriesSplit
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from src.core.logging import get_logger

logger = get_logger(__name__)

#: Rows of history needed per engineered feature. Below this the regression is
#: fitting noise, so features are added only while this ratio holds.
MIN_SAMPLES_PER_FEATURE = 4

#: How many distinct features to allow, whatever the data says. One lag level
#: over the seven canonical components is 14 columns, so this has to clear 14
#: or lags could never be added at all - which is the point of having them.
MAX_FEATURES = 24


#: Column names accepted for the item's own price, in preference order.
PRICE_COLUMNS = ("price", "base_price", "final_price")

#: The price column this module normalises incoming history into. Both
#: ``_pivot_prices`` and the target alignment read it, so the two cannot drift.
PRICE_COLUMN = "price"


class InsufficientHistory(ValueError):
    """
    Raised when there is not enough price history to fit a model at all.

    Separate from a zero R². "We could not try" and "we tried and it explained
    nothing" lead to different decisions: the first falls back to LLM-only
    weights with a confidence flag, the second is a finding worth reporting.
    """


@dataclass
class MLWeightResult:
    component_weights: Dict[str, float]
    r2_scores: Dict[str, float]
    feature_importance: Dict[str, Dict[str, float]]
    model_type: str


class MLRegressor:
    """ML-based weight attribution by regression of item price on market indices."""

    COMPONENTS = ["copper", "steel", "cement", "polymer", "energy", "labor", "overhead"]

    #: Candidate lag widths in periods (months), ordered cheapest first. Chosen
    #: from the data by :meth:`_choose_lags`, never applied wholesale.
    LAG_CANDIDATES = (1, 3, 6, 12)

    def __init__(self, min_samples: int = None):
        """
        Args:
            min_samples: rows of history needed before a fit is attempted.
                Defaults to two rows per canonical component, which is the
                floor for a two-fold time-series split to be meaningful - the
                first fold trains on half the data and still has to predict a
                held-out block.

                This is deliberately *not* ``MIN_SAMPLES_PER_FEATURE *
                len(COMPONENTS)`` (28). That ratio is the rule for *adding*
                features, and holding the base level set to it would mean the
                model never runs at all on the history a real tender item
                actually has. Under-determined here is acceptable because ridge
                regularisation keeps the fit finite and the resulting R² is
                poor, and a poor R² is exactly the signal FR-029's confidence
                calculation consumes. The model says "I don't know much" rather
                than refusing to speak - which is the more useful failure.
        """
        self.logger = logger
        self.min_samples = (
            min_samples if min_samples is not None else 2 * len(self.COMPONENTS)
        )
        self.pipeline: Optional[Pipeline] = None
        #: ``(lag, component)`` pairs, in the column order ``X`` was built with.
        #: Persisted so a loaded model is fed features in the order it expects.
        self.feature_layout: List[Tuple[int, str]] = []
        #: The lag widths actually built, e.g. ``[0, 1, 3]``. The source of
        #: truth for the layout - never reconstructed from a count.
        self.lag_levels: List[int] = [0]
        #: Per-column index level each feature is measured relative to, captured
        #: at fit time. Read by :meth:`_base_levels`, and persisted with the model
        #: because a weight is not recoverable from the coefficients alone.
        self._training_base_levels: Optional[np.ndarray] = None
        self.r2_score: float = 0.0
        self.is_trained = False
        self.n_observations = 0

    @property
    def lag_count(self) -> int:
        """How many lag levels were added beyond the current-period one."""
        return len(self.lag_levels) - 1

    # ----------------------------------------------------------------- #
    # Features
    # ----------------------------------------------------------------- #
    def prepare_features(
        self,
        market_data: pd.DataFrame,
        item_history: pd.DataFrame,
    ) -> Tuple[np.ndarray, np.ndarray]:
        """
        Pivot market indices and item prices onto a shared date index.

        Args:
            market_data: long frame with ``date`` and one of ``component`` /
                ``component_code``, plus ``value``. Extra rows for components
                outside :attr:`COMPONENTS` are dropped.
            item_history: frame with ``date`` and one of ``price`` / ``base_price``
                / ``final_price`` - the item's own observed price on that date.

        Returns:
            ``(X, y)`` where ``X`` is ``(n_samples, n_features)`` and ``y`` is
            ``(n_samples,)`` - the price to be explained.

        Raises:
            InsufficientHistory: the two frames share too few dates.
        """
        indices = self._pivot_indices(market_data)
        prices = self._pivot_prices(item_history)

        joined = indices.join(prices, how="inner").dropna()
        if len(joined) < self.min_samples:
            raise InsufficientHistory(
                f"{len(joined)} overlapping observations between the market "
                f"indices and the item's price history; {self.min_samples} are "
                f"needed to fit a model. LLM-only weights will be used instead."
            )

        self.lag_levels = self._choose_lags(len(joined), indices.shape[1])
        self.feature_layout = [
            (lag, component)
            for lag in self.lag_levels
            for component in self.COMPONENTS
            if component in indices.columns
        ]
        self.logger.info(
            "ml_feature_layout",
            observations=len(joined),
            components=int(indices.shape[1]),
            lags=self.lag_levels,
            features=len(self.feature_layout),
        )

        X, index = self._build_matrix(joined)
        # y is taken from *X's* index, not the joined frame's. The lagged
        # features drop the earliest rows, and taking y from the untrimmed frame
        # would pair each feature vector with the price from `lag` months
        # earlier - a misalignment that still fits, still scores, and is wrong.
        y = joined.loc[index, PRICE_COLUMN].to_numpy(dtype=float)
        return X, y

    def _pivot_indices(self, market_data: pd.DataFrame) -> pd.DataFrame:
        """Wide frame, one column per component, indexed by date."""
        if market_data is None or len(market_data) == 0:
            return pd.DataFrame(columns=list(self.COMPONENTS))

        frame = market_data.copy()
        frame["date"] = pd.to_datetime(frame["date"]).dt.to_period("M").dt.to_timestamp()

        code_column = next(
            (c for c in ("component", "component_code", "code") if c in frame.columns),
            None,
        )
        if code_column is None or "value" not in frame.columns:
            raise ValueError(
                "market_data needs a component column (component / component_code "
                "/ code) and a 'value' column"
            )

        known = frame[frame[code_column].isin(self.COMPONENTS)]
        if known.empty:
            return pd.DataFrame(columns=list(self.COMPONENTS))

        wide = known.pivot_table(
            index="date", columns=code_column, values="value", aggfunc="last"
        )
        # Reindex onto the canonical order so a missing component becomes a
        # NaN column rather than silently shifting the coefficient/weight
        # correspondence.
        return wide.reindex(columns=[c for c in self.COMPONENTS if c in wide.columns])

    def _pivot_prices(self, item_history: pd.DataFrame) -> pd.DataFrame:
        """Single-column frame of the item's own price, indexed by date."""
        if item_history is None or len(item_history) == 0:
            return pd.DataFrame(columns=[PRICE_COLUMN])

        frame = item_history.copy()
        frame["date"] = pd.to_datetime(frame["date"]).dt.to_period("M").dt.to_timestamp()

        price_column = next(
            (c for c in PRICE_COLUMNS if c in frame.columns),
            None,
        )
        if price_column is None:
            raise ValueError(
                "item_history needs a price column "
                f"({' / '.join(PRICE_COLUMNS)})"
            )

        series = frame.groupby("date")[price_column].last().to_frame(PRICE_COLUMN)
        return series

    def _choose_lags(self, n_rows: int, n_components: int) -> List[int]:
        """
        The lag widths to build, as a list starting at 0.

        Tracked as a list of admitted widths rather than a single "how many
        lags" number, because the widths are not consecutive - 1, 3, 6 and 12
        are four *levels*, and treating the width 3 as "three levels" would
        build lags 0,1,2,3 instead of 0,1,3 and quietly change the model.

        Returns:
            ``[0]`` when no lag is affordable, otherwise ``[0, w1, w2, ...]``.

        A level is admitted only if the resulting width satisfies both the
        sample ratio and the feature cap, so the two rules are applied to the
        same candidate rather than to different notions of the layout.
        """
        levels: List[int] = [0]
        if n_components == 0:
            return levels

        for candidate in self.LAG_CANDIDATES:
            width = n_components * (len(levels) + 1)
            if n_rows < MIN_SAMPLES_PER_FEATURE * width:
                break
            if width > MAX_FEATURES:
                break
            levels.append(candidate)

        self.lag_levels = levels
        return levels

    def _build_matrix(self, joined: pd.DataFrame) -> Tuple[np.ndarray, pd.Index]:
        """
        Materialise ``self.feature_layout`` as a dense float matrix.

        Returns the matrix together with the dates it covers, so the caller can
        align the target to exactly these rows and no others.
        """
        columns = []
        for lag, component in self.feature_layout:
            if lag == 0:
                columns.append(joined[component])
            else:
                columns.append(joined[component].shift(lag))
        frame = pd.concat(columns, axis=1).dropna()
        return frame.to_numpy(dtype=float), frame.index

    # ----------------------------------------------------------------- #
    # Training
    # ----------------------------------------------------------------- #
    def train(
        self,
        X: np.ndarray,
        y: np.ndarray,
        component_names: Sequence[str],
    ) -> Dict[str, float]:
        """
        Fit the price regression and read the weights off its coefficients.

        Args:
            X: feature matrix from :meth:`prepare_features`, columns in
                ``self.feature_layout`` order.
            y: the item's price on each row of ``X``.
            component_names: labels for the target, for the caller's records.
                Each component is reported with the R² of the *overall* fit
                weighted by that component's share, so a caller can see which
                parts of the model the score is resting on.

        Returns:
            Per-component weights summing to 1.0.

        Raises:
            InsufficientHistory: fewer rows than folds, or no variation in the
                target to explain.
        """
        X = np.asarray(X, dtype=float)
        y = np.asarray(y, dtype=float).ravel()

        if X.ndim != 2:
            raise ValueError(f"X must be 2-dimensional, got shape {X.shape}")
        if len(X) != len(y):
            raise ValueError(f"X has {len(X)} rows but y has {len(y)}")
        if len(X) < self.min_samples:
            raise InsufficientHistory(
                f"{len(X)} training rows; {self.min_samples} are needed"
            )
        if not self.feature_layout:
            # Without a layout the coefficients cannot be attributed to
            # components: :meth:`_weights_from_coefficients` walks the layout and
            # the coefficients together, so an empty layout leaves every
            # component at zero and the method returns its equal-split fallback.
            # That failure is invisible - the caller gets a confident, uniform,
            # meaningless weight vector - and it is reachable by any caller that
            # assembles its own matrix instead of going through
            # :meth:`prepare_features`. Refused here rather than papered over.
            raise ValueError(
                "train() needs a feature layout: call prepare_features() first, "
                "or set feature_layout to match X's columns."
            )
        if len(self.feature_layout) != X.shape[1]:
            # Same failure, one step later: a layout of the wrong length zips
            # against the coefficients without complaint and attributes them to
            # the wrong components, which is worse than refusing because the
            # result looks specific.
            raise ValueError(
                f"feature_layout describes {len(self.feature_layout)} column(s) "
                f"but X has {X.shape[1]}"
            )
        if np.std(y) < 1e-9:
            # A constant price has no variation to attribute. Reporting an R² of
            # 0.0 would be a claim about the model; the truth is that there was
            # nothing to fit.
            raise InsufficientHistory(
                "The item's price never varies across the history, so there is "
                "no cost structure to attribute. LLM-only weights will be used."
            )

        n_splits = min(5, max(2, len(X) // 4))
        splitter = TimeSeriesSplit(n_splits=n_splits)

        self._assert_identifiable(X)

        self.pipeline = Pipeline([
            ("scaler", StandardScaler()),
            ("ridge", RidgeCV(
                alphas=np.logspace(-4, 4, 20),
                cv=splitter,
                scoring="r2",
            )),
        ])
        self.pipeline.fit(X, y)
        self._training_base_levels = X[0].astype(float).copy()

        weights = self._weights_from_coefficients()
        self.r2_score = self._cross_validated_r2(X, y, splitter)
        self.is_trained = True
        self.n_observations = len(X)

        self.logger.info(
            "ml_model_trained",
            observations=len(X),
            features=X.shape[1],
            r2=round(self.r2_score, 4),
            alpha=float(self.pipeline.named_steps["ridge"].alpha_),
            top_component=max(weights, key=weights.get),
        )
        return weights

    def _assert_identifiable(self, X: np.ndarray) -> None:
        """
        Refuse a design whose columns cannot be told apart.

        A price is a function of the indices whether or not they are
        distinguishable from one another, so a rank-deficient design can still
        reach an R² of 1.0. What it cannot do is say *which* index the movement
        belongs to: the coefficients are then determined only up to a null-space
        vector, and ridge returns one particular solution that looks exactly like
        a real attribution. Two indices that both track oil over the pricing
        window is not a hypothetical - it is the normal case in a metals-heavy
        bill of quantities.

        The failure this prevents is specific and expensive. A defence document
        showing "copper 35%, steel 25%" derived from a model that cannot
        distinguish copper from steel invites exactly the question it cannot
        answer, and the answer is that the split was arbitrary. Raising here
        hands the item to the LLM side, which discloses its own uncertainty -
        the degraded path, and an honest one.

        Checked on the standardised columns, because that is the space the
        coefficients live in: a raw copper index in rial/kg and a labour index
        in rial/day differ in scale, and their conditioning is not comparable
        until the scaler has removed the units.
        """
        scaled = StandardScaler().fit_transform(X)
        if scaled.shape[1] < 2:
            return

        singular = np.linalg.svd(scaled, compute_uv=False)
        rank = int(np.linalg.matrix_rank(scaled))
        if rank == scaled.shape[1]:
            return

        smallest = float(singular[-1])
        largest = float(singular[0])
        condition = largest / smallest if smallest > 0 else float("inf")
        raise InsufficientHistory(
            f"the {scaled.shape[1]} index series move together (rank {rank}, "
            f"condition number {condition:.3g}), so the data cannot say which "
            f"component the price movement belongs to. LLM-only weights will be "
            f"used."
        )

    def _weights_from_coefficients(self) -> Dict[str, float]:
        """
        Non-negative, normalised shares from the coefficients, in index-point terms.

        The scaler is a conditioning device, not a unit conversion, and the two
        must not be confused. A ridge fitted on standardised columns has
        coefficients that answer "how much does the price move per *standard
        deviation* of this index" - so a series that swings widely claims a large
        share of the price no matter how little of the cost it actually is. A
        copper index with 5x the volatility of a labour index comes back weighted
        5x too heavily, and the resulting vector is not a cost breakdown at all.

        So the coefficient is put back the way it went in: divided by the
        scaler's ``scale_`` it is the rial-of-item-price per unit of index. The
        share that reaches the price formula is then the movement per unit of
        *relative* index movement, and relative to what the index was at the
        pricing date - which is what ``Index_current / Index_base`` in
        ``P_new = P_base * SUM(W_i * ratio_i)`` is measuring. Hence the
        multiplication by the base level: coefficient-per-index-point, times the
        index level it is relative to, is rial of item price per unit of relative
        index movement, which is the weight the formula consumes.

        A common base level across the seven components is what makes this exact
        rather than approximate, and it is not an accident of the data: a real
        index family is rebased to 100 at the pricing date, so every column's
        base level is the same and the two constructions coincide. The paths do
        drift over a long history, so the first observed value is used as the
        base - it is the earliest reading the model was fitted on, and it is
        stable against a series that trends within the window.

        Sign is dropped rather than taken absolutely at the raw-coefficient
        stage, for the reason :meth:`coefficient_signs` exists: a negative
        coefficient means the component moved against the price, which for a bill
        of quantities is a data problem, not a negative share.

        The intercept is excluded throughout. It is a fixed price level, not a
        cost share, and folding it in would make an item look copper-heavy
        because it carries a mobilisation charge.
        """
        ridge = self.pipeline.named_steps["ridge"]
        scaler = self.pipeline.named_steps["scaler"]
        coefficients = np.asarray(ridge.coef_, dtype=float).ravel()
        scales = np.asarray(scaler.scale_, dtype=float).ravel()
        base_levels = self._base_levels(X_width=len(coefficients))

        per_component: Dict[str, float] = {c: 0.0 for c in self.COMPONENTS}

        for (lag, component), coefficient, scale, base in zip(
            self.feature_layout, coefficients, scales, base_levels
        ):
            if component not in per_component:
                continue
            # ``scale`` is guarded rather than trusted: a column the scaler found
            # constant gets a scale of 1.0 by sklearn's own convention, but a
            # zero here would make the weight infinite and quietly take over the
            # whole vector. Such a column carries no information about the price,
            # so it is given no weight.
            if not np.isfinite(scale) or scale <= 0 or not np.isfinite(base):
                continue
            # A component's weight pools its level and its lags: how much of the
            # price the material accounts for, not how much of one month it did.
            per_component[component] += abs(coefficient) * base / scale

        total = sum(per_component.values())
        if total <= 0:
            return self.fallback_weights()
        return {c: v / total for c, v in per_component.items()}

    def _base_levels(self, X_width: int) -> np.ndarray:
        """
        The index level each feature column is relative to: its first reading.

        The first observation, not the window mean. The mean of a trending series
        sits somewhere in the middle of the window, and the price formula divides
        by the index *at the pricing date* - so a base that drifts with the window
        silently rescales that component's share by however far the series moved
        over the fitting period. On a copper index that climbed 30% across the
        history the mean sits 15% high and the item's copper weight comes back
        15% light. The first reading is the level the indices are rebased to, and
        it is the one base the model has no ambiguity about.

        For a lagged column the first non-null reading is that column's own base,
        which is consistent: every column is measured against the level it was
        measured from.

        Falls back to the fitted scaler's ``mean_`` and then to ones, so a model
        reloaded from a file written before these levels were persisted still
        reports a valid vector rather than raising.
        """
        if self._training_base_levels is not None and len(
            self._training_base_levels
        ) == X_width:
            return np.asarray(self._training_base_levels, dtype=float).ravel()

        scaler = self.pipeline.named_steps.get("scaler")
        means = getattr(scaler, "mean_", None)
        if means is not None and len(means) == X_width:
            return np.asarray(means, dtype=float).ravel()
        return np.ones(X_width, dtype=float)

    def coefficient_signs(self) -> Dict[str, int]:
        """
        How many lags of each component came back negative.

        A component whose coefficients are all positive is behaving like a cost
        input. One that flips sign is telling us the series is too short to
        trust, and the expert reviewing the defence document is entitled to see
        that rather than a silently absoluted weight.
        """
        ridge = self.pipeline.named_steps["ridge"]
        coefficients = np.asarray(ridge.coef_, dtype=float).ravel()
        signs: Dict[str, int] = {}
        for (lag, component), coefficient in zip(self.feature_layout, coefficients):
            if coefficient < 0:
                signs[component] = signs.get(component, 0) + 1
        return signs

    def _cross_validated_r2(
        self, X: np.ndarray, y: np.ndarray, splitter: TimeSeriesSplit
    ) -> float:
        """
        R² from time-ordered hold-outs.

        ``TimeSeriesSplit`` and not ``train_test_split``: a price series is
        autocorrelated, so a random split lets the model interpolate a test
        point from its own future and reports a confidence that will not survive
        contact with a real tender.
        """
        scores = []
        for train_idx, val_idx in splitter.split(X):
            fold = Pipeline([
                ("scaler", StandardScaler()),
                ("ridge", RidgeCV(alphas=np.logspace(-4, 4, 20), cv=None)),
            ])
            fold.fit(X[train_idx], y[train_idx])
            scores.append(r2_score(y[val_idx], fold.predict(X[val_idx])))
        return float(np.mean(scores)) if scores else 0.0

    def component_r2_scores(self) -> Dict[str, float]:
        """
        The overall R², attributed to components in proportion to their weight.

        A single number cannot answer "which part of this model do I trust", and
        FR-029 makes confidence a function of R². The split is the model's own
        weighting, so it says where the explanatory power is rather than
        pretending each component was fitted separately.
        """
        if not self.is_trained:
            return {c: 0.0 for c in self.COMPONENTS}
        weights = self.weights()
        return {c: self.r2_score * w for c, w in weights.items()}

    # ----------------------------------------------------------------- #
    # Use
    # ----------------------------------------------------------------- #
    def predict(self, X: np.ndarray) -> Tuple[Dict[str, float], Dict[str, float]]:
        """
        The fitted weights, and the R² that qualifies them.

        Weights come from the coefficients rather than from evaluating the model
        on ``X``, because a weight is a property of the fit, not of whatever
        month is being asked about. ``X`` is still validated: feeding the model
        a different column count than it was trained on is a caller bug, and
        silently ignoring it would return confident nonsense.
        """
        if not self.is_trained:
            return self.fallback_weights(), {c: 0.0 for c in self.COMPONENTS}

        if X is not None and np.asarray(X).ndim == 2 and np.asarray(X).shape[1] != len(
            self.feature_layout
        ):
            raise ValueError(
                f"X has {np.asarray(X).shape[1]} columns but the model was "
                f"trained on {len(self.feature_layout)}"
            )

        return self.weights(), self.component_r2_scores()

    def weights(self) -> Dict[str, float]:
        """The fitted weights, or an equal split when there is no model."""
        if not self.is_trained:
            return self.fallback_weights()
        return self._weights_from_coefficients()

    def predict_price(self, X: np.ndarray) -> np.ndarray:
        """The model's price estimate - the residual diagnostic, not the weights."""
        if not self.is_trained:
            raise ValueError("predict_price needs a trained model; train() first")
        return self.pipeline.predict(np.asarray(X, dtype=float))

    def fallback_weights(self) -> Dict[str, float]:
        """
        An equal split across the canonical components.

        Used when no model could be fitted. This is a *disclosed* placeholder,
        not an estimate: it is reported with an R² of 0.0 so the confidence
        calculation inverts rather than treating it as a real answer.
        """
        return {c: 1.0 / len(self.COMPONENTS) for c in self.COMPONENTS}

    # ----------------------------------------------------------------- #
    # Persistence
    # ----------------------------------------------------------------- #
    def save_models(self, path: str) -> None:
        """
        Persist the model.

        ``feature_layout`` and the lag count travel with it. They are not
        cosmetic: the feature order defines which coefficient belongs to which
        component, and a model loaded without them cannot label its own weights.
        """
        if not self.is_trained:
            raise ValueError("save_models needs a trained model; train() first")
        joblib.dump({
            "pipeline": self.pipeline,
            "feature_layout": self.feature_layout,
            "lag_levels": self.lag_levels,
            "r2_score": self.r2_score,
            "n_observations": self.n_observations,
            "min_samples": self.min_samples,
            "training_base_levels": self._training_base_levels,
        }, path)

    def load_models(self, path: str) -> None:
        """
        Restore a model saved by :meth:`save_models`.

        Refuses a file without a pipeline rather than loading something whose
        provenance is unknown: a model that cannot name its own feature order
        will hand back weights labelled against the wrong components.
        """
        data = joblib.load(path)
        if "pipeline" not in data:
            raise ValueError(
                f"{path} is not a model written by MLRegressor.save_models "
                f"(no 'pipeline' key)"
            )
        self.pipeline = data["pipeline"]
        self.is_trained = True
        self.feature_layout = [tuple(pair) for pair in data.get("feature_layout", [])]
        self.lag_levels = list(data.get("lag_levels", [0]))
        self.r2_score = data.get("r2_score", 0.0)
        self.n_observations = data.get("n_observations", 0)
        self.min_samples = data.get("min_samples", self.min_samples)
        self._training_base_levels = data.get("training_base_levels")


def train_weight_models(
    market_data: pd.DataFrame,
    item_history: pd.DataFrame,
    model_path: Optional[str] = None,
) -> MLRegressor:
    """
    Convenience function to train weight models.

    A regressor that could not be fitted is returned, not raised, with
    ``is_trained`` false and the equal-split weights in place. Callers get a
    usable - and clearly uninformative - answer either way, and the caller's own
    confidence logic turns the 0.0 R² into an LLM-weighted result rather than
    silently treating noise as evidence.
    """
    regressor = MLRegressor()
    try:
        X, y = regressor.prepare_features(market_data, item_history)
        regressor.train(X, y, regressor.COMPONENTS)
    except (InsufficientHistory, ValueError) as error:
        logger.info("ml_model_skipped", reason=str(error))

    if model_path and regressor.is_trained:
        regressor.save_models(model_path)
    return regressor
