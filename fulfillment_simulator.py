"""Deterministic TikTok Shipping fixtures and USPS-format demo label preparation.

All records are synthetic. PDFs demonstrate the warehouse workflow and cannot be
used as postage. The service prepares documents; it never purchases postage or
sends a job to a printer.
"""
from __future__ import annotations

from collections import Counter
from datetime import date, datetime
from hashlib import sha256
from io import BytesIO
import json
from zoneinfo import ZoneInfo

WAREHOUSE = "Los Angeles warehouse"
TIMEZONE = "America/Los_Angeles"
PRODUCTS = {
    "pad-blk": "Pad / Black",
    "pad-org": "Pad / Orange",
    "pad-sil": "Pad / Silver",
}


def list_mock_shops() -> list[dict]:
    return [
        {
            "shop_id": f"mock-us-{number:02d}",
            "name": f"Lumen US Shop {number:02d}",
            "warehouse": WAREHOUSE,
            "region": "US",
            "mode": "simulation",
        }
        for number in range(1, 21)
    ]


def _selected_shops(shop_ids: list[str] | None) -> list[dict]:
    shops = {shop["shop_id"]: shop for shop in list_mock_shops()}
    if shop_ids is None:
        return list(shops.values())
    if not isinstance(shop_ids, list) or not shop_ids:
        raise ValueError("Select at least one simulated shop")
    if any(not isinstance(shop_id, str) or shop_id not in shops for shop_id in shop_ids):
        raise ValueError("Unknown simulated shop")
    return [shops[shop_id] for shop_id in sorted(set(shop_ids))]


def _ship_date(value: str) -> str:
    try:
        parsed = date.fromisoformat(value)
    except (TypeError, ValueError):
        raise ValueError("ship_date must be an ISO date (YYYY-MM-DD)") from None
    if parsed.isoformat() != value:
        raise ValueError("ship_date must use YYYY-MM-DD")
    return value


def _packages(shops: list[dict], ship_date: str) -> list[dict]:
    packages = []
    date_key = ship_date.replace("-", "")
    for shop in shops:
        number = int(shop["shop_id"].rsplit("-", 1)[1])
        if number == 1:
            contents = [[("pad-blk", 1)]] * 2 + [[("pad-org", 1)]] * 5
        elif number == 2:
            contents = [[("pad-blk", 1)]] * 3 + [[("pad-org", 1)]] * 6 + [[("pad-sil", 1)]] * 5
        else:
            contents = [[("pad-blk", 2)], [("pad-org", 1), ("pad-sil", 1)]]
        # Every shop also demonstrates a cancelled order and a pending label.
        for index, items in enumerate(contents + [[("pad-org", 1)], [("pad-sil", 1)]], 1):
            state = "ready"
            if index == len(contents) + 1:
                state = "cancelled"
            elif index == len(contents) + 2:
                state = "label_pending"
            suffix = f"{number:02d}-{date_key}-{index:03d}"
            packages.append({
                "package_id": f"DEMO-PKG-{suffix}",
                "order_id": f"DEMO-ORDER-{suffix}",
                "shop_id": shop["shop_id"],
                "shop_name": shop["name"],
                "warehouse": WAREHOUSE,
                "ship_date": ship_date,
                "shipping_type": "TIKTOK",
                "carrier": "USPS",
                "status": state,
                "label_available": state == "ready",
                "tracking_reference": f"DEMO-ONLY-{suffix}",
                "items": [
                    {"sku": sku, "product_name": PRODUCTS[sku], "quantity": quantity}
                    for sku, quantity in items
                ],
            })
    return packages


def list_mock_packages(shop_ids: list[str] | None = None) -> list[dict]:
    """List synthetic orders for the current warehouse-local day."""
    today = datetime.now(ZoneInfo(TIMEZONE)).date().isoformat()
    return _packages(_selected_shops(shop_ids), today)


def build_batch(shop_ids: list[str], ship_date: str) -> dict:
    """Prepare each eligible package exactly once, grouped by warehouse SKU.

    No model inference or product-name fuzzy matching is used. A mixed-SKU
    package belongs to the mixed group and retains every item on its label.
    """
    shops = _selected_shops(shop_ids)
    ship_date = _ship_date(ship_date)
    grouped = {}
    excluded = []
    for package in _packages(shops, ship_date):
        if package["status"] != "ready":
            excluded.append({
                "package_id": package["package_id"],
                "shop_id": package["shop_id"],
                "reason": package["status"],
            })
            continue
        group_id = package["items"][0]["sku"] if len(package["items"]) == 1 else "mixed"
        grouped.setdefault(group_id, []).append(package)
    groups = []
    for group_id in sorted(grouped):
        packages = sorted(grouped[group_id], key=lambda package: package["package_id"])
        totals = Counter()
        for package in packages:
            for item in package["items"]:
                totals[item["sku"]] += item["quantity"]
        groups.append({
            "group_id": group_id,
            "sku": group_id,
            "package_count": len(packages),
            "unit_count": sum(totals.values()),
            "items": [{"sku": sku, "quantity": quantity} for sku, quantity in sorted(totals.items())],
            "packages": packages,
        })
    batch = {
        "mode": "simulation",
        "ship_date": ship_date,
        "timezone": TIMEZONE,
        "warehouse": WAREHOUSE,
        "selected_shops": shops,
        "package_count": sum(group["package_count"] for group in groups),
        "unit_count": sum(group["unit_count"] for group in groups),
        "groups": groups,
        "excluded_count": len(excluded),
        "excluded": excluded,
    }
    canonical = json.dumps(batch, sort_keys=True, separators=(",", ":")).encode()
    batch["batch_id"] = "demo-" + sha256(canonical).hexdigest()[:24]
    return batch


def render_batch_pdf(batch: dict, group_id: str | None = None) -> bytes:
    """Render 4x6-inch demo labels with a separate warehouse footer overlay.

    Original mock transport content occupies the top area. SKU quantities are
    merged into a reserved bottom area to exercise the intended PDF pipeline.
    """
    from pypdf import PdfReader, PdfWriter
    from reportlab.lib.units import inch
    from reportlab.pdfgen import canvas

    groups = batch["groups"]
    if group_id is not None:
        groups = [group for group in groups if group["group_id"] == group_id]
        if not groups:
            raise ValueError("Unknown label group")
    width, height = 4 * inch, 6 * inch
    writer = PdfWriter()
    for group in groups:
        for package in group["packages"]:
            original = BytesIO()
            page = canvas.Canvas(original, pagesize=(width, height))
            page.setTitle("SIMULATED USPS LABEL - NOT VALID FOR POSTAGE")
            page.setFont("Helvetica-Bold", 17)
            page.drawString(15, height - 30, "USPS / TikTok Shipping")
            page.setFillColorRGB(0.7, 0.1, 0.1)
            page.setFont("Helvetica-Bold", 14)
            page.drawString(15, height - 53, "DEMO - NOT VALID FOR POSTAGE")
            page.setFillColorRGB(0, 0, 0)
            page.setFont("Helvetica", 9)
            for offset, text in enumerate([
                f"Warehouse: {WAREHOUSE}",
                f"Shop: {package['shop_name']}",
                f"Date: {batch['ship_date']} ({TIMEZONE})",
                f"Order: {package['order_id']}",
                f"Package: {package['package_id']}",
                "Recipient: SYNTHETIC CUSTOMER",
                "Address: NO REAL DELIVERY ADDRESS",
            ]):
                page.drawString(15, height - 85 - offset * 19, text)
            page.setFont("Helvetica-Bold", 10)
            page.drawString(15, 175, "NO VALID TRACKING NUMBER / BARCODE")
            page.setFont("Helvetica", 8)
            page.drawString(15, 158, package["tracking_reference"])
            page.line(15, 120, width - 15, 120)
            page.showPage()
            page.save()

            footer = BytesIO()
            stamp = canvas.Canvas(footer, pagesize=(width, height))
            stamp.setFont("Helvetica-Bold", 10)
            stamp.drawString(15, 103, "WAREHOUSE PICKING / PACKAGE CONTENTS")
            stamp.setFont("Helvetica", 10)
            for index, item in enumerate(package["items"]):
                stamp.drawString(15, 83 - index * 17,
                                 f"{item['sku']}  x {item['quantity']}  /  {item['product_name']}")
            stamp.setFont("Helvetica", 8)
            stamp.drawString(15, 25, "SIMULATION ONLY - No postage purchased / No print job sent")
            stamp.showPage()
            stamp.save()
            label = PdfReader(original).pages[0]
            label.merge_page(PdfReader(footer).pages[0])
            writer.add_page(label)
    output = BytesIO()
    writer.add_metadata({"/Title": "EchoBridge simulated shipping labels", "/Subject": batch["batch_id"]})
    writer.write(output)
    return output.getvalue()
