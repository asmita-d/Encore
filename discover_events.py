"""Find artist-linked listings using Ticketmaster Discovery API v2."""

import os
import re
import argparse
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv


EVENTS_URL = "https://app.ticketmaster.com/discovery/v2/events.json"
ATTRACTIONS_URL = "https://app.ticketmaster.com/discovery/v2/attractions.json"
ARTISTS = ["Coldplay", "Dua Lipa", "Prateek Kuhad", "Zakir Khan", "Arijit Singh"]
RESULT_LIMIT = 20
REQUEST_TIMEOUT = 15


def get_api_key():
    """Load the project .env without overriding an existing environment value."""
    load_dotenv(Path(__file__).resolve().parent / ".env")
    api_key = os.getenv("TICKETMASTER_API_KEY", "").strip()
    if not api_key:
        raise ValueError("Missing TICKETMASTER_API_KEY. Set it in .env before running.")
    return api_key


def utc_start_time():
    """Return UTC in Ticketmaster's YYYY-MM-DDTHH:mm:ssZ format."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def request_items(url, params, item_type):
    """Read one page, keeping credentials out of error messages."""
    try:
        response = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        data = response.json()
    except requests.RequestException:
        # Exception messages can contain the request URL and its API key.
        raise RuntimeError("Ticketmaster HTTP request failed. Check connectivity and API access.") from None
    except ValueError:
        raise RuntimeError("Ticketmaster returned invalid JSON.") from None

    if not isinstance(data, dict):
        raise RuntimeError("Ticketmaster returned an unexpected JSON structure.")
    embedded = data.get("_embedded") or {}
    events = embedded.get(item_type) if isinstance(embedded, dict) else None
    if events is None:
        return []
    if not isinstance(events, list):
        raise RuntimeError("Ticketmaster returned an unexpected events structure.")
    return [event for event in events if isinstance(event, dict)]


def find_attraction(artist, api_key):
    attractions = request_items(ATTRACTIONS_URL, {
        "apikey": api_key, "keyword": artist, "size": RESULT_LIMIT,
    }, "attractions")
    matches = [item for item in attractions
               if isinstance(item.get("name"), str)
               and item["name"].strip().casefold() == artist.strip().casefold()
               and item.get("id")]
    # Multiple exact-name identities are ambiguous; do not guess which is the artist.
    ids = {item["id"] for item in matches}
    if len(ids) > 1:
        raise RuntimeError("Multiple exact-name attractions found; artist identity is ambiguous.")
    return matches[0] if matches else None


def search_events(attraction_id, api_key, start_time):
    """Make one worldwide attraction request; deliberately do not paginate."""
    return request_items(EVENTS_URL, {
        "apikey": api_key, "attractionId": attraction_id, "size": RESULT_LIMIT,
        "sort": "date,asc", "startDateTime": start_time,
    }, "events")


def nested_value(data, *keys):
    """Read optional nested fields, using a readable fallback."""
    for key in keys:
        if not isinstance(data, dict):
            return "Unknown"
        data = data.get(key)
    return data if data else "Unknown"


def extract_event(event, artist):
    embedded = event.get("_embedded") or {}
    venues = embedded.get("venues") if isinstance(embedded, dict) else None
    venue = venues[0] if isinstance(venues, list) and venues else {}
    return {
        "id": nested_value(event, "id"), "artist": artist,
        "title": nested_value(event, "name"),
        "date": nested_value(event, "dates", "start", "localDate"),
        "time": nested_value(event, "dates", "start", "localTime"),
        "venue": nested_value(venue, "name"),
        "city": nested_value(venue, "city", "name"),
        "country": nested_value(venue, "country", "name"),
        "url": nested_value(event, "url"),
        "status": nested_value(event, "dates", "status", "code"),
    }


def cancellation_label(event):
    if str(event["status"]).casefold() in ("cancelled", "canceled"):
        return "CANCELLED (provider status)"
    # A title is weaker evidence: flag it for review, never call it a normal show.
    if re.search(r"\b(cancelled|canceled|cancellation)\b", str(event["title"]), re.I):
        return "POSSIBLY CANCELLED (title only; review source)"
    return "Provider listing; confirm current status at source"


def normalize(value):
    return " ".join(str(value).casefold().split())


def group_duplicates(events):
    """Group possible duplicates, retaining every original listing."""
    groups = []
    by_key = {}
    for event in events:
        # Require location and title evidence, not just a shared date. Include
        # title and time to keep different performances at the same venue apart.
        fields = ("artist", "date", "venue", "city", "country", "title")
        if any(event[field] == "Unknown" or not str(event[field]).strip() for field in fields):
            groups.append([event])
            continue
        key = tuple(normalize(event[field]) for field in fields + ("time",))
        if key not in by_key:
            by_key[key] = []
            groups.append(by_key[key])
        by_key[key].append(event)
    return groups


def print_event(group):
    print(f"  Listings grouped: {len(group)} (possible duplicates only)")
    # Print every source, so cancellation differences and original IDs/URLs survive.
    for event in group:
        print(f"  {cancellation_label(event)}")
        for field in ("id", "artist", "title", "date", "venue", "city", "country", "url", "status"):
            print(f"    {field}: {event[field]}")
    print()


def print_analysis(records):
    """Keep optional annotations separate from authoritative provider output."""
    from llm_analyzer import analyze_events

    if not records:
        print("\nGemini analysis: no event records to analyze.")
        return
    print(f"\nGemini analysis: first {len(records)} source listings only (unverified).")
    results = analyze_events(records)
    if results is None:
        print("  Analysis unavailable; original provider results retained.")
        return
    for event, result in zip(records, results):
        print(f"  Artist: {event['artist']} | Source ID: {event['id']} | URL: {event['url']}")
        if "CANCELLED" in event["cancellation"]:
            # Deterministic cancellation takes precedence over every LLM annotation.
            print(f"    {event['cancellation']}; not recommended as upcoming. Analysis suppressed.")
            continue
        print(f"    Unverified classification: {result['classification']}")
        print(f"    Title excerpt summary: {result['summary']}")


def main(analyze=False):
    try:
        api_key = get_api_key()
    except ValueError as error:
        print(f"Error: {error}")
        return 1

    print("Attraction-linked provider listings; these are NOT verified artist appearances.")
    start_time = utc_start_time()
    had_failure = False
    analysis_records = []
    for artist in ARTISTS:
        print(f"\n{artist} - attraction-linked listings")
        try:
            attraction = find_attraction(artist, api_key)
            if attraction is None:
                print("  No exact-name attraction found on the first page; no event search performed.")
                continue
            events = search_events(attraction["id"], api_key, start_time)
        except RuntimeError as error:
            print(f"  Error: {error}")
            had_failure = True
            continue
        if not events:
            print("  No event listings found.")
        for group in group_duplicates([extract_event(event, artist) for event in events]):
            print_event(group)
            if analyze:
                for event in group:
                    if len(analysis_records) < 5:
                        analysis_records.append(dict(event, cancellation=cancellation_label(event)))
    if analyze:
        print_analysis(analysis_records)
    return 1 if had_failure else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--analyze", action="store_true",
                        help="Analyze up to five listings with one optional Gemini request")
    raise SystemExit(main(analyze=parser.parse_args().analyze))
