from __future__ import annotations

from datetime import date
from math import isfinite

from ..types import FeatureSnapshot, PredictionRow, PredictionSnapshot


class ModelTrainer:
    """Legacy direct LightGBM entry point; never used as a Qlib failure fallback."""

    def fit_predict(
        self,
        features: FeatureSnapshot,
        prediction_dates: list[date],
        labels: dict[tuple[date, str], float] | None = None,
    ) -> PredictionSnapshot:
        predictions: list[PredictionRow] = []
        train_ends: list[date] = []
        reason = "missing_current_features"
        plans = []
        for prediction_date in prediction_dates:
            current = [row for row in features.rows if row.as_of_date == prediction_date]
            if not current:
                continue
            all_historical = [row for row in features.rows if row.as_of_date < prediction_date]
            if not all_historical:
                reason = "missing_training_features"
                continue
            if labels is None:
                reason = "missing_labels"
                continue
            historical = [row for row in all_historical if (row.as_of_date, row.ts_code) in labels]
            if not historical:
                reason = "missing_labels"
                continue
            if len(historical) < 4:
                reason = "insufficient_training_samples"
                continue
            plans.append((prediction_date, current, historical))
        if not plans:
            return PredictionSnapshot([], self._metadata(reason, prediction_dates))
        try:
            import lightgbm as lgb
            import pandas as pd
        except (ImportError, OSError):
            return PredictionSnapshot([], self._metadata("lightgbm_unavailable", prediction_dates))

        for prediction_date, current, historical in plans:
            names = sorted({name for row in historical for name in row.values})
            x_train = pd.DataFrame([{name: row.values.get(name) for name in names} for row in historical])
            y_train = pd.Series([labels[(row.as_of_date, row.ts_code)] for row in historical])
            finite = y_train.map(lambda value: isfinite(float(value)))
            x_train, y_train = x_train.loc[finite], y_train.loc[finite]
            if y_train.nunique() < 2:
                reason = "train_labels_not_distinct"
                continue
            model = lgb.LGBMRegressor(
                n_estimators=500,
                learning_rate=0.05,
                num_leaves=31,
                random_state=7,
                deterministic=True,
                force_col_wise=True,
                verbosity=-1,
            )
            model.fit(x_train, y_train)
            x_current = pd.DataFrame([{name: row.values.get(name) for name in names} for row in current])
            for row, score in zip(current, model.predict(x_current)):
                predictions.append(PredictionRow(prediction_date, row.ts_code, float(score)))
            train_ends.append(max(row.as_of_date for row in historical))
        status = "completed" if predictions else "not_trainable"
        return PredictionSnapshot(predictions, {
            "model_version": "legacy-lightgbm-v1",
            "engine": "legacy.lightgbm.LGBMRegressor",
            "train_end": max(train_ends) if train_ends else date.min,
            "prediction_dates": prediction_dates,
            "time_isolated": True,
            "status": status,
            "status_reason": None if predictions else reason,
        })

    @staticmethod
    def _metadata(reason: str, prediction_dates: list[date]) -> dict:
        return {
            "model_version": "legacy-lightgbm-v1",
            "engine": "legacy.lightgbm.LGBMRegressor",
            "train_end": date.min,
            "prediction_dates": prediction_dates,
            "time_isolated": True,
            "status": "not_trainable",
            "status_reason": reason,
        }
