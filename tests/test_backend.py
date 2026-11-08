import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

try:
    from fastapi.testclient import TestClient
    from backend.main import create_app
except ModuleNotFoundError as error:
    raise unittest.SkipTest(f"Backend dependencies unavailable: {error.name}") from error


def provider_event(event_id="event-1", status="onsale"):
    return {
        "id": event_id, "name": "Coldplay", "url": f"https://example.com/{event_id}",
        "dates": {"start": {"localDate": "2027-01-01", "localTime": "20:00:00"},
                  "status": {"code": status}},
        "_embedded": {"venues": [{"name": "Arena", "city": {"name": "London"},
                                   "country": {"name": "United Kingdom"}}]},
    }


class BackendTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "test.sqlite3"
        self.network = patch("requests.get", side_effect=AssertionError("Live HTTP forbidden")).start()
        self.addCleanup(patch.stopall)
        self.llm = patch("llm_analyzer.analyze_events", side_effect=AssertionError("LLM forbidden")).start()
        self.key = patch("discover_events.get_api_key", return_value="offline-key").start()
        self.find = patch("discover_events.find_attraction", return_value={"id": "attraction-1"}).start()
        self.search = patch("discover_events.search_events", return_value=[provider_event()]).start()
        self.client = TestClient(create_app(self.path))
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)

    def tearDown(self):
        self.network.assert_not_called()
        self.llm.assert_not_called()

    def add_artist(self, name="Coldplay"):
        response = self.client.post("/artists", json={"name": name})
        self.assertEqual(response.status_code, 201)
        return response.json()

    def test_health_docs_and_artist_crud(self):
        self.assertEqual(self.client.get("/health").json(), {"status": "ok", "last_refresh": None})
        self.assertEqual(self.client.get("/docs").status_code, 200)
        artist = self.add_artist("  Coldplay  ")
        self.assertEqual(self.client.get("/artists").json(), [artist])
        self.assertEqual(self.client.post("/artists", json={"name": "cOLDPLAY"}).status_code, 409)
        self.assertEqual(self.client.post("/artists", json={"name": "   "}).status_code, 422)
        self.assertEqual(self.client.post("/artists", json={}).status_code, 422)
        self.assertEqual(self.client.delete(f"/artists/{artist['id']}").status_code, 204)
        self.assertEqual(self.client.delete(f"/artists/{artist['id']}").status_code, 404)
        self.assertEqual(self.client.get("/artists").json(), [])
        self.find.assert_not_called()
        self.search.assert_not_called()

    def test_refresh_upsert_filters_duplicates_and_persistence(self):
        self.add_artist()
        self.search.return_value = [provider_event(), provider_event("event-2", "cancelled")]
        first = self.client.post("/refresh").json()
        self.assertEqual((first["discovered"], first["created"], first["updated"]), (2, 2, 0))
        self.search.assert_called_once_with("attraction-1", "offline-key", first["started_at"])
        events = self.client.get("/events").json()
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["listings_grouped"], 2)
        self.assertEqual(events[1]["status"], "cancelled")
        self.assertIn("CANCELLED", events[1]["cancellation"])
        self.assertTrue(all(not event["confirmed_upcoming"] for event in events))
        filtered = self.client.get("/events", params={"artist": "coldplay", "status": "CANCELLED"}).json()
        self.assertEqual([event["provider_event_id"] for event in filtered], ["event-2"])
        self.assertEqual(self.client.get("/events?artist=Other").json(), [])
        self.search.return_value = [provider_event(status="cancelled")]
        second = self.client.post("/refresh").json()
        self.assertEqual((second["created"], second["updated"]), (0, 1))
        with TestClient(create_app(self.path)) as reopened:
            self.assertEqual(len(reopened.get("/artists").json()), 1)
            stored = reopened.get("/events").json()
            self.assertEqual(len(stored), 2)  # Unseen listings are not deleted.
            self.assertEqual(stored[0]["url"], "https://example.com/event-1")
            self.assertEqual(stored[0]["status"], "cancelled")
            self.assertEqual(reopened.get("/health").json()["last_refresh"]["id"], second["id"])

    def test_no_match_empty_results_missing_fields_and_provider_error(self):
        self.add_artist()
        self.find.return_value = None
        result = self.client.post("/refresh").json()
        self.assertEqual(result["artists"][0]["status"], "no_match")
        self.search.assert_not_called()
        self.find.return_value = {"id": "attraction-1"}
        self.search.return_value = [{}, {"id": "minimal"}, provider_event("cancel-title")]
        self.search.return_value[2]["name"] = "Coldplay CANCELED"
        result = self.client.post("/refresh").json()
        self.assertEqual((result["discovered"], result["created"], result["skipped"]), (3, 2, 1))
        events = self.client.get("/events").json()
        self.assertTrue(any(event["venue"] == "Unknown" for event in events))
        self.assertTrue(any("POSSIBLY CANCELLED" in event["cancellation"] for event in events))
        self.search.side_effect = RuntimeError("private-key-in-error")
        failed = self.client.post("/refresh")
        self.assertEqual(failed.status_code, 200)
        self.assertEqual(failed.json()["status"], "partial")
        self.assertNotIn("private-key", failed.text)
        self.assertEqual(len(self.client.get("/events").json()), 2)
        self.search.side_effect = None
        self.search.return_value = []
        self.assertEqual(self.client.post("/refresh").json()["discovered"], 0)

    def test_empty_watchlist_and_missing_key(self):
        self.assertEqual(self.client.post("/refresh").json()["created"], 0)
        self.key.assert_not_called()
        self.add_artist()
        self.key.side_effect = ValueError("private error")
        response = self.client.post("/refresh")
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("private error", response.text)
        self.assertEqual(self.client.get("/health").json()["last_refresh"]["status"], "failed")

    def test_shared_provider_listing_and_artist_delete(self):
        first = self.add_artist()
        self.add_artist("Dua Lipa")
        result = self.client.post("/refresh").json()
        self.assertEqual((result["created"], result["updated"]), (1, 1))
        events = self.client.get("/events").json()
        self.assertEqual(len({event["database_id"] for event in events}), 1)
        self.client.delete(f"/artists/{first['id']}")
        self.assertEqual([event["artist"] for event in self.client.get("/events").json()], ["Dua Lipa"])


if __name__ == "__main__":
    unittest.main()
