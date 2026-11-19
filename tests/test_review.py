from datetime import date, timedelta
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import publisher
import review

DAY = date(2026, 12, 1)
POSTS = {key: (f"Caption for {key} with https://example.org/source",
               [b"abc", b"def", b"ghi"]) for key in sorted(publisher.EXPECTED)}
ACTORS = {"GITHUB_ACTOR": "iffat336", "GITHUB_TRIGGERING_ACTOR": "iffat336",
          "LINKEDIN_REVIEWER_LOGIN": "iffat336"}


class ReviewTests(unittest.TestCase):
    @patch("review.smtplib.SMTP_SSL")
    def test_gmail_contains_four_captions_and_twelve_images(self, smtp):
        with patch.dict(os.environ, GMAIL_REVIEW_ADDRESS="test@gmail.com", GMAIL_APP_PASSWORD="test",
                        GITHUB_REPOSITORY="iffat336/itrvix-linkedin-automation"):
            review.email_batch(DAY, POSTS, "batch123")
        message = smtp.return_value.__enter__.return_value.send_message.call_args.args[0]
        self.assertEqual(message["To"], "test@gmail.com")
        body = message.get_body().get_content()
        for caption, _ in POSTS.values():
            self.assertIn(caption, body)
        self.assertIn("Batch ID: batch123", body)
        self.assertEqual(len(list(message.iter_attachments())), 12)

    @patch("publisher.git")
    @patch("publisher.prepare_day", return_value=POSTS)
    @patch("review.email_batch")
    def test_email_is_recorded_once_and_only_after_success(self, email, prepare, git):
        with tempfile.TemporaryDirectory() as directory:
            ledger = Mock(checkout=Path(directory))
            email.side_effect = OSError("SMTP error")
            with self.assertRaises(OSError):
                publisher.send_review(ledger, Path(directory), DAY)
            self.assertFalse(review.read_record(ledger, DAY))
            email.side_effect = None
            publisher.send_review(ledger, Path(directory), DAY)
            publisher.send_review(ledger, Path(directory), DAY)
            self.assertEqual(email.call_count, 2)
            self.assertEqual(review.read_record(ledger, DAY)["batch_id"], review.batch_id(DAY, POSTS))

    @patch("publisher.git")
    @patch("publisher.prepare_day", return_value=POSTS)
    def test_only_correct_actor_can_approve_exact_emailed_batch(self, prepare, git):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ledger = Mock(checkout=root)
            identifier = review.batch_id(DAY, POSTS)
            with patch.dict(os.environ, ACTORS), patch("publisher.utc_now", return_value=(
                    publisher.due_at(DAY, "10:00") - timedelta(hours=1))):
                with self.assertRaisesRegex(RuntimeError, "exact batch ID"):
                    publisher.approve_day(ledger, root, DAY, identifier)
                publisher.save_review(ledger, DAY, {"batch_id": identifier, "emailed_at": "earlier"}, "sent")
                self.assertIsNone(publisher.approved_posts(ledger, root, DAY))
                with self.assertRaisesRegex(RuntimeError, "exact batch ID"):
                    publisher.approve_day(ledger, root, DAY, "wrong")
                with patch.dict(os.environ, GITHUB_ACTOR="someone-else"):
                    with self.assertRaisesRegex(RuntimeError, "configured GitHub reviewer"):
                        publisher.approve_day(ledger, root, DAY, identifier)
                with patch("publisher.utc_now", return_value=publisher.due_at(DAY, "10:00")):
                    with self.assertRaisesRegex(RuntimeError, "deadline"):
                        publisher.approve_day(ledger, root, DAY, identifier)
                publisher.approve_day(ledger, root, DAY, identifier)
                self.assertEqual(publisher.approved_posts(ledger, root, DAY), POSTS)
                key = ("iffat-nazir", "10:00")
                caption, images = POSTS[key]
                prepare.return_value = POSTS | {key: (caption, images[::-1])}
                with self.assertRaisesRegex(RuntimeError, "differs"):
                    publisher.approved_posts(ledger, root, DAY)

    @patch("publisher.submit")
    @patch("publisher.Buffer")
    @patch("publisher.open_ledger")
    def test_live_flag_alone_never_submits_unapproved_posts(self, open_ledger, buffer, submit):
        state = open_ledger.return_value.__enter__.return_value
        state.rows = {}
        settings = {"LINKEDIN_PUBLISHING_ENABLED": "true", "BUFFER_IFFAT_CHANNEL_ID": "iffat",
                    "BUFFER_ITRVIX_CHANNEL_ID": "itrvix"}
        with tempfile.TemporaryDirectory() as directory:
            state.checkout = Path(directory)
            with patch.dict(os.environ, settings), patch("sys.argv", ["publisher.py", "run"]):
                publisher.main()
        submit.assert_not_called()


if __name__ == "__main__":
    unittest.main()
