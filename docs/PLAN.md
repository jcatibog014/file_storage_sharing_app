# J: Drive: the plan

*"Your files, on the J side."*

## Context

Janelle wants a new learning project: an app to upload, organise, download and share files, with access controls, like Google Drive or OneDrive. The skills to practise are **object storage, signed URLs and access security**. What the user chose:

- **Name:** J: Drive (the J from Janelle + a drive letter). Package `jdrive`, local port **5012**, repo `github.com/jcatibog014/file_storage_sharing_app` (made by the user; it already has a `main` branch with a first commit). First step: clone it to a new folder `C:\Users\USER\Documents\Workspace\file_storage_sharing_app`, then branch `build-j-drive`.
- **Accounts:** open sign-up. The email must be **real**: the account starts only after a 6-digit code sent to that email is entered.
- **Sharing:** a folder (or file) can be opened by **anyone with the link**, and the owner can also add people by **email as Viewer, Editor or Co-owner**. They get a real email notice.
- **Storage:** **Backblaze B2** on the live site (S3-compatible, 10 GB free, no card), through `boto3` and presigned URLs. Locally and in tests a built-in **local bucket** works the same way.
- **Look:** "Lavender studio": soft lilac page, deep plum sidebar, violet accent, light + dark.
- **Extras (all four):** Safety net, Link controls, Find & preview, Activity & security.
- **Demo:** a per-visitor Try Demo with sample content on every page, deleted after 24 h.
- **PRs:** all 8 stages in **one PR** (branch `build-j-drive`), with a commit per stage and no stops.

**Email on the live site:** Render's free plan blocks outgoing SMTP (ports 25/465/587), so Gmail SMTP can't send from there. `mailer.py` therefore has three senders:
- **Brevo HTTP API** (free 300/day, no domain needed; the sender is the user's own verified Gmail address) for the live site;
- Gmail SMTP with an app password, which works when the app runs on the PC;
- an outbox printed to the console, plus a dev page at `/dev/outbox` (local and tests only).

Mail from Brevo with a Gmail sender may land in spam the first time. DEPLOY.md says so.

## Stack (same as Akda / Plan-It / Ticket Tock)

- Flask 3, SQLAlchemy 2.0 ORM + Alembic (`flask --app jdrive upgrade-db`), SQLite locally, Neon Postgres live, Render (Blueprint `j-drive`), pytest, `.venv`, `AGENTS.md` + `CLAUDE.md`.
- New: `boto3` (S3 API for B2), `moto[s3]` (fake S3 in tests), `pyotp` + `segno` (2-step codes + QR as SVG), Pillow (thumbnails), `stream-zip` is not needed (stdlib `zipfile` writes to a stream).
- I copy the shared pieces from Ticket Tock (`support_ticket_system/tickettock/`) and adapt them: `db.py` (with Hire Wire's SQLite FK-safe `upgrade_db`), `auth.py` (password hashing, CSRF, `session_version`, random demo session_version), `mailer.py`, `formatting.py`, `_toasts.html`, `theme.js`, `sw.js` / manifest, `docs/make_icons.py`, `tests/conftest.py` (quick pbkdf2 patch), `test_readme.py`, `test_icons.py`, `.github/workflows/tick.yml` + `/tasks/tick`.
- Lessons from earlier projects, built in from the start: `sqlite_autoincrement` on `users`/`items`; every FK to a parent that gets bulk-deleted has an index starting with that column; new NOT NULL columns get a `server_default`; template helpers are Jinja globals.

## Core design

### Data model (`models.py`)

| Table | What it holds |
|---|---|
| `users` | email (unique, lower-case), name, password hash, `email_verified_at`, quota bytes, `is_demo`, `session_version`, TOTP secret (stage 7) |
| `email_codes` | hashed 6-digit codes for sign-up / reset: purpose, expiry (15 min), tries left (5) |
| `items` | **one table for files and folders**: `kind`, `name`, `parent_id`, `owner_id`, `current_version_id`, `trashed_at`, `starred`, size cache. A name that is already taken in the folder becomes "report (1).pdf". |
| `blobs` | one stored object: `key` (random `b/<uuid4>`, never the filename), size, content type, `ref_count`, `state` (pending / ready) |
| `file_versions` | item → blob, uploaded_by, created_at |
| `shares` | item → (user_id **or** pending email), role `viewer`/`editor`/`coowner`, invited_by |
| `share_links` | item, **sha256 of the token** (the token itself is never stored), role `viewer`/`uploader`, expiry, password hash, allow_download, disabled_at, open count |
| `activity` | stage 7: append-only events |
| `sessions` | stage 7: one row per login, for "log out other devices" |

### Storage layer (`storage.py`): the object-storage lesson

One interface, two buckets:

- `presign_put(key, content_type, size)` → URL + headers
- `presign_get(key, filename, inline=False, ttl=60)`
- `head(key)`, `delete(key)`, `copy(src, dst)`, `open(key)` (stream, for thumbnails and ZIP)

`S3Bucket` uses boto3 against B2's S3 endpoint. ContentLength and ContentType are signed into the PUT URL. `response-content-disposition` makes downloads use the real filename.

`LocalBucket` keeps files in `instance/bucket/` and serves `/_bucket/<key>?op=&exp=&sig=`, checking an HMAC-SHA256 over (op, key, expiry, disposition) with a key derived from SECRET_KEY. Expired or changed links get a 403, just like S3.

**Upload flow:**
1. The browser asks `/api/uploads`. The server checks permission, quota and the size limit (100 MB per file), makes a `pending` blob and returns a presigned PUT (valid 10 min).
2. The browser PUTs the file **straight to the bucket**, with a progress bar and drag & drop.
3. The browser calls `/api/uploads/<id>/done`. The server `HEAD`s the object, checks the size and type, and marks the blob `ready`, or deletes it if anything doesn't match.

A plain form upload through Flask is the fallback when JavaScript is off. Pending uploads older than 1 h are cleaned up by `/tasks/tick`.

**Download:** every click goes through `/files/<id>/download`, which checks permission, logs the activity and redirects to a **60-second signed GET**. The bucket is private, and signed links are never put in the page HTML ahead of time.

**Safe serving:** files are sent as attachments by default. Inline is allowed only for images (not SVG), PDF, video/audio and plain text. Live, files come from B2's own domain. Locally, `/_bucket` adds `Content-Security-Policy: sandbox`, `X-Content-Type-Options: nosniff`, and `Content-Disposition`.

### Permissions (`permissions.py`): the access-security lesson

- Roles in order: **Viewer < Editor < Co-owner < Owner**.

  | Role | What it can do |
  |---|---|
  | Viewer | open, preview, download |
  | Editor | + upload, new folder, rename, move inside, trash, new version |
  | Co-owner | + manage people and links, restore from trash |
  | Owner | + delete for good, transfer ownership; can't be removed |

- **Inheritance:** a share on a folder covers everything inside it. The effective role is the **highest** role found on the item or any parent, found with a **recursive CTE** (works on SQLite and Postgres). A link token gives its role only inside the linked item's tree.
- One gate for every route: `require(item_id, action)`. It returns **404 (not 403)** when the person can't see the item, so a guessed ID tells you nothing. Moving an item is allowed only within a tree where you are Editor+, and never into its own child.
- **Inviting by email:**
  - An existing, verified user gets access straight away, plus an email.
  - An unknown email becomes a **pending share** and gets an email with a sign-up link.
  - Pending shares are claimed **only after that email address is verified** by its code. Signing up with someone else's email gives you nothing.
- Storage used by Editors inside a shared folder counts against the **folder owner's** quota (the OneDrive way).
- Signed-in pages are rate-limited on login, code entry and link passwords (per IP + per account, in the DB so it works on Render).

## Progress

- [x] Stage 1: setup, accounts, look
- [x] Stage 2: folders, files and object storage
- [x] Stage 3: sharing and permissions
- [x] Stage 4: safety net
- [ ] Stage 5: link controls
- [ ] Stage 6: find & preview
- [ ] Stage 7: activity & security
- [ ] Stage 8: demo, phone app, deploy, README

## Stages (one PR, a commit each)

1. **Setup, accounts, look.** App factory, models, migrations 0001, `upgrade-db`, sign up → email code → verified, log in/out, forgot password (emailed code), Settings (name, password, theme, Log out, where every control lives), the Lavender studio theme (tokens on `:root`, dark mode), sidebar (My Drive, Shared with me, Recent, Starred, Trash, Settings), the J: Drive logo + icons, `mailer.py` with Brevo/Gmail/console, CSRF, security headers + CSP.
2. **Folders, files and object storage.** `storage.py` (both buckets), the upload flow above, download redirect, new folder, rename, move (folder picker), breadcrumbs, list/grid view, sort, file-type icons, upload progress + drag & drop, duplicate-name handling. Tests use LocalBucket, and `moto` tests S3Bucket's presigning and HEAD checks.
3. **Sharing and permissions.** `permissions.py`, the Share dialog (add an email + role; list of people with role changes/removal; "Anyone with the link: Viewer"; Copy link), pending invites + emails, claim on verify, the Shared with me page, the public link page `/s/<token>` (folder browsing + download for visitors), and a big permission test matrix (every role × every action, plus outsiders, links and guessed IDs).
4. **Safety net.** Trash (restore, empty, auto-delete after 30 days by `/tasks/tick`), file versions (upload a new version, list, download or restore an old one, keep the last 10), blobs swept from the bucket once nothing points at them (a reference check, safer than a counter when a whole account cascades away), a storage quota (1 GB per account; demo smaller) with a usage bar in the sidebar.
5. **Link controls.** Expiry date, optional password (hashed, rate-limited, remembered in the session for 1 h), **"Can view" or "Can upload"** links (file-request style: visitors can add files but not see others), allow downloads on/off (preview only, marked "best effort"), turn the link off, make a new link (the old token stops working), and an open count.
6. **Find & preview.** Search (name, type, owner, in-folder), Starred, Recent (last opened/changed), the preview panel: images (Pillow WebP thumbnails stored as their own blobs), PDF (inline signed URL in an iframe), text/code (first 200 KB, escaped), video/audio (`<video>` with a signed URL). **Download folder as ZIP** streamed with `zipfile`, capped at 500 MB / 1,000 files, and only including what the person may see.
7. **Activity & security.** Append-only activity log (uploaded, downloaded, previewed, shared, role changed, link opened, renamed, moved, trashed, restored) with an Activity panel per item and a page in Settings. Notifications (a bell in the app + email when someone shares with you). Two-step sign-in with an authenticator app (pyotp, QR by segno, 8 one-time recovery codes). Active sessions with "Log out other devices" (`sessions` table + `session_version`). A new-device login email.
8. **Demo, phone app, deploy, README.**
   - **Try Demo** gives each visitor their own drive (owner "Janelle Demo"), with nested folders, photos, PDFs, text, an audio clip, a shared-with-you folder from sample people, a public link with a password and expiry, versions, trash, starred, activity and notifications. The **sample files are generated once** (`docs/make_samples.py`: Pillow images + PDF, text, a WAV tone) and uploaded with `flask upload-samples`. Each demo **points at the same blobs** (ref_count), so a demo uses no extra storage, and demo uploads are limited to 20 MB. A purge runs after 24 h. `tests/test_demo.py` fails if any demo page is empty.
   - PWA (install, offline page).
   - `render.yaml` (service `j-drive`) and `docs/DEPLOY.md`: B2 bucket (private) + app key + CORS rule for the Render URL, Brevo key + verified sender, Neon, GitHub secrets `TICK_URL`/`TICK_KEY`.
   - README in simple English with a tools table and screenshots from Try Demo (`docs/take_screenshots.py`, made-up data only), plus `docs/PLAN.md` with a progress list.

## Files to create (main ones)

`jdrive/__init__.py`, `models.py`, `db.py`, `auth.py`, `mailer.py`, `storage.py`, `permissions.py`, `files.py` (upload/version/move/trash logic), `sharing.py`, `links.py`, `previews.py`, `zips.py`, `activity.py`, `twostep.py`, `seed.py`, `tasks.py`, `migrations/versions/0001…`, `templates/` (drive, item panel, share dialog, link page, settings, auth), `static/style.css`, `app.js` (uploads, drag & drop, dialogs), `theme.js`, `sw.js`, `docs/PLAN.md`, `DEPLOY.md`, `make_icons.py`, `make_samples.py`, `take_screenshots.py`, `tests/test_*.py` per stage.

## Verification

- `pytest` after each stage. Every role × action is covered, plus signed-URL expiry and tampering, the upload HEAD check (wrong size → deleted), pending invite claim only after verify, inheritance and moves, quota, trash purge + blob ref counts, link expiry/password/upload-only, ZIP filtering, TOTP, session logout, demo completeness, README pictures and migrations up from empty.
- Run on port 5012 with `--debug` and click through as: owner, an invited Editor, a Co-owner, a Viewer, a logged-out visitor with a link, and Try Demo. Check that a guessed `/files/<id>` gives 404 and that an expired `/_bucket` link gives 403.
- Visual check and screenshots with Playwright, borrowed from expense_tracker's `.venv` as before. Contrast of the lavender/plum tokens checked in light and dark.
- Not testable by me: real B2 and Brevo (needs the user's keys), and PostgreSQL (the user runs the suite on an empty Neon branch). The PR description will say so.

## User steps (after the PR)

1. To go live: a Backblaze B2 bucket + app key, a Brevo account + verified sender, Neon, a Render Blueprint, the GitHub tick secrets. All of these are step-by-step in DEPLOY.md, each command starting with `cd` into the project.
