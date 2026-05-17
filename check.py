import json
import logging
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, List, Set
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup


ARTIST_URL = "https://www.district.in/events/samay-raina/artist"
DISTRICT_BASE_URL = "https://www.district.in"
KEYWORD = "latent"
STATE_FILE = Path(__file__).with_name("state.json")
REQUEST_TIMEOUT = 20
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


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
    cleaned = url.strip()
    if not cleaned:
        return ""
    full_url = urljoin(DISTRICT_BASE_URL, cleaned)
    return full_url.split("#", 1)[0].rstrip("/")


def load_state() -> dict:
    if not STATE_FILE.exists():
        return {
            "version": 1,
            "artist_url": ARTIST_URL,
            "keyword": KEYWORD,
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

    state.setdefault("version", 1)
    state.setdefault("artist_url", ARTIST_URL)
    state.setdefault("keyword", KEYWORD)
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


def fetch_artist_page() -> str:
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    }

    try:
        response = requests.get(
            ARTIST_URL,
            headers=headers,
            timeout=REQUEST_TIMEOUT,
        )
