"""Focused regressions for the Admin Workbench Phase 1 final fix wave."""

from __future__ import annotations

from collections import UserDict
from collections.abc import Mapping
from contextlib import contextmanager
from datetime import date
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import pandas as pd
from streamlit.testing.v1 import AppTest

import quant.storage as storage
from quant.storage import PostgresStore
from quant.workflows import sync_universe


class _CaptureConnection:
    def __init__(self):
        self.calls: list[tuple[str, tuple]] = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def execute(self, statement, parameters=()):
        self.calls.append((statement, parameters))


class _CustomMapping(Mapping):
    def __init__(self, values):
        self._values = values

    def __getitem__(self, key):
        return self._values[key]

    def __iter__(self):
        return iter(self._values)

    def __len__(self):
        return len(self._values)


class _StringifiedKey:
    def __init__(self, value):
        self.value = value

    def __str__(self):
        return self.value


class _SyncCaptureStore:
    def __init__(self, historical_member_codes=()):
        self.recorded: list[dict] = []
        self.sync_state_errors: list[str | None] = []
        self.finished: list[dict] = []
        self.historical_member_codes = set(historical_member_codes)
        self.synced_universe: list[str] = []

    def list_runs(self):
        return []

    def record_run(self, run_type, status, parameters, payload=None, error=None):
        self.recorded.append(
            {
                "run_type": run_type,
                "status": status,
                "parameters": parameters,
                "payload": payload,
                "error": error,
            }
        )
        return "sync-run-1"

    def update_run_progress(self, run_id, progress):
        return None

    def sync(self, provider, *, sync_key, skip_codes):
        self.synced_universe = list(provider.universe or ())
        return None

    def index_member_codes(self, index_code):
        return sorted(self.historical_member_codes) if index_code == "000300.SH" else []

    def sync_index_members(self, index_code, members):
        return None

    def status(self):
        return {"counts": {"prices": 1}}

    def data_quality(self, universe):
        return {"is_complete": True}

    def set_sync_state(self, universe, dataset, watermark, status, run_id, error=None):
        self.sync_state_errors.append(error)

    def finish_run(self, run_id, status, payload=None, error=None):
        self.finished.append({"status": status, "payload": payload, "error": error})


class _ProviderWithSecretErrors:
    def __init__(self, token, start, end, *, universe=None, **kwargs):
        self.universe = universe
        self.errors = (
            []
            if universe is None
            else [
                {
                    "dataset": "market",
                    "error": (
                        "token=raw-token "
                        "postgresql://reader:password@db.example/research "
                        "postgresql+psycopg://analytics.internal/warehouse "
                        "postgres://host.example/no-userinfo"
                    ),
                }
            ]
        )

    def index_weight(self, **kwargs):
        return pd.DataFrame(
            [{"con_code": "000001.SZ", "trade_date": "2026-08-27"}]
        )


class _ProviderRaisingSecretError:
    def __init__(self, *args, **kwargs):
        self.errors = []

    def index_weight(self, **kwargs):
        raise RuntimeError(
            "TUSHARE_TOKEN=raw-token "
            "database_url=postgresql://reader:password@db.example/research"
        )


class AdminFinalFixTests(unittest.TestCase):
    def test_incremental_sync_includes_saved_historical_members_in_market_universe(self):
        """Would fail if a current constituent response omitted a PIT-held stock's T+1 bars."""
        store = _SyncCaptureStore(historical_member_codes={"688169.SH"})

        with patch("quant.workflows.TushareProvider", _ProviderWithSecretErrors):
            sync_universe(
                store,
                "token-not-persisted",
                universe="hs300",
                start_date=date(2026, 8, 27),
                end_date=date(2026, 9, 3),
                _locked=True,
            )

        self.assertEqual(store.synced_universe, ["000001.SZ", "688169.SH"])

    def test_storage_redacts_custom_canonical_sensitive_keys_and_values_at_record_boundary(self):
        """Using the original key type would leave custom-key secret values persisted."""
        fields = ("token", "TUSHARE_TOKEN", "password", "secret", "dsn", "database_url")
        original = _CustomMapping(
            {_StringifiedKey(field): f"raw-{field}-value" for field in fields}
        )
        expected = {
            "[已隐藏]": "[已隐藏]",
            "[已隐藏]#2": "[已隐藏]",
            "[已隐藏]#3": "[已隐藏]",
            "[已隐藏]#4": "[已隐藏]",
            "[已隐藏]#5": "[已隐藏]",
            "[已隐藏]#6": "[已隐藏]",
        }

        self.assertEqual(storage.sanitize_for_storage(original), expected)

        connection = _CaptureConnection()
        store = PostgresStore("postgresql://unused")
        store.initialize = lambda: None
        store._connect = lambda: connection
        store.record_run("sync", "running", original, original)

        parameters = json.loads(connection.calls[0][1][3])
        payload = json.loads(connection.calls[0][1][4])
        self.assertEqual(parameters, expected)
        self.assertEqual(payload, expected)
        persisted = "\n".join((connection.calls[0][1][3], connection.calls[0][1][4]))
        for field in fields:
            self.assertNotIn(f"raw-{field}-value", persisted)

    def test_storage_sanitizes_general_mappings_before_json_recording(self):
        """A non-dict mapping must not bypass recursive secret sanitization."""
        database_url = "postgresql://reader:password@db.example/research"
        original = UserDict(
            {
                "token": "outer-token",
                "mapping": _CustomMapping(
                    {
                        7: "integer seven",
                        "7": "string seven",
                        "credentials": UserDict(
                            {"TUSHARE_TOKEN": "inner-token", "url": database_url}
                        ),
                    }
                ),
                "items": [UserDict({"database_url": database_url, "label": "safe"})],
                "[已隐藏]": "already safe",
            }
        )
        expected = {
            "[已隐藏]": "[已隐藏]",
            "mapping": {
                "7": "integer seven",
                "7#2": "string seven",
                "credentials": {"[已隐藏]": "[已隐藏]", "url": "[已隐藏]"},
            },
            "items": [{"[已隐藏]": "[已隐藏]", "label": "safe"}],
            "[已隐藏]#2": "already safe",
        }

        clean = storage.sanitize_for_storage(original)

        self.assertEqual(clean, expected)
        self.assertIs(type(clean), dict)
        self.assertIs(type(clean["mapping"]), dict)
        self.assertIs(type(clean["mapping"]["credentials"]), dict)
        self.assertIs(type(clean["items"][0]), dict)
        self.assertEqual(json.loads(json.dumps(clean, default=str)), expected)

        connection = _CaptureConnection()
        store = PostgresStore("postgresql://unused")
        store.initialize = lambda: None
        store._connect = lambda: connection
        store.record_run("sync", "running", original, {"payload": original})

        parameters = json.loads(connection.calls[0][1][3])
        payload = json.loads(connection.calls[0][1][4])
        self.assertEqual(parameters, expected)
        self.assertEqual(payload["payload"], expected)
        persisted = "\n".join((connection.calls[0][1][3], connection.calls[0][1][4]))
        for forbidden in ("outer-token", "inner-token", "postgresql://", "reader", "password", "db.example"):
            self.assertNotIn(forbidden, persisted)

    def test_storage_preserves_null_error_values_at_each_database_boundary(self):
        """Converting an absent error to empty text would change SQL NULL semantics."""
        connection = _CaptureConnection()
        store = PostgresStore("postgresql://unused")
        store.initialize = lambda: None
        store._connect = lambda: connection

        self.assertIsNone(storage.sanitize_sensitive_text(None))
        run_id = store.record_run("sync", "running", error=None)
        store.finish_run(run_id, "success", error=None)
        store.set_sync_state("hs300", "market", date(2026, 8, 29), "success", run_id, None)

        self.assertIsNone(connection.calls[0][1][5])
        self.assertIsNone(connection.calls[1][1][2])
        self.assertIsNone(connection.calls[2][1][5])

    def test_storage_sanitizer_redacts_string_keys_without_losing_colliding_entries(self):
        """Leaving keys raw or overwriting a redacted collision would persist secrets or drop data."""
        database_url = "postgresql://reader:password@db.example/research"
        original = {
            "token": "raw-token",
            "TUSHARE_TOKEN": "raw-token-two",
            database_url: "connection details",
            "[已隐藏]": "already safe",
            "ordinary": ["retained", (7, True)],
            7: "non-string key",
        }

        clean = storage.sanitize_for_storage(original)

        self.assertEqual(len(clean), len(original))
        self.assertEqual(
            list(clean),
            ["[已隐藏]", "[已隐藏]#2", "[已隐藏]#3", "[已隐藏]#4", "ordinary", "7"],
        )
        self.assertEqual(clean["ordinary"], ["retained", (7, True)])
        self.assertEqual(clean["7"], "non-string key")
        self.assertEqual(clean["[已隐藏]"], "[已隐藏]")
        self.assertEqual(clean["[已隐藏]#2"], "[已隐藏]")
        self.assertEqual(clean["[已隐藏]#3"], "connection details")
        self.assertEqual(clean["[已隐藏]#4"], "already safe")
        rendered = json.dumps(clean, default=str)
        for forbidden in ("raw-token", "raw-token-two", "postgresql://", "reader", "password", "db.example"):
            self.assertNotIn(forbidden, rendered)

    def test_storage_sanitizer_preserves_mixed_key_mappings_through_json_and_record_run(self):
        """Detecting collisions before JSON key coercion would drop persisted entries."""
        original = {
            7: "integer seven",
            "7": "string seven",
            None: "none key",
            "null": "literal null key",
            True: "boolean true key",
            "true": "literal true key",
            "token": "raw-token",
            "[已隐藏]#2": "already safe",
            "TUSHARE_TOKEN": "raw-token-two",
            "nested": {
                "token": "nested raw-token",
                7: "nested integer seven",
                "7": "nested string seven",
                None: "nested none key",
                "null": "nested literal null key",
            },
        }
        expected = {
            "7": "integer seven",
            "7#2": "string seven",
            "null": "none key",
            "null#2": "literal null key",
            "true": "boolean true key",
            "true#2": "literal true key",
            "[已隐藏]": "[已隐藏]",
            "[已隐藏]#2": "already safe",
            "[已隐藏]#3": "[已隐藏]",
            "nested": {
                "[已隐藏]": "[已隐藏]",
                "7": "nested integer seven",
                "7#2": "nested string seven",
                "null": "nested none key",
                "null#2": "nested literal null key",
            },
        }

        clean = storage.sanitize_for_storage(original)

        self.assertEqual(clean, expected)
        self.assertEqual(json.loads(json.dumps(clean, default=str)), expected)

        connection = _CaptureConnection()
        store = PostgresStore("postgresql://unused")
        store.initialize = lambda: None
        store._connect = lambda: connection
        store.record_run("sync", "running", original, {"payload": original})

        parameters = json.loads(connection.calls[0][1][3])
        payload = json.loads(connection.calls[0][1][4])
        self.assertEqual(parameters, expected)
        self.assertEqual(payload["payload"], expected)
        persisted = "\n".join((connection.calls[0][1][3], connection.calls[0][1][4]))
        self.assertNotIn("raw-token", persisted)

    def test_storage_sanitizer_recursively_redacts_sensitive_strings_without_changing_values(self):
        """Removing recursive sanitization would leave nested workflow details unsafe."""
        sanitizer = getattr(storage, "sanitize_for_storage", None)
        self.assertIsNotNone(sanitizer, "storage must expose a reusable sanitization helper")
        if sanitizer is None:
            return

        as_of = date(2026, 8, 29)
        original = {
            "as_of": as_of,
            "retry_count": 2,
            "enabled": True,
            "optional": None,
            "token": "raw-token",
            "nested": [
                {
                    "message": (
                        "TUSHARE_TOKEN=raw-token "
                        "postgresql://reader:password@db.example/research "
                        "postgresql+psycopg://analytics.internal/warehouse "
                        "postgres://host.example/no-userinfo"
                    )
                }
            ],
        }

        clean = sanitizer(original)

        self.assertEqual(clean["as_of"], as_of)
        self.assertEqual(clean["retry_count"], 2)
        self.assertIs(clean["enabled"], True)
        self.assertIsNone(clean["optional"])
        self.assertIsInstance(clean["nested"], list)
        self.assertIsInstance(clean["nested"][0], dict)
        rendered = json.dumps(clean, default=str)
        for forbidden in (
            "raw-token",
            "postgresql://",
            "postgresql+psycopg://",
            "postgres://",
            "reader",
            "password",
            "db.example",
            "analytics.internal",
            "warehouse",
            "host.example",
            "no-userinfo",
        ):
            self.assertNotIn(forbidden, rendered)

    def test_storage_run_and_sync_boundaries_never_receive_sensitive_content(self):
        """A storage-boundary guard protects callers that omit workflow sanitization."""
        connection = _CaptureConnection()
        store = PostgresStore("postgresql://unused")
        store.initialize = lambda: None
        store._connect = lambda: connection
        leak = (
            "token=raw-token postgresql://reader:password@db.example/research "
            "postgresql+psycopg://analytics.internal/warehouse "
            "postgres://host.example/no-userinfo"
        )

        run_id = store.record_run(
            "sync",
            "running",
            {"nested": {"database_url": leak}},
            {"dataset_errors": [{"error": leak}]},
            error=leak,
        )
        store.update_run_progress(run_id, {"detail": leak})
        store.finish_run(run_id, "failed", {"reason": leak}, error=leak)
        store.set_sync_state("hs300", "market", date(2026, 8, 29), "failed", run_id, leak)

        persisted = "\n".join(repr(parameters) for _, parameters in connection.calls)
        for forbidden in (
            "raw-token",
            "postgresql://",
            "postgresql+psycopg://",
            "postgres://",
            "reader",
            "password",
            "db.example",
            "analytics.internal",
            "warehouse",
            "host.example",
            "no-userinfo",
        ):
            self.assertNotIn(forbidden, persisted)

    def test_sync_workflow_sanitizes_provider_errors_before_the_storage_boundary(self):
        """Workflow-side defense keeps provider errors safe for non-Postgres stores too."""
        store = _SyncCaptureStore()
        with patch("quant.workflows.TushareProvider", _ProviderWithSecretErrors):
            sync_universe(
                store,
                "token-not-persisted",
                universe="hs300",
                start_date=date(2026, 8, 1),
                end_date=date(2026, 8, 29),
                _locked=True,
            )

        captured = repr(store.sync_state_errors) + repr(store.finished)
        for forbidden in (
            "raw-token",
            "postgresql://",
            "postgresql+psycopg://",
            "postgres://",
            "reader",
            "password",
            "db.example",
            "analytics.internal",
            "warehouse",
            "host.example",
            "no-userinfo",
        ):
            self.assertNotIn(forbidden, captured)

    def test_sync_workflow_sanitizes_exceptions_before_finishing_a_failed_run(self):
        """A raw provider exception must not bypass the workflow's failure path."""
        store = _SyncCaptureStore()
        with patch("quant.workflows.TushareProvider", _ProviderRaisingSecretError):
            sync_universe(
                store,
                "token-not-persisted",
                universe="hs300",
                start_date=date(2026, 8, 1),
                end_date=date(2026, 8, 29),
                _locked=True,
            )

        self.assertEqual(store.finished[0]["status"], "failed")
        self.assertNotIn("raw-token", store.finished[0]["error"])
        self.assertNotIn("postgresql://", store.finished[0]["error"])
        self.assertNotIn("db.example", store.finished[0]["error"])

    def test_public_status_uses_reader_facing_dataset_captions(self):
        """Internal table names are not meaningful in the public data-status page."""
        app_file = Path(__file__).parent / "fixtures" / "public_status_app.py"
        at = AppTest.from_file(str(app_file)).run(timeout=10)

        self.assertEqual(len(at.exception), 0)
        rendered = "\n".join(element.value for element in at.markdown)
        self.assertIn("日行情记录", rendered)
        self.assertIn("估值数据记录", rendered)
        self.assertNotIn("price_bars", rendered)
        self.assertNotIn("daily_basic", rendered)

    def test_admin_model_caption_describes_the_selected_factor_run(self):
        """The model form must not say it uses the latest run when a user chose another one."""
        app_file = Path(__file__).parent / "fixtures" / "admin_workbench_app.py"
        with patch.dict("os.environ", {"ADMIN_FIXTURE_MODE": "enabled"}, clear=False):
            at = AppTest.from_file(str(app_file)).run(timeout=10)

        captions = [element.value for element in at.caption]
        self.assertIn("使用所选的已完成因子运行。", captions)
        self.assertNotIn("使用最近完成的因子运行。", captions)

    def test_public_no_database_state_uses_neutral_research_preparation_copy(self):
        """An ordinary user must not be instructed to configure a connection."""
        app_file = Path(__file__).parents[1] / "streamlit_app.py"
        with patch.dict("os.environ", {"QUANT_ADMIN_MODE": "false"}, clear=True):
            at = AppTest.from_file(str(app_file)).run(timeout=10)

        rendered = "\n".join(
            str(element.value)
            for elements in (at.markdown, at.caption)
            for element in elements
        )
        self.assertIn("研究数据正在准备", rendered)
        self.assertIn("请稍后刷新或联系管理员。", rendered)
        self.assertNotIn("配置数据连接", rendered)

    def test_public_portfolio_copy_describes_historical_simulation_not_buying(self):
        """Portfolio metrics must not frame a historical result as an investment action."""
        source = (Path(__file__).parents[1] / "streamlit_app.py").read_text(encoding="utf-8")
        self.assertIn("按保存权重模拟持有", source)
        self.assertNotIn("按保存权重买入并持有", source)

    def test_readme_feature_list_describes_public_pages_and_gated_admin_workbench(self):
        """The README overview must match the user-facing information architecture."""
        readme = (Path(__file__).parents[1] / "README.md").read_text(encoding="utf-8")
        self.assertIn("研究首页、候选池、个股详情、行业观察、我的组合、历史验证和数据状态", readme)
        self.assertIn("受部署开关控制的管理员工作台", readme)
        self.assertNotIn("Streamlit 页面：总览、数据管理、个股数据、因子研究、模型训练、策略配置、回测、运行记录", readme)


if __name__ == "__main__":
    unittest.main()
