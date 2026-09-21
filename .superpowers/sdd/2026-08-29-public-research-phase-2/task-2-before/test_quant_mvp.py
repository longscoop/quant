import unittest
from datetime import date
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from quant.backtest import BacktestConfig, run_backtest
from quant.factors import FactorEngine
from quant.model import ModelTrainer, export_qlib
from quant.pit import PITRepository
from quant.presentation import (
    format_metric,
    public_run_feedback,
    public_run_label,
    public_run_rows,
    sanitize_display_rows,
    status_feedback,
)
from quant.providers import FixtureProvider
from quant.storage import InMemoryStore


class QuantMvpTests(unittest.TestCase):
    def setUp(self):
        self.store = InMemoryStore()
        self.store.sync(FixtureProvider())

    def test_sync_is_idempotent_and_records_audit(self):
        before = self.store.counts()
        self.store.sync(FixtureProvider())
        self.assertEqual(before, self.store.counts())
        self.assertEqual(self.store.audit[-1].status, "success")

    def test_pit_snapshot_hides_future_announcement_and_uses_visible_revision(self):
        snapshot = PITRepository(self.store).snapshot(date(2024, 4, 15))
        financial = {row.ts_code: row for row in snapshot.financials}
        self.assertEqual(financial["000001.SZ"].net_profit, 100.0)
        self.assertNotIn("000003.SZ", {row.ts_code for row in snapshot.universe})
        later = PITRepository(self.store).snapshot(date(2024, 5, 1))
        self.assertEqual({row.ts_code: row for row in later.financials}["000001.SZ"].net_profit, 120.0)

    def test_factor_engine_normalizes_only_eligible_stocks(self):
        engine = FactorEngine(PITRepository(self.store))
        features = engine.build_features([date(2024, 4, 15)])
        self.assertTrue(features.rows)
        self.assertNotIn("000003.SZ", {row.ts_code for row in features.rows})
        self.assertIn("momentum_1m", features.rows[0].values)
        self.assertEqual(features.metadata["pit_safe"], True)

    def test_model_never_trains_on_prediction_date_or_later(self):
        engine = FactorEngine(PITRepository(self.store))
        features = engine.build_features([date(2024, 3, 15), date(2024, 4, 15), date(2024, 5, 15)])
        prediction = ModelTrainer().fit_predict(features, [date(2024, 5, 15)])
        self.assertTrue(prediction.rows)
        self.assertTrue(all(row.as_of_date == date(2024, 5, 15) for row in prediction.rows))
        self.assertLess(prediction.metadata["train_end"], date(2024, 5, 15))

    def test_model_explains_when_prediction_date_has_no_features(self):
        features = FactorEngine(PITRepository(self.store)).build_features([date(2024, 4, 15)])
        result = ModelTrainer().fit_predict(features, [date(2024, 5, 15)], labels={})
        self.assertEqual(result.metadata["status"], "not_trainable")
        self.assertEqual(result.metadata["status_reason"], "missing_current_features")

    def test_model_explains_when_training_features_are_absent(self):
        features = FactorEngine(PITRepository(self.store)).build_features([date(2024, 5, 15)])
        result = ModelTrainer().fit_predict(features, [date(2024, 5, 15)])
        self.assertEqual(result.metadata["status_reason"], "missing_training_features")

    def test_model_explains_when_training_labels_are_absent(self):
        features = FactorEngine(PITRepository(self.store)).build_features([date(2024, 4, 15), date(2024, 5, 15)])
        result = ModelTrainer().fit_predict(features, [date(2024, 5, 15)], labels={})
        self.assertEqual(result.metadata["status_reason"], "missing_labels")

    def test_qlib_export_writes_pit_feature_and_label_snapshots(self):
        import tempfile
        engine = FactorEngine(PITRepository(self.store))
        features = engine.build_features([date(2024, 3, 15)])
        with tempfile.TemporaryDirectory() as directory:
            manifest = export_qlib(features, directory)
            self.assertTrue(manifest["features"].endswith("features.csv"))
            with open(manifest["features"], encoding="utf-8") as handle:
                self.assertIn("datetime,instrument", handle.readline())

    def test_backtest_rejects_an_incomplete_holding_interval(self):
        engine = FactorEngine(PITRepository(self.store))
        features = engine.build_features([date(2024, 3, 15), date(2024, 4, 15), date(2024, 5, 15)])
        prediction = ModelTrainer().fit_predict(features, [date(2024, 5, 15)])
        result = run_backtest(BacktestConfig(top_n=2), prediction, PITRepository(self.store))
        self.assertEqual(result.status_reason, "缺少共同持有期终止日")

    def test_cli_exposes_database_backed_research_stages(self):
        result = subprocess.run([sys.executable, "-m", "quant.cli", "--help"], capture_output=True, text=True, check=True)
        self.assertIn("init-db", result.stdout)
        self.assertIn("sync-hs300", result.stdout)
        self.assertIn("build-factors", result.stdout)
        self.assertIn("train", result.stdout)
        self.assertIn("backtest", result.stdout)

    def test_streamlit_entrypoint_compiles(self):
        result = subprocess.run([sys.executable, "-m", "py_compile", "streamlit_app.py"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_public_run_presentation_hides_run_ids_and_stored_failures(self):
        """Public labels, feedback, and history rows must not disclose operational values."""
        failed = {
            "run_id": "run-secret-123",
            "run_type": "model",
            "status": "failed",
            "error": "connection dsn=postgresql://user:pass@db.example/private-research",
        }
        completed = {"run_id": "run-secret-456", "run_type": "backtest", "status": "completed"}

        self.assertEqual(public_run_label(failed), "模型研究 · 失败")
        self.assertEqual(
            public_run_feedback(failed, "模型研究已完成。"),
            ("error", "模型研究未能完成。请稍后重试或联系管理员。"),
        )
        self.assertEqual(
            public_run_feedback(completed, "历史回测已完成。"),
            ("success", "历史回测已完成。"),
        )
        rows = public_run_rows([failed, completed])
        self.assertEqual(
            rows,
            [
                {"研究类型": "模型研究", "状态": "失败"},
                {"研究类型": "历史回测", "状态": "已完成"},
            ],
        )
        rendered = repr(rows) + public_run_label(failed) + public_run_feedback(failed, "")[1]
        for forbidden in ("run-secret-123", "run-secret-456", "postgresql://", "db.example", "private-research"):
            self.assertNotIn(forbidden, rendered)

    def test_streamlit_entrypoint_gates_navigation_and_database_copy_by_audience(self):
        """The no-database entrypoint keeps public output operationally safe."""
        def run_entrypoint(admin_mode: str):
            with patch.dict(
                os.environ,
                {"QUANT_ADMIN_MODE": admin_mode},
                clear=True,
            ):
                return AppTest.from_file(str(Path(__file__).parents[1] / "streamlit_app.py")).run(timeout=10)

        public = run_entrypoint("false")
        self.assertEqual(len(public.exception), 0)
        self.assertEqual(
            public.sidebar.radio[0].options,
            ["今日机会", "股票池", "个股研究", "行业景气", "策略实验室", "组合", "回测", "数据状态"],
        )
        public_output = "\n".join(
            str(element.value) for elements in (public.markdown, public.caption) for element in elements
        )
        self.assertIn("研究数据正在准备", public_output)
        self.assertIn("请稍后刷新或联系管理员。", public_output)
        for forbidden in ("DATABASE_URL", "PostgreSQL", "Tushare Token"):
            self.assertNotIn(forbidden, public_output)

        admin = run_entrypoint("true")
        self.assertEqual(len(admin.exception), 0)
        self.assertEqual(
            admin.sidebar.radio[0].options,
            ["今日机会", "股票池", "个股研究", "行业景气", "策略实验室", "组合", "回测", "数据状态", "管理员"],
        )
        admin_output = "\n".join(
            str(element.value) for elements in (admin.markdown, admin.caption) for element in elements
        )
        self.assertIn("尚未连接研究数据库", admin_output)
        self.assertIn("请在部署环境配置 DATABASE_URL。", admin_output)

    def test_streamlit_app_uses_research_summary_helpers(self):
        source = Path("streamlit_app.py").read_text(encoding="utf-8")
        self.assertIn("data_quality_summary", source)
        self.assertIn("run_status_summary", source)
        self.assertIn("研究信号", source)
        self.assertNotIn('"买入"', source)
        self.assertNotIn('"卖出"', source)

    def test_streamlit_uses_paged_pool_and_candlestick_data_path(self):
        source = Path("streamlit_app.py").read_text(encoding="utf-8")
        self.assertIn("paginate_candidate_rows", source)
        self.assertIn("plotly_chart", source)
        self.assertIn("refresh_today_opportunities", source)
        self.assertNotIn('st.sidebar.text_input("PostgreSQL DATABASE_URL"', source)

    def test_display_sanitizer_marks_absent_nan_and_infinite_values_unavailable(self):
        rows = sanitize_display_rows([{
            "缺失": None,
            "NaN": float("nan"),
            "正无穷": float("inf"),
            "负无穷": float("-inf"),
            "有限值": 1.25,
        }])
        self.assertEqual(rows, [{
            "缺失": "不可用",
            "NaN": "不可用",
            "正无穷": "不可用",
            "负无穷": "不可用",
            "有限值": 1.25,
        }])
        self.assertEqual(format_metric(float("nan"), percent=True), "不可用")

    def test_status_feedback_warns_for_not_trainable_model_and_succeeds_only_when_completed(self):
        not_trainable = {
            "run_type": "model",
            "status": "not_trainable",
            "payload": {"metadata": {"status_reason": "missing_current_features"}},
        }
        self.assertEqual(status_feedback(not_trainable, "模型已完成")[0], "warning")
        self.assertIn("因子", status_feedback(not_trainable, "模型已完成")[1])
        self.assertEqual(status_feedback({"run_type": "model", "status": "completed"}, "模型已完成"), ("success", "模型已完成"))
        self.assertEqual(status_feedback({"run_type": "factors", "status": "not_trainable"}, "因子运行已完成")[0], "warning")
        self.assertEqual(
            status_feedback(
                {
                    "run_type": "factors",
                    "status": "failed",
                    "error": "dsn=postgresql://user:pass@db.example/research",
                },
                "因子运行已完成",
            ),
            ("error", "因子构建未能完成。请稍后重试或联系管理员。"),
        )

    def test_model_falls_back_when_lightgbm_shared_library_cannot_load(self):
        engine = FactorEngine(PITRepository(self.store))
        features = engine.build_features([date(2024, 3, 15), date(2024, 4, 15), date(2024, 5, 15)])
        original_import = __import__

        def broken_lightgbm_import(name, *args, **kwargs):
            if name == "lightgbm":
                raise OSError("libgomp.so.1 unavailable")
            return original_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=broken_lightgbm_import):
            prediction = ModelTrainer().fit_predict(features, [date(2024, 5, 15)])
        self.assertTrue(prediction.rows)


if __name__ == "__main__":
    unittest.main()
