import copy
import io
import json
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

import discover_events as discovery
import llm_analyzer as analyzer


def sample_event(title="Coldplay", status="onsale"):
    event = discovery.extract_event({
        "id": "source-1", "name": title, "url": "https://example.com/original",
        "dates": {"start": {"localDate": "2027-01-01"}, "status": {"code": status}},
    }, "Coldplay")
    return dict(event, cancellation=discovery.cancellation_label(event))


class AnalyzerTests(unittest.TestCase):
    @patch("llm_analyzer.load_dotenv")
    @patch.dict("os.environ", {"GEMINI_API_KEY": "test-secret",
                               "GEMINI_MODEL": "configured-model"}, clear=True)
    @patch("google.genai.Client")
    def test_successful_structured_analysis(self, client_class, load_env):
        records = [sample_event(), sample_event("Coldplay tribute"), sample_event("Music night")]
        original = copy.deepcopy(records)
        answers = [{"index": index, "classification": classification, "summary": record["title"]}
                   for index, (record, classification) in enumerate(zip(
                       records, ("actual_artist", "tribute_cover", "uncertain")))]
        client = client_class.return_value.__enter__.return_value
        client.models.generate_content.return_value.text = json.dumps(answers)
        self.assertEqual(analyzer.analyze_events(records), answers)
        self.assertEqual(records, original)
        options = client_class.call_args.kwargs["http_options"]
        self.assertEqual(options.timeout, 20000)
        self.assertEqual(options.retry_options.attempts, 1)
        client.models.generate_content.assert_called_once()
        call = client.models.generate_content.call_args.kwargs
        self.assertEqual(call["model"], "configured-model")
        self.assertEqual(call["config"].response_mime_type, "application/json")
        self.assertEqual(len(json.loads(call["contents"])), 3)

    def test_malformed_or_ungrounded_responses(self):
        valid = {"index": 0, "classification": "uncertain", "summary": "Coldplay"}
        invalid = ["not JSON", "null", "{}", "[]", json.dumps([valid, valid]),
                   json.dumps([dict(valid, index=True)]),
                   json.dumps([dict(valid, index=2)]),
                   json.dumps([dict(valid, classification="confirmed")]),
                   json.dumps([dict(valid, summary=123)]),
                   json.dumps([dict(valid, summary="New concert in Paris on 2028-01-01")]),
                   json.dumps([dict(valid, url="https://invented.example")])]
        for text in invalid:
            with self.subTest(text=text), self.assertRaises((ValueError, TypeError)):
                analyzer.validate_analysis(text, [sample_event()])

    @patch("llm_analyzer.load_dotenv")
    @patch.dict("os.environ", {"GEMINI_API_KEY": "test-secret"}, clear=True)
    @patch("google.genai.Client")
    def test_api_errors_and_invalid_responses_are_nonfatal(self, client_class, load_env):
        generate = client_class.return_value.__enter__.return_value.models.generate_content
        for failure in (RuntimeError("quota test-secret"), TimeoutError("test-secret"), None):
            generate.reset_mock()
            generate.side_effect = failure
            generate.return_value.text = "malformed"
            with redirect_stdout(io.StringIO()) as output:
                self.assertIsNone(analyzer.analyze_events([sample_event()]))
            generate.assert_called_once()
            self.assertNotIn("test-secret", output.getvalue())

    @patch("llm_analyzer.load_dotenv")
    @patch.dict("os.environ", {}, clear=True)
    @patch("google.genai.Client")
    def test_missing_key_empty_and_oversized_batches_make_no_request(self, client, load_env):
        self.assertIsNone(analyzer.analyze_events([sample_event()]))
        self.assertEqual(analyzer.analyze_events([]), [])
        self.assertIsNone(analyzer.analyze_events([sample_event()] * 6))
        client.assert_not_called()


class WorkflowAnalysisTests(unittest.TestCase):
    @patch("llm_analyzer.analyze_events")
    @patch("discover_events.get_api_key", return_value="ticketmaster-test")
    @patch("discover_events.find_attraction", return_value={"id": "artist-id"})
    @patch("discover_events.search_events", return_value=[{"id": "event", "name": "Concert"}])
    def test_no_analysis_without_flag(self, search, find, key, analyze):
        with redirect_stdout(io.StringIO()):
            self.assertEqual(discovery.main(), 0)
        analyze.assert_not_called()

    @patch("llm_analyzer.analyze_events", return_value=None)
    @patch("discover_events.get_api_key", return_value="ticketmaster-test")
    @patch("discover_events.find_attraction", return_value={"id": "artist-id"})
    @patch("discover_events.search_events", return_value=[
        {"id": str(index), "name": "Concert", "url": f"https://example.com/{index}"}
        for index in range(7)])
    def test_one_batch_failure_retains_all_provider_results(self, search, find, key, analyze):
        with redirect_stdout(io.StringIO()) as output:
            self.assertEqual(discovery.main(analyze=True), 0)
        analyze.assert_called_once()
        self.assertEqual(len(analyze.call_args.args[0]), 5)
        self.assertIn("Analysis unavailable", output.getvalue())
        self.assertIn("https://example.com/6", output.getvalue())

    @patch("llm_analyzer.analyze_events", return_value=[{
        "index": 0, "classification": "actual_artist", "summary": "Upcoming recommendation"}])
    def test_cancelled_annotation_cannot_be_recommended(self, analyze):
        for event in (sample_event(status="cancelled"), sample_event("Coldplay CANCELED")):
            original = dict(event)
            with redirect_stdout(io.StringIO()) as output:
                discovery.print_analysis([event])
            self.assertEqual(event, original)
            self.assertIn("CANCELLED", output.getvalue())
            self.assertIn("not recommended as upcoming", output.getvalue())
            self.assertNotIn("Upcoming recommendation", output.getvalue())
            self.assertIn(event["url"], output.getvalue())


if __name__ == "__main__":
    unittest.main()
