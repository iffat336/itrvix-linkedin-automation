from datetime import date
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import editorial
import publisher


DAY = date(2026, 11, 20)
QUOTE_ONE = "The experiments included a separate trial at each location during the study."
QUOTE_TWO = "The validation results varied across locations in this group of trials."
SOURCES = [
    {"title": "Trial design study", "publisher": "Journal One",
     "url": "https://example.org/one", "body": QUOTE_ONE},
    {"title": "Validation study", "publisher": "Journal Two",
     "url": "https://example.org/two", "body": QUOTE_TWO},
]


def draft(topic):
    return {
        "hook": f"Where does the evidence for {topic} come from?",
        "first_claim": "The first study used separate trials across locations in its evaluation, making the site aspect explicit in its design.",
        "first_quote": QUOTE_ONE,
        "second_claim": "The second study found that its validation results varied between sites in the trials it examined.",
        "second_quote": QUOTE_TWO,
        "application": "For a planning discussion, consider specifying which locations to hold out before reviewing the results and discussing the next decision.",
        "question": "What would you put in a trial plan to make the validation design easier to evaluate?",
    }


class EditorialTests(unittest.TestCase):
    @patch("editorial.doc_source")
    @patch("editorial.crop_sources", return_value=SOURCES)
    def test_research_to_four_validated_posts_with_twelve_original_images(self, crop, docs):
        docs.side_effect = SOURCES * 2
        writer = Mock()
        writer.generate.side_effect = lambda kind, schema, instruction, context: (
            draft(context["topic"]) if kind == "linkedin_draft" else {"passes": True}
        )
        plan = editorial.make_plan(DAY, Mock(), writer)
        self.assertEqual(crop.call_count, 2)
        self.assertEqual(docs.call_count, 4)
        self.assertEqual(writer.generate.call_count, 8)
        self.assertEqual(plan["image_rights"]["third_party_assets"], [])
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            posts = publisher.generated_posts(plan, DAY, folder)
            self.assertEqual(set(posts), publisher.EXPECTED)
            self.assertEqual(len(list((folder / str(DAY)).glob("*.png"))), 12)
            self.assertEqual(len({publisher.digest(img) for _, images in posts.values() for img in images}), 12)
            for text, _ in posts.values():
                self.assertIn("[1] Journal One: https://example.org/one", text)
                self.assertNotIn(QUOTE_ONE, text)

    def test_unmatched_quote_fails_before_fact_check(self):
        topic = editorial.CURRICULUM["iffat-nazir"][0]
        writer = Mock()
        writer.generate.return_value = draft(topic.title) | {"first_quote": QUOTE_TWO}
        with self.assertRaisesRegex(editorial.EditorialError, "did not match"):
            editorial.compose(writer, topic, "iffat-nazir", "angle", SOURCES)
        writer.generate.assert_called_once()

    def test_failed_fact_check_blocks_draft(self):
        topic = editorial.CURRICULUM["iffat-nazir"][0]
        writer = Mock()
        writer.generate.side_effect = [draft(topic.title), {"passes": False}]
        with self.assertRaisesRegex(editorial.EditorialError, "review rejected"):
            editorial.compose(writer, topic, "iffat-nazir", "angle", SOURCES)

    @patch("editorial.fetch")
    def test_crop_search_excludes_retractions_and_duplicate_dois(self, fetch):
        def hit(index, **fields):
            return {"id": str(index), "doi": f"10.1/{index}", "source": "MED",
                    "title": f"Article {index}", "abstractText": "<p>" + QUOTE_ONE * 5 + "</p>"} | fields
        fetch.return_value = json.dumps({"resultList": {"result": [
            hit(1), hit(2, doi="10.1/1"), hit(3, pubTypeList={"pubType": ["Retracted Publication"]}), hit(4),
        ]}})
        result = editorial.crop_sources(Mock(), '"genomic prediction" AND wheat', DAY)
        self.assertEqual({entry["url"] for entry in result},
                         {"https://pubmed.ncbi.nlm.nih.gov/1/", "https://pubmed.ncbi.nlm.nih.gov/4/"})

    @patch("publisher.git")
    @patch("publisher.generated_posts", return_value={"generated": True})
    @patch("editorial.generate_plan")
    def test_freezes_a_validated_plan_before_returning_it_to_publisher(self, plan, render, git):
        plan.return_value = {"date": str(DAY)}
        with tempfile.TemporaryDirectory() as directory:
            ledger = Mock(checkout=Path(directory), rows={})
            self.assertEqual(publisher.prepare_day(ledger, Path(directory), DAY), {"generated": True})
            ledger.save.assert_called_once()
            self.assertEqual(json.loads((Path(directory) / "editorial" / f"{DAY}.json").read_text()),
                             {"date": str(DAY)})
            publisher.prepare_day(ledger, Path(directory), DAY)
            plan.assert_called_once()
            ledger.save.assert_called_once()

    @patch("editorial.generate_plan", side_effect=editorial.EditorialError("review rejected"))
    @patch("review.email_batch")
    def test_failed_batch_never_emails(self, email_batch, plan):
        with tempfile.TemporaryDirectory() as directory:
            ledger = Mock(checkout=Path(directory), rows={})
            with self.assertRaisesRegex(editorial.EditorialError, "review rejected"):
                publisher.send_review(ledger, Path(directory), DAY)
        email_batch.assert_not_called()
        ledger.save.assert_not_called()
        plan.assert_called_once_with(DAY)

    @patch("publisher.open_ledger")
    @patch("publisher.Buffer")
    @patch("publisher.upload")
    @patch("publisher.generated_posts")
    @patch("editorial.generate_plan")
    def test_preview_has_no_publishing_side_effects(self, plan, render, upload, buffer, ledger):
        with tempfile.TemporaryDirectory() as directory:
            with patch("sys.argv", ["publisher.py", "preview", "--date", str(DAY), "--output", directory]):
                publisher.main()
        plan.assert_called_once_with(DAY)
        render.assert_called_once()
        upload.assert_not_called()
        buffer.assert_not_called()
        ledger.assert_not_called()


if __name__ == "__main__":
    unittest.main()
