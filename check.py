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
        html = fetch_artist_page()
        event_urls = extract_event_urls_from_html(html)
        latent_urls = filter_latent_event_urls(event_urls)
        current_time = utc_now_iso()

        logger.info("Discovered %s event URLs, %s latent match(es).", len(event_urls), len(latent_urls))

        if force_test_alert:
            if not bot_token or not chat_id:
                raise MonitoringError("FORCE_TEST_ALERT was requested, but BOT_TOKEN and/or CHAT_ID are missing.")
            send_test_telegram_alert(bot_token, chat_id)
            state["last_checked_at"] = current_time
            save_state(state)
            logger.info("Sent Telegram test alert.")
            return 0

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
