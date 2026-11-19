"""Validate and schedule dated LinkedIn content through Buffer."""
import argparse
from contextlib import contextmanager
from datetime import date, datetime, time, timedelta, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import cloudinary.uploader
from PIL import Image
import requests

import editorial

KARACHI = ZoneInfo("Asia/Karachi")
ACCOUNTS = {"iffat-nazir": "BUFFER_IFFAT_CHANNEL_ID", "itrvix": "BUFFER_ITRVIX_CHANNEL_ID"}
SLOTS = {"10:00", "17:00"}
EXPECTED = {(account, slot) for account in ACCOUNTS for slot in SLOTS}
CLOUD_NAME = "ki5jtsz7"
PRESET = "linkedin_public_images"
STATE_REF = "refs/heads/linkedin-publishing-state"
POST_FIELDS = """
    id status dueAt sentAt externalLink channelId schedulingType
    assets { type } error { supportUrl }
"""


def utc_now():
    return datetime.now(timezone.utc)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def load_day(root, day):
    folder = root / day.isoformat()
    entries = json.loads((folder / "posts.json").read_text(encoding="utf-8"))
    if not isinstance(entries, list) or len(entries) != 4:
        raise ValueError(f"{day}: expected exactly four posts")
    posts = {}
    for entry in entries:
        key = entry["account"], entry["time"]
        if key not in EXPECTED or key in posts:
            raise ValueError(f"{day}: invalid or duplicate account/time pair")
        caption = entry["text"].strip()
        if not caption or len(caption.encode("utf-16-le")) // 2 > 3000:
            raise ValueError(f"{day} {key}: expected 1–3000 caption characters")
        names = entry["images"]
        if not isinstance(names, list) or len(names) != 3:
            raise ValueError(f"{day} {key}: expected exactly three images")
        images = []
        for name in names:
            path = (folder / name).resolve()
            if not path.is_relative_to(folder.resolve()):
                raise ValueError(f"{day} {key}: image path must stay inside its date folder")
            with path.open("rb") as source:
                data = source.read(10_000_001)
            if len(data) > 10_000_000:
                raise ValueError(f"{day} {key}: image exceeds 10 MB")
            with Image.open(io.BytesIO(data)) as image:
                if image.format not in {"JPEG", "PNG"} or getattr(image, "is_animated", False):
                    raise ValueError(f"{day} {key}: expected a static JPEG or PNG")
                image.verify()
            images.append(data)
        if len({digest(image) for image in images}) != 3:
            raise ValueError(f"{day} {key}: expected three distinct images")
        posts[key] = caption, images
    return posts


def due_at(day, slot):
    return datetime.combine(day, time.fromisoformat(slot), KARACHI).astimezone(timezone.utc)


class Buffer:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers["Authorization"] = f"Bearer {os.environ['BUFFER_API_KEY']}"

    def query(self, query, **variables):
        response = self.session.post(
            "https://api.buffer.com", json={"query": query, "variables": variables}, timeout=30
        )
        response.raise_for_status()
        result = response.json()
        if result.get("errors"):
            raise RuntimeError("Buffer returned GraphQL errors")
        return result["data"]

    def check_channels(self, channels):
        for account, channel in channels.items():
            data = self.query(
                """query ($input: ChannelInput!) {
                  channel(input: $input) { id service isDisconnected isLocked isQueuePaused }
                }""",
                input={"id": channel},
            )["channel"]
            if (data["id"] != channel or data["service"] != "linkedin"
                    or any(data[key] for key in ("isDisconnected", "isLocked", "isQueuePaused"))):
                raise RuntimeError(f"{account}: Buffer channel is unavailable for LinkedIn scheduling")

    def create(self, channel, due, caption, urls):
        result = self.query(
            """mutation ($input: CreatePostInput!) {
              createPost(input: $input) {
                __typename
                ... on PostActionSuccess { post { """ + POST_FIELDS + """ } }
              }
            }""",
            input={
                "channelId": channel,
                "text": caption,
                "mode": "customScheduled",
                "schedulingType": "automatic",
                "dueAt": due.isoformat().replace("+00:00", "Z"),
                "assets": [{"image": {"url": url}} for url in urls],
            },
        )["createPost"]
        if result["__typename"] != "PostActionSuccess":
            raise RuntimeError(f"Buffer createPost returned {result['__typename']}")
        return result["post"]

    def get(self, post_id):
        return self.query(
            "query ($input: PostInput!) { post(input: $input) { " + POST_FIELDS + " } }",
            input={"id": post_id},
        )["post"]


def upload(images):
    urls = []
    for image in images:
        result = cloudinary.uploader.upload(
            io.BytesIO(image),
            cloud_name=CLOUD_NAME,
            api_key=os.environ["CLOUDINARY_API_KEY"],
            api_secret=os.environ["CLOUDINARY_API_SECRET"],
            resource_type="image",
            type="upload",
            upload_preset=PRESET,
            public_id=f"linkedin/{digest(image)}",
            overwrite=False,
            timeout=30,
        )
        url = result["secure_url"]
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.netloc != "res.cloudinary.com":
            raise RuntimeError("Cloudinary did not return the expected HTTPS delivery URL")
        # Anonymous GET verifies Buffer can fetch a direct public image.
        with requests.get(url, timeout=30, stream=True) as response:
            response.raise_for_status()
            if not response.headers.get("Content-Type", "").startswith("image/"):
                raise RuntimeError("Cloudinary delivery URL did not return an image")
        urls.append(url)
    if len(set(urls)) != 3:
        raise RuntimeError("Cloudinary did not return three distinct URLs")
    return urls


def git(*args, cwd=None):
    return subprocess.check_output(["git", *args], cwd=cwd, text=True).strip()


@contextmanager
def open_ledger(initialize=False):
    existing = git("ls-remote", "origin", STATE_REF)
    if initialize and existing:
        print("State branch already exists")
        yield None
        return
    if not existing and not initialize:
        raise RuntimeError("State branch is missing. Run the initialize workflow operation.")
    if existing:
        git("fetch", "--no-tags", "origin", STATE_REF)
    with tempfile.TemporaryDirectory() as temp:
        checkout = Path(temp) / "ledger"
        git("worktree", "add", "--detach", str(checkout), "FETCH_HEAD" if existing else "HEAD")
        try:
            if initialize:
                git("rm", "-rf", "--", ".", cwd=checkout)
            yield Ledger(checkout, existing.split()[0] if existing else "")
        finally:
            git("worktree", "remove", "--force", str(checkout))


class Ledger:
    def __init__(self, checkout, revision):
        self.checkout = checkout
        self.revision = revision
        self.path = checkout / "ledger.json"
        self.rows = json.loads(self.path.read_text()) if revision else {}

    def save(self, message):
        self.path.write_text(json.dumps(self.rows, indent=2, sort_keys=True) + "\n")
        git("add", "ledger.json", cwd=self.checkout)
        if not git("status", "--porcelain", cwd=self.checkout):
            return
        git("-c", "user.name=github-actions[bot]",
            "-c", "user.email=41898282+github-actions[bot]@users.noreply.github.com",
            "commit", "-m", message, cwd=self.checkout)
        git("push", f"--force-with-lease={STATE_REF}:{self.revision}",
            "origin", f"HEAD:{STATE_REF}", cwd=self.checkout)
        self.revision = git("rev-parse", "HEAD", cwd=self.checkout)


def update_status(row, post):
    row.update(
        buffer_id=post["id"],
        buffer_status=post["status"],
        sent_at=post["sentAt"],
        linkedin_url=post["externalLink"],
        buffer_help_url=(post["error"] or {}).get("supportUrl"),
    )
    if [asset["type"] for asset in post["assets"]] != ["image"] * 3:
        row["integrity_error"] = "Buffer did not return exactly three image assets"
    if post["schedulingType"] != "automatic":
        row["integrity_error"] = "Buffer did not return automatic scheduling"
    if post["status"] == "sent" and post["sentAt"] and post["schedulingType"] == "automatic":
        row["status"] = "published"
    elif post["status"] == "error":
        row["status"] = "failed"
    else:
        row["status"] = post["status"] if post["status"] != "sent" else "unconfirmed"


def refresh(ledger, buffer):
    errors = []
    for key, row in sorted(ledger.rows.items()):
        if row.get("buffer_id") and row["status"] != "published":
            try:
                update_status(row, buffer.get(row["buffer_id"]))
            except (RuntimeError, requests.RequestException, LookupError, ValueError) as error:
                print(f"{key}: Buffer status lookup failed ({type(error).__name__})")
                errors.append(key)
        overdue = (
            row["status"] in {"scheduled", "sending"}
            and utc_now() > datetime.fromisoformat(row["due_at"]) + timedelta(minutes=30)
        )
        if (overdue or row.get("integrity_error")
                or row["status"] in {"failed", "submitting", "draft", "needs_approval", "unconfirmed"}):
            print(f"{key}: review required ({row['status']})")
            errors.append(key)
    ledger.save("Refresh Buffer publication status")
    return errors


def submit(ledger, buffer, channel, key, due, caption, images):
    fingerprint = digest(json.dumps([
        channel, " ".join(caption.split()).casefold(), sorted(digest(image) for image in images),
    ]).encode())
    if key in ledger.rows:
        return
    if any(row["fingerprint"] == fingerprint for row in ledger.rows.values()):
        raise RuntimeError("Duplicate content previously submitted for this channel")
    urls = upload(images)
    if due <= utc_now() + timedelta(minutes=5):
        raise RuntimeError("Scheduled time is less than five minutes away")
    row = {"status": "submitting", "due_at": due.isoformat(), "fingerprint": fingerprint}
    ledger.rows[key] = row
    # Persist the reservation remotely before the non-idempotent call.
    ledger.save(f"Reserve {key}")
    post = buffer.create(channel, due, caption, urls)
    update_status(row, post)
    returned_due = datetime.fromisoformat(post["dueAt"]) if post["dueAt"] else None
    if post["channelId"] != channel or returned_due != due:
        row["integrity_error"] = "Buffer returned a different channel or scheduled time"
    ledger.save(f"Record Buffer response for {key}")
    if row["status"] != "scheduled" or row.get("integrity_error"):
        raise RuntimeError("Buffer did not confirm the expected scheduled post")


def generated_posts(plan, day, directory):
    if plan["date"] != day.isoformat():
        raise editorial.EditorialError("Editorial plan does not match the requested date")
    editorial.render_plan(plan, directory)
    posts = load_day(directory, day)
    if len({digest(image) for _, images in posts.values() for image in images}) != 12:
        raise editorial.EditorialError("Expected twelve distinct editorial images")
    if len({caption.split("\n", 1)[0].casefold() for caption, _ in posts.values()}) != 4:
        raise editorial.EditorialError("A daily batch repeated a caption opening")
    return posts


def prepare_day(ledger, root, day):
    plan_path = ledger.checkout / "editorial" / f"{day}.json"
    if plan_path.is_file():
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
    elif (root / day.isoformat() / "posts.json").is_file():
        return load_day(root, day)
    else:
        if any(key.startswith(f"{day}/") for key in ledger.rows):
            raise editorial.EditorialError("A day with existing reservations lacks its original editorial plan")
        plan = editorial.generate_plan(day)
    with tempfile.TemporaryDirectory() as directory:
        posts = generated_posts(plan, day, Path(directory))
    if not plan_path.is_file():
        plan_path.parent.mkdir(exist_ok=True)
        plan_path.write_text(json.dumps(plan, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        git("add", str(plan_path), cwd=ledger.checkout)
        ledger.save(f"Freeze reviewed LinkedIn content for {day}")
    return posts


def main():

    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=("validate", "preview", "run", "initialize"), nargs="?", default="validate")
    parser.add_argument("--date", type=date.fromisoformat, help="Karachi date for preview; defaults to tomorrow")
    parser.add_argument("--output", type=Path, default=Path("editorial-preview"), help="Preview output directory")
    args = parser.parse_args()
    root = Path("content")
    if args.operation == "validate":
        files = sorted(root.glob("*/posts.json"))
        for manifest in files:
            load_day(root, date.fromisoformat(manifest.parent.name))
        print(f"Validated {len(files)} dated content manifests")
        return
    if args.operation == "preview":
        day = args.date or utc_now().astimezone(KARACHI).date() + timedelta(days=1)
        args.output.mkdir(parents=True, exist_ok=True)
        generated_posts(editorial.generate_plan(day), day, args.output)
        print(f"Prepared four preview posts and twelve images for {day}")
        return
    if args.operation == "initialize":
        with open_ledger(initialize=True) as ledger:
            if ledger is not None:
                ledger.save("Initialize LinkedIn publishing ledger")
        return
    if os.environ.get("LINKEDIN_PUBLISHING_ENABLED") != "true":
        print("Live publishing is disabled")
        return
    channels = {account: os.environ[name].strip() for account, name in ACCOUNTS.items()}
    if "" in channels.values() or len(set(channels.values())) != 2:
        raise ValueError("Configure two distinct Buffer channel IDs")
    buffer = Buffer()
    today = utc_now().astimezone(KARACHI).date()
    tomorrow = today + timedelta(days=1)
    with open_ledger() as ledger:
        failures = refresh(ledger, buffer)
        buffer.check_channels(channels)
        for day in (today, tomorrow):
            missing_slots = [(account, slot) for account, slot in EXPECTED
                             if f"{day}/{account}/{slot}" not in ledger.rows]
            for account, slot in missing_slots:
                if due_at(day, slot) <= utc_now() + timedelta(minutes=5):
                    key = f"{day}/{account}/{slot}"
                    print(f"{key}: missed scheduling window")
                    failures.append(key)
            if not any(due_at(day, slot) > utc_now() + timedelta(minutes=5)
                       for _, slot in missing_slots):
                continue
            try:
                posts = prepare_day(ledger, root, day)
            except (OSError, ValueError, KeyError, TypeError, editorial.EditorialError,
                    requests.RequestException) as error:
                details = f": {error}" if isinstance(error, editorial.EditorialError) else ""
                print(f"{day}: editorial batch unavailable ({type(error).__name__}){details}")
                failures.append(day)
                continue
            for (account, slot), (caption, images) in sorted(posts.items()):
                key = f"{day}/{account}/{slot}"
                due = due_at(day, slot)
                if key in ledger.rows:
                    continue
                if due <= utc_now() + timedelta(minutes=5):
                    if key not in failures:
                        print(f"{key}: missed scheduling window")
                        failures.append(key)
                    continue
                try:
                    submit(ledger, buffer, channels[account], key, due, caption, images)
                except (RuntimeError, requests.RequestException, cloudinary.exceptions.Error,
                        KeyError, ValueError) as error:
                    print(f"{key}: scheduling requires review ({type(error).__name__})")
                    failures.append(key)
        if failures:
            raise SystemExit("Publishing run has items that require review")


if __name__ == "__main__":
    main()
