"""
LSTM Forecast Service
"""
import numpy as np
import pandas as pd
import warnings
from pathlib import Path
from typing import Dict, Optional, Tuple

from src.core.logging import get_logger

logger = get_logger(__name__)


class LSTMForecastService:
    """LSTM-based forecasting service for volatile time series"""

    def __init__(
        self,
        epochs: int = 50,
        batch_size: int = 32,
        lookback: int = 12,
        units: int = 64,
        dropout: float = 0.2,
        learning_rate: float = 0.001,
        verbose: int = 1,
    ):
        self.epochs = epochs
        self.batch_size = batch_size
        self.lookback = lookback
        self.units = units
        self.dropout = dropout
        self.learning_rate = learning_rate
        self.verbose = verbose

        self.model = None
        self.scaler = None
        self.train_data = None
        self.is_fitted = False
        self.history = None

    # ------------------------------------------------------------------ #
    # Data preparation
    # ------------------------------------------------------------------ #
    def prepare_data(self, data: pd.DataFrame, lookback: Optional[int] = None) -> Tuple[np.ndarray, np.ndarray]:
        """
        Build LSTM sequences from a time-series DataFrame.

        Public so that it can be unit-tested without TensorFlow installed.

        Returns:
            (X, y) where X has shape [samples, lookback, 1] and y [samples]
        """
        lb = lookback or self.lookback
        series = self._extract_series(data)

        scaled = self._scale(series.values, fit=True)
        X, y = self._make_sequences(scaled, lb)
        return X, y

    def _extract_series(self, data: pd.DataFrame) -> pd.Series:
        if not isinstance(data, pd.DataFrame):
            raise ValueError("Data must be a DataFrame with 'ds' and 'y' columns")
        if "ds" not in data.columns or "y" not in data.columns:
            raise ValueError("Data must have 'ds' and 'y' columns")

        df = data[["ds", "y"]].copy().sort_values("ds").reset_index(drop=True)
        return df["y"]

    def _scale(self, values: np.ndarray, fit: bool) -> np.ndarray:
        from sklearn.preprocessing import MinMaxScaler

        reshaped = values.reshape(-1, 1)
        if self.scaler is None:
            self.scaler = MinMaxScaler(feature_range=(0, 1))
            return self.scaler.fit_transform(reshaped)
        if fit:
            self.scaler.fit(reshaped)
        return self.scaler.transform(reshaped)

    @staticmethod
    def _make_sequences(scaled: np.ndarray, lookback: int) -> Tuple[np.ndarray, np.ndarray]:
        X, y = [], []
        for i in range(lookback, len(scaled)):
            X.append(scaled[i - lookback:i, 0])
            y.append(scaled[i, 0])

        if not X:
            raise ValueError("Insufficient data: not enough points to build sequences")

        X = np.asarray(X).reshape((len(X), lookback, 1))
        y = np.asarray(y)
        return X, y

    # ------------------------------------------------------------------ #
    # Model
    # ------------------------------------------------------------------ #
    def _build_model(self, input_shape: Tuple[int, int]):
        try:
            from tensorflow.keras.layers import LSTM, Dense, Dropout
            from tensorflow.keras.models import Sequential
            from tensorflow.keras.optimizers import Adam
        except ImportError:
            raise ImportError("TensorFlow not installed. Install with: pip install tensorflow")

        model = Sequential(
            [
                LSTM(
                    self.units,
                    return_sequences=True,
                    input_shape=input_shape,
                    dropout=self.dropout,
                    recurrent_dropout=self.dropout,
                ),
                LSTM(
                    self.units // 2,
                    return_sequences=False,
                    dropout=self.dropout,
                    recurrent_dropout=self.dropout,
                ),
                Dense(self.units // 4, activation="relu"),
                Dropout(self.dropout),
                Dense(1),
            ]
        )

        model.compile(optimizer=Adam(learning_rate=self.learning_rate), loss="mse")
        return model

    def fit(
        self,
        data: pd.DataFrame,
        lookback: Optional[int] = None,
        validation_split: float = 0.2,
        **kwargs,
    ) -> "LSTMForecastService":
        """
        Fit LSTM model to time series data

        Args:
            data: DataFrame with 'ds' (dates) and 'y' (values) columns
            lookback: Number of previous time steps to use
            validation_split: Fraction of data held out for validation
        """
        lb = lookback or self.lookback
        min_samples = lb + 10
        if len(data) < min_samples:
            raise ValueError(
                f"Insufficient data: need at least {min_samples} points, got {len(data)}"
            )

        df = data[["ds", "y"]].copy().sort_values("ds").reset_index(drop=True)
        self.train_data = df
        self.lookback = lb

        X, y = self.prepare_data(df, lb)
        self.model = self._build_model((X.shape[1], X.shape[2]))

        from tensorflow.keras.callbacks import EarlyStopping, ReduceLROnPlateau

        callbacks = [
            EarlyStopping(monitor="val_loss", patience=10, restore_best_weights=True, verbose=0),
            ReduceLROnPlateau(monitor="val_loss", factor=0.5, patience=5, min_lr=1e-6, verbose=0),
        ]

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            self.history = self.model.fit(
                X,
                y,
                epochs=self.epochs,
                batch_size=self.batch_size,
                validation_split=validation_split,
                callbacks=callbacks,
                verbose=self.verbose,
                shuffle=False,
                **kwargs,
            )

        self.is_fitted = True
        logger.info(
            "lstm_model_fitted",
            epochs_trained=len(self.history.history["loss"]),
            final_loss=self.history.history["loss"][-1],
            data_points=len(df),
            lookback=lb,
        )
        return self

    # ------------------------------------------------------------------ #
    # Prediction
    # ------------------------------------------------------------------ #
    def _future_dates(self, periods: int) -> pd.DatetimeIndex:
        last_date = self.train_data["ds"].max()
        freq = pd.infer_freq(self.train_data["ds"])
        if freq is None:
            freq="ME"
        offset = pd.tseries.frequencies.to_offset(freq)
        return pd.date_range(start=last_date + offset, periods=periods, freq=freq)

    def predict(self, periods: int = 12, confidence: float = 0.8) -> pd.DataFrame:
        """
        Generate forecast

        Args:
            periods: Number of periods to forecast
            confidence: Confidence level for the interval band

        Returns:
            DataFrame with ds, yhat, yhat_lower, yhat_upper
        """
        if not self.is_fitted:
            raise ValueError("Model must be fitted before prediction")

        last_sequence = self.scaler.transform(
            self.train_data["y"].values[-self.lookback:].reshape(-1, 1)
        )

        # Deterministic pass
        predictions: list = []
        current = last_sequence.copy()
        for _ in range(periods):
            pred_scaled = self.model.predict(current.reshape((1, self.lookback, 1)), verbose=0)
            predictions.append(float(self.scaler.inverse_transform(pred_scaled)[0, 0]))
            current = np.roll(current, -1)
            current[-1] = pred_scaled[0, 0]

        # Monte-Carlo dropout passes for uncertainty band
        lower, upper = self._mc_dropout_intervals(last_sequence, periods, confidence)

        result = pd.DataFrame(
            {
                "ds": self._future_dates(periods),
                "yhat": predictions,
                "yhat_lower": lower,
                "yhat_upper": upper,
            }
        )
        logger.info("lstm_forecast_generated", periods=periods, confidence=confidence)
        return result

    def _mc_dropout_intervals(
        self, last_sequence: np.ndarray, periods: int, confidence: float, n_samples: int = 100
    ) -> Tuple[np.ndarray, np.ndarray]:
        """Estimate prediction bands with MC dropout (falls back to a normal band)."""
        try:
            mc = []
            for _ in range(n_samples):
                preds = []
                seq = last_sequence.copy()
                for _ in range(periods):
                    out = self.model(seq.reshape((1, self.lookback, 1)), training=True)
                    val = float(self.scaler.inverse_transform(out.numpy())[0, 0])
                    preds.append(val)
                    seq = np.roll(seq, -1)
                    seq[-1] = out.numpy()[0, 0]
                mc.append(preds)
        except Exception as exc:  # pragma: no cover - defensive fallback
            logger.warning("mc_dropout_failed", error=str(exc))
            mc = None

        if mc is None:
            point = self.scaler.inverse_transform(
                self.model.predict(
                    np.tile(last_sequence, (periods, 1, 1)), verbose=0
                )
            ).reshape(-1)
            spread = float(np.std(point)) or 1.0
            z = 1.2816  # 80% two-sided
            return point - z * spread, point + z * spread

        mc_arr = np.asarray(mc)
        alpha = (1 - confidence) / 2 * 100
        return (
            np.percentile(mc_arr, alpha, axis=0),
            np.percentile(mc_arr, 100 - alpha, axis=0),
        )

    # ------------------------------------------------------------------ #
    # Diagnostics / persistence
    # ------------------------------------------------------------------ #
    def get_training_history(self) -> Dict:
        if not self.is_fitted or self.history is None:
            return {}
        return dict(self.history.history)

    def get_diagnostics(self) -> Dict:
        if not self.is_fitted:
            return {}
        return {
            "lookback": self.lookback,
            "units": self.units,
            "epochs_trained": len(self.history.history["loss"]) if self.history else 0,
            "final_loss": self.history.history["loss"][-1] if self.history else None,
            "final_val_loss": self.history.history.get("val_loss", [None])[-1]
            if self.history
            else None,
            "train_size": len(self.train_data),
        }

    def save(self, path: str) -> None:
        """Persist the Keras model plus scaler/config sidecar."""
        import joblib

        if not self.is_fitted:
            raise ValueError("Cannot save unfitted model")

        model_path = Path(path).with_suffix(".keras")
        self.model.save(str(model_path))
        joblib.dump(
            {
                "scaler": self.scaler,
                "lookback": self.lookback,
                "units": self.units,
                "dropout": self.dropout,
                "learning_rate": self.learning_rate,
                "train_data": self.train_data,
            },
            str(Path(path).with_suffix(".pkl")),
        )
        logger.info("lstm_model_saved", path=str(model_path))

    def load(self, path: str) -> "LSTMForecastService":
        import joblib

        try:
            import tensorflow as tf
        except ImportError:
            raise ImportError("TensorFlow not installed")

        self.model = tf.keras.models.load_model(str(Path(path).with_suffix(".keras")))
        config = joblib.load(str(Path(path).with_suffix(".pkl")))
        self.scaler = config["scaler"]
        self.lookback = config["lookback"]
        self.units = config["units"]
        self.dropout = config["dropout"]
        self.learning_rate = config["learning_rate"]
        self.train_data = config["train_data"]
        self.is_fitted = True
        logger.info("lstm_model_loaded", path=path)
        return self
