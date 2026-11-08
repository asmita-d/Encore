"""Local, single-user Encore API. Start with uvicorn backend.main:app."""

import json
import sqlite3
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query, Response
from pydantic import BaseModel, Field

import discover_events as discovery
from backend.database import connect, database_path, initialize, upsert_event


class ArtistInput(BaseModel):
    name: str = Field(min_length=1, max_length=200)


def create_app(database=None):
    """Tests supply their own temporary database; imports do not create tables."""
    @asynccontextmanager
    async def lifespan(app):
        app.state.database = database_path(database)
        initialize(app.state.database)
        yield

    app = FastAPI(title="Encore", version="0.1.0", lifespan=lifespan)

    @app.get("/health")
    def health():
        with connect(app.state.database) as db:
            db.execute("SELECT 1")
            latest = db.execute("SELECT * FROM refreshes ORDER BY id DESC LIMIT 1").fetchone()
        metadata = dict(latest) if latest else None
        if metadata:
            metadata["details"] = json.loads(metadata["details"])
        return {"status": "ok", "last_refresh": metadata}

    @app.get("/artists")
    def artists():
        with connect(app.state.database) as db:
            return [dict(row) for row in db.execute("SELECT id, name FROM artists ORDER BY id")]

    @app.post("/artists", status_code=201)
    def add_artist(artist: ArtistInput):
        name = " ".join(artist.name.split())
        if not name:
            raise HTTPException(422, "Artist name must not be blank")
        try:
            with connect(app.state.database) as db:
                cursor = db.execute("INSERT INTO artists (name, name_key) VALUES (?, ?)",
                                    (name, name.casefold()))
                return {"id": cursor.lastrowid, "name": name}
        except sqlite3.IntegrityError:
            raise HTTPException(409, "Artist is already in the watchlist") from None

    @app.delete("/artists/{artist_id}", status_code=204)
    def delete_artist(artist_id: int):
        with connect(app.state.database) as db:
            if not db.execute("DELETE FROM artists WHERE id = ?", (artist_id,)).rowcount:
                raise HTTPException(404, "Artist not found")
        return Response(status_code=204)

    @app.get("/events")
    def events(artist: str | None = Query(default=None, description="Exact artist name, ignoring case"),
               status: str | None = Query(default=None, description="Provider status code, ignoring case")):
        # Keep every source listing; duplicate hints do not delete or merge rows.
        with connect(app.state.database) as db:
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

    @app.post("/refresh")
    def refresh():
        """Refresh saved artists only. Gemini is available through the CLI, not this endpoint."""
        started = discovery.utc_start_time()
        with connect(app.state.database) as db:
            watchlist = [dict(row) for row in db.execute("SELECT id, name FROM artists ORDER BY id")]
            refresh_id = db.execute("INSERT INTO refreshes (started_at, status) VALUES (?, ?)",
                                    (started, "running")).lastrowid
        counts = {"discovered": 0, "created": 0, "updated": 0, "skipped": 0}
        details = []
        configuration_error = False
        api_key = None
        if watchlist:
            try:
                api_key = discovery.get_api_key()
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
            with connect(app.state.database) as db:
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
        with connect(app.state.database) as db:
            db.execute("""UPDATE refreshes SET finished_at = ?, status = ?, discovered = ?,
                       created = ?, updated = ?, skipped = ?, details = ? WHERE id = ?""",
                       (finished, result_status, counts["discovered"], counts["created"],
                        counts["updated"], counts["skipped"], json.dumps(details), refresh_id))
        result = {"id": refresh_id, "status": result_status, "started_at": started,
                  "finished_at": finished, **counts, "artists": details, "analysis_enabled": False}
        if configuration_error:
            raise HTTPException(503, detail=result)
        return result

    return app


app = create_app()
