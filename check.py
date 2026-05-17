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
        "x-app-type": "ed_web",
        "x-app-version": "11.11.1",
        "x-available-tabs": "events,movies,dining,attraction,play,shopping,ipl",
        "x-city-id": "3",
        "x-city-name": "Mumbai",
        "x-client-id": "district-web",
        "x-country-id": "1",
        "x-device-id": "116b984b-d018-47c6-8f01-94479de122c2",
        "x-gps-lat": "19.128567073099326",
        "x-gps-lng": "72.87749886851958",
        "x-gps-permission-given": "0",
        "x-guest-token": "1212",
        "x-is-dining-supported": "true",
        "x-is-events-supported": "true",
        "x-is-granular-loc": "false",
        "x-is-movies-supported": "true",
        "x-pcity-id": "20",
        "x-pcity-key": "mumbai",
        "x-pcity-name": "Mumbai",
        "x-place-id": "ChIJE6xvrR_I5zsRalYN9TPYB9M",
        "x-place-type": "GOOGLE_PLACE",
        "x-pstate-key": "maharashtra",
        "x-subzone-id": "2117",
        "x-user-lat": "19.128567073099326",
        "x-user-lng": "72.87749886851958",
    }


def fetch_search_results(keyword: str) -> dict:
    payload = {
        "get_search_results_request_type": 1,
        "post_body": {"hp_selected_tab_id": "home_v2"},
        "search_id": str(uuid.uuid4()),
        "keyword": keyword,
        "tab_id": "all",
    }

    try:
        response = requests.post(
            SEARCH_API_URL,
            headers=district_headers(),
            json=payload,
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        data = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise MonitoringError(f"Failed to fetch District search results: {exc}") from exc

    if not isinstance(data, dict):
        raise MonitoringError("District search API returned a non-object response.")

    return data


def is_true_latent_match(text: str) -> bool:
    return bool(LATENT_PATTERN.search(text or ""))


def extract_latent_events(search_response: dict) -> List[dict]:
    raw_results = search_response.get("results", [])
    if not isinstance(raw_results, list):
        raise MonitoringError("District search API response did not include a valid results list.")

    latent_events: Dict[str, dict] = {}

    for item in raw_results:
        if not isinstance(item, dict):
            continue
        if item.get("entity_type") != "EntityTypeEvent":
            continue

        title = (item.get("display_title") or "").strip()
        metadata = item.get("metadata") or {}
        if not isinstance(metadata, dict):
            metadata = {}

        slug = (metadata.get("slug") or "").strip()
        artist_name = (metadata.get("artist_name") or "").strip()
        venue_name = (metadata.get("venue_name") or "").strip()
        city_name = (metadata.get("city_name") or "").strip()

        haystacks = [title, slug, artist_name, venue_name, city_name]
        if not any(is_true_latent_match(value) for value in haystacks):
            continue

        if not slug:
            logger.warning("Skipping latent-like event without slug: %s", title)
            continue

        event_url = build_event_url_from_slug(slug)
        event_key = event_url.lower()
        latent_events[event_key] = {
            "title": title,
            "url": event_url,
            "slug": slug,
            "artist_name": artist_name,
            "venue_name": venue_name,
            "city_name": city_name,
        }

    return sorted(latent_events.values(), key=lambda event: event["url"].lower())


def send_telegram_alert(bot_token: str, chat_id: str, events: List[dict]) -> None:
    if not events:
        return

    lines = ["🚨 INDIA'S GOT LATENT ALERT 🚨", ""]
    for index, event in enumerate(events, start=1):
        lines.append(f"New event found #{index}:")
        lines.append(event["title"])
        lines.append(event["url"])
        if index != len(events):
            lines.append("")

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

        search_response = fetch_search_results(KEYWORD)
        latent_events = extract_latent_events(search_response)
        latent_urls = [event["url"] for event in latent_events]

        logger.info("District search returned %s true latent event match(es).", len(latent_events))

        known_urls = {
            normalize_event_url(url).lower()
            for url in state.get("known_latent_event_urls", [])
            if normalize_event_url(url)
        }
        current_events_by_key = {event["url"].lower(): event for event in latent_events}
        new_events = sorted(
            (
                current_events_by_key[key]
                for key in current_events_by_key
                if key not in known_urls
            ),
            key=lambda event: event["url"].lower(),
        )

        if not STATE_FILE.exists():
            logger.info("First run detected. Seeding state without sending alerts.")
            state["known_latent_event_urls"] = latent_urls
            state["last_checked_at"] = current_time
            save_state(state)
            return 0

        if new_events:
            if not bot_token or not chat_id:
                raise MonitoringError(
                    "New latent event(s) found, but BOT_TOKEN and/or CHAT_ID are missing."
                )
            send_telegram_alert(bot_token, chat_id, new_events)
            state["last_alerted_at"] = current_time
            logger.info("Sent Telegram alert for %s new latent event(s).", len(new_events))
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
