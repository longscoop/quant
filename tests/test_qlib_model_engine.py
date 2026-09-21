from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from types import ModuleType
import unittest
from contextlib import nullcontext
from unittest.mock import Mock, patch

import pandas as pd

from quant.markets import DateSegment, MarketConfig, ResearchContext, StaticMarketCalendar, TimeSplitConfig
from quant.markets.cn import CnInstrumentMapper
from quant.legacy import compare_legacy_lightgbm_on_qlib_dataset
from quant.qlib_dataset import PreparedQlibDataset
from quant.qlib_model import QlibModelEngine, QlibRuntime
from quant.qlib_records import QlibRecordBindings
from quant.storage import InMemoryStore
from quant.types import BenchmarkBar, PriceBar
from quant.workflows import run_model_inference, train_model_run


class _SpyLGBModel:
    def __init__(self, **kwargs):
        self.params = kwargs
        self.fit_dataset = None
        self.predict_call = None

    def fit(self, dataset):
        self.fit_dataset = dataset

    def predict(self, dataset, segment="test"):
        self.predict_call = (dataset, segment)
        return pd.Series(
            [0.7, 0.3],
            index=pd.MultiIndex.from_tuples(
                [(pd.Timestamp("2024-04-01"), "SZ000001"), (pd.Timestamp("2024-04-01"), "SH600000")],
                names=["datetime", "instrument"],
            ),
        )


class _Record:
    def __init__(self, args):
        self.args = args

    def generate(self):
        return None


class QlibModelEngineTest(unittest.TestCase):
    def setUp(self):
        self.context = ResearchContext("CN", "CNY", "CN_A_SHARE", "000300.SH", "000300.SH")
        index = pd.MultiIndex.from_tuples(
            [
                (pd.Timestamp("2024-01-02"), "SZ000001"),
                (pd.Timestamp("2024-01-03"), "SZ000001"),
                (pd.Timestamp("2024-02-01"), "SZ000001"),
                (pd.Timestamp("2024-04-01"), "SZ000001"),
                (pd.Timestamp("2024-04-01"), "SH600000"),
            ],
            names=["datetime", "instrument"],
        )
        self.frame = pd.DataFrame(
            {
                ("feature", "q_roe"): [0.1, 0.2, 0.3, 0.4, 0.5],
                ("label", "LABEL0"): [0.01, 0.02, 0.03, 0.04, 0.05],
            },
            index=index,
        )
        self.prepared = PreparedQlibDataset(
            handler="handler",
            dataset="dataset",
            frame=self.frame,
            segments={
                "train": (date(2024, 1, 2), date(2024, 1, 3)),
                "valid": (date(2024, 2, 1), date(2024, 2, 1)),
                "test": (date(2024, 4, 1), date(2024, 4, 1)),
            },
            feature_columns=("q_roe",),
            audit={"split": {}},
        )

    def test_engine_fits_qlib_model_and_predicts_only_test_segment(self):
        with TemporaryDirectory() as directory:
            spy = _SpyLGBModel()
            engine = QlibModelEngine(
                runtime=QlibRuntime(Path(directory)),
                context=self.context,
                mapper=CnInstrumentMapper(),
                model_factory=lambda **kwargs: spy,
            )

            result = engine.fit_predict(self.prepared, model_version="qlib-lgb-v1")

        self.assertEqual(spy.fit_dataset, "dataset")
        self.assertEqual(spy.predict_call, ("dataset", "test"))
        self.assertEqual([row.canonical_instrument_id for row in result.predictions], ["000001.SZ", "600000.SH"])
        self.assertTrue(all(row.market_id == "CN" and row.currency == "CNY" for row in result.predictions))
        self.assertEqual(result.metadata["engine"], "qlib.contrib.model.gbdt.LGBModel")

    def test_engine_refuses_constant_train_labels(self):
        frame = self.frame.copy()
        frame.loc[pd.IndexSlice[pd.Timestamp("2024-01-02"):pd.Timestamp("2024-01-03"), :], ("label", "LABEL0")] = 0.01
        prepared = PreparedQlibDataset(**{**self.prepared.__dict__, "frame": frame})
        with TemporaryDirectory() as directory:
            engine = QlibModelEngine(
                runtime=QlibRuntime(Path(directory)),
                context=self.context,
                mapper=CnInstrumentMapper(),
                model_factory=_SpyLGBModel,
            )
            result = engine.fit_predict(prepared)

        self.assertEqual(result.status, "not_trainable")
        self.assertEqual(result.status_reason, "train_labels_not_distinct")
        self.assertEqual(result.predictions, [])

    def test_default_parameters_are_deterministic_and_use_early_stopping(self):
        with TemporaryDirectory() as directory:
            engine = QlibModelEngine(
                runtime=QlibRuntime(Path(directory)),
                context=self.context,
                mapper=CnInstrumentMapper(),
                model_factory=_SpyLGBModel,
            )
            result = engine.fit_predict(self.prepared)

        self.assertEqual(result.model.params["num_boost_round"], 500)
        self.assertEqual(result.model.params["early_stopping_rounds"], 50)
        self.assertEqual(result.model.params["seed"], 7)
        self.assertTrue(result.model.params["deterministic"])
        self.assertTrue(result.model.params["force_col_wise"])

    def test_runtime_reinitializes_after_another_provider_owns_global_state(self):
        fake_qlib = ModuleType("qlib")
        fake_qlib.init = Mock()
        with TemporaryDirectory() as first, TemporaryDirectory() as second, patch.dict("sys.modules", {"qlib": fake_qlib}):
            QlibRuntime._active_fingerprint = None
            first_runtime = QlibRuntime(first, qlib_region="cn")
            second_runtime = QlibRuntime(second, qlib_region="cn")

            first_runtime.initialize()
            first_runtime.initialize()
            second_runtime.initialize()
            QlibRuntime.invalidate_global_state()
            first_runtime.initialize()

        self.assertEqual(fake_qlib.init.call_count, 3)

    def test_inference_uses_loaded_model_without_labels(self):
        index = pd.MultiIndex.from_tuples(
            [(pd.Timestamp("2024-05-01"), "SZ000001")],
            names=["datetime", "instrument"],
        )
        prepared = PreparedQlibDataset(
            handler="handler",
            dataset="inference-dataset",
            frame=pd.DataFrame({("feature", "q_roe"): [0.4]}, index=index),
            segments={"inference": (date(2024, 5, 1), date(2024, 5, 1))},
            feature_columns=("q_roe",),
            audit={"mode": "inference"},
        )
        model = _SpyLGBModel()
        with TemporaryDirectory() as directory:
            result = QlibModelEngine(
                runtime=QlibRuntime(Path(directory)),
                context=self.context,
                mapper=CnInstrumentMapper(),
                model_factory=_SpyLGBModel,
            ).predict(model, prepared)

        self.assertEqual(model.predict_call, ("inference-dataset", "inference"))
        self.assertEqual(result.metadata["mode"], "inference")

    def test_legacy_comparison_uses_exact_purged_train_and_test_indices(self):
        class Estimator:
            def __init__(self, **kwargs):
                self.params = kwargs

            def fit(self, features, labels):
                self.fit_index = features.index

            def predict(self, features):
                return [0.25] * len(features)

        qlib_prediction = pd.Series(
            [0.7, 0.3],
            index=self.frame.loc[pd.IndexSlice[pd.Timestamp("2024-04-01"), :], :].index,
        )
        comparison = compare_legacy_lightgbm_on_qlib_dataset(
            self.prepared,
            qlib_prediction,
            estimator_factory=Estimator,
        )

        self.assertEqual(comparison.train_index.tolist(), self.frame.index[:2].tolist())
        self.assertEqual(comparison.test_index.tolist(), self.frame.index[-2:].tolist())
        self.assertEqual(comparison.feature_columns, ("q_roe",))


class QlibModelWorkflowTest(unittest.TestCase):
    def test_production_workflow_persists_prediction_v2_from_qlib_engine(self):
        sessions = [date(2024, 1, 2) + timedelta(days=offset) for offset in range(120)]
        context = ResearchContext("CN", "CNY", "CN_A_SHARE", "000300.SH", "000300.SH")
        calendar = StaticMarketCalendar("CN_A_SHARE", sessions)
        config = MarketConfig(
            market_id="CN",
            default_context=context,
            instrument_mapper=CnInstrumentMapper(),
            calendar=calendar,
        )

        class Store(InMemoryStore):
            def get_run(self, run_id):
                self.asserted_run_id = run_id
                rows = [
                    {"as_of_date": sessions[offset], "ts_code": code, "values": {"q_roe": value}}
                    for offset, value in ((0, 0.1), (1, 0.2), (50, 0.3), (90, 0.4))
                    for code in ("000001.SZ", "600000.SH")
                ]
                return {"run_type": "factors", "status": "completed", "payload": {"rows": rows, "metadata": {}}}

            def load_memory(self):
                return self

            def record_run(self, run_type, status, parameters, payload=None, error=None):
                self.saved_run = {"run_type": run_type, "status": status, "parameters": parameters, "payload": payload or {}, "error": error}
                return "model-1"

        store = Store()
        for code, multiplier in (("000001.SZ", 1.0), ("600000.SH", 1.1)):
            for offset in (0, 1, 20, 21, 50, 70, 90, 110):
                store.prices[(code, sessions[offset])] = PriceBar(code, sessions[offset], (10 + offset / 10) * multiplier)
        for offset in (0, 1, 20, 21, 50, 70, 90, 110):
            store.benchmarks[("000300.SH", sessions[offset])] = BenchmarkBar("000300.SH", sessions[offset], 100 + offset / 10)

        class Dataset:
            def __init__(self, *, handler, segments):
                self.handler = handler
                self.segments = segments

        class Model(_SpyLGBModel):
            def predict(self, dataset, segment="test"):
                start, end = (pd.Timestamp(value) for value in dataset.segments[segment])
                dates = dataset.handler.index.get_level_values("datetime")
                test = dataset.handler.loc[(dates >= start) & (dates <= end)]
                return test[("feature", "q_roe")]

        class Recorder:
            id = "recorder-workflow"
            def save_objects(self, **kwargs):
                self.saved = kwargs
            def list_metrics(self):
                return {"IC": 0.1, "Rank IC": 0.2}

        recorder = Recorder()
        record_bindings = QlibRecordBindings(
            start=lambda **kwargs: nullcontext(),
            get_recorder=lambda: recorder,
            signal_record_factory=lambda *args: _Record(args),
            signal_analysis_factory=lambda *args: _Record(args),
        )

        split = TimeSplitConfig(
            calendar_id="CN_A_SHARE",
            train=DateSegment(sessions[0], sessions[39]),
            valid=DateSegment(sessions[50], sessions[79]),
            test=DateSegment(sessions[90], sessions[119]),
        )
        with TemporaryDirectory() as directory:
            runtime = QlibRuntime(
                directory,
                handler_factory=lambda frame: frame,
                dataset_factory=Dataset,
                model_factory=Model,
                record_bindings=record_bindings,
            )
            run_id = train_model_run(store, "factor-1", context, split, market_config=config, runtime=runtime)

        self.assertEqual(run_id, "model-1")
        self.assertEqual(store.saved_run["status"], "completed")
        self.assertEqual(store.saved_run["payload"]["metadata"]["engine"], "qlib.contrib.model.gbdt.LGBModel")
        self.assertEqual(store.saved_run["payload"]["metadata"]["recorder_id"], "recorder-workflow")
        self.assertEqual(len(store.saved_run["payload"]["rows"]), 2)
        self.assertEqual(
            set(store.saved_run["payload"]["rows"][0]),
            {"trade_date", "canonical_instrument_id", "score", "model_version", "feature_snapshot_date", "market_id", "currency", "created_at"},
        )

    def test_inference_loads_recorded_model_and_accepts_unlabeled_features(self):
        context = ResearchContext("CN", "CNY", "CN_A_SHARE", "000300.SH", "000300.SH")
        inference_day = date(2024, 6, 3)
        config = MarketConfig(
            market_id="CN",
            default_context=context,
            instrument_mapper=CnInstrumentMapper(),
        )

        class Model:
            def predict(self, dataset, segment="inference"):
                return dataset.handler[("feature", "q_roe")].rename("score")

        class Recorder:
            def load_object(self, name):
                self.loaded = name
                return Model()

        recorder = Recorder()

        class Dataset:
            def __init__(self, *, handler, segments):
                self.handler = handler
                self.segments = segments

        class Store(InMemoryStore):
            def get_run(self, run_id):
                if run_id == "model-1":
                    return {
                        "run_type": "model",
                        "status": "completed",
                        "payload": {"metadata": {
                            "engine": "qlib.contrib.model.gbdt.LGBModel",
                            "context": context.to_dict(),
                            "feature_columns": ["q_roe"],
                            "model_version": "qlib-lgb-v1",
                            "recorder_id": "rec-1",
                            "recorder_experiment_name": "model-cn",
                        }},
                    }
                return {
                    "run_type": "factors",
                    "status": "completed",
                    "payload": {
                        "rows": [{
                            "as_of_date": inference_day,
                            "ts_code": "000001.SZ",
                            "values": {"q_roe": 0.42},
                        }],
                        "metadata": {},
                    },
                }

            def record_run(self, run_type, status, parameters, payload=None, error=None):
                self.saved_run = {"run_type": run_type, "status": status, "parameters": parameters, "payload": payload or {}, "error": error}
                return "inference-1"

        store = Store()
        bindings = QlibRecordBindings(
            start=lambda **kwargs: nullcontext(),
            get_recorder=lambda **kwargs: recorder,
            signal_record_factory=lambda *args: _Record(args),
            signal_analysis_factory=lambda *args: _Record(args),
        )
        with TemporaryDirectory() as directory:
            runtime = QlibRuntime(
                directory,
                handler_factory=lambda frame: frame,
                dataset_factory=Dataset,
                record_bindings=bindings,
            )
            run_id = run_model_inference(
                store,
                "model-1",
                "factor-2",
                [inference_day],
                context,
                market_config=config,
                runtime=runtime,
            )

        self.assertEqual(run_id, "inference-1")
        self.assertEqual(recorder.loaded, "trained_model")
        self.assertEqual(store.saved_run["status"], "completed")
        self.assertEqual(store.saved_run["payload"]["rows"][0]["trade_date"], inference_day)


if __name__ == "__main__":
    unittest.main()
