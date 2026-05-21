import os
import logging
import requests


def send_alert(title: str, body: str) -> None:
    """Send a simple alert to the configured webhook if available.

    This is intentionally minimal — a best-effort notifier.
    """
    webhook = os.environ.get("ALERT_WEBHOOK")
    if not webhook:
        logging.warning("No ALERT_WEBHOOK configured; alert: %s - %s", title, body[:200])
        return
    payload = {"title": title, "body": body}
    try:
        requests.post(webhook, json=payload, timeout=3)
    except Exception as exc:
        logging.exception("Failed to send alert to webhook: %s", exc)
