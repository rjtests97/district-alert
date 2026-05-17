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
        response.raise_for_status()
    except requests.RequestException as exc:
        raise MonitoringError(f"Failed to fetch artist page: {exc}") from exc

    return response.text or ""


def extract_event_urls_from_html(html: str) -> List[str]:
    discovered: Set[str] = set()

    try:
        soup = BeautifulSoup(html, "html.parser")
        for anchor in soup.find_all("a", href=True):
            href = anchor.get("href")
            if href:
                discovered.add(href)
    except Exception as exc:  # noqa: BLE001
        logger.warning("BeautifulSoup parsing issue: %s", exc)

    regex_matches = re.findall(
        r"""(?:"|')(/events/[^"'?#\s]+)(?:\?[^"'#\s]*)?(?:"|')""",
        html,
        flags=re.IGNORECASE,
    )
    discovered.update(regex_matches)

    normalized: Set[str] = set()
    for raw_url in discovered:
        normalized_url = normalize_event_url(raw_url)
        if "/events/" not in normalized_url.lower():
            continue
        normalized.add(normalized_url)

    return sorted(normalized)


def filter_latent_event_urls(urls: Iterable[str]) -> List[str]:
    seen: Set[str] = set()
    matches: List[str] = []

    for url in urls:
        normalized = normalize_event_url(url)
        lowered = normalized.lower()
        if KEYWORD not in lowered:
            continue
        if lowered in seen:
            continue
        seen.add(lowered)
        matches.append(normalized)

    return sorted(matches)


def send_telegram_alert(bot_token: str, chat_id: str, event_urls: List[str]) -> None:
    if not event_urls:
        return

    lines = [
        "🚨 INDIA'S GOT LATENT ALERT 🚨",
        "",
        "New event found:" if len(event_urls) == 1 else "New events found:",
        *event_urls,
    ]
    message = "\n".join(lines)

    endpoint = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message,
        "disable_web_page_preview": True,
    }

    try:
        response = requests.post(endpoint, data=payload, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        body = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise MonitoringError(f"Telegram notification failed: {exc}") from exc

    if not body.get("ok"):
        description = body.get("description", "Unknown Telegram API error")
        raise MonitoringError(f"Telegram notification failed: {description}")


def send_test_telegram_alert(bot_token: str, chat_id: str) -> None:
    endpoint = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    message = (
        "TEST ALERT\n\n"
        "District latent monitor is connected to Telegram and can send messages."
    )
    payload = {
        "chat_id": chat_id,
        "text": message,
        "disable_web_page_preview": True,
    }

    try:
        response = requests.post(endpoint, data=payload, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        body = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise MonitoringError(f"Telegram test notification failed: {exc}") from exc

    if not body.get("ok"):
        description = body.get("description", "Unknown Telegram API error")
        raise MonitoringError(f"Telegram test notification failed: {description}")


def main() -> int:
    bot_token = os.environ.get("BOT_TOKEN", "").strip()
    chat_id = os.environ.get("CHAT_ID", "").strip()
    force_test_alert = os.environ.get("FORCE_TEST_ALERT", "").strip().lower() == "true"

    try:
        state = load_state()
        current_time = utc_now_iso()

        if force_test_alert:
            if not bot_token or not chat_id:
                raise MonitoringError("FORCE_TEST_ALERT was requested, but BOT_TOKEN and/or CHAT_ID are missing.")
            send_test_telegram_alert(bot_token, chat_id)
            state["last_checked_at"] = current_time
            save_state(state)
            logger.info("Sent Telegram test alert.")
            return 0

        html = fetch_artist_page()
        event_urls = extract_event_urls_from_html(html)
        latent_urls = filter_latent_event_urls(event_urls)

        logger.info("Discovered %s event URLs, %s latent match(es).", len(event_urls), len(latent_urls))

        known_urls = {
            normalize_event_url(url).lower()
            for url in state.get("known_latent_event_urls", [])
            if normalize_event_url(url)
        }
        current_urls_by_key = {url.lower(): url for url in latent_urls}
        new_urls = sorted(
            current_urls_by_key[key]
            for key in current_urls_by_key
            if key not in known_urls
        )

        if not STATE_FILE.exists():
            logger.info("First run detected. Seeding state without sending alerts.")
            state["known_latent_event_urls"] = latent_urls
            state["last_checked_at"] = current_time
            save_state(state)
            return 0

        if new_urls:
            if not bot_token or not chat_id:
                raise MonitoringError(
                    "New latent event(s) found, but BOT_TOKEN and/or CHAT_ID are missing."
                )
            send_telegram_alert(bot_token, chat_id, new_urls)
            state["last_alerted_at"] = current_time
            logger.info("Sent Telegram alert for %s new latent event(s).", len(new_urls))
        else:
            logger.info("No new latent events found.")

        state["known_latent_event_urls"] = latent_urls
        state["last_checked_at"] = current_time
        save_state(state)
        return 0

    except MonitoringError as exc:
        logger.error("%s", exc)
        return 1
    except Exception as exc:  # noqa: BLE001
        logger.exception("Unexpected error: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
