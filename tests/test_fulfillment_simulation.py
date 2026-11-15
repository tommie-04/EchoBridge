"""Warehouse grouping, PDF output and persisted simulation API regression checks."""
from io import BytesIO
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from pypdf import PdfReader

from api_service import app
from fulfillment_simulator import build_batch, list_mock_shops, render_batch_pdf


class FulfillmentSimulationTests(unittest.TestCase):
    def test_two_shop_business_example(self):
        batch = build_batch(["mock-us-01", "mock-us-02"], "2026-11-14")
        self.assertEqual((batch["package_count"], batch["unit_count"]), (21, 21))
        self.assertEqual({g["sku"]: g["unit_count"] for g in batch["groups"]}, {
            "pad-blk": 5, "pad-org": 11, "pad-sil": 5})
        self.assertEqual({e["reason"] for e in batch["excluded"]}, {"cancelled", "label_pending"})
        reordered = build_batch(["mock-us-02", "mock-us-01", "mock-us-01"], "2026-11-14")
        self.assertEqual(batch, reordered)

    def test_mixed_package_is_one_label_with_every_item(self):
        batch = build_batch(["mock-us-03"], "2026-11-14")
        self.assertEqual((batch["package_count"], batch["unit_count"]), (2, 4))
        pdf = PdfReader(BytesIO(render_batch_pdf(batch, "mixed")))
        self.assertEqual(len(pdf.pages), 1)
        text = pdf.pages[0].extract_text()
        self.assertIn("pad-org  x 1", text)
        self.assertIn("pad-sil  x 1", text)
        self.assertIn("DEMO - NOT VALID FOR POSTAGE", text)
        self.assertEqual(tuple(map(float, pdf.pages[0].mediabox[2:])), (288.0, 432.0))
        single = PdfReader(BytesIO(render_batch_pdf(batch, "pad-blk")))
        self.assertIn("pad-blk  x 2", single.pages[0].extract_text())

    def test_twenty_shop_counts_and_date_boundary(self):
        shops = [s["shop_id"] for s in list_mock_shops()]
        self.assertEqual(len(shops), 20)
        batch = build_batch(shops, "2026-11-14")
        self.assertEqual((batch["package_count"], batch["unit_count"]), (57, 93))
        packages = [p for g in batch["groups"] for p in g["packages"]]
        self.assertEqual(len({p["package_id"] for p in packages}), 57)
        self.assertTrue(all(p["ship_date"] == "2026-11-14" for p in packages))
        self.assertNotEqual(batch["batch_id"], build_batch(shops, "2026-11-15")["batch_id"])

    def test_unknown_shop_date_and_group_fail(self):
        for shops, day in [([], "2026-11-14"), (["real-shop"], "2026-11-14"),
                           (["mock-us-01"], "2026-02-30")]:
            with self.assertRaises(ValueError):
                build_batch(shops, day)
        with self.assertRaises(ValueError):
            render_batch_pdf(build_batch(["mock-us-01"], "2026-11-14"), "../private")

    def test_api_replay_and_download_survive_client_restart(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
            "FULFILLMENT_SIM_DB": str(Path(directory) / "simulation.db")
        }):
            client = TestClient(app)
            payload = {"shop_ids": ["mock-us-01", "mock-us-02"], "ship_date": "2026-11-14"}
            first = client.post("/api/simulation/batches", json=payload)
            self.assertEqual(first.status_code, 200)
            batch = first.json()
            self.assertEqual(client.post("/api/simulation/batches", json=payload).json(), batch)
            resumed = TestClient(app)
            base = "/api/simulation/batches/" + batch["batch_id"]
            self.assertEqual(resumed.get(base).json(), batch)
            pdf = resumed.get(base + "/pdf", params={"group_id": "pad-blk"})
            self.assertEqual(pdf.status_code, 200)
            self.assertEqual(pdf.headers["content-type"], "application/pdf")
            self.assertEqual(len(PdfReader(BytesIO(pdf.content)).pages), 5)
            self.assertEqual(resumed.get(base + "/pdf?group_id=unknown").status_code, 422)
            self.assertEqual(resumed.get("/api/simulation/batches/unknown/pdf").status_code, 404)
            self.assertEqual(client.post("/api/simulation/batches", json={
                **payload, "shop_ids": ["unauthorized"]}).status_code, 422)


if __name__ == "__main__":
    unittest.main()
