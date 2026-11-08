"""Exercise sanitized provider failures without network or real credentials."""

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

import requests
from fastapi.testclient import TestClient

import discover_events as discovery
from backend.main import create_app
from backend.service import EncoreService
from frontend import EncoreAPI, LocalAPI


PRIVATE = "https://private.invalid/request?apikey=fake-private-key&keyword=private-query private-body"
CASES = [(401, "HTTP 401"), (403, "HTTP 403"), (429, "HTTP 429"),
         (500, "HTTP 500"), (503, "HTTP 503"),
         ("timeout", "request timed out"), ("connection", "connection failed"),
         ("connect_timeout", "request timed out"), ("request", "request failed")]


def failure(kind):
    if isinstance(kind, int):
        response = requests.Response()
        response.status_code = kind
        response.url = PRIVATE
        response._content = b"private-body"
        return requests.HTTPError(PRIVATE, response=response)
    return {"timeout": requests.Timeout, "connection": requests.ConnectionError,
            "connect_timeout": requests.ConnectTimeout,
            "request": requests.RequestException}[kind](PRIVATE)


class DiagnosticTests(unittest.TestCase):
    def assert_safe(self, value):
        for private in ("fake-private-key", "private.invalid", "apikey", "private-query", "private-body"):
            self.assertNotIn(private, value)

    def test_request_diagnostics_and_no_output(self):
        for kind, expected in CASES:
            with self.subTest(kind=kind), patch("discover_events.requests.get") as get, \
                    redirect_stdout(io.StringIO()) as stdout, redirect_stderr(io.StringIO()) as stderr:
                error = failure(kind)
                if isinstance(kind, int):
                    get.return_value.raise_for_status.side_effect = error
                else:
                    get.side_effect = error
                with self.assertRaises(discovery.DiscoveryRequestError) as caught:
                    discovery.search_events("artist", "fake-private-key", "start")
                self.assertIn(expected, str(caught.exception))
                self.assert_safe(str(caught.exception))
                self.assertIsNone(caught.exception.__cause__)
                self.assertTrue(caught.exception.__suppress_context__)
                get.assert_called_once()
                get.return_value.json.assert_not_called()
                self.assertEqual(stdout.getvalue() + stderr.getvalue(), "")

    def test_refresh_response_local_http_and_persisted_details(self):
        with tempfile.TemporaryDirectory() as temporary, patch("backend.database.load_dotenv"), \
                patch("discover_events.get_api_key", return_value="fake-private-key"), \
                patch("requests.request", side_effect=AssertionError("Live HTTP forbidden")), \
                TestClient(create_app(Path(temporary) / "test.sqlite3")) as http:
            service = http.app.state.service
            local = LocalAPI(service)
            service.add_artist("Artist")
            # Keep a saved record to verify failures never erase listings.
            with patch("discover_events.find_attraction", return_value={"id": "artist"}), \
                    patch("discover_events.search_events", return_value=[{"id": "saved", "name": "Listing"}]):
                service.refresh()
            for stage in ("attraction", "events"):
                for kind, expected in CASES:
                    with self.subTest(stage=stage, kind=kind), \
                            patch("discover_events.requests.get") as get:
                        error = failure(kind)
                        if stage == "events":
                            get.return_value.json.return_value = {
                                "_embedded": {"attractions": [{"id": "artist", "name": "Artist"}]}}
                            get.side_effect = [get.return_value, error]
                        else:
                            get.side_effect = error
                        result = local.request("POST", "/refresh")
                        self.assertEqual(result["status"], "partial")
                        self.assertEqual(result["discovered"], 0)
                        self.assertIn(expected, result["artists"][0]["message"])
                        self.assert_safe(json.dumps(result))
                        self.assertEqual(len(service.events()), 1)
                        self.assertEqual(service.health()["last_refresh"]["details"], result["artists"])
                        # Exercise the unchanged HTTP frontend adapter too.
                        get.side_effect = error
                        def backend_response(method, url, **kwargs):
                            result = http.request(method, "/refresh")
                            response = requests.Response()
                            response.status_code = result.status_code
                            response._content = result.content
                            return response

                        with patch("frontend.requests.request", side_effect=backend_response):
                            response = EncoreAPI().request("POST", "/refresh")
                        self.assertIn(expected, response["artists"][0]["message"])
                        self.assert_safe(json.dumps(response))

    def test_streamlit_refresh_displays_safe_diagnostics(self):
        from streamlit.testing.v1 import AppTest

        with tempfile.TemporaryDirectory() as temporary, patch("backend.database.load_dotenv"), \
                patch("requests.request", side_effect=AssertionError("Live HTTP forbidden")):
            service = EncoreService(Path(temporary) / "ui.sqlite3", key_getter=lambda: "fake-private-key")
            service.add_artist("Artist")
            with patch("frontend.create_client", return_value=LocalAPI(service, demo=True)), \
                    patch("streamlit.secrets", {}):
                app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "streamlit_app.py")).run()
                for kind, expected in CASES:
                    with self.subTest(kind=kind), patch("discover_events.requests.get", side_effect=failure(kind)):
                        next(b for b in app.button if b.label == "Refresh Events").click().run()
                        self.assertFalse(app.exception)
                        warnings = "\n".join(w.value for w in app.warning)
                        self.assertIn(expected, warnings)
                        self.assert_safe(warnings)


if __name__ == "__main__":
    unittest.main()
