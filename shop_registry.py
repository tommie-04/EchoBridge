"""Registry and channel resolution for multi-store TikTok storefronts."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_SHOPS_CONFIG = PROJECT_ROOT / "shops.json"


def load_shops_config(config_path: Path | str | None = None) -> dict[str, Any]:
    target = Path(config_path).expanduser().resolve() if config_path else DEFAULT_SHOPS_CONFIG
    if not target.exists():
        raise FileNotFoundError(f"Store registry configuration not found: {target}")
    return json.loads(target.read_text(encoding="utf-8"))


def list_shops(config_path: Path | str | None = None) -> list[dict[str, Any]]:
    config = load_shops_config(config_path)
    return config.get("shops", [])


def get_default_shop_id(config_path: Path | str | None = None) -> str:
    config = load_shops_config(config_path)
    return config.get("default_shop_id", "tt-us-west")


def get_shop_by_id(shop_id: str, config_path: Path | str | None = None) -> dict[str, Any] | None:
    for shop in list_shops(config_path):
        if shop.get("shop_id") == shop_id:
            return shop
    return None


def get_shop_for_channel(channel: str, config_path: Path | str | None = None) -> dict[str, Any]:
    """Resolve incoming Nanobot channel string (e.g. 'telegram:us_west' or 'telegram') to a shop."""
    shops = list_shops(config_path)
    cleaned = (channel or "").strip().lower()

    # 1. Exact match with any configured channel identifier
    for shop in shops:
        configured_channels = [c.lower() for c in shop.get("channels", [])]
        if cleaned in configured_channels:
            return shop

    # 2. Match by channel platform prefix (e.g. channel='telegram:bot-us-west' matching pattern)
    if ":" in cleaned:
        prefix, sub = cleaned.split(":", 1)
        for shop in shops:
            configured_channels = [c.lower() for c in shop.get("channels", [])]
            for c in configured_channels:
                if c.startswith(f"{prefix}:") and sub in c:
                    return shop

    # 3. Fallback to channel platform name alone (e.g. 'qq' or 'telegram')
    platform_name = cleaned.split(":", 1)[0]
    for shop in shops:
        configured_channels = [c.lower() for c in shop.get("channels", [])]
        if platform_name in configured_channels:
            return shop

    # 4. Final fallback: default shop
    default_id = get_default_shop_id(config_path)
    default_shop = get_shop_by_id(default_id, config_path)
    if default_shop:
        return default_shop
    if shops:
        return shops[0]
    raise ValueError("No shops configured in registry")
