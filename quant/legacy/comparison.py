from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Any, Callable

import pandas as pd

from ..qlib_dataset import PreparedQlibDataset


@dataclass(frozen=True)
class LegacyQlibComparison:
    rows: pd.DataFrame
    feature_columns: tuple[str, ...]
    train_index: pd.MultiIndex
    test_index: pd.MultiIndex


def compare_legacy_lightgbm_on_qlib_dataset(
    prepared: PreparedQlibDataset,
    qlib_prediction: pd.Series,
    *,
    estimator_factory: Callable[..., Any] | None = None,
) -> LegacyQlibComparison:
    """Explicit rollback comparison on the exact purged Qlib train and test indices."""
    datetimes = prepared.frame.index.get_level_values("datetime")
    train_start, train_end = (pd.Timestamp(value) for value in prepared.segments["train"])
    test_start, test_end = (pd.Timestamp(value) for value in prepared.segments["test"])
    train = prepared.frame.loc[(datetimes >= train_start) & (datetimes <= train_end)]
    test = prepared.frame.loc[(datetimes >= test_start) & (datetimes <= test_end)]
    x_train = train.loc[:, "feature"].apply(pd.to_numeric, errors="coerce")
    y_train = pd.to_numeric(train[("label", "LABEL0")], errors="coerce")
    usable = y_train.map(lambda value: pd.notna(value) and isfinite(float(value)))
    if usable.sum() == 0 or y_train.loc[usable].nunique() < 2:
        raise ValueError("legacy comparison requires finite, non-constant purged train labels")
    if estimator_factory is None:
        try:
            from lightgbm import LGBMRegressor
        except (ImportError, OSError) as exc:
            raise RuntimeError("LightGBM is required for explicit legacy comparison") from exc
        estimator_factory = LGBMRegressor
    estimator = estimator_factory(
        n_estimators=500,
        learning_rate=0.05,
        num_leaves=31,
        random_state=7,
        deterministic=True,
        force_col_wise=True,
        verbosity=-1,
    )
    estimator.fit(x_train.loc[usable], y_train.loc[usable])
    legacy = pd.Series(estimator.predict(test.loc[:, "feature"]), index=test.index, name="legacy_score")
    qlib = qlib_prediction.reindex(test.index).rename("qlib_score")
    if qlib.isna().any():
        raise ValueError("Qlib prediction index does not match the exact comparison test index")
    rows = pd.concat([qlib, legacy, test[("label", "LABEL0")].rename("label")], axis=1)
    return LegacyQlibComparison(rows, prepared.feature_columns, train.index, test.index)
