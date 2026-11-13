"""Unit tests for shop registry loading and channel-to-storefront resolution."""
from pathlib import Path
import unittest

from shop_registry import (
    get_default_shop_id,
    get_shop_by_id,
    get_shop_for_channel,
    list_shops,
    load_shops_config,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class ShopRegistryTests(unittest.TestCase):
    def test_load_shops_and_defaults(self):
        config = load_shops_config()
        self.assertIn("shops", config)
        self.assertEqual(get_default_shop_id(), "tt-us-west")
        shops = list_shops()
        self.assertGreaterEqual(len(shops), 3)
        shop_ids = {s["shop_id"] for s in shops}
        self.assertTrue({"tt-us-west", "tt-us-east", "tt-uk"}.issubset(shop_ids))

    def test_get_shop_by_id(self):
        west = get_shop_by_id("tt-us-west")
        self.assertIsNotNone(west)
        self.assertEqual(west["currency"], "USD")
        self.assertEqual(west["return_window_days"], 30)

        uk = get_shop_by_id("tt-uk")
        self.assertIsNotNone(uk)
        self.assertEqual(uk["currency"], "GBP")
        self.assertEqual(uk["return_window_days"], 14)

        self.assertIsNone(get_shop_by_id("non-existent-shop"))

    def test_get_shop_for_channel_resolution(self):
        # Exact channel key matching
        self.assertEqual(get_shop_for_channel("telegram:us_west")["shop_id"], "tt-us-west")
        self.assertEqual(get_shop_for_channel("telegram:us_east")["shop_id"], "tt-us-east")
        self.assertEqual(get_shop_for_channel("telegram:uk")["shop_id"], "tt-uk")

        # Sub-bot matching
        self.assertEqual(get_shop_for_channel("telegram:bot-us-west")["shop_id"], "tt-us-west")
        self.assertEqual(get_shop_for_channel("telegram:bot-us-east")["shop_id"], "tt-us-east")
        self.assertEqual(get_shop_for_channel("telegram:bot-uk")["shop_id"], "tt-uk")

        # Platform default mappings
        self.assertEqual(get_shop_for_channel("qq")["shop_id"], "tt-us-west")
        self.assertEqual(get_shop_for_channel("weixin")["shop_id"], "tt-us-east")
        self.assertEqual(get_shop_for_channel("discord")["shop_id"], "tt-uk")

        # Unknown fallback returns default shop
        self.assertEqual(get_shop_for_channel("unknown_platform:999")["shop_id"], "tt-us-west")

    def test_configured_faq_files_exist_on_disk(self):
        shops = list_shops()
        for shop in shops:
            for rel_path in shop.get("faq_files", []):
                full_path = PROJECT_ROOT / "workspace" / rel_path
                self.assertTrue(
                    full_path.exists(),
                    f"FAQ file {rel_path} for shop {shop['shop_id']} does not exist at {full_path}"
                )


if __name__ == "__main__":
    unittest.main()
