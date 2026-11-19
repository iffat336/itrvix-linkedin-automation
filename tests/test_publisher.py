from datetime import date, timedelta
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from PIL import Image

import publisher


class PublisherTests(unittest.TestCase):
    def test_karachi_schedule(self):
        day = date(2026, 11, 20)
        self.assertEqual(publisher.due_at(day, "10:00").isoformat(), "2026-11-20T05:00:00+00:00")
        self.assertEqual(publisher.due_at(day, "17:00").isoformat(), "2026-11-20T12:00:00+00:00")

    def test_validate_four_slots_and_exactly_three_images(self):
        day = date(2026, 11, 20)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            folder = root / day.isoformat()
            folder.mkdir()
            names = ["one.png", "two.png", "three.png"]
            for index, name in enumerate(names):
                Image.new("RGB", (10, 10), (index, 0, 0)).save(folder / name)
            entries = [{"account": account, "time": slot, "text": "Caption", "images": names}
                       for account, slot in sorted(publisher.EXPECTED)]
            manifest = folder / "posts.json"
            manifest.write_text(json.dumps(entries))
            self.assertEqual(set(publisher.load_day(root, day)), publisher.EXPECTED)
            entries[0]["images"] = [names[0]] * 3
            manifest.write_text(json.dumps(entries))
            with self.assertRaisesRegex(ValueError, "distinct"):
                publisher.load_day(root, day)
            entries[0]["images"] = names[:2]
            manifest.write_text(json.dumps(entries))
            with self.assertRaisesRegex(ValueError, "exactly three"):
                publisher.load_day(root, day)

    @patch("publisher.Buffer")
    def test_run_defaults_to_disabled(self, buffer):
        with patch.dict(os.environ, {}, clear=True), patch("sys.argv", ["publisher.py", "run"]):
            publisher.main()
        buffer.assert_not_called()

    @patch("publisher.requests.get")
    @patch("publisher.cloudinary.uploader.upload")
    def test_signed_upload_returns_three_public_urls(self, upload, get):
        urls = [f"https://res.cloudinary.com/ki5jtsz7/image/upload/{i}.png" for i in range(3)]
        upload.side_effect = [{"secure_url": url} for url in urls]
        get.return_value.__enter__.return_value.headers = {"Content-Type": "image/png"}
        with patch.dict(os.environ, CLOUDINARY_API_KEY="test-key", CLOUDINARY_API_SECRET="test-secret"):
            self.assertEqual(publisher.upload([b"a", b"b", b"c"]), urls)
        self.assertEqual(upload.call_count, 3)
        for call in upload.call_args_list:
            self.assertEqual(call.kwargs["cloud_name"], "ki5jtsz7")
            self.assertEqual(call.kwargs["upload_preset"], "linkedin_public_images")
            self.assertEqual(call.kwargs["api_secret"], "test-secret")
            self.assertFalse(call.kwargs["overwrite"])

    def test_buffer_create_payload(self):
        urls = ["https://example.com/one", "https://example.com/two", "https://example.com/three"]
        with patch.dict(os.environ, BUFFER_API_KEY="test"):
            buffer = publisher.Buffer()
        with patch.object(buffer, "query", return_value={
            "createPost": {"__typename": "PostActionSuccess", "post": {"id": "post"}}
        }) as query:
            buffer.create("channel", publisher.due_at(date(2026, 11, 20), "17:00"), "Caption", urls)
        payload = query.call_args.kwargs["input"]
        self.assertEqual(payload["assets"], [{"image": {"url": url}} for url in urls])
        self.assertEqual(payload["dueAt"], "2026-11-20T12:00:00Z")
        self.assertEqual(payload["mode"], "customScheduled")
        self.assertEqual(payload["schedulingType"], "automatic")

    @patch("publisher.upload", return_value=["url1", "url2", "url3"])
    def test_ambiguous_create_keeps_reservation_and_blocks_duplicates(self, upload):
        ledger, buffer = Mock(rows={}), Mock()
        ledger.save.side_effect = lambda message: self.assertEqual(
            ledger.rows["slot"]["status"], "submitting"
        )
        def fail_after_reservation(*args):
            ledger.save.assert_called_once()
            raise RuntimeError("Timed out after request")
        buffer.create.side_effect = fail_after_reservation
        due = publisher.utc_now() + timedelta(hours=1)
        with self.assertRaisesRegex(RuntimeError, "Timed out"):
            publisher.submit(ledger, buffer, "channel", "slot", due, "Caption", [b"a", b"b", b"c"])
        publisher.submit(ledger, buffer, "channel", "slot", due, "Edited", [b"a", b"b", b"c"])
        with self.assertRaisesRegex(RuntimeError, "Duplicate content"):
            publisher.submit(ledger, buffer, "channel", "other", due, "Caption", [b"c", b"a", b"b"])
        buffer.create.assert_called_once()
        upload.assert_called_once()

    def test_publication_requires_automatic_sent_timestamp(self):
        row = {}
        post = {"id": "xyz", "status": "scheduled", "sentAt": None, "externalLink": None,
                "error": None, "assets": [{"type": "image"}] * 3, "schedulingType": "automatic"}
        publisher.update_status(row, post)
        self.assertEqual(row["status"], "scheduled")
        post["status"] = "sent"
        publisher.update_status(row, post)
        self.assertEqual(row["status"], "unconfirmed")
        post.update(sentAt="2026-11-20T05:00:03Z", externalLink="https://www.linkedin.com/feed/update/1")
        publisher.update_status(row, post)
        self.assertEqual(row["status"], "published")
        self.assertEqual(row["linkedin_url"], post["externalLink"])


if __name__ == "__main__":
    unittest.main()
