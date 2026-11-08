"""HTTP/local adapters and offline-testable presentation helpers for Encore."""

import os
import re
import sqlite3
import tempfile
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit

import requests

from backend.service import EncoreService, ServiceError, generate_digest

class APIError(Exception):
    def __init__(self, message, details=None):
        super().__init__(message)
        self.details = details


class EncoreAPI:
    demo = False
    local = False

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

    def digest(self, records, artists, reference_date=None):
        return generate_digest(records, artists, reference_date)


class LocalAPI(EncoreAPI):
    """Small adapter keeping the Streamlit call sites the same in either mode."""
    local = True
    url = "In-process SQLite"

    def __init__(self, service, demo=False):
        self.service = service
        self.demo = demo

    def request(self, method, path, **kwargs):
        try:
            if method == "GET" and path in ("/artists", "/events", "/health"):
                return getattr(self.service, path[1:])(**kwargs.get("params", {}))
            if method == "POST" and path == "/artists":
                return self.service.add_artist(kwargs.get("json", {}).get("name"))
            if method == "DELETE" and path.startswith("/artists/"):
                return self.service.delete_artist(int(path.rsplit("/", 1)[1]))
            if method == "POST" and path == "/refresh":
                return self.service.refresh()
            raise APIError("Unsupported local operation.")
        except ServiceError as error:
            raise APIError(str(error), error.detail if isinstance(error.detail, dict) else None) from None
        except sqlite3.Error:
            raise APIError("Local storage is unavailable. Check DATABASE_URL and folder permissions.") from None

    def digest(self, records, artists, reference_date=None):
        try:
            return self.service.digest(reference_date)
        except sqlite3.Error:
            raise APIError("Local storage is unavailable. Reload saved data before generating a digest.") from None


def create_client(secrets=None):
    """Environment overrides Streamlit secrets; credentials are read only on refresh."""
    def setting(name, default=None):
        if name in os.environ:
            return os.environ[name]
        return secrets.get(name, default) if secrets is not None else default

    mode = str(setting("ENCORE_MODE", "http")).strip().casefold()
    if mode == "http":
        return EncoreAPI()
    if mode != "local":
        raise APIError("ENCORE_MODE must be http or local.")
    demo = str(setting("ENCORE_DEMO", "false")).casefold() in ("true", "1", "yes")
    seeds = setting("ENCORE_SEED_ARTISTS", "Coldplay,Dua Lipa")
    if isinstance(seeds, str):
        seeds = [name.strip() for name in seeds.split(",") if name.strip()]
    if not isinstance(seeds, (list, tuple)):
        raise APIError("ENCORE_SEED_ARTISTS must be a comma-separated string or list of names.")

    def api_key():
        value = str(setting("TICKETMASTER_API_KEY", "")).strip()
        if not value:
            # Preserve local .env support through the existing discovery helper.
            if not demo:
                from discover_events import get_api_key
                return get_api_key()
            raise ValueError("Ticketmaster API key is not configured")
        return value

    database = setting("DATABASE_URL")
    if demo and not database:
        database = Path(tempfile.gettempdir()) / "encore-demo.sqlite3"
    try:
        return LocalAPI(EncoreService(database, key_getter=api_key,
                                     seed_artists=seeds if demo else None), demo)
    except (ValueError, ServiceError, sqlite3.Error, OSError):
        raise APIError("Cannot initialize local storage. Check database and seed configuration.") from None


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
