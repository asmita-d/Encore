"""Small SQLite helpers; each operation owns and closes its connection."""

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from dotenv import load_dotenv


def database_path(value=None):
    root = Path(__file__).resolve().parent.parent
    load_dotenv(root / ".env")
    value = str(value or os.getenv("DATABASE_URL") or root / "encore.db")
    if value.startswith("sqlite:///"):
        value = value[len("sqlite:///"):]
    elif "://" in value:
        raise ValueError("DATABASE_URL must be a SQLite URL or file path")
    if value == ":memory:":
        raise ValueError("Use a SQLite file path; connections are opened per operation")
    path = Path(value)
    return path if path.is_absolute() else root / path


@contextmanager
def connect(path):
    connection = sqlite3.connect(str(path), timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def initialize(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with connect(path) as db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS artists (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                name_key TEXT NOT NULL UNIQUE
            );
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY,
                provider TEXT NOT NULL,
                provider_event_id TEXT NOT NULL,
                title TEXT NOT NULL,
                date TEXT NOT NULL,
                time TEXT NOT NULL,
                venue TEXT NOT NULL,
                city TEXT NOT NULL,
                country TEXT NOT NULL,
                url TEXT NOT NULL,
                status TEXT NOT NULL,
                cancellation TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                UNIQUE(provider, provider_event_id)
            );
            CREATE TABLE IF NOT EXISTS artist_events (
                artist_id INTEGER NOT NULL REFERENCES artists(id) ON DELETE CASCADE,
                event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
                PRIMARY KEY (artist_id, event_id)
            );
            CREATE TABLE IF NOT EXISTS refreshes (
                id INTEGER PRIMARY KEY,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                status TEXT NOT NULL,
                discovered INTEGER NOT NULL DEFAULT 0,
                created INTEGER NOT NULL DEFAULT 0,
                updated INTEGER NOT NULL DEFAULT 0,
                skipped INTEGER NOT NULL DEFAULT 0,
                details TEXT NOT NULL DEFAULT '[]'
            );
        """)


def upsert_event(db, event, artist_id, timestamp):
    fields = ("title", "date", "time", "venue", "city", "country", "url", "status", "cancellation")
    values = [event[field] for field in fields]
    existing = db.execute(
        "SELECT id FROM events WHERE provider = ? AND provider_event_id = ?",
        ("ticketmaster", event["id"]),
    ).fetchone()
    if existing:
        event_id = existing["id"]
        assignments = ", ".join(f"{field} = ?" for field in fields)
        db.execute(f"UPDATE events SET {assignments}, last_seen_at = ? WHERE id = ?",
                   values + [timestamp, event_id])
    else:
        columns = ", ".join(fields)
        placeholders = ", ".join("?" for _ in fields)
        cursor = db.execute(
            f"INSERT INTO events (provider, provider_event_id, {columns}, last_seen_at) "
            f"VALUES (?, ?, {placeholders}, ?)",
            ["ticketmaster", event["id"]] + values + [timestamp],
        )
        event_id = cursor.lastrowid
    db.execute("INSERT OR IGNORE INTO artist_events VALUES (?, ?)", (artist_id, event_id))
    return "updated" if existing else "created"
