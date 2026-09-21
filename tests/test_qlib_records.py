from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from quant.markets import ResearchContext
from quant.qlib_model import QlibRuntime
from quant.qlib_records import QlibRecordBindings, QlibRecordService


class _Recorder:
    id = "recorder-123"

    def __init__(self):
        self.saved = {}

    def save_objects(self, **kwargs):
        self.saved.update(kwargs)

    def list_metrics(self):
        return {"IC": 0.12, "ICIR": 0.8, "Rank IC": 0.15, "Rank ICIR": 0.9}

    def load_object(self, name):
        return self.saved[name]


class _RecordTemplate:
    def __init__(self, *args):
        self.args = args
        self.generated = False

    def generate(self):
        self.generated = True


class QlibRecordServiceTest(unittest.TestCase):
    def test_generates_signal_and_analysis_records_with_context_metadata(self):
        recorder = _Recorder()
        created = {"signal": [], "analysis": []}

        def signal_factory(*args):
            record = _RecordTemplate(*args)
            created["signal"].append(record)
            return record

        def analysis_factory(*args):
            record = _RecordTemplate(*args)
            created["analysis"].append(record)
            return record

        bindings = QlibRecordBindings(
            start=lambda **kwargs: nullcontext(),
            get_recorder=lambda: recorder,
            signal_record_factory=signal_factory,
            signal_analysis_factory=analysis_factory,
        )
        context = ResearchContext("CN", "CNY", "CN_A_SHARE", "000300.SH", "000300.SH")
        with TemporaryDirectory() as directory:
            service = QlibRecordService(QlibRuntime(Path(directory)), bindings=bindings)
            result = service.generate(
                model="model",
                dataset="dataset",
                context=context,
                experiment_name="cn-model",
                recorder_name="run-1",
            )

        self.assertTrue(created["signal"][0].generated)
        self.assertTrue(created["analysis"][0].generated)
        self.assertEqual(recorder.saved["trained_model"], "model")
        self.assertEqual(result.recorder_id, "recorder-123")
        self.assertEqual(result.metrics, {"ic": 0.12, "icir": 0.8, "rank_ic": 0.15, "rank_icir": 0.9})
        self.assertEqual(result.context, context.to_dict())
        self.assertEqual(result.series, {"ic": [], "rank_ic": []})
        self.assertEqual(result.effective_sample_count, 0)
        self.assertEqual(
            set(result.artifact_paths),
            {"trained_model", "pred.pkl", "label.pkl", "sig_analysis/ic.pkl", "sig_analysis/ric.pkl"},
        )

    def test_loads_trained_model_from_explicit_recorder(self):
        recorder = _Recorder()
        recorder.saved["trained_model"] = "loaded-model"
        requested = {}

        def get_recorder(**kwargs):
            requested.update(kwargs)
            return recorder

        bindings = QlibRecordBindings(
            start=lambda **kwargs: nullcontext(),
            get_recorder=get_recorder,
            signal_record_factory=_RecordTemplate,
            signal_analysis_factory=_RecordTemplate,
        )
        with TemporaryDirectory() as directory:
            model = QlibRecordService(QlibRuntime(Path(directory)), bindings=bindings).load_model(
                recorder_id="recorder-123",
                experiment_name="cn-model",
            )

        self.assertEqual(model, "loaded-model")
        self.assertEqual(requested, {"recorder_id": "recorder-123", "experiment_name": "cn-model"})


if __name__ == "__main__":
    unittest.main()
