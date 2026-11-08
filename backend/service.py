"""Shared single-user business logic for FastAPI and in-process Streamlit."""

import json
import sqlite3

import discover_events as discovery
from backend.database import connect, database_path, initialize, upsert_event
from digest import build_digest, render_markdown


class ServiceError(Exception):
    def __init__(self, status_code, detail):
        super().__init__(detail if isinstance(detail, str) else "Refresh failed")
        self.status_code = status_code
        self.detail = detail


def clean_artist_name(name):
    if not isinstance(name, str) or not 1 <= len(name) <= 200 or not name.strip():
        raise ServiceError(422, "Enter an artist name between 1 and 200 characters.")
    return " ".join(name.split())


def generate_digest(records, artists, reference_date=None):
    digest = build_digest(records, artists, reference_date)
    return digest, render_markdown(digest)


class EncoreService:
    def __init__(self, database=None, key_getter=None, seed_artists=None):
        self.database = database_path(database)
        self.key_getter = key_getter or discovery.get_api_key
        initialize(self.database)
        if seed_artists is not None:
            self.seed_watchlist(seed_artists)

    def seed_watchlist(self, names):
        names = [clean_artist_name(name) for name in names]
        with connect(self.database) as db:
            db.execute("CREATE TABLE IF NOT EXISTS demo_seed (id INTEGER PRIMARY KEY)")
            # A transaction and marker keep reruns from restoring deleted artists.
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM demo_seed").fetchone():
                return
            if not db.execute("SELECT 1 FROM artists").fetchone():
                for name in names:
                    db.execute("INSERT OR IGNORE INTO artists (name, name_key) VALUES (?, ?)",
                               (name, name.casefold()))
            db.execute("INSERT INTO demo_seed VALUES (1)")

    def digest(self, reference_date=None):
        return generate_digest(self.events(), self.artists(), reference_date)

    def health(self):
        with connect(self.database) as db:
            db.execute("SELECT 1")
            latest = db.execute("SELECT * FROM refreshes ORDER BY id DESC LIMIT 1").fetchone()
        metadata = dict(latest) if latest else None
        if metadata:
            metadata["details"] = json.loads(metadata["details"])
        return {"status": "ok", "last_refresh": metadata}

    def artists(self):
        with connect(self.database) as db:
            return [dict(row) for row in db.execute("SELECT id, name FROM artists ORDER BY id")]

    def add_artist(self, name):
        name = clean_artist_name(name)
        try:
            with connect(self.database) as db:
                cursor = db.execute("INSERT INTO artists (name, name_key) VALUES (?, ?)",
                                    (name, name.casefold()))
                return {"id": cursor.lastrowid, "name": name}
        except sqlite3.IntegrityError:
            raise ServiceError(409, "Artist is already in the watchlist") from None

    def delete_artist(self, artist_id):
        with connect(self.database) as db:
            if not db.execute("DELETE FROM artists WHERE id = ?", (artist_id,)).rowcount:
                raise ServiceError(404, "Artist not found")
        return None

    def events(self, artist=None, status=None):
        with connect(self.database) as db:
            rows = db.execute("""
                SELECT e.*, a.id AS artist_id, a.name AS artist
                FROM events e JOIN artist_events ae ON e.id = ae.event_id
                JOIN artists a ON a.id = ae.artist_id ORDER BY e.date, e.id, a.id
            """).fetchall()
        records = []
        for row in rows:
            record = dict(row)
            if artist is not None and discovery.normalize(record["artist"]) != discovery.normalize(artist):
                continue
            if status is not None and record["status"].casefold() != status.strip().casefold():
                continue
            # The grouper expects 'id'; use the original provider ID in its input.
            record["database_id"] = record["id"]
            record["id"] = record["provider_event_id"]
            record["confirmed_upcoming"] = False
            records.append(record)
        for group in discovery.group_duplicates(records):
            sources = [item["provider_event_id"] for item in group]
            for record in group:
                record["possible_duplicate_ids"] = sources
                record["listings_grouped"] = len(group)
        return records

    def refresh(self):
        """Refresh saved artists only; optional Gemini analysis stays in the CLI."""
        started = discovery.utc_start_time()
        with connect(self.database) as db:
            watchlist = [dict(row) for row in db.execute("SELECT id, name FROM artists ORDER BY id")]
            refresh_id = db.execute("INSERT INTO refreshes (started_at, status) VALUES (?, ?)",
                                    (started, "running")).lastrowid
        counts = {"discovered": 0, "created": 0, "updated": 0, "skipped": 0}
        details = []
        configuration_error = False
        api_key = None
        if watchlist:
            try:
                api_key = self.key_getter()
            except ValueError:
                configuration_error = True
        for artist in watchlist:
            if configuration_error:
                details.append({"artist": artist["name"], "status": "error",
                                "message": "Ticketmaster API key is not configured"})
                continue
            try:
                attraction = discovery.find_attraction(artist["name"], api_key)
                if attraction is None:
                    details.append({"artist": artist["name"], "status": "no_match",
                                    "message": "No exact-name attraction on the first page"})
                    continue
                listings = discovery.search_events(attraction["id"], api_key, started)
            except RuntimeError:
                # Do not expose provider exception strings or credentials in JSON.
                details.append({"artist": artist["name"], "status": "error",
                                "message": "Ticketmaster discovery failed; previous listings retained"})
                continue
            counts["discovered"] += len(listings)
            with connect(self.database) as db:
                for listing in listings:
                    event = discovery.extract_event(listing, artist["name"])
                    # Missing IDs cannot safely participate in provider upserts.
                    if not isinstance(event["id"], str) or not event["id"].strip() or event["id"] == "Unknown":
                        counts["skipped"] += 1
                        continue
                    # Unexpected nested objects are not usable scalar provider fields.
                    for field, value in event.items():
                        if not isinstance(value, str):
                            event[field] = "Unknown"
                    event["cancellation"] = discovery.cancellation_label(event)
                    result = upsert_event(db, event, artist["id"], started)
                    counts[result] += 1
            details.append({"artist": artist["name"], "status": "ok", "discovered": len(listings)})
        errors = sum(item["status"] == "error" for item in details)
        result_status = "failed" if configuration_error else "partial" if errors else "completed"
        finished = discovery.utc_start_time()
        with connect(self.database) as db:
            db.execute("""UPDATE refreshes SET finished_at = ?, status = ?, discovered = ?,
                       created = ?, updated = ?, skipped = ?, details = ? WHERE id = ?""",
                       (finished, result_status, counts["discovered"], counts["created"],
                        counts["updated"], counts["skipped"], json.dumps(details), refresh_id))
        result = {"id": refresh_id, "status": result_status, "started_at": started,
                  "finished_at": finished, **counts, "artists": details, "analysis_enabled": False}
        if configuration_error:
            raise ServiceError(503, detail=result)
        return result
