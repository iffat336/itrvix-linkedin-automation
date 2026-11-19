"""Email exact daily batches and bind approval to their contents."""
from email.message import EmailMessage
import hashlib
import json
import os
import smtplib
import ssl

ACCOUNT_NAMES = {"iffat-nazir": "Iffat Nazir (personal profile)", "itrvix": "ITRVIX (company page)"}


def batch_id(day, posts):
    manifest = [
        [account, slot, caption, [hashlib.sha256(image).hexdigest() for image in images]]
        for (account, slot), (caption, images) in sorted(posts.items())
    ]
    payload = json.dumps([day.isoformat(), manifest], ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def record_path(ledger, day):
    return ledger.checkout / "review" / f"{day}.json"


def read_record(ledger, day):
    path = record_path(ledger, day)
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def email_batch(day, posts, identifier):
    address = os.environ["GMAIL_REVIEW_ADDRESS"].strip()
    password = os.environ["GMAIL_APP_PASSWORD"].replace(" ", "")
    if not address.lower().endswith("@gmail.com") or not password:
        raise RuntimeError("Configure the Gmail address and app password in Actions secrets.")
    repository = os.environ["GITHUB_REPOSITORY"]
    message = EmailMessage()
    message["From"] = address
    message["To"] = address
    message["Subject"] = f"Approve 4 LinkedIn posts for {day} (Asia/Karachi)"
    message["Message-ID"] = f"<linkedin-review-{identifier}@reviews.itrvix>"
    body = [
        f"Review the four captions below and their twelve attached images. All times are Asia/Karachi.",
        "To approve all four posts, sign in to GitHub and open:",
        f"https://github.com/{repository}/actions/workflows/publish.yml",
        "Select Run workflow on the default branch and choose approve. Enter:",
        f"Review date: {day}",
        f"Batch ID: {identifier}",
        "Approve before 9:55 AM on the posting date. If you want changes, leave the batch unapproved.",
    ]
    attachments = []
    for (account, slot), (caption, images) in sorted(posts.items(), key=lambda item: (item[0][1], item[0][0])):
        prefix = f"{account}-{slot.replace(':', '')}"
        names = []
        for index, image in enumerate(images, start=1):
            is_png = image.startswith(b"\x89PNG\r\n\x1a\n")
            name = f"{prefix}-{index}.{'png' if is_png else 'jpg'}"
            attachments.append((image, "png" if is_png else "jpeg", name))
            names.append(name)
        body.append(f"\n{slot} · {ACCOUNT_NAMES[account]}\n{caption}\nImages: {', '.join(names)}")
    message.set_content("\n".join(body))
    for image, subtype, name in attachments:
        message.add_attachment(image, maintype="image", subtype=subtype, filename=name)
    if len(message.as_bytes()) > 24_000_000:
        raise RuntimeError("The review email exceeds the Gmail attachment size limit.")
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30, context=ssl.create_default_context()) as smtp:
        smtp.login(address, password)
        smtp.send_message(message)
