import unittest
from datetime import date
from unittest.mock import patch

import requests

from frontend import APIError, EncoreAPI, event_bucket, source_url, unique_listings


class ClientTests(unittest.TestCase):
    @patch("frontend.requests.request")
    @patch.dict("os.environ", {"ENCORE_API_URL": "http://offline.example/api/"})
    def test_crud_and_refresh(self, request):
        api = EncoreAPI()
        request.return_value.status_code = 200
        request.return_value.ok = True
        request.return_value.json.return_value = [{"id": 1, "name": "Artist"}]
        self.assertEqual(api.request("GET", "/artists")[0]["name"], "Artist")
        request.assert_called_with("GET", "http://offline.example/api/artists", timeout=(5, 15))
        request.return_value.json.return_value = {"id": 1}
        api.request("POST", "/artists", json={"name": "Artist"})
        request.assert_called_with("POST", "http://offline.example/api/artists",
                                   timeout=(5, 15), json={"name": "Artist"})
        request.return_value.status_code = 204
        request.return_value.json.reset_mock()
        self.assertIsNone(api.request("DELETE", "/artists/1"))
        request.return_value.json.assert_not_called()
        request.return_value.status_code = 200
        api.request("POST", "/refresh")
        request.assert_called_with("POST", "http://offline.example/api/refresh", timeout=(5, 300))

    @patch.dict("os.environ", {}, clear=True)
    def test_default_url(self):
        self.assertEqual(EncoreAPI().url, "http://127.0.0.1:8000")

    @patch("frontend.requests.request")
    def test_connection_timeout_and_invalid_json(self, request):
        for failure in (requests.ConnectionError("private"), requests.Timeout("private")):
            request.reset_mock()
            request.side_effect = failure
            with self.assertRaises(APIError) as caught:
                EncoreAPI().request("POST", "/refresh")
            self.assertNotIn("private", str(caught.exception))
            request.assert_called_once()
        request.side_effect = None
        request.return_value.status_code = 200
        request.return_value.json.side_effect = ValueError()
        with self.assertRaisesRegex(APIError, "unreadable"):
            EncoreAPI().request("GET", "/events")

    @patch("frontend.requests.request")
    def test_error_details_and_bad_shapes(self, request):
        response = request.return_value
        response.ok = False
        response.status_code = 503
        details = {"status": "failed", "artists": [{"message": "Key not configured"}]}
        response.json.return_value = {"detail": details}
        with self.assertRaises(APIError) as caught:
            EncoreAPI().request("POST", "/refresh")
        self.assertEqual(caught.exception.details, details)
        response.status_code = 409
        with self.assertRaisesRegex(APIError, "already"):
            EncoreAPI().request("POST", "/artists")
        response.ok = True
        response.status_code = 200
        for payload in ({}, [None], ["invalid"]):
            response.json.return_value = payload
            with self.assertRaisesRegex(APIError, "format"):
                EncoreAPI().request("GET", "/events")


class PresentationTests(unittest.TestCase):
    def test_date_and_cancellation_priority(self):
        today = date(2026, 11, 8)
        for fields, expected in [
            ({"date": "2026-11-08"}, "Upcoming"),
            ({"date": "2027-01-01"}, "Upcoming"),
            ({"date": "2026-11-07"}, "Past"),
            ({"date": "Unknown"}, "Date unknown"),
            ({"date": None}, "Date unknown"),
            ({"date": "2027-01-01", "status": "canceled"}, "Cancelled"),
            ({"date": "2027-01-01", "title": "Artist CANCELLATION"}, "Possibly cancelled"),
            ({"date": "2020-01-01", "cancellation": "POSSIBLY CANCELLED (title only)"}, "Possibly cancelled"),
            ({"status": "cancelled", "title": "Artist CANCELED"}, "Cancelled"),
        ]:
            with self.subTest(fields=fields):
                original = dict(fields)
                self.assertEqual(event_bucket(fields, today), expected)
                self.assertEqual(fields, original)

    def test_shared_listing_counts_keep_possible_duplicates(self):
        first = {"provider": "ticketmaster", "provider_event_id": "1", "artist": "A"}
        events = [first, dict(first, artist="B"), dict(first, provider_event_id="2")]
        self.assertEqual(len(unique_listings(events)), 2)
        self.assertEqual(len(events), 3)

    def test_source_links(self):
        for invalid in (None, "Unknown", "javascript:alert(1)", "https:///missing", "//example.com"):
            self.assertIsNone(source_url(invalid))
        self.assertEqual(source_url("https://example.com/one"), "https://example.com/one")


if __name__ == "__main__":
    unittest.main()
