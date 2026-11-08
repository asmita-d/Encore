import os
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from backend.service import EncoreService
from frontend import APIError, EncoreAPI, LocalAPI, create_client


class ServiceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "test.sqlite3"
        self.addCleanup(patch.stopall)
        self.http = patch("requests.request", side_effect=AssertionError("HTTP forbidden")).start()
        self.network = patch("requests.get", side_effect=AssertionError("Provider HTTP forbidden")).start()
        self.llm = patch("llm_analyzer.analyze_events", side_effect=AssertionError("LLM forbidden")).start()
        patch.dict(os.environ, {"ENCORE_MODE": "local", "ENCORE_DEMO": "false",
                               "TICKETMASTER_API_KEY": "",
                               "DATABASE_URL": str(self.path)}).start()
        patch("backend.database.load_dotenv").start()

    def tearDown(self):
        self.http.assert_not_called()
        self.network.assert_not_called()
        self.llm.assert_not_called()

    def test_local_crud_persistence_and_validation(self):
        client = create_client({})
        self.assertIsInstance(client, LocalAPI)
        artist = client.request("POST", "/artists", json={"name": " Coldplay "})
        self.assertEqual(artist["name"], "Coldplay")
        self.assertEqual(create_client({}).request("GET", "/artists"), [artist])
        with self.assertRaises(APIError):
            client.request("POST", "/artists", json={"name": "COLDPLAY"})
        for invalid in (None, " ", "x" * 201):
            with self.assertRaises(APIError):
                client.request("POST", "/artists", json={"name": invalid})
        client.request("DELETE", f"/artists/{artist['id']}")
        self.assertEqual(client.request("GET", "/artists"), [])

    def test_seed_once_and_do_not_restore_deleted_artists(self):
        secrets = {"ENCORE_SEED_ARTISTS": [" Coldplay ", "coldplay", "Dua Lipa"]}
        with patch.dict(os.environ, {"ENCORE_DEMO": "true"}):
            first = create_client(secrets)
            self.assertEqual([a["name"] for a in first.service.artists()], ["Coldplay", "Dua Lipa"])
            for artist in first.service.artists():
                first.service.delete_artist(artist["id"])
            self.assertEqual(create_client(secrets).service.artists(), [])
            # A lost database is seeded again, as on a fresh cloud instance.
            with patch.dict(os.environ, {"DATABASE_URL": str(self.path.parent / "fresh.sqlite3")}):
                self.assertEqual(len(create_client(secrets).service.artists()), 2)

    def test_seed_does_not_change_existing_database_or_local_mode(self):
        service = EncoreService(self.path)
        artist = service.add_artist("My artist")
        seeded = EncoreService(self.path, seed_artists=["Coldplay"])
        self.assertEqual(seeded.artists(), [artist])
        self.assertFalse(create_client({}).demo)
        self.assertEqual(create_client({}).service.artists(), [artist])

    def test_demo_defaults_to_temporary_storage(self):
        with patch.dict(os.environ, {"ENCORE_DEMO": "true"}):
            del os.environ["DATABASE_URL"]
            with patch("frontend.tempfile.gettempdir", return_value=str(self.path.parent)):
                client = create_client({})
            self.assertEqual(client.service.database, self.path.parent / "encore-demo.sqlite3")
            self.assertEqual(len(client.service.artists()), 2)

    def test_refresh_reuses_discovery_and_digest_rules(self):
        with patch.dict(os.environ):
            del os.environ["TICKETMASTER_API_KEY"]
            client = create_client({"TICKETMASTER_API_KEY": "secret-test"})
            self.assertEqual(client.service.key_getter(), "secret-test")
            # Keep an explicit fake key for refresh after the temporary override.
            client.service.key_getter = lambda: "secret-test"
        client.request("POST", "/artists", json={"name": "Coldplay"})
        event = {"id": "one", "name": "Coldplay", "url": "https://example.com/one",
                 "dates": {"start": {"localDate": "2026-11-08", "localTime": "20:00:00"},
                           "status": {"code": "onsale"}},
                 "_embedded": {"venues": [{"name": "Hall", "city": {"name": "London"},
                                           "country": {"name": "UK"}}]}}
        with patch("discover_events.find_attraction", return_value={"id": "attraction"}) as find, \
                patch("discover_events.search_events", return_value=[event, dict(event, id="two")]) as search:
            result = client.request("POST", "/refresh")
            find.assert_called_once_with("Coldplay", "secret-test")
            search.assert_called_once_with("attraction", "secret-test", result["started_at"])
        self.assertEqual(result["created"], 2)
        events = client.request("GET", "/events")
        self.assertEqual(events[0]["listings_grouped"], 2)
        digest, markdown = client.digest(None, None, date(2026, 11, 8))
        self.assertEqual(len(digest["artists"][0]["events"]), 1)
        self.assertIn("ID: two", markdown)
        self.assertIn("https://example.com/one", markdown)
        self.assertNotIn("secret-test", markdown)
        with patch("discover_events.find_attraction", return_value={"id": "attraction"}), \
                patch("discover_events.search_events", return_value=[dict(event, name="Coldplay CANCELED")]):
            client.request("POST", "/refresh")
        digest, _ = client.digest(None, None, date(2026, 11, 8))
        self.assertEqual(len(digest["artists"][0]["events"]), 1)
        self.assertEqual(digest["artists"][0]["events"][0]["sources"][0]["id"], "two")

    def test_missing_key_startup_and_digest_still_work(self):
        with patch.dict(os.environ, {"ENCORE_DEMO": "true"}), \
                patch("discover_events.find_attraction") as find:
            client = create_client({})
            with self.assertRaises(APIError) as error:
                client.request("POST", "/refresh")
            self.assertIn("not configured", str(error.exception.details))
            self.assertEqual(client.request("GET", "/health")["last_refresh"]["status"], "failed")
            self.assertIn("No eligible", client.digest(None, None)[1])
            find.assert_not_called()

    def test_environment_precedence_and_http_mode(self):
        with patch.dict(os.environ, {"TICKETMASTER_API_KEY": "environment-key"}):
            client = create_client({"TICKETMASTER_API_KEY": "secret-key"})
            self.assertEqual(client.service.key_getter(), "environment-key")
        with patch.dict(os.environ, {"ENCORE_MODE": "http"}):
            self.assertIsInstance(create_client({}), EncoreAPI)
            self.assertNotIsInstance(create_client({}), LocalAPI)
        with patch.dict(os.environ, {"ENCORE_MODE": "invalid"}):
            with self.assertRaises(APIError):
                create_client({})

    def test_local_missing_key_uses_existing_safe_fallback(self):
        client = create_client({})
        client.service.add_artist("Artist")
        with patch("discover_events.get_api_key", side_effect=ValueError("private-details")), \
                patch("discover_events.find_attraction") as find:
            with self.assertRaises(APIError) as error:
                client.request("POST", "/refresh")
            self.assertNotIn("private-details", str(error.exception.details))
            find.assert_not_called()

    def test_streamlit_demo_without_backend(self):
        from streamlit.testing.v1 import AppTest

        with patch.dict(os.environ, {"ENCORE_DEMO": "true"}), \
                patch("streamlit.secrets", {}):
            app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "streamlit_app.py")).run()
            self.assertFalse(app.exception)
            self.assertTrue(any("may reset" in w.value for w in app.warning))
            next(b for b in app.button if b.label == "Generate Digest").click().run()
            self.assertFalse(app.exception)
            self.assertTrue(any("No eligible saved provider listings" in m.value for m in app.markdown))
            self.assertEqual(len(app.get("download_button")), 1)
            next(b for b in app.button if b.label == "Refresh Events").click().run()
            self.assertFalse(app.exception)
            self.assertTrue(any("not configured" in w.value for w in app.warning))


if __name__ == "__main__":
    unittest.main()
