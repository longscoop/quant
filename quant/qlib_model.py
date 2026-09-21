from __future__ import annotations

from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from datetime import date
import fcntl
from math import isfinite
from pathlib import Path
import threading
from typing import Any, Callable

import pandas as pd

from .markets import InstrumentMapper, ResearchContext
from .qlib_dataset import PreparedQlibDataset, QlibDependencyError
from .types import MarketPredictionRow


DEFAULT_LGB_PARAMS: dict[str, Any] = {
    "loss": "mse",
    "num_boost_round": 500,
    "early_stopping_rounds": 50,
    "learning_rate": 0.05,
    "num_leaves": 31,
    "seed": 7,
    "deterministic": True,
    "force_col_wise": True,
}


class QlibRuntime:
    """Serialize process-global Qlib state and keep artifacts under one root."""

    _thread_lock = threading.RLock()
    _active_fingerprint: tuple[str, str | None, str] | None = None

    @classmethod
    def invalidate_global_state(cls) -> None:
        """Mark Qlib state unknown after a provider is initialized externally."""
        with cls._thread_lock:
            cls._active_fingerprint = None

    def __init__(
        self,
        artifact_root: str | Path,
        provider_uri: str | Path | None = None,
        qlib_region: str | None = None,
        *,
        handler_factory: Callable[[pd.DataFrame], Any] | None = None,
        dataset_factory: Callable[..., Any] | None = None,
        model_factory: Callable[..., Any] | None = None,
        record_bindings: Any | None = None,
    ):
        self.artifact_root = Path(artifact_root).expanduser().resolve()
        self.provider_uri = Path(provider_uri).expanduser().resolve() if provider_uri is not None else None
        self.qlib_region = qlib_region
        self.handler_factory = handler_factory
        self.dataset_factory = dataset_factory
        self.model_factory = model_factory
        self.record_bindings = record_bindings
        self._initialized = False

    def initialize(self) -> None:
        if not self.qlib_region:
            raise ValueError("QlibRuntime.qlib_region must be provided by MarketConfig")
        fingerprint = (
            str(self.artifact_root),
            str(self.provider_uri) if self.provider_uri is not None else None,
            self.qlib_region,
        )
        if self._initialized and self.__class__._active_fingerprint == fingerprint:
            return
        try:
            import qlib
        except (ImportError, OSError) as exc:
            raise QlibDependencyError("pyqlib==0.9.7 is required for the MODEL workflow") from exc
        self.artifact_root.mkdir(parents=True, exist_ok=True)
        mlflow_root = self.artifact_root / "mlruns"
        mlflow_root.mkdir(parents=True, exist_ok=True)
        with self._thread_lock:
            if self._initialized and self.__class__._active_fingerprint == fingerprint:
                return
            init_kwargs: dict[str, Any] = {
                "region": self.qlib_region,
                "exp_manager": {
                    "class": "MLflowExpManager",
                    "module_path": "qlib.workflow.expm",
                    "kwargs": {
                        "uri": f"file:{mlflow_root}",
                        "default_exp_name": "Experiment",
                    },
                },
            }
            if self.provider_uri is not None:
                init_kwargs["provider_uri"] = str(self.provider_uri)
            qlib.init(**init_kwargs)
            self._initialized = True
            self.__class__._active_fingerprint = fingerprint

    @contextmanager
    def serialized(self):
        self.artifact_root.mkdir(parents=True, exist_ok=True)
        lock_path = self.artifact_root / ".runtime.lock"
        with self._thread_lock, lock_path.open("a+") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@dataclass(frozen=True)
class QlibModelResult:
    status: str
    predictions: list[MarketPredictionRow]
    metadata: dict[str, Any]
    model: Any | None = None
    raw_prediction: pd.Series | pd.DataFrame | None = None
    status_reason: str | None = None


class QlibModelEngine:
    def __init__(
        self,
        *,
        runtime: QlibRuntime,
        context: ResearchContext,
        mapper: InstrumentMapper,
        model_factory: Callable[..., Any] | None = None,
        model_params: dict[str, Any] | None = None,
    ):
        self.runtime = runtime
        self.context = context
        self.mapper = mapper
        self.model_factory = model_factory or runtime.model_factory
        self.model_params = {**DEFAULT_LGB_PARAMS, **(model_params or {})}

    @staticmethod
    def _segment(frame: pd.DataFrame, segment: tuple[date, date]) -> pd.DataFrame:
        start, end = (pd.Timestamp(value) for value in segment)
        datetimes = frame.index.get_level_values("datetime")
        return frame.loc[(datetimes >= start) & (datetimes <= end)]

    def _trainability_reason(self, prepared: PreparedQlibDataset) -> str | None:
        train = self._segment(prepared.frame, prepared.segments["train"])
        if train.empty:
            return "empty_train_segment"
        features = train.loc[:, "feature"].apply(pd.to_numeric, errors="coerce")
        labels = pd.to_numeric(train[("label", "LABEL0")], errors="coerce")
        finite_feature_rows = features.apply(lambda row: any(isfinite(float(value)) for value in row.dropna()), axis=1)
        finite_labels = labels.map(lambda value: pd.notna(value) and isfinite(float(value)))
        usable_labels = labels.loc[finite_feature_rows & finite_labels]
        if usable_labels.empty:
            return "train_has_no_finite_samples"
        if usable_labels.nunique(dropna=True) < 2:
            return "train_labels_not_distinct"
        return None

    def _new_model(self):
        if self.model_factory is not None:
            return self.model_factory(**self.model_params)
        self.runtime.initialize()
        try:
            from qlib.contrib.model.gbdt import LGBModel
        except (ImportError, OSError) as exc:
            raise QlibDependencyError("qlib.contrib.model.gbdt.LGBModel is unavailable") from exc
        return LGBModel(**self.model_params)

    def fit_predict(
        self,
        prepared: PreparedQlibDataset,
        *,
        model_version: str = "qlib-lgb-v1",
        serialize: bool = True,
    ) -> QlibModelResult:
        reason = self._trainability_reason(prepared)
        metadata = {
            "engine": "qlib.contrib.model.gbdt.LGBModel",
            "model_version": model_version,
            "context": self.context.to_dict(),
            "feature_columns": list(prepared.feature_columns),
            "segments": {
                name: {"start": bounds[0].isoformat(), "end": bounds[1].isoformat()}
                for name, bounds in prepared.segments.items()
            },
            "model_params": dict(self.model_params),
            "dataset_audit": prepared.audit,
        }
        if reason is not None:
            return QlibModelResult("not_trainable", [], metadata, status_reason=reason)

        with (self.runtime.serialized() if serialize else nullcontext()):
            model = self._new_model()
            model.fit(prepared.dataset)
            raw_prediction = model.predict(prepared.dataset, segment="test")
        return self._prediction_result(model, raw_prediction, metadata, model_version)

    def predict(
        self,
        model: Any,
        prepared: PreparedQlibDataset,
        *,
        segment: str = "inference",
        model_version: str = "qlib-lgb-v1",
    ) -> QlibModelResult:
        metadata = {
            "engine": "qlib.contrib.model.gbdt.LGBModel",
            "model_version": model_version,
            "context": self.context.to_dict(),
            "feature_columns": list(prepared.feature_columns),
            "segments": {
                name: {"start": bounds[0].isoformat(), "end": bounds[1].isoformat()}
                for name, bounds in prepared.segments.items()
            },
            "mode": "inference",
        }
        with self.runtime.serialized():
            raw_prediction = model.predict(prepared.dataset, segment=segment)
        return self._prediction_result(model, raw_prediction, metadata, model_version)

    def _prediction_result(
        self,
        model: Any,
        raw_prediction: pd.Series | pd.DataFrame,
        metadata: dict[str, Any],
        model_version: str,
    ) -> QlibModelResult:
        series = raw_prediction.iloc[:, 0] if isinstance(raw_prediction, pd.DataFrame) else raw_prediction
        if not isinstance(series, pd.Series) or not isinstance(series.index, pd.MultiIndex):
            raise ValueError("Qlib prediction must be a Series/DataFrame indexed by (datetime, instrument)")
        predictions = [
            MarketPredictionRow(
                trade_date=pd.Timestamp(index[0]).date(),
                canonical_instrument_id=self.mapper.normalize(str(index[1]), source="qlib"),
                score=float(score),
                model_version=model_version,
                feature_snapshot_date=pd.Timestamp(index[0]).date(),
                market_id=self.context.market_id,
                currency=self.context.currency,
            )
            for index, score in series.items()
            if pd.notna(score) and isfinite(float(score))
        ]
        metadata["prediction_row_count"] = len(predictions)
        return QlibModelResult(
            "completed" if predictions else "not_trainable",
            predictions,
            metadata,
            model=model,
            raw_prediction=series,
            status_reason=None if predictions else "empty_test_prediction",
        )
