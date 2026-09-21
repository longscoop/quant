import unittest

from fastapi.testclient import TestClient

from backend.app.main import create_app


class ApplicationTests(unittest.TestCase):
    def test_health_endpoint_reports_service_without_database_access(self):
        response = TestClient(create_app()).get("/api/v1/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    def test_unexpected_route_error_returns_opaque_message(self):
        app = create_app()

        @app.get("/test-error")
        def raise_database_error():
            raise RuntimeError("postgresql://secret@example.invalid/quant")

        response = TestClient(app, raise_server_exceptions=False).get("/test-error")

        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json()["detail"], "服务暂时不可用，请稍后重试。")
        self.assertNotIn("secret", response.text)

