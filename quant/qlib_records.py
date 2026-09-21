from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Any, Callable

from .markets import ResearchContext
from .qlib_dataset import QlibDependencyError
from .qlib_model import QlibRuntime


@dataclass(frozen=True)
class QlibRecordBindings:
    start: Callable[..., Any]
    get_recorder: Callable[..., Any]
    signal_record_factory: Callable[..., Any]
    signal_analysis_factory: Callable[..., Any]


@dataclass(frozen=True)
class QlibRecordResult:
    recorder_id: str
    artifact_paths: tuple[str, ...]
    metrics: dict[str, float | None]
    context: dict[str, str]
    series: dict[str, list[dict[str, Any]]]
    effective_sample_count: int
    status_reason: str | None = None


class QlibRecordService:
    ARTIFACT_PATHS = (
        "trained_model",
        "pred.pkl",
        "label.pkl",
        "sig_analysis/ic.pkl",
        "sig_analysis/ric.pkl",
    )

    def __init__(self, runtime: QlibRuntime, *, bindings: QlibRecordBindings | None = None):
        self.runtime = runtime
        self.bindings = bindings

    def _bindings(self) -> QlibRecordBindings:
        if self.bindings is not None:
            return self.bindings
        self.runtime.initialize()
        try:
            from qlib.workflow import R
            from qlib.workflow.record_temp import SigAnaRecord, SignalRecord
        except (ImportError, OSError) as exc:
            raise QlibDependencyError("Qlib Recorder templates are unavailable") from exc
        return QlibRecordBindings(
            start=R.start,
            get_recorder=R.get_recorder,
            signal_record_factory=SignalRecord,
            signal_analysis_factory=SigAnaRecord,
        )

    @staticmethod
    def _metric(metrics: dict, *keys: str) -> float | None:
        for key in keys:
            value = metrics.get(key)
            if value is not None:
                try:
                    number = float(value)
                except (TypeError, ValueError):
                    continue
                return number if isfinite(number) else None
        return None

    def generate(
        self,
        *,
        model,
        dataset,
        context: ResearchContext,
        experiment_name: str,
        recorder_name: str,
    ) -> QlibRecordResult:
        bindings = self._bindings()
        with self.runtime.serialized():
            with bindings.start(experiment_name=experiment_name, recorder_name=recorder_name):
                recorder = bindings.get_recorder()
                recorder.save_objects(trained_model=model)
                bindings.signal_record_factory(model, dataset, recorder).generate()
                bindings.signal_analysis_factory(recorder).generate()
                raw_metrics = recorder.list_metrics() if hasattr(recorder, "list_metrics") else {}
                series = self._load_analysis_series(recorder)
                recorder_id = str(getattr(recorder, "id", None) or getattr(getattr(recorder, "info", None), "id", ""))
        return self._result(recorder_id, raw_metrics, series, context)

    def fit_and_generate(
        self,
        *,
        engine,
        prepared,
        context: ResearchContext,
        experiment_name: str,
        recorder_name: str,
        model_version: str = "qlib-lgb-v1",
    ):
        reason = engine._trainability_reason(prepared)
        if reason is not None:
            return engine.fit_predict(prepared, model_version=model_version), None
        bindings = self._bindings()
        with self.runtime.serialized():
            with bindings.start(experiment_name=experiment_name, recorder_name=recorder_name):
                recorder = bindings.get_recorder()
                model_result = engine.fit_predict(
                    prepared,
                    model_version=model_version,
                    serialize=False,
                )
                recorder.save_objects(trained_model=model_result.model)
                bindings.signal_record_factory(model_result.model, prepared.dataset, recorder).generate()
                bindings.signal_analysis_factory(recorder).generate()
                raw_metrics = recorder.list_metrics() if hasattr(recorder, "list_metrics") else {}
                series = self._load_analysis_series(recorder)
                recorder_id = str(getattr(recorder, "id", None) or getattr(getattr(recorder, "info", None), "id", ""))
        return model_result, self._result(recorder_id, raw_metrics, series, context)

    def _result(self, recorder_id: str, raw_metrics: dict, series: dict, context: ResearchContext) -> QlibRecordResult:
        if not recorder_id:
            raise ValueError("Qlib Recorder did not expose a recorder ID")
        metrics = {
            "ic": self._metric(raw_metrics, "IC", "ic"),
            "icir": self._metric(raw_metrics, "ICIR", "icir"),
            "rank_ic": self._metric(raw_metrics, "Rank IC", "RankIC", "rank_ic"),
            "rank_icir": self._metric(raw_metrics, "Rank ICIR", "RankICIR", "rank_icir"),
        }
        reason = "signal_metrics_unavailable" if all(value is None for value in metrics.values()) else None
        return QlibRecordResult(
            recorder_id=recorder_id,
            artifact_paths=self.ARTIFACT_PATHS,
            metrics=metrics,
            context=context.to_dict(),
            series=series,
            effective_sample_count=max((len(values) for values in series.values()), default=0),
            status_reason=reason,
        )

    @staticmethod
    def _load_analysis_series(recorder) -> dict[str, list[dict[str, Any]]]:
        output: dict[str, list[dict[str, Any]]] = {"ic": [], "rank_ic": []}
        if not hasattr(recorder, "load_object"):
            return output
        for name, artifact in (("ic", "sig_analysis/ic.pkl"), ("rank_ic", "sig_analysis/ric.pkl")):
            try:
                values = recorder.load_object(artifact)
            except Exception as exc:
                if isinstance(exc, (FileNotFoundError, KeyError, OSError)) or exc.__class__.__name__ == "LoadObjectError":
                    continue
                raise
            if not hasattr(values, "items"):
                continue
            output[name] = [
                {"date": str(index), "value": float(value)}
                for index, value in values.items()
                if value is not None and isfinite(float(value))
            ]
        return output

    def load_model(self, *, recorder_id: str, experiment_name: str):
        if not recorder_id or not experiment_name:
            raise ValueError("recorder_id and experiment_name are required to load a Qlib model")
        bindings = self._bindings()
        with self.runtime.serialized():
            recorder = bindings.get_recorder(
                recorder_id=recorder_id,
                experiment_name=experiment_name,
            )
            return recorder.load_object("trained_model")
