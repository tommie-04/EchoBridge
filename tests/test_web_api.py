"""Integration checks for the fulfillment-only HTTP surface."""
from io import BytesIO
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from pypdf import PdfReader

from api_service import app
from web_server import app as web_app


class WebAPITests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        environment = patch.dict("os.environ", {
            "FULFILLMENT_SIM_DB": str(Path(temporary.name) / "simulation.db")
        })
        environment.start()
        self.addCleanup(environment.stop)
        self.client = TestClient(app)

    def test_health_and_twenty_us_shops(self):
        health = self.client.get("/api/health")
        self.assertEqual(health.status_code, 200)
        self.assertEqual(health.json()["mode"], "simulation")
        self.assertEqual(health.json()["registered_shops_count"], 20)
        shops = self.client.get("/api/simulation/shops").json()["shops"]
        self.assertEqual(len(shops), 20)
        self.assertEqual({shop["region"] for shop in shops}, {"US"})
        self.assertEqual({shop["warehouse"] for shop in shops}, {"Los Angeles warehouse"})

    def test_snapshot_replay_and_group_download(self):
        payload = {"shop_ids": ["mock-us-01", "mock-us-02"], "ship_date": "2026-11-14"}
        created = self.client.post("/api/simulation/batches", json=payload)
        self.assertEqual(created.status_code, 200)
        snapshot = created.json()
        repeated = self.client.post("/api/simulation/batches", json=payload)
        self.assertEqual(repeated.json(), snapshot)
        path = "/api/simulation/batches/" + snapshot["batch_id"]
        self.assertEqual(self.client.get(path).json(), snapshot)
        document = self.client.get(path + "/pdf", params={"group_id": "pad-org"})
        self.assertEqual(document.status_code, 200)
        self.assertEqual(len(PdfReader(BytesIO(document.content)).pages), 11)

    def test_invalid_selection_and_missing_batch(self):
        for payload in [
            {"shop_ids": [], "ship_date": "2026-11-14"},
            {"shop_ids": ["unknown"], "ship_date": "2026-11-14"},
            {"shop_ids": ["mock-us-01"], "ship_date": "2026-02-30"},
        ]:
            self.assertEqual(self.client.post("/api/simulation/batches", json=payload).status_code, 422)
        self.assertEqual(self.client.get("/api/simulation/batches/unknown").status_code, 404)
        self.assertEqual(self.client.get("/api/simulation/batches/unknown/pdf").status_code, 404)

    def test_removed_customer_and_ticket_endpoints(self):
        for path in ["/api/tickets", "/api/shops", "/api/knowledge/tt-us-west"]:
            self.assertEqual(self.client.get(path).status_code, 404)
        self.assertEqual(self.client.post("/api/agent/chat", json={"message": "hello"}).status_code, 404)
        self.assertEqual(self.client.post("/api/tickets/create", json={}).status_code, 404)

    def test_static_dashboard_mounted(self):
        response = TestClient(web_app).get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("EchoBridge", response.text)


if __name__ == "__main__":
    unittest.main()
