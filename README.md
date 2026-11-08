# Encore

A personal live music watchlist that discovers Ticketmaster event listings and
turns saved results into a downloadable weekly Markdown digest.

**Live demo:** [encore-asmita.streamlit.app](https://encore-asmita.streamlit.app)

## Key features

- Add and remove artists from an editable watchlist.
- Refresh Ticketmaster listings with exact artist-name matching.
- Browse upcoming, cancelled, possibly cancelled, past and unknown-date listings.
- Filter by artist and provider status; open original provider links.
- Generate a weekly digest grouped by artist, with conservative duplicate
  grouping that retains all original source IDs and links.
- Preview and download the digest as Markdown, including an empty-results message.
- See refresh history and safe HTTP status, timeout and connection diagnostics.

## Technology stack

| Component | Technology |
| --- | --- |
| Application | Python |
| Interface | Streamlit |
| Shared business logic and storage | Python service layer and built-in `sqlite3` |
| Event discovery | Ticketmaster Discovery API via `requests` |
| Optional local HTTP backend | FastAPI, Pydantic and Uvicorn |
| Local configuration | Environment variables, Streamlit secrets and `python-dotenv` |
| Digest | Pure Python and Markdown; deterministic introduction |
| Optional CLI analysis | Google Gemini via `google-genai` |
| Offline testing | `unittest`, mocks, HTTPX/FastAPI TestClient and Streamlit AppTest |
| Demo hosting | Streamlit Community Cloud |

Gemini is used only by the optional discovery CLI analysis. The Streamlit app
and weekly digest do not require a Gemini key or make Gemini requests.

## Architecture and data flow

```text
Streamlit interface
  -> frontend adapter
     -> shared service (ENCORE_MODE=local)
        OR local FastAPI -> shared service (ENCORE_MODE=http)
  -> SQLite: saved artists, provider listings and refresh history

Refresh Events -> shared service -> Ticketmaster discovery -> SQLite
Generate Digest -> saved listings -> digest.py -> preview / Markdown download
```

The deployed app runs as one Streamlit process using the shared service directly.
The optional HTTP mode uses the same business logic. Only **Refresh Events**
contacts Ticketmaster; startup, reloads, filters and digest generation use saved
local data. Failed refreshes retain existing listings, so check last-seen times.

## Local setup

The commands below use PowerShell from the project directory. Python 3.12 is a
suitable choice for this setup. If `.venv` already exists, reuse it.

```powershell
cd C:\encore
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Provide `TICKETMASTER_API_KEY` through your environment, a local `.env` file,
or Streamlit secrets. To create `.env` from the template **only if it does not
already exist**:

```powershell
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
```

Edit your local `.env` and replace the Ticketmaster placeholder with your key.
Do not print keys in logs or share screenshots containing credentials.
**Never commit secrets:** `.env`, `.env.*` (except the placeholder-only
`.env.example`), `.streamlit/secrets.toml` and local databases are ignored by Git.
Check staged files before publishing; `.gitignore` does not remove files that
were already tracked.

### Run Streamlit without a separate server

```powershell
$env:ENCORE_MODE = 'local'
$env:ENCORE_DEMO = 'false'
.\.venv\Scripts\python.exe -m streamlit run streamlit_app.py --server.address 127.0.0.1 --server.port 8501
```

Open `http://127.0.0.1:8501`. Add an artist, then select **Refresh Events**.
A missing Ticketmaster key does not block startup, watchlist editing or digest
generation; refresh will report that the key is not configured.

Local data persists in `encore.db` by default. Optionally set `DATABASE_URL`
to a file path or SQLite URL, such as `sqlite:///data/encore.sqlite3`. Relative
paths resolve from the project root; in-memory databases are not supported.
Environment settings take precedence over Streamlit secrets; ordinary local
mode also supports the existing `.env` fallback. No mode modifies `.env`.

### Optional local FastAPI mode

In a separate terminal, start the backend:

```powershell
cd C:\encore
.\.venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

In the Streamlit terminal, select HTTP mode and run the interface:

```powershell
$env:ENCORE_MODE = 'http'
$env:ENCORE_API_URL = 'http://127.0.0.1:8000'
.\.venv\Scripts\python.exe -m streamlit run streamlit_app.py
```

HTTP is the default mode when `ENCORE_MODE` is unset. In this mode, configure
provider credentials in the backend environment or `.env`. API documentation
is available at `http://127.0.0.1:8000/docs`.

| Endpoint | Purpose |
| --- | --- |
| `GET /health` | Database check and latest refresh metadata |
| `GET /artists` | List saved artists |
| `POST /artists` | Add an artist with `{"name":"Coldplay"}` |
| `DELETE /artists/{id}` | Remove an artist and its listing associations |
| `GET /events` | Saved listings; optional `artist` and `status` filters |
| `POST /refresh` | Refresh listings for saved artists |

Artist names are whitespace-normalized and unique ignoring case. Source listings
are stored by provider and original event ID, and can be associated with multiple
artists. Removing an artist retains underlying event history. An HTTP timeout may
leave a refresh running; reload saved data before retrying.

## Weekly digest

1. Add artists and use **Refresh Events** to save current provider listings.
2. Select **Generate Digest** in the **Weekly Digest** section.
3. Review the preview, then select **Download Markdown** to save the `.md` file.

The digest uses all saved artists, independently of the browsing filters. It
covers today through six days later: seven calendar dates, using the server's
generation date and each listing's venue-local event date. Cancelled, possibly
cancelled, past and unknown-date listings are excluded. Today's finished events
may still be included because filtering is by calendar date, not current time.

Possible duplicates must match artist, date, venue, city, country, title and
local time, with compatible backend hints. Missing identifying fields prevent
grouping. This is a heuristic, not proof of the same performance. Every combined
item keeps its original source links, IDs and statuses. The browsing cards keep
individual listings visible.

The introduction uses factual counts without an LLM. The preview is a snapshot;
generate it again after changing saved data. Empty results are handled normally.
For Python use, `build_digest(records, saved_artists, reference_date)` returns
structured data, and `render_markdown(digest)` produces Markdown. Supply a Python
`date` as `reference_date` for reproducible results.

## Cloud demo configuration

The live demo uses `streamlit_app.py` as its entrypoint, `ENCORE_MODE=local` and
`ENCORE_DEMO=true`. There is no separately hosted FastAPI server. Dependencies
come from `requirements.txt`.

For your own Streamlit Community Cloud deployment, choose that entrypoint and
set the following TOML in the dashboard's **Advanced settings / Secrets** field.
Replace the key placeholder there, never in tracked source files:

```toml
ENCORE_MODE = "local"
ENCORE_DEMO = true
ENCORE_SEED_ARTISTS = ["Coldplay", "Dua Lipa"]
TICKETMASTER_API_KEY = "replace-with-your-ticketmaster-key"
```

Leave `DATABASE_URL` unset to use the demo's temporary SQLite file. On initial
startup, an empty watchlist is seeded from `ENCORE_SEED_ARTISTS`. A database marker
prevents later reruns from restoring artists you removed. Existing nonempty
watchlists are left alone. Seeding never fetches events automatically.

To test demo mode locally, set `ENCORE_DEMO=true` and use a separate
`DATABASE_URL`, such as `sqlite:///data/demo.sqlite3`. The seed setting also
accepts a comma-separated environment string. Return to `ENCORE_DEMO=false`
and remove that database override to resume ordinary local persistence.

## Testing

Run the complete offline suite and compile the Python files:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m compileall -q backend tests discover_events.py frontend.py streamlit_app.py digest.py llm_analyzer.py test_gemini_search.py
```

Tests mock Ticketmaster and Gemini and use temporary databases. Coverage includes
artist CRUD, refresh and seeding, safe failure diagnostics, date boundaries,
cancellations, duplicate grouping, digest grammar, Markdown output and the
Streamlit interface. Compilation does not execute the scripts.

**Do not run `test_gemini_search.py` as an offline test.** Despite its name, this
root-level manual script makes a live Gemini request. The offline command above
discovers only the `tests/` directory.

## Optional discovery CLI

```powershell
.\.venv\Scripts\python.exe discover_events.py
.\.venv\Scripts\python.exe discover_events.py --analyze
```

These commands make live requests. The CLI searches the fixed artist list in
`discover_events.py`. The `--analyze` flag optionally annotates at most five
listings with one Gemini request, no retries and a 20-second timeout. Configure
`GEMINI_API_KEY` only for this option; `GEMINI_MODEL` is optional. Invalid or
unavailable analysis falls back to provider results. Annotations are unverified
and do not alter source records. Account/model quotas and costs may vary.

## Limitations

- Ticketmaster coverage is incomplete. Discovery requires an exact artist-name
  attraction match and reads only the first page, up to 20 results per request.
  There are at most two provider requests per saved artist per refresh, with no
  pagination, keyword fallback or retries.
- Listings are **not verified artist performances**. Provider associations can
  include tribute or other related events. Check the original source and lineup.
- Some events may be cancelled or change after discovery. Known and possible
  cancellations are labelled, and excluded from the digest, but saved data can
  become stale. Missing dates cannot be treated as upcoming.
- Free cloud SQLite data may reset after restarts or redeploys. All visitors
  share one editable demo watchlist; there are no separate user accounts.
  Download a digest to keep a copy. Ordinary local SQLite persistence is unchanged.
- There is no automatic email delivery, scheduling or background refresh.
  Refresh and digest generation are manual.
