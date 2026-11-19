# ITRVIX LinkedIn Automation

Automated LinkedIn publishing using Codex, Cloudinary, GitHub Actions and Buffer.

## Schedule and content

Each Karachi calendar day has four slots: Iffat Nazir at 10 AM and 5 PM,
and ITRVIX at 10 AM and 5 PM. The corresponding Buffer targets are
05:00 and 12:00 UTC. The workflow wakes hourly at minute 23 and queues
future slots for today and tomorrow using Buffer's custom schedule. Supply
content at least one day ahead. GitHub Actions can be delayed, so Buffer
owns delivery at the scheduled timestamp. Missed slots are reported and
require review.

Create `content/YYYY-MM-DD/posts.json` and put its images in the same dated
directory. Use the format in [the sample](content/posts.example.json).
Use the account keys and times as shown, replace all sample captions, and
add the image files. Each post must have exactly three distinct static JPEG
or PNG images of up to 10 MB each. Image order is preserved. This pipeline uses
a dated editorial queue. It expects you to supply captions and images. This
repository is public, so content committed here and the Cloudinary delivery
URLs are visible publicly, including before the scheduled publication time.

## Validate locally

```sh
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
python publisher.py validate
```

Validation and tests require zero credentials. No real LinkedIn content is
included in the sample, and the sample is outside the dated queue.

## Activation after approval

1. Connect Iffat Nazir's profile and the ITRVIX page in Buffer and confirm
   the target channels can publish automatically. Use a Buffer personal API
   key and the two corresponding LinkedIn channel IDs.
2. In GitHub Actions settings, add repository **secrets** `BUFFER_API_KEY`,
   `CLOUDINARY_API_KEY` and `CLOUDINARY_API_SECRET`. Add repository
   **variables** `BUFFER_IFFAT_CHANNEL_ID` and `BUFFER_ITRVIX_CHANNEL_ID`.
   Keep `LINKEDIN_PUBLISHING_ENABLED` unset or `false` until approved.
3. In Cloudinary cloud `ki5jtsz7`, confirm `linkedin_public_images` is a
   signed preset for publicly deliverable image uploads. The application signs
   the upload, uses content-addressed IDs with overwrite disabled, and checks
   that the three resulting URLs each return an image without authentication.
4. After this workflow lands on the default branch, dispatch **LinkedIn
   publishing → initialize** once to create the durable state branch. This
   operation writes only the empty ledger and can run while live posting is
   disabled. Run **validate** to check the manifest files.
5. After approval, set `LINKEDIN_PUBLISHING_ENABLED` to the exact value
   `true`. Dispatch **run** for the first scheduling pass, or wait for the
   hourly run. Set the variable back to `false` to stop further submissions
   and polling. Posts already scheduled in Buffer remain scheduled and can be
   managed in Buffer.

The API contract was checked against Buffer's
[LinkedIn image specifications](https://support.buffer.com/articles/ideal-image-sizes-and-formats-for-your-buffer-posts-JxHNGZFvf9),
[image-post example](https://developers.buffer.com/examples/create-image-post.md),
[custom scheduling example](https://developers.buffer.com/examples/create-scheduled-post.md)
and [GraphQL reference](https://developers.buffer.com/reference.md). Verify
the first approved run by checking both channels' queues, the three attached
images on each post, and each resulting LinkedIn link after publication.

## Publication tracking and recovery

The `linkedin-publishing-state` branch holds `ledger.json`. Keep that branch
and its history intact. The workflow records a reservation before contacting
Buffer and serializes runs. Git checks the previous branch revision on writes.
The ledger suppresses repeats by account, date, and time and blocks the same
caption and image set from being submitted again to the same channel.

Scheduling success records the Buffer post ID. Later runs query Buffer for
the actual status. A post is marked `published` only after Buffer reports
automatic delivery as `sent` with a `sentAt` timestamp. The ledger keeps the
timestamp and the LinkedIn URL when available. Failed, unconfirmed, and
overdue items surface in the workflow. The ledger stores fingerprints, status
and identifiers, with captions, image URLs and raw API error bodies excluded.

A `submitting` entry without a Buffer ID means a prior submission may have
reached Buffer. Review the matching channel's queue, drafts, failed and sent
posts before intervening. If you find the post, have a repository maintainer
add its `buffer_id` to the matching ledger entry so the workflow can reconcile
it. If you can confirm Buffer never created it, a maintainer can remove just
that reservation before the slot expires. Pause publishing while editing the
ledger and wait for any active run to finish. For a known failed post, review
or retry the same post in Buffer so its ID and audit history are preserved.
