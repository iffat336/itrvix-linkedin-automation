# ITRVIX LinkedIn Automation

Automated LinkedIn publishing using Codex, Cloudinary, GitHub Actions and Buffer.

## Schedule and content

Each Karachi calendar day has four slots: Iffat Nazir at 10 AM and 5 PM,
and ITRVIX at 10 AM and 5 PM. The corresponding Buffer targets are
05:00 and 12:00 UTC. The workflow wakes hourly at minute 23 and queues
future slots for today and tomorrow using Buffer's custom schedule. GitHub
Actions can be delayed, so Buffer
owns delivery at the scheduled timestamp. Missed slots are reported and
require review.

## Automatic daily research and production

- **Iffat Nazir** rotates through field-crop breeding, crop genomics,
  genomic prediction, and AI for crop phenotyping. The research step pulls
  two distinct, eligible PubMed-indexed abstracts per topic through Europe
  PMC and filters out retraction records.
- **ITRVIX** rotates through software automation, university portals, cloud
  reliability, and AI agents. The research step retrieves two official
  technical references for each topic, from sources such as GitHub, W3C,
  AWS, Microsoft, and the OpenAI Agents SDK.
- OpenAI drafts a LinkedIn caption with two source-backed takeaways, a
  practical application, and a discussion question. The application checks
  both evidence quotes against the text fetched for that run and performs a
  separate model fact-check for relevance, scope, and unsupported claims.
  The caption includes links to both sources. Model review can miss errors,
  so inspect the preview before enabling live publishing.
- The application draws three original, topic-specific PNGs per post using
  Pillow and DejaVu Sans. They contain illustrative workflows, review items,
  and decision prompts, without third-party photographs, logos, or figures.
  The audit records the image provenance and font information.
- Only a complete four-post batch with twelve distinct PNGs reaches
  scheduling. Failed research, fact-checks, or asset validation leave
  the affected day unscheduled and visible in the Actions run. A later run
  can retry while slots are still open. Once validated, the plan is committed
  to the state branch before the first upload so retries reuse its content.

Daily files are optional. For an intentional override, add
`content/YYYY-MM-DD/posts.json` and its images before the day is generated.
Use [the sample format](content/posts.example.json). An existing frozen plan
takes precedence. This repository is public. Committed captions and editorial
audit data, GitHub preview artifacts, and Cloudinary delivery URLs can be
visible before publication.

## Validate locally

```sh
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
python publisher.py validate
```

Validation and tests require zero credentials. The sample is outside the
dated queue. For a preview, provide `OPENAI_API_KEY` in the local environment
and run `python publisher.py preview` (defaults to tomorrow, Karachi time).
Use `--date YYYY-MM-DD` and `--output PATH` to change the preview date and
directory. The **preview** workflow operation generates a seven-day artifact
with captions, PNGs, source citations, exact evidence excerpts, and rights
information. It requires the OpenAI key and does not contact Cloudinary or
Buffer. By default the writer and reviewer use separate requests to
`gpt-4.1-mini`; set `LINKEDIN_EDITORIAL_MODEL` to override it with a model
that supports structured Chat Completions output.

## Activation after approval

1. Connect Iffat Nazir's profile and the ITRVIX page in Buffer and confirm
   the target channels can publish automatically. Use a Buffer personal API
   key and the two corresponding LinkedIn channel IDs.
2. In GitHub Actions settings, add repository **secrets** `BUFFER_API_KEY`,
   `CLOUDINARY_API_KEY`, `CLOUDINARY_API_SECRET`, and `OPENAI_API_KEY`. Add repository
   **variables** `BUFFER_IFFAT_CHANNEL_ID` and `BUFFER_ITRVIX_CHANNEL_ID`.
   Keep `LINKEDIN_PUBLISHING_ENABLED` unset or `false` until approved.
3. In Cloudinary cloud `ki5jtsz7`, confirm `linkedin_public_images` is a
   signed preset for publicly deliverable image uploads. The application signs
   the upload, uses content-addressed IDs with overwrite disabled, and checks
   that the three resulting URLs each return an image without authentication.
4. After this workflow lands on the default branch, dispatch **LinkedIn
   publishing → initialize** once to create the durable state branch. This
   operation writes only the empty ledger and can run while live posting is
   disabled. Run **preview** to inspect a generated day before activation.
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
and identifiers. The state branch keeps captions and audit metadata in
`editorial/YYYY-MM-DD.json`. It excludes Cloudinary image URLs and raw API
error bodies.

A `submitting` entry without a Buffer ID means a prior submission may have
reached Buffer. Review the matching channel's queue, drafts, failed and sent
posts before intervening. If you find the post, have a repository maintainer
add its `buffer_id` to the matching ledger entry so the workflow can reconcile
it. If you can confirm Buffer never created it, a maintainer can remove just
that reservation before the slot expires. Pause publishing while editing the
ledger and wait for any active run to finish. For a known failed post, review
or retry the same post in Buffer so its ID and audit history are preserved.
