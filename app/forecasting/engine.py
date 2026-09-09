"""
Hybrid ARIMA + LSTM demand forecasting engine.

Pipeline (matches the project report's Low-Level Design):
  1. Pull sales history for the item, aggregate to a daily series, fill gaps.
  2. Fit ARIMA on the series to capture trend/seasonal (linear) structure.
  3. Extract ARIMA's residuals and train an LSTM on them to capture the
     non-linear / irregular patterns ARIMA misses.
  4. Combine ARIMA's forecast with the LSTM's residual forecast (weighted
     blend) to produce the final hybrid forecast.

Heavy imports (TensorFlow) are done lazily inside the function so the rest of
the app starts quickly and still works even in environments where TensorFlow
isn't installed (the caller will just get a clear error when forecasting).
"""
import numpy as np
import pandas as pd
from datetime import timedelta

from app.extensions import db
from app.models import SalesHistory

SEQUENCE_LENGTH = 7  # how many past days the LSTM looks at to predict the next residual
MAX_HISTORY_DAYS = 400  # cap how much history we fit on — recent data matters far
                         # more than 5-year-old data for next-2-week forecasts, and
                         # this is what keeps runtime to seconds instead of minutes


def _load_daily_series(item_id):
    rows = (SalesHistory.query
            .filter_by(item_id=item_id)
            .order_by(SalesHistory.sale_date.asc())
            .all())
    if not rows:
        raise ValueError("No sales history available for this item.")

    df = pd.DataFrame([{"date": r.sale_date, "qty": r.quantity_sold} for r in rows])
    df["date"] = pd.to_datetime(df["date"])
    df = df.groupby("date", as_index=True)["qty"].sum()

    full_range = pd.date_range(df.index.min(), df.index.max(), freq="D")
    df = df.reindex(full_range)
    df = df.ffill().fillna(0)
    df.index.name = "date"

    if len(df) > MAX_HISTORY_DAYS:
        df = df.iloc[-MAX_HISTORY_DAYS:]

    return df


def _fit_arima(series):
    from statsmodels.tsa.arima.model import ARIMA
    from statsmodels.tsa.stattools import adfuller

    # Determine differencing order via ADF test (as described in the report)
    d = 0
    test_series = series.copy()
    for _ in range(2):
        try:
            p_value = adfuller(test_series)[1]
        except Exception:
            break
        if p_value < 0.05:
            break
        test_series = test_series.diff().dropna()
        d += 1

    best_model = None
    best_aic = np.inf
    best_order = (1, d, 1)
    for p in range(0, 3):
        for q in range(0, 3):
            try:
                model = ARIMA(series, order=(p, d, q)).fit()
                if model.aic < best_aic:
                    best_aic = model.aic
                    best_model = model
                    best_order = (p, d, q)
            except Exception:
                continue

    if best_model is None:
        # fall back to a safe default
        best_model = ARIMA(series, order=(1, d, 0)).fit()
        best_order = (1, d, 0)

    return best_model, best_order


def _make_sequences(values, seq_len):
    X, y = [], []
    for i in range(len(values) - seq_len):
        X.append(values[i:i + seq_len])
        y.append(values[i + seq_len])
    return np.array(X), np.array(y)


def _fit_lstm_on_residuals(residuals, horizon, epochs=40, patience=6):
    """Trains a small LSTM on ARIMA residuals and forecasts `horizon` steps ahead."""
    from sklearn.preprocessing import MinMaxScaler
    import tensorflow as tf
    from tensorflow.keras.models import Sequential
    from tensorflow.keras.layers import LSTM, Dense, Dropout, Input
    from tensorflow.keras.callbacks import EarlyStopping

    tf.random.set_seed(42)

    values = residuals.values.reshape(-1, 1)
    scaler = MinMaxScaler(feature_range=(-1, 1))
    scaled = scaler.fit_transform(values).flatten()

    seq_len = min(SEQUENCE_LENGTH, max(2, len(scaled) // 4))
    X, y = _make_sequences(scaled, seq_len)
    if len(X) < 10:
        # Not enough data for a meaningful LSTM — return zeros (ARIMA carries the forecast)
        return np.zeros(horizon)

    X = X.reshape((X.shape[0], X.shape[1], 1))

    model = Sequential([
        Input(shape=(seq_len, 1)),
        LSTM(32, return_sequences=True),
        Dropout(0.2),
        LSTM(32),
        Dropout(0.2),
        Dense(1),
    ])
    model.compile(optimizer="adam", loss="mae")
    model.fit(
        X, y,
        epochs=epochs,
        batch_size=32,
        verbose=0,
        callbacks=[EarlyStopping(monitor="loss", patience=patience, restore_best_weights=True)],
    )

    # Iteratively forecast `horizon` steps using a rolling window
    window = list(scaled[-seq_len:])
    preds_scaled = []
    for _ in range(horizon):
        x_input = np.array(window[-seq_len:]).reshape((1, seq_len, 1))
        next_val = model.predict(x_input, verbose=0)[0][0]
        preds_scaled.append(next_val)
        window.append(next_val)

    preds = scaler.inverse_transform(np.array(preds_scaled).reshape(-1, 1)).flatten()
    return preds


def run_hybrid_forecast(item_id, horizon_days=14):
    """
    Runs the full ARIMA + LSTM hybrid pipeline for one inventory item.
    Returns (forecast_rows, metrics, validation_rows) where forecast_rows is a
    list of {"date": date, "demand": float} for the future horizon, metrics has
    rmse/mae from a held-out validation split, and validation_rows is a list of
    {"date": date, "actual": float, "predicted": float} for that same held-out
    slice — this is what the Forecast Accuracy dashboard plots and computes
    MAE/RMSE/MAPE from.
    """
    import time
    t0 = time.time()

    series = _load_daily_series(item_id)
    if len(series) < 20:
        raise ValueError(
            f"Only {len(series)} days of history available; need at least 20."
        )
    print(f"[forecast] item {item_id}: using last {len(series)} days of history")

    # Hold out the last 20% for validation (as described in the report)
    split_idx = max(int(len(series) * 0.8), len(series) - 14)
    train, test = series.iloc[:split_idx], series.iloc[split_idx:]

    print("[forecast] fitting ARIMA on training split...")
    arima_model, order = _fit_arima(train)
    residuals = arima_model.resid
    print(f"[forecast] ARIMA order {order} fitted in {time.time()-t0:.1f}s")

    # --- Validation: how well does the hybrid model do on the held-out slice? ---
    rmse, mae = None, None
    validation_rows = []
    if len(test) > 0:
        print("[forecast] training validation LSTM...")
        arima_val_forecast = arima_model.forecast(steps=len(test))
        lstm_val_residual = _fit_lstm_on_residuals(residuals, len(test), epochs=30, patience=5)
        hybrid_val = np.clip(arima_val_forecast.values + lstm_val_residual, a_min=0, a_max=None)
        errors = hybrid_val - test.values
        rmse = float(np.sqrt(np.mean(errors ** 2)))
        mae = float(np.mean(np.abs(errors)))
        validation_rows = [
            {"date": d.date(), "actual": float(a), "predicted": round(float(p), 2)}
            for d, a, p in zip(test.index, test.values, hybrid_val)
        ]
        print(f"[forecast] validation done in {time.time()-t0:.1f}s total (RMSE {rmse:.2f})")

    # --- Refit on the FULL series for the actual future forecast ---
    print("[forecast] fitting ARIMA on full series...")
    full_model, _ = _fit_arima(series)
    full_residuals = full_model.resid
    arima_future = full_model.forecast(steps=horizon_days)
    print("[forecast] training final LSTM...")
    lstm_future_residual = _fit_lstm_on_residuals(full_residuals, horizon_days)

    hybrid_forecast = np.clip(arima_future.values + lstm_future_residual, a_min=0, a_max=None)

    last_date = series.index[-1]
    forecast_rows = [
        {"date": (last_date + timedelta(days=i + 1)).date(), "demand": round(float(v), 2)}
        for i, v in enumerate(hybrid_forecast)
    ]

    metrics = {"rmse": rmse, "mae": mae, "arima_order": order,
               "history_days": len(series)}
    print(f"[forecast] complete in {time.time()-t0:.1f}s total")
    return forecast_rows, metrics, validation_rows
