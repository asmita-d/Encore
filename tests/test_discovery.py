import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

import requests

import discover_events as discovery


class DiscoveryTests(unittest.TestCase):
    @patch("discover_events.load_dotenv")
    @patch.dict("os.environ", {}, clear=True)
    def test_missing_api_key(self, load_dotenv):
        with self.assertRaisesRegex(ValueError, "Missing TICKETMASTER_API_KEY"):
            discovery.get_api_key()

    @patch("discover_events.requests.get")
    def test_search_parameters_and_events(self, get):
        event = {"id": "event-1", "name": "Coldplay"}
        get.return_value.json.return_value = {"_embedded": {"events": [event]}}
        start = "2026-11-08T12:00:00Z"
        self.assertEqual(discovery.search_events("Coldplay", "test-key", start), [event])
        get.assert_called_once_with(
            discovery.EVENTS_URL,
            params={"apikey": "test-key", "attractionId": "Coldplay", "size": 20,
                    "sort": "date,asc", "startDateTime": start},
            timeout=15,
        )
        get.return_value.raise_for_status.assert_called_once_with()

    @patch("discover_events.requests.get")
    def test_no_events(self, get):
        get.return_value.json.return_value = {"page": {"totalElements": 0}}
        self.assertEqual(discovery.search_events("Artist", "key", "start"), [])

    @patch("discover_events.requests.get")
    def test_http_failures_do_not_expose_secret(self, get):
        for error in (requests.Timeout("secret-key"), requests.HTTPError("secret-key")):
            with self.subTest(error=type(error).__name__):
                get.side_effect = error
                with self.assertRaises(RuntimeError) as caught:
                    discovery.search_events("Artist", "secret-key", "start")
                self.assertNotIn("secret-key", str(caught.exception))

    @patch("discover_events.requests.get")
    def test_http_error_status(self, get):
        get.return_value.raise_for_status.side_effect = requests.HTTPError("secret-key")
        with self.assertRaisesRegex(RuntimeError, "HTTP request failed"):
            discovery.search_events("Artist", "secret-key", "start")
        get.return_value.json.assert_not_called()

    @patch("discover_events.requests.get")
    def test_invalid_json(self, get):
        get.return_value.json.side_effect = ValueError("Invalid JSON")
        with self.assertRaisesRegex(RuntimeError, "invalid JSON"):
            discovery.search_events("Artist", "key", "start")

    def test_print_event_fields_and_missing_values(self):
        event = {
            "id": "123", "name": "Example concert", "url": "https://example.com/event",
            "dates": {"start": {"localDate": "2027-01-01"}},
            "_embedded": {"venues": [{"name": "Arena", "city": {"name": "London"},
                                       "country": {"name": "United Kingdom"}}]},
        }
        output = io.StringIO()
        with redirect_stdout(output):
            discovery.print_event([discovery.extract_event(event, "Artist")])
            discovery.print_event([discovery.extract_event(
                {"dates": None, "_embedded": {"venues": [None]}}, "Artist")])
        for value in ("123", "Example concert", "2027-01-01", "Arena", "London",
                      "United Kingdom", "https://example.com/event", "Unknown"):
            self.assertIn(value, output.getvalue())

    def test_utc_format(self):
        self.assertRegex(discovery.utc_start_time(), r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

    @patch("discover_events.search_events", return_value=[])
    @patch("discover_events.find_attraction", return_value={"id": "artist-id"})
    @patch("discover_events.get_api_key", return_value="secret-key")
    def test_main_makes_one_search_per_artist(self, get_key, find, search):
        with redirect_stdout(io.StringIO()) as output:
            self.assertEqual(discovery.main(), 0)
        self.assertEqual(search.call_count, 5)
        self.assertEqual([call.args[0] for call in find.call_args_list], discovery.ARTISTS)
        self.assertEqual([call.args[0] for call in search.call_args_list], ["artist-id"] * 5)
        self.assertIn("NOT verified artist appearances", output.getvalue())
        self.assertNotIn("secret-key", output.getvalue())


    @patch("discover_events.requests.get")
    def test_exact_attraction_match(self, get):
        get.return_value.json.return_value = {"_embedded": {"attractions": [
            {"id": "wrong", "name": "Prateek Kuhad tribute"},
            {"id": "K8vZ9179RQf", "name": "PRATEEK KUHAD"},
            {"name": "Prateek Kuhad"},
            {"id": "missing-name"},
        ]}}
        self.assertEqual(discovery.find_attraction("Prateek Kuhad", "key")["id"],
                         "K8vZ9179RQf")
        get.assert_called_once_with(discovery.ATTRACTIONS_URL, params={
            "apikey": "key", "keyword": "Prateek Kuhad", "size": 20,
        }, timeout=15)

    @patch("discover_events.requests.get")
    def test_no_exact_or_ambiguous_attraction(self, get):
        for data in ({}, {"_embedded": {"attractions": [
                {"id": "tribute", "name": "Artist tribute"}, {"name": "Artist"}]}}):
            get.return_value.json.return_value = data
            self.assertIsNone(discovery.find_attraction("Artist", "key"))
        get.return_value.json.return_value = {"_embedded": {"attractions": [
            {"id": "one", "name": "Artist"}, {"id": "two", "name": "Artist"}]}}
        with self.assertRaisesRegex(RuntimeError, "ambiguous"):
            discovery.find_attraction("Artist", "key")

    @patch("discover_events.search_events")
    @patch("discover_events.find_attraction", return_value=None)
    @patch("discover_events.get_api_key", return_value="key")
    def test_no_match_never_searches_events(self, get_key, find, search):
        with redirect_stdout(io.StringIO()) as output:
            self.assertEqual(discovery.main(), 0)
        search.assert_not_called()
        self.assertIn("No exact-name attraction found", output.getvalue())

    def test_cancellation(self):
        event = discovery.extract_event({
            "name": "Prateek Kuhad", "dates": {"start": {"localDate": "2026-11-19"},
                                                   "status": {"code": "cancelled"}},
        }, "Prateek Kuhad")
        self.assertEqual(discovery.cancellation_label(event), "CANCELLED (provider status)")
        event["status"] = "Unknown"
        event["title"] = "Prateek Kuhad - CANCELED"
        self.assertIn("POSSIBLY CANCELLED", discovery.cancellation_label(event))
        event["title"] = "Prateek Kuhad"
        self.assertNotIn("CANCELLED", discovery.cancellation_label(event))

    def test_conservative_duplicate_grouping(self):
        first = {
            "id": "one", "artist": "Prateek Kuhad", "title": "Prateek Kuhad",
            "date": "2026-11-10", "time": "20:00:00", "venue": "Brooklyn Hall",
            "city": "Brooklyn", "country": "United States", "url": "https://example.com/one",
            "status": "onsale",
        }
        second = dict(first, id="two", venue="  BROOKLYN   Hall ",
                      url="https://example.com/two", status="cancelled")
        separate = [dict(first, venue="Other Hall"), dict(first, time="18:00:00"),
                    dict(first, title="Afterparty"), dict(first, city="Queens"),
                    dict(first, artist="Other Artist"), dict(first, date="2026-11-11")]
        groups = discovery.group_duplicates([first, second] + separate)
        self.assertEqual([len(group) for group in groups], [2, 1, 1, 1, 1, 1, 1])
        with redirect_stdout(io.StringIO()) as output:
            discovery.print_event(groups[0])
        for value in ("Listings grouped: 2", "https://example.com/one",
                      "https://example.com/two", "id: one", "id: two", "CANCELLED (provider status)"):
            self.assertIn(value, output.getvalue())

    def test_missing_fields_do_not_group(self):
        event = discovery.extract_event({}, "Artist")
        self.assertEqual(event["status"], "Unknown")
        self.assertEqual(event["venue"], "Unknown")
        self.assertEqual(len(discovery.group_duplicates([event, dict(event)])), 2)


if __name__ == "__main__":
    unittest.main()
