# Encore: Ticketmaster discovery proof of concept

Encore uses a shared Python service with SQLite. Streamlit can run by itself or
connect to the local FastAPI backend. The CLI commands below remain unchanged.

## Single-process local testing

Use the existing virtual environment and dependencies. No FastAPI server is
needed in this mode:

```powershell
cd C:\encore
$env:ENCORE_MODE = 'local'
$env:ENCORE_DEMO = 'false'
.\.venv\Scripts\python.exe -m streamlit run streamlit_app.py --server.address 127.0.0.1 --server.port 8501
```

Open `http://127.0.0.1:8501`. Add/remove artists, select **Refresh Events** to
fetch Ticketmaster listings, then **Generate Digest** and **Download Markdown**.
Normal local mode keeps using `encore.db` (or `DATABASE_URL`) across restarts,
without seeding a watchlist. Refresh reads `TICKETMASTER_API_KEY` from the
environment or Streamlit secrets; local mode also retains the existing `.env`
fallback. A missing key produces a safe refresh message; startup, CRUD, saved
listings and digest generation still work. Gemini is never required by the app.

To test the cloud demo locally in a separate database:

```powershell
$env:ENCORE_MODE = 'local'
$env:ENCORE_DEMO = 'true'
$env:DATABASE_URL = 'sqlite:///data/demo.sqlite3'
$env:ENCORE_SEED_ARTISTS = 'Coldplay,Dua Lipa'
.\.venv\Scripts\python.exe -m streamlit run streamlit_app.py
```

The first startup seeds only an empty watchlist. A database marker prevents
later reruns or restarts from restoring artists you intentionally removed.
Existing nonempty watchlists are left alone. The seed accepts a comma-separated
environment string or a list in Streamlit secrets. Only names are seeded;
events are fetched when you select **Refresh Events**, never at startup.

After demo testing, restore normal local settings:

```powershell
$env:ENCORE_DEMO = 'false'
Remove-Item Env:DATABASE_URL -ErrorAction SilentlyContinue
```

Run the complete offline suite, including the mocked Streamlit demo test:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m py_compile streamlit_app.py frontend.py backend/main.py backend/service.py backend/database.py discover_events.py digest.py
```

Tests use temporary databases and mocked providers. They do not run the live
root-level `test_gemini_search.py` script.

## Free deployment on Streamlit Community Cloud

This is a single-user demo with a shared editable watchlist, not separate
accounts. All visitors see and can change the same data. Cloud SQLite is
temporary: watchlist edits and saved events may reset after restarts, reboots
or redeploys. The interface displays this warning. Download Markdown digests
to retain copies. There is no claim of permanent cloud persistence.

1. When you are ready, put the project source and `requirements.txt` in a GitHub
   repository. Include `backend/`, `digest.py`, `discover_events.py`,
   `llm_analyzer.py`, `frontend.py` and `streamlit_app.py`. Do not upload `.env`,
   `.streamlit/secrets.toml`, virtual environments or SQLite files. These are
   excluded by `.gitignore`.
2. Sign in at `https://share.streamlit.io`, choose **Create app**, and select
   your repository and branch. Set the entrypoint/main file path to
   **`streamlit_app.py`** at the repository root.
3. Open **Advanced settings**, choose Python **3.12**, and paste the following
   TOML into the dashboard's **Secrets** field, replacing only the placeholder
   key with your Ticketmaster key:

   ```toml
   ENCORE_MODE = "local"
   ENCORE_DEMO = true
   ENCORE_SEED_ARTISTS = ["Coldplay", "Dua Lipa"]
   TICKETMASTER_API_KEY = "replace-with-your-ticketmaster-key"
   ```

4. Save the settings and select **Deploy**. Community Cloud installs
   `requirements.txt` and starts Streamlit; no second server or FastAPI URL is
   needed. Leave `DATABASE_URL` unset to use the demo's temporary SQLite file.
5. Check the demo warning and seeded artists, select **Refresh Events**, then
   generate and download a digest. No listings is a valid result. Missing keys
   are reported on refresh without blocking the rest of the app.

You can change secrets later in the app's dashboard settings. For local secrets
testing, the same TOML can go into `.streamlit/secrets.toml`, which is ignored;
never commit it. Environment settings take precedence over Streamlit secrets.
Do not set `GEMINI_API_KEY` for this app; the deterministic digest needs none.
No extra paid service or database is used. Provider requests remain bounded by
the existing discovery limits and your Ticketmaster account quota.

See the official [deployment instructions](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/deploy)
and [dashboard secrets instructions](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/secrets-management).

## Streamlit interface

The default `ENCORE_MODE=http` uses FastAPI over HTTP. Both modes use the same
service logic; HTTP mode leaves storage access to the server.
Install dependencies once, if needed:

```powershell
cd C:\encore
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Terminal 1 — start FastAPI:

```powershell
cd C:\encore
.\.venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

Terminal 2 — start Streamlit:

```powershell
cd C:\encore
$env:ENCORE_MODE = 'http'
.\.venv\Scripts\python.exe -m streamlit run streamlit_app.py --server.address 127.0.0.1 --server.port 8501
```

Open `http://127.0.0.1:8501`. To use another backend, set
`$env:ENCORE_API_URL = 'http://127.0.0.1:8000'` in Terminal 2 before starting
Streamlit. This environment variable defaults to that address. In HTTP mode,
provider credentials belong to the backend only. No mode modifies `.env`.

Add artists to the watchlist, then select **Refresh Events**. Only this button
calls provider discovery; ordinary reruns and **Reload saved data** read saved
data through the selected mode. Removal requires a confirmation checkbox. Refresh results include
per-artist failures and no-match messages; saved listings survive failed refreshes.
A timeout may leave the backend processing: reload saved data and inspect the
last refresh before retrying. No Gemini calls are made by the frontend.

Listings default to the Upcoming tab, followed by separate cancelled, possibly
cancelled, past, and unknown-date tabs. Artist and provider-status filters apply
to every tab. Upcoming means today or later by provider-local calendar date
(today's finished performances may remain there); it does not verify appearances.
Overview counts count each provider/source ID once, even when linked to multiple
artists. Cards retain each artist/listing link, original URL, status, last-seen
time, and backend duplicate hints. Possible duplicates are not collapsed;
different group members may appear under different filters or tabs.

### Weekly Digest

Select **Generate Digest** in the **Weekly Digest** section to preview a
newsletter from saved backend data for all watchlist artists. Select **Download
Markdown** to save the `.md` file. This works with empty results too. The preview
is a snapshot; generate again after changing saved artists or listings.

The window includes today through six days later (seven calendar dates), using
venue-local event dates and the computer's generation date. Cancelled, possibly
cancelled, past and unknown-date listings are excluded. Possible duplicates use
the existing conservative identity rules and API hints; each combined item keeps
all original provider links, IDs and statuses. These remain provider listings,
not verified artist appearances; saved data may be stale. Digest generation uses
a deterministic factual introduction, with no Gemini, provider calls, new
services, email or scheduling, and no additional cost.

`digest.py` also exposes `build_digest(records, saved_artists, reference_date)`
for structured output and `render_markdown(digest)` for Markdown. Pass a Python
`date` as `reference_date` for reproducible results; omit it to use today.

Offline verification (does not run the live `test_gemini_search.py` script):

```powershell
cd C:\encore
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m py_compile streamlit_app.py frontend.py digest.py
```

Frontend client tests mock every HTTP call; presentation tests cover cancellation
precedence, date handling, shared-listing counts, and source URL validation.

## Local REST backend

From PowerShell in `C:\encore`, install dependencies and start the server:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000/docs` for interactive endpoint documentation.
Tables are created at startup; startup and GET requests do not call Ticketmaster
or Gemini. The backend starts with an empty watchlist. Add artists before refresh.

| Endpoint | Behavior |
| --- | --- |
| `GET /health` | Database check and latest persisted refresh metadata |
| `GET /artists` | List favorite artists |
| `POST /artists` | Add `{"name":"Coldplay"}`; duplicate names return 409 |
| `DELETE /artists/{id}` | Remove favorite; 204 on success, 404 if absent |
| `GET /events` | Saved source listings; optional `artist` exact-name and `status` provider-code filters, ignoring case |
| `POST /refresh` | Refresh the saved watchlist and persist provider listings |

Example manual requests (refresh calls Ticketmaster):

```powershell
Invoke-RestMethod http://127.0.0.1:8000/artists -Method Post -ContentType 'application/json' -Body '{"name":"Coldplay"}'
Invoke-RestMethod http://127.0.0.1:8000/refresh -Method Post
Invoke-RestMethod 'http://127.0.0.1:8000/events?artist=Coldplay&status=cancelled'
```

The default database is `C:\encore\encore.db`. Optionally configure `DATABASE_URL`
in the environment or your existing `.env` as a file path or SQLite URL, for
example `$env:DATABASE_URL = 'sqlite:///data/encore.sqlite3'`. Relative paths are
relative to the project root; in-memory databases are not supported. No schema
migration system is included in this milestone. Local SQLite files are ignored
by Git. `httpx` is included for offline FastAPI TestClient tests.

Artist names are whitespace-normalized and unique using Unicode case folding.
Events are unique by provider and original event ID; shared listings can link to
multiple favorite artists. `GET /events` returns one row per artist/listing link,
with `database_id` for the internal row and `id`/`provider_event_id` for the
original provider ID. Duplicate hints use the existing conservative logic, retain
all source URLs/IDs, and are calculated within the filtered response.

Refresh uses exact attraction matching, at most two Ticketmaster requests per
saved artist, no keyword fallback, and no pagination. It returns discovered,
created, updated, and skipped counts plus per-artist results. `updated` counts
existing rows seen again, even if their data is unchanged; `discovered` counts
raw listings across all artists. Listings without usable provider IDs are skipped;
other missing fields use `Unknown`. No-match artists and provider errors are
reported separately. Provider errors return a `partial` result; a missing API key
returns 503 with persisted failure metadata. Existing listings are retained after
failures or empty searches, so saved results can become stale; check `last_seen_at`.
Removing a favorite removes its links, retaining underlying event history.

Cancelled and possibly cancelled records remain stored and explicitly labelled;
`status` preserves the provider code and `cancellation` adds the deterministic
warning. Every listing has `confirmed_upcoming: false`; provider association is
not confirmation of an appearance. Gemini is not invoked by the backend in this
milestone. Optional analysis remains available through the CLI `--analyze` flag.

Run all offline tests, including temporary SQLite/TestClient tests:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Backend tests skip if FastAPI dependencies are absent. All provider and Gemini
access in tests is mocked; no live requests are needed.

## Existing discovery CLI

A minimal Python 3.10 script for a single user. It searches Ticketmaster Discovery
API v2 globally for Coldplay, Dua Lipa, Prateek Kuhad, Zakir Khan, and Arijit Singh.
Each artist is looked up by keyword in attractions, requiring a case-insensitive
exact name and an attraction ID before searching events by `attractionId`.
These are **provider-linked listings, not verified artist appearances**. Missing
exact matches are reported without falling back to event keyword search. Multiple
exact-name identities are reported as ambiguous rather than guessed.

From PowerShell in `C:\encore`, use the existing virtual environment:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Edit `.env` and set `TICKETMASTER_API_KEY` to your Ticketmaster developer API key.
Keep the key private; `.env` is ignored by Git. An existing environment variable
takes precedence over `.env`.

Run mocked unit tests (no network requests):

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Run discovery (requires internet access and an API key):

```powershell
.\.venv\Scripts\python.exe discover_events.py
```

Optional Gemini analysis:

```powershell
.\.venv\Scripts\python.exe discover_events.py --analyze
```

Add `GEMINI_API_KEY` to your existing `.env` to enable analysis; optionally set
`GEMINI_MODEL` (default `gemini-3.1-flash-lite`). Do not overwrite your existing
`.env` with the example. Without `--analyze`, no Gemini requests are made.
With it, the first five normalized source listings in discovery output order
are sent in at most one request, with no retries and a 20-second SDK timeout.
This includes source URLs and other event metadata, but no Ticketmaster key.
Remaining listings are still printed without analysis. Empty results make no
Gemini request. Use an account with available free quota; these limits reduce
usage but do not guarantee free service for every account or model.

Gemini adds an unverified `actual_artist`, `tribute_cover`, or `uncertain`
classification and a brief extractive title summary. Summaries must exactly
quote part of the source title (or state insufficient title evidence), preventing
invented event details. They are not independent verification. IDs, URLs, dates,
cancellation status, and duplicate grouping remain provider-controlled. Analysis
of cancelled or possibly cancelled records is suppressed in the display, and
they are never recommended as upcoming. Missing keys, SDK errors, quota failures,
or invalid structured output show `Analysis unavailable` and retain provider
results without failing discovery. Unit tests mock Gemini and Ticketmaster.

Each run makes at most ten requests: one attraction lookup and, when matched,
one event search per artist. Each request retrieves at most 20 results; event
results are sorted by date ascending from the current UTC time. There are no
city/country restrictions, retries, or pagination. Results cover Ticketmaster's
inventory only, not every performance worldwide. First-page results can be incomplete,
including attraction matches. Each listing shows its provider ID, artist, title,
local date, venue, city, country, original event URL, and provider status code. Missing values
appear as `Unknown`. Request failures are reported without printing request URLs
or credentials; other artists are still searched. A missing key or any failed
search produces a nonzero exit code.

Provider cancellation codes are flagged as cancelled; cancellation wording in a
title is flagged as possible cancellation for review. Listings are not asserted to
be normal upcoming shows. Possible duplicates require the same artist, date,
venue, city, country, title, and local time (including matching unknown times).
Text comparison ignores letter case and repeated whitespace. Missing identifying
fields prevent grouping. Different titles or times remain separate, so some real
duplicates may remain ungrouped. Every grouped listing retains its own ID, URL,
and status, and the output shows the listing count. Grouping is a heuristic and
does not establish that listings represent the same performance.
