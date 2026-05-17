import json
import logging
import os
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

import requests


DISTRICT_BASE_URL = "https://www.district.in"
SEARCH_API_URL = "https://www.district.in/gw/web/search"
KEYWORD = "latent"
STATE_FILE = Path(__file__).with_name("state.json")
REQUEST_TIMEOUT = 20
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
LATENT_PATTERN = re.compile(r"\blatent\b", re.IGNORECASE)


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)


class MonitoringError(Exception):
    """Raised for recoverable monitoring failures."""


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def normalize_event_url(url: str) -> str:
    cleaned = (url or "").strip()
    if not cleaned:
        return ""
    if cleaned.startswith("http://") or cleaned.startswith("https://"):
        full_url = cleaned
    else:
        full_url = f"{DISTRICT_BASE_URL.rstrip('/')}/{cleaned.lstrip('/')}"
    return full_url.split("#", 1)[0].rstrip("/")


def build_event_url_from_slug(slug: str) -> str:
    return normalize_event_url(f"/events/{slug}")


def load_state() -> dict:
    if not STATE_FILE.exists():
        return {
            "version": 2,
            "keyword": KEYWORD,
            "search_api_url": SEARCH_API_URL,
            "known_latent_event_urls": [],
            "last_checked_at": None,
            "last_alerted_at": None,
        }

    try:
        with STATE_FILE.open("r", encoding="utf-8") as handle:
            state = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise MonitoringError(f"Failed to load {STATE_FILE.name}: {exc}") from exc

    if not isinstance(state, dict):
        raise MonitoringError(f"{STATE_FILE.name} must contain a JSON object.")

    state.setdefault("version", 2)
    state.setdefault("keyword", KEYWORD)
    state.setdefault("search_api_url", SEARCH_API_URL)
    state.setdefault("known_latent_event_urls", [])
    state.setdefault("last_checked_at", None)
    state.setdefault("last_alerted_at", None)
    return state


def save_state(state: dict) -> None:
    tmp_path = STATE_FILE.with_suffix(".json.tmp")
    with tmp_path.open("w", encoding="utf-8") as handle:
        json.dump(state, handle, indent=2, sort_keys=True)
        handle.write("\n")
    tmp_path.replace(STATE_FILE)


def district_headers() -> Dict[str, str]:
    return {
        "User-Agent": USER_AGENT,
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "Content-Type": "application/json",
        "Origin": DISTRICT_BASE_URL,
        "Referer": f"{DISTRICT_BASE_URL}/",
