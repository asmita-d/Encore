"""HTTP boundary and small, offline-testable presentation helpers for Encore."""

import os
import re
from datetime import date
from urllib.parse import urlsplit

import requests


class APIError(Exception):
    def __init__(self, message, details=None):
        super().__init__(message)
        self.details = details


class EncoreAPI:
    def __init__(self):
        self.url = (os.getenv("ENCORE_API_URL", "http://127.0.0.1:8000").strip()
                    or "http://127.0.0.1:8000").rstrip("/")

    def request(self, method, path, **kwargs):
        # Refresh can take two provider requests per artist. Never retry mutations:
        # a timeout does not mean the server stopped processing the request.
        try:
            response = requests.request(method, self.url + path,
                                        timeout=(5, 300 if path == "/refresh" else 15), **kwargs)
        except requests.Timeout:
            raise APIError("The API timed out. The action may still be running; reload saved data before retrying.") from None
        except requests.RequestException:
            raise APIError("Cannot reach Encore's API. Start FastAPI and check ENCORE_API_URL.") from None
        try:
            data = response.json() if response.status_code != 204 else None
        except ValueError:
            raise APIError("The API returned an unreadable response. Check the FastAPI terminal.") from None
        if not response.ok:
            detail = data.get("detail") if isinstance(data, dict) else None
            message = {409: "That artist is already on your watchlist.",
                       404: "That artist no longer exists. Reload saved data.",
                       422: "Enter an artist name between 1 and 200 characters."}.get(
                           response.status_code, f"The API could not complete the request (HTTP {response.status_code}).")
            raise APIError(message, detail if isinstance(detail, dict) else None)
        expected = list if path in ("/artists", "/events") and method == "GET" else dict
        if response.status_code != 204 and (
                not isinstance(data, expected)
                or isinstance(data, list) and any(not isinstance(row, dict) for row in data)):
            raise APIError("The API returned an unexpected response format.")
        return data


def event_bucket(event, today=None):
    """Use provider-local calendar dates; unknown dates never imply upcoming."""
    status = str(event.get("status", "")).casefold()
    warning = str(event.get("cancellation", "")).casefold()
    if status in ("cancelled", "canceled") or warning.startswith("cancelled"):
        return "Cancelled"
    if "possibly cancelled" in warning or re.search(
            r"\b(cancelled|canceled|cancellation)\b", str(event.get("title", "")), re.I):
        return "Possibly cancelled"
    try:
        event_date = date.fromisoformat(event.get("date", ""))
    except (ValueError, TypeError):
        return "Date unknown"
    return "Upcoming" if event_date >= (today or date.today()) else "Past"


def unique_listings(events):
    """Count source listings once even when linked to multiple saved artists."""
    return list({(e.get("provider"), e.get("provider_event_id", e.get("id"))): e
                 for e in events}.values())


def source_url(value):
    """Only offer navigable HTTP(S) source links; retain other values as text."""
    try:
        parsed = urlsplit(value)
        return value if parsed.scheme in ("http", "https") and parsed.netloc else None
    except (ValueError, TypeError):
        return None
