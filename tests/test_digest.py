import copy
import unittest
from datetime import date

from digest import build_digest, render_markdown


TODAY = date(2026, 11, 8)


def listing(event_id="one", **changes):
    return dict({"provider": "ticketmaster", "provider_event_id": event_id,
                 "artist": "Artist A", "title": "Live listing", "date": "2026-11-08",
                 "time": "20:00:00", "venue": "Hall", "city": "London",
                 "country": "UK", "status": "onsale", "url": f"https://example.com/{event_id}"},
                **changes)


class DigestTests(unittest.TestCase):
    def build(self, records, artists=("Artist A",)):
        return build_digest(records, artists, TODAY)

    def test_seven_calendar_dates_and_unknown_dates(self):
        records = [listing(str(i), date=value) for i, value in enumerate(
            ("2026-11-07", "2026-11-08", "2026-11-14", "2026-11-15",
             "Unknown", None, "2026-02-30"))]
        digest = self.build(records)
        self.assertEqual(digest["start_date"], "2026-11-08")
        self.assertEqual(digest["end_date"], "2026-11-14")
        self.assertEqual([e["date"] for e in digest["artists"][0]["events"]],
                         ["2026-11-08", "2026-11-14"])

    def test_cancellation_signals(self):
        for change in ({"status": "cancelled"}, {"status": "CANCELED"},
                       {"cancellation": "POSSIBLY CANCELLED (title only)"},
                       {"cancellation": "CANCELLED (provider status)"},
                       {"title": "Live CANCELLATION"}, {"title": "Live canceled"}):
            with self.subTest(change=change):
                self.assertEqual(self.build([listing(**change)])["artists"], [])

    def test_duplicates_retain_sources_without_mutation(self):
        records = [listing(possible_duplicate_ids=["one", "two"]),
                   listing("two", venue=" HALL ", possible_duplicate_ids=["one", "two"])]
        original = copy.deepcopy(records)
        items = self.build(records)["artists"][0]["events"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["sources"], records)
        markdown = render_markdown(self.build(records))
        for value in ("https://example.com/one", "https://example.com/two", "ID: one", "ID: two",
                      "Possible duplicate listings combined"):
            self.assertIn(value, markdown)
        self.assertEqual(records, original)

    def test_ambiguous_records_stay_separate(self):
        for change in ({"time": "18:00:00"}, {"title": "Afterparty"},
                       {"venue": "Unknown"}, {"city": ""},
                       {"possible_duplicate_ids": ["two"]}):
            with self.subTest(change=change):
                items = self.build([listing(), listing("two", **change)])["artists"][0]["events"]
                self.assertEqual(len(items), 2)
        # Raw stored records without hints use the existing conservative fields.
        self.assertEqual(len(self.build([listing(), listing("two")])["artists"][0]["events"]), 1)

    def test_multiple_saved_artists_and_shared_source(self):
        records = [listing(), listing(artist="Artist B"), listing(artist="Unsaved")]
        digest = self.build(records, [{"name": "Artist B"}, {"name": "Artist A"}])
        self.assertEqual([s["artist"] for s in digest["artists"]], ["Artist A", "Artist B"])
        self.assertEqual(len(digest["artists"][1]["events"]), 1)
        self.assertEqual(self.build(records, [])['artists'], [])

    def test_empty_and_markdown_details(self):
        empty = render_markdown(self.build([]))
        self.assertIn("# Encore Weekly Digest", empty)
        self.assertIn("No eligible saved provider listings", empty)
        text = render_markdown(self.build([listing()]))
        for value in ("2026-11-08 through 2026-11-14", "## Artist A", "### Live listing",
                      "20:00:00", "Hall, London, UK", "status: onsale",
                      "not verified artist appearances"):
            self.assertIn(value, text)

    def test_introduction_singular_counts_after_duplicate_grouping(self):
        records = [listing(), listing("two")]
        original = copy.deepcopy(records)
        digest = self.build(records)
        expected = "This week's saved provider listings include 1 display item across 1 artist."
        self.assertTrue(digest["introduction"].startswith(expected))
        self.assertIn(expected, render_markdown(digest))
        self.assertEqual(digest["artists"][0]["events"][0]["sources"], original)
        self.assertEqual(records, original)

    def test_introduction_plural_counts(self):
        cases = [
            ([], ("Artist A",), "0 display items across 0 artists."),
            ([listing(), listing("two", title="Other listing")],
             ("Artist A",), "2 display items across 1 artist."),
            ([listing(), listing("two", artist="Artist B"), listing("three", artist="Artist C")],
             ("Artist A", "Artist B", "Artist C"), "3 display items across 3 artists."),
        ]
        for records, artists, expected in cases:
            with self.subTest(expected=expected):
                digest = self.build(records, artists)
                self.assertIn(expected, digest["introduction"])
                self.assertIn(expected, render_markdown(digest))

    def test_untrusted_markdown_and_urls(self):
        text = render_markdown(self.build([listing(title="<script>bad</script> [click]",
                                                   url="javascript:alert(1)")]))
        self.assertNotIn("<script>", text)
        self.assertIn(r"\[click\]", text)
        self.assertIn("Original URL (unavailable as a link)", text)
        self.assertNotIn("](javascript:", text)


if __name__ == "__main__":
    unittest.main()
