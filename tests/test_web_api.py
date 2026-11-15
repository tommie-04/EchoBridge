"""Integration tests for FastAPI endpoints, multi-tenant ticket APIs, and Agent chat traces."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from api_service import app
from web_server import app as web_app


class WebAPITests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db_path = str(Path(self.tmp.name) / "test_api.db")
        self.env_patch = patch.dict("os.environ", {"TICKET_DB_PATH": self.db_path})
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)
        self.client = TestClient(app)

    def test_health_endpoint(self):
        resp = self.client.get("/api/health")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "healthy")
        self.assertGreaterEqual(data["registered_shops_count"], 3)

    def test_shops_endpoints(self):
        resp = self.client.get("/api/shops")
        self.assertEqual(resp.status_code, 200)
        shops = resp.json()["shops"]
        shop_ids = {s["shop_id"] for s in shops}
        self.assertTrue({"tt-us-west", "tt-us-east", "tt-uk"}.issubset(shop_ids))

        detail_resp = self.client.get("/api/shops/tt-us-west")
        self.assertEqual(detail_resp.status_code, 200)
        self.assertEqual(detail_resp.json()["shop"]["currency"], "USD")

        not_found = self.client.get("/api/shops/non-existing")
        self.assertEqual(not_found.status_code, 404)

    def test_tickets_crud_and_shop_filter(self):
        # 1. Create a ticket in tt-us-west
        create_resp = self.client.post("/api/tickets/create", json={
            "title": "升降桌电机报错 E08",
            "description": "加州客户升降桌无法升降",
            "shop_id": "tt-us-west",
            "user_id": "customer-ca-1",
            "platform": "web",
            "category": "damaged",
            "priority": "high",
            "product_name": "Lumen Pure Standing Desk"
        })
        self.assertEqual(create_resp.status_code, 200)
        ticket = create_resp.json()["ticket"]
        ticket_id = ticket["ticket_id"]
        self.assertEqual(ticket["shop_id"], "tt-us-west")

        # 2. Create another ticket in tt-uk
        self.client.post("/api/tickets/create", json={
            "title": "英国店退货咨询",
            "description": "14天内退货",
            "shop_id": "tt-uk",
            "user_id": "customer-uk-1",
            "platform": "web",
            "category": "return",
            "priority": "medium"
        })

        # 3. Filter by shop_id
        west_resp = self.client.get("/api/tickets?shop_id=tt-us-west")
        self.assertEqual(west_resp.status_code, 200)
        self.assertEqual(west_resp.json()["total"], 1)
        self.assertEqual(west_resp.json()["tickets"][0]["ticket_id"], ticket_id)

        uk_resp = self.client.get("/api/tickets?shop_id=tt-uk")
        self.assertEqual(uk_resp.status_code, 200)
        self.assertEqual(uk_resp.json()["total"], 1)

        # 4. Update ticket status & add comment
        update_resp = self.client.post(f"/api/tickets/{ticket_id}/update", json={
            "status": "in_progress",
            "comment": "专员已出具加州 RMA 退货标签",
            "operator_id": "admin-tommie"
        })
        self.assertEqual(update_resp.status_code, 200)
        updated = update_resp.json()["ticket"]
        self.assertEqual(updated["status"], "in_progress")
        self.assertEqual(updated["comments"][-1]["text"], "专员已出具加州 RMA 退货标签")

    def test_knowledge_endpoint(self):
        resp = self.client.get("/api/knowledge/tt-us-west")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["shop_id"], "tt-us-west")
        self.assertIn("Lumen Home", data["common_policy"])
        self.assertIn("加利福尼亚州", data["shop_policy"])

    def test_demo_handoffs_persist_and_replay_per_shop(self):
        import asyncio
        from demo_handoffs import replay
        asyncio.run(replay(Path(self.db_path)))
        first = self.client.get("/api/tickets").json()["tickets"]
        self.assertEqual(len(first), 6)
        target = first[0]["ticket_id"]
        self.client.post(f"/api/tickets/{target}/update", json={"status": "in_progress", "comment": "已联系仓库"}).raise_for_status()
        asyncio.run(replay(Path(self.db_path)))
        for shop in ("tt-us-west", "tt-us-east", "tt-uk"):
            rows = self.client.get("/api/tickets", params={"shop_id": shop}).json()["tickets"]
            self.assertEqual(len(rows), 2)
            self.assertEqual(len({row["user_id"] for row in rows}), 2)
            self.assertTrue(all(row["shop_id"] == shop for row in rows))
        updated = self.client.get(f"/api/tickets/{target}").json()["ticket"]
        self.assertEqual(updated["status"], "in_progress")
        self.assertEqual(updated["comments"][-1]["text"], "已联系仓库")

    def test_static_dashboard_mounted(self):
        web_client = TestClient(web_app)
        resp = web_client.get("/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("EchoBridge", resp.text)


if __name__ == "__main__":
    unittest.main()
