# Put J: Drive on the Internet

J: Drive uses four free services:

| Service | What it does for J: Drive |
|---|---|
| **Render** | runs the website |
| **Neon** | keeps the database (accounts, folders, shares) |
| **Backblaze B2** | keeps the files themselves, in a private bucket |
| **Brevo** | sends the emails (sign-up codes, invites) |

Render's free plan forgets its own files every time it restarts, and it blocks normal email (SMTP).
That is why the files go to B2 and the emails go through Brevo.

Do the steps in this order. It takes about 40 minutes. Run each command on its own line, in PowerShell.

## 1. Make the Database (Neon)

1. Log in at https://neon.tech.
2. Press **New Project**. Name: `j-drive`. Region: **Asia Pacific (Singapore)**.
3. Open **Connection Details** and copy the **connection string**. It starts with `postgresql://`.
   Keep it secret: it holds the database password. Never paste it in a chat.

## 2. Make the Bucket (Backblaze B2)

1. Sign up at https://www.backblaze.com/sign-up/cloud-storage (free for 10 GB; no card needed).
2. Open **Buckets**, then **Create a Bucket**:
   - Name: something only yours, like `j-drive-janelle-files` (names are shared by everyone on B2).
   - Files in Bucket: **Private**. (J: Drive gives out signed links itself. Never make it public.)
   - Default Encryption: **Enable** (B2 encrypts the files on its disks).
   - Object Lock: **Disable**.
3. On the new bucket's card, copy the **Endpoint**, like `s3.us-west-004.backblazeb2.com`.
   - `S3_ENDPOINT` is that with `https://` in front: `https://s3.us-west-004.backblazeb2.com`
   - `S3_REGION` is the middle part: `us-west-004`
   - `S3_BUCKET` is the bucket's name.
4. Open **Application Keys**, then **Add a New Application Key**:
   - Name: `j-drive`. Allow access to: **only your bucket**. Type of access: **Read and Write**.
   - Copy the **keyID** (`S3_KEY_ID`) and the **applicationKey** (`S3_SECRET`). B2 shows the applicationKey only once.

## 3. Get an Email Sender (Brevo)

1. Sign up at https://www.brevo.com (free: 300 emails a day).
2. Open **Senders, Domains & Dedicated IPs**, then **Senders**, then **Add a sender**:
   your own Gmail address. Brevo emails you a link: press it.
3. Open **SMTP & API**, then **API Keys**, then **Generate a new API key**. Copy it (`BREVO_API_KEY`).
4. `MAIL_FROM` is the Gmail address you added in step 2.

The first emails may land in **Spam**, because they say "from Gmail" but come from Brevo.
Tell the first people to check there (and press "Not spam").

## 4. Make the Website (Render)

1. Log in at https://render.com with your GitHub account.
2. Press **New**, then **Blueprint**, and choose the `file_storage_sharing_app` repo.
3. Render reads `render.yaml` and asks for the values from steps 1 to 3:
   `DATABASE_URL`, `S3_ENDPOINT`, `S3_REGION`, `S3_BUCKET`, `S3_KEY_ID`, `S3_SECRET`, `BREVO_API_KEY`, `MAIL_FROM`.
4. Press **Apply**. Wait for **Live** (about 5 minutes).
   Render makes `SECRET_KEY` and `TICK_KEY` by itself. **Never change `SECRET_KEY` later**:
   every shared link would stop working, and everyone would be logged out.
5. Copy your site's address, like `https://j-drive.onrender.com`.

## 5. Let the Browser Upload to the Bucket (CORS)

Uploads go from the browser straight to B2. B2 only takes them from sites you name.
From your computer (the B2 values are the same as on Render):

```
cd C:\Users\USER\Documents\Workspace\file_storage_sharing_app
```
```
$env:S3_ENDPOINT = "https://s3.us-west-004.backblazeb2.com"
```
```
$env:S3_REGION = "us-west-004"
```
```
$env:S3_BUCKET = "j-drive-janelle-files"
```
```
$env:S3_KEY_ID = "...your keyID..."
```
```
$env:S3_SECRET = "...your applicationKey..."
```
```
.venv\Scripts\flask --app jdrive bucket-cors https://j-drive.onrender.com
```

It says "The bucket now takes uploads from ...". Then close PowerShell, so the keys are not kept.

If B2 answers with an error instead, set the same rule with B2's own tool:

```
.venv\Scripts\python -m pip install b2
```
```
.venv\Scripts\b2 account authorize
```
```
.venv\Scripts\b2 bucket update --cors-rules (Get-Content docs\b2-cors.json -Raw) j-drive-janelle-files allPrivate
```

(First change the address in `docs\b2-cors.json` to your site's.)

Until CORS is set, uploads with the Upload button fail with "The connection dropped". Uploading without JavaScript still works.

## 6. Start the Timer (GitHub Actions)

The timer ends unfinished uploads, empties 30-day-old Trash, deletes files nothing uses any more, and deletes old demos.

1. On Render, open the service, then **Environment**, and copy the value of `TICK_KEY`.
2. On GitHub, open the repo, then **Settings**, **Secrets and variables**, **Actions**.
3. Add two secrets:
   - `TICK_URL`: `https://<your-site>.onrender.com/tasks/tick`
   - `TICK_KEY`: the value you copied.
4. Open **Actions**, then **Drive timer**, and press **Run workflow** once to test it.

## 7. Try It

1. Open your site and **Sign up** with your real email. The 6-digit code comes by email (check Spam).
2. Upload a file, open it, share a folder with a second email of yours, and turn on a link.
3. Press **Try demo** on the login page too.

## Check the Database Side Once

The tests run on SQLite. To run them on PostgreSQL too, make an **empty** branch in Neon
(**Branches**, **New branch**, name `test`), copy its connection string, then:

```
cd C:\Users\USER\Documents\Workspace\file_storage_sharing_app
```
```
$env:TEST_DATABASE_URL = "postgresql://...the TEST branch's string..."
```
```
.venv\Scripts\python -m pytest
```

Never use the main branch's string here: the tests delete every table.
