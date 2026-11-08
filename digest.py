"""Offline weekly provider-listing digest. No storage, network, or SDK dependencies."""

import html
import re
from datetime import date, timedelta
from urllib.parse import quote, urlsplit


def _normalize(value):
    return " ".join(str(value or "").casefold().split())


def _identity(record):
    return record.get("provider_event_id", record.get("id"))


def _duplicates(left, right):
    # Follow discovery's conservative identity fields. API hints must also agree
    # when supplied; never treat a shared date or one overlapping hint as proof.
    fields = ("artist", "date", "venue", "city", "country", "title", "time")
    for field in fields:
        a, b = _normalize(left.get(field)), _normalize(right.get(field))
        if field != "time" and (a in ("", "unknown") or b in ("", "unknown")):
            return False
        if a != b:
            return False
    for record, other in ((left, right), (right, left)):
        hints = record.get("possible_duplicate_ids")
        if hints is not None and _identity(other) not in hints:
            return False
    return True


def build_digest(records, saved_artists, reference_date=None):
    """Return JSON-friendly sections from stored records without changing inputs.

    saved_artists accepts artist names or API artist dictionaries. The window is
    [reference_date, reference_date + 7 days), using provider-local dates.
    Possible duplicates retain complete original records in each item's sources.
    """
    start = reference_date if reference_date is not None else date.today()
    end = start + timedelta(days=6)
    names = {_normalize(a["name"] if isinstance(a, dict) else a):
             a["name"] if isinstance(a, dict) else a for a in saved_artists}
    sections = {}
    for record in records:
        artist = names.get(_normalize(record.get("artist")))
        if not artist:
            continue
        status = _normalize(record.get("status"))
        warning = _normalize(record.get("cancellation"))
        if (status in ("cancelled", "canceled")
                or re.search(r"\b(cancelled|canceled|cancellation)\b",
                             warning + " " + str(record.get("title", "")), re.I)):
            continue
        try:
            event_date = date.fromisoformat(record.get("date", ""))
        except (ValueError, TypeError):
            continue
        if not start <= event_date <= end:
            continue
        items = sections.setdefault(artist, [])
        item = next((item for item in items
                     if all(_duplicates(source, record) for source in item["sources"])), None)
        if item is None:
            item = {field: record.get(field, "Unknown") for field in
                    ("title", "date", "time", "venue", "city", "country")}
            item["sources"] = []
            items.append(item)
        item["sources"].append(dict(record))
    artists = []
    for artist in sorted(sections, key=str.casefold):
        items = sorted(sections[artist], key=lambda e: (e["date"], str(e["time"]), str(e["title"])))
        artists.append({"artist": artist, "events": items})
    count = sum(len(section["events"]) for section in artists)
    item_word = "item" if count == 1 else "items"
    artist_word = "artist" if len(artists) == 1 else "artists"
    return {"generated_on": start.isoformat(), "start_date": start.isoformat(),
            "end_date": end.isoformat(), "artists": artists,
            "introduction": f"This week's saved provider listings include {count} display {item_word} "
                            f"across {len(artists)} {artist_word}. These are provider listings, not verified "
                            "artist appearances. Check original sources for lineups and current status."}


def _text(value):
    text = html.escape(" ".join(str(value).split()), quote=False)
    return re.sub(r"([\\`*_{}\[\]()#+.!|>~-])", r"\\\1", text)


def render_markdown(digest):
    """Render only supplied facts, escaping provider text and unsafe links."""
    lines = ["# Encore Weekly Digest", "",
             f"{digest['start_date']} through {digest['end_date']} (inclusive)", "",
             digest["introduction"], "",
             "Dates and times are venue local. Saved listings may be stale. "
             "Cancelled, possibly cancelled, past and unknown-date listings are excluded.", ""]
    if not digest["artists"]:
        lines.append("No eligible saved provider listings for your artists this week.")
    for section in digest["artists"]:
        lines.extend([f"## {_text(section['artist'])}", ""])
        for item in section["events"]:
            lines.extend([f"### {_text(item['title'])}", "",
                          f"- Date: {_text(item['date'])}; time: {_text(item['time'])} (venue local)",
                          f"- Location: {_text(item['venue'])}, {_text(item['city'])}, {_text(item['country'])}"])
            if len(item["sources"]) > 1:
                lines.append("- Possible duplicate listings combined; this does not confirm the same performance.")
            for source in item["sources"]:
                label = f"{source.get('provider', 'Provider')} ID: {_identity(source)}"
                url = source.get("url", "Unknown")
                try:
                    parsed = urlsplit(url)
                    safe = parsed.scheme in ("http", "https") and bool(parsed.netloc)
                except (ValueError, TypeError):
                    safe = False
                link = f"[{_text(label)}](<{quote(url, safe=':/?&=%#@+;,')}>)" if safe else _text(label)
                lines.append(f"- Source: {link}; status: {_text(source.get('status', 'Unknown'))}; "
                             f"last seen: {_text(source.get('last_seen_at', 'Unknown'))}")
                if not safe:
                    lines.append(f"  Original URL (unavailable as a link): {_text(url)}")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"
