"""Encore's single-user interface, with HTTP and in-process service modes."""

import streamlit as st

from frontend import APIError, create_client, event_bucket, source_url, unique_listings


st.set_page_config(page_title="Encore · Your next live moment", page_icon="🎵", layout="wide")
st.markdown("""<style>
.block-container {max-width: 1120px; padding-top: 2rem; padding-bottom: 3rem;}
h1 {letter-spacing: -.04em;}
div[data-testid="stMetric"] {border: 1px solid #8884; border-radius: 14px; padding: 18px;}
div[data-testid="stForm"] {border-radius: 14px;}
.encore-kicker {color: #a78bfa; font-weight: 700; letter-spacing: .18em; font-size: .8rem;}
</style>""", unsafe_allow_html=True)

try:
    # Missing local secrets files are normal; never display their contents/errors.
    try:
        secrets = dict(st.secrets)
    except FileNotFoundError:
        secrets = {}
    api = create_client(secrets)
except APIError as error:
    st.error(str(error))
    st.stop()


def refresh_result(result):
    status = result.get("status", "unknown")
    message = (f"Refresh {status} · {result.get('discovered', 0)} discovered · "
               f"{result.get('created', 0)} new · {result.get('updated', 0)} updated · "
               f"{result.get('skipped', 0)} skipped")
    (st.success if status == "completed" else st.warning)(message)
    for item in result.get("artists", result.get("details", [])):
        if item.get("status") != "ok":
            st.warning(f"{item.get('artist', 'Artist')}: {item.get('message', item.get('status'))}")
    st.caption("Saved listings are retained after errors or empty searches. Check each listing's last-seen time.")


with st.sidebar:
    st.title("🎵 Encore")
    st.caption("Your personal live music watchlist")
    st.code(api.url, language=None)
    st.button("Reload saved data", use_container_width=True,
              help="Read saved data. This does not contact Ticketmaster.")
    if not api.local:
        st.caption("Set ENCORE_API_URL in the Streamlit terminal to use another backend.")
    if api.demo:
        st.warning("Single-user cloud demo: watchlist and event changes may reset after restarts or redeploys. All visitors share this demo's data. Download digests to keep a copy.")

st.markdown('<div class="encore-kicker">KEEP YOUR FAVORITES CLOSE</div>', unsafe_allow_html=True)
st.title("Encore")
st.markdown("### Your next live moment starts here.")
st.write("Save artists you love, refresh their Ticketmaster listings, and explore what's coming up in one place.")
st.info("These are provider-linked listings, not verified artist appearances. Check the original source for the lineup and current status.")

if "notice" in st.session_state:
    st.success(st.session_state.pop("notice"))
if "refresh_result" in st.session_state:
    refresh_result(st.session_state.pop("refresh_result"))

try:
    artists = api.request("GET", "/artists")
except APIError as error:
    st.error(str(error))
    if not api.local:
        st.code("python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000", language="powershell")
    st.caption("Check your configuration, then select Reload saved data in the sidebar.")
    st.stop()

events = None
try:
    events = api.request("GET", "/events")
except APIError as error:
    st.error(f"Saved events unavailable: {error}")

metrics = st.columns(3)
metrics[0].metric("Saved artists", len(artists))
sources = unique_listings(events or [])
metrics[1].metric("Upcoming listings", sum(event_bucket(e) == "Upcoming" for e in sources) if events is not None else "—")
metrics[2].metric("Cancelled listings", sum(event_bucket(e) == "Cancelled" for e in sources) if events is not None else "—")
st.caption("Counts are unique provider listings, not confirmed performances. Upcoming includes today's listings by provider-local date; possible cancellations and unknown dates are excluded.")

st.header("Artist watchlist")
with st.form("add_artist", clear_on_submit=True):
    name = st.text_input("Artist name", max_chars=200, placeholder="e.g. Coldplay")
    if st.form_submit_button("Add artist", type="primary"):
        if not name.strip():
            st.warning("Enter an artist name first.")
        else:
            try:
                api.request("POST", "/artists", json={"name": name.strip()})
                st.session_state.notice = "Artist added. Select Refresh Events to discover listings."
                st.rerun()
            except APIError as error:
                st.error(str(error))

if not artists:
    st.info("Your watchlist is ready for its first artist. Add a name above to get started.")
for artist in artists:
    with st.expander(artist["name"]):
        st.caption("Removing this artist removes it from your watchlist and hides its saved listing links.")
        confirm = st.checkbox("I want to remove this artist", key=f"confirm_{artist['id']}")
        if st.button("Remove artist", key=f"remove_{artist['id']}", disabled=not confirm):
            try:
                api.request("DELETE", f"/artists/{artist['id']}")
                st.session_state.notice = "Artist removed."
                st.rerun()
            except APIError as error:
                st.error(str(error))

st.header("Explore listings")
st.caption("Refresh only when you want new provider data. Adding an artist or changing filters does not refresh Ticketmaster.")
if st.button("Refresh Events", type="primary", disabled=not artists):
    try:
        with st.spinner("Searching Ticketmaster for your saved artists. Larger watchlists can take a few minutes…"):
            result = api.request("POST", "/refresh")
        st.session_state.refresh_result = result
        st.rerun()
    except APIError as error:
        st.error(str(error))
        if error.details:
            refresh_result(error.details)

try:
    health = api.request("GET", "/health")
    latest = health.get("last_refresh")
    if latest:
        st.caption(f"Last refresh: {latest.get('finished_at') or latest.get('started_at')} · {latest.get('status')}")
        with st.expander("Last refresh details"):
            refresh_result(latest)
    else:
        st.caption("No refresh yet. Add artists, then select Refresh Events.")
except APIError:
    st.caption("Last refresh information is unavailable.")

if events is None:
    st.stop()

st.header("Weekly Digest")
st.caption("Create a free Markdown newsletter from all saved artists and listings, independent of the filters below. No provider or Gemini requests are made.")
if st.button("Generate Digest"):
    try:
        digest, markdown = api.digest(events, artists)
        st.session_state.weekly_digest = (digest["generated_on"], markdown)
    except APIError as error:
        st.error(str(error))
if "weekly_digest" in st.session_state:
    generated_on, markdown = st.session_state.weekly_digest
    st.caption(f"Snapshot generated on {generated_on}. Select Generate Digest again to use current saved data.")
    st.markdown(markdown)
    st.download_button("Download Markdown", data=markdown,
                       file_name=f"encore-weekly-{generated_on}.md", mime="text/markdown")

if not events:
    st.info("No saved listings yet. Add artists and refresh. No exact artist match or an empty provider search can also return no listings.")
    st.stop()

left, right = st.columns(2)
selected_artist = left.selectbox("Artist", [None] + [a["name"] for a in artists],
                                 format_func=lambda value: "All artists" if value is None else value)
selected_status = right.selectbox("Provider status", [None] + sorted({e["status"] for e in events}),
                                  format_func=lambda value: "All statuses" if value is None else value)
filtered = [e for e in events if (selected_artist is None or e["artist"] == selected_artist)
            and (selected_status is None or e["status"] == selected_status)]
st.caption("Possible duplicates use the API's hints. Every artist/listing link remains visible with its original source; group members may appear in different tabs or filters.")

categories = ("Upcoming", "Cancelled", "Possibly cancelled", "Past", "Date unknown")
tabs = st.tabs([f"{category} ({sum(event_bucket(e) == category for e in filtered)})" for category in categories])
for category, tab in zip(categories, tabs):
    with tab:
        rows = sorted((e for e in filtered if event_bucket(e) == category),
                      key=lambda e: (e.get("date", ""), e.get("time", ""), e.get("title", "")))
        if not rows:
            st.info("No listings in this view. Try another tab or filter.")
        for event in rows:
            with st.container(border=True):
                st.subheader(event.get("title", "Untitled listing"))
                st.write(event["artist"])
                if category in ("Cancelled", "Possibly cancelled"):
                    st.error(event.get("cancellation") or category)
                st.write(f"📅 {event.get('date', 'Unknown')} · {event.get('time', 'Unknown')} (venue local time)")
                st.write(f"📍 {event.get('venue', 'Unknown')} · {event.get('city', 'Unknown')}, {event.get('country', 'Unknown')}")
                st.text(f"Provider status: {event.get('status', 'Unknown')}")
                url = source_url(event.get("url"))
                if url:
                    st.link_button("Open original provider listing ↗", url)
                else:
                    st.text(f"Original source URL unavailable: {event.get('url', 'Unknown')}")
                if event.get("listings_grouped", 1) > 1:
                    st.caption(f"Possible duplicate group: {event['listings_grouped']} listings · Source IDs: "
                               + ", ".join(map(str, event.get("possible_duplicate_ids", []))))
                st.caption(f"{event.get('provider', 'Provider')} · Source ID: {event.get('provider_event_id', event.get('id'))} · Last seen: {event.get('last_seen_at', 'Unknown')}")
