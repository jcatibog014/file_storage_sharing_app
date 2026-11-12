# J: Drive

*Your files, on the J side.*

J: Drive is a place to keep your files. You upload them, put them in folders, and download them again.
You can share a folder with a link, or add people by email. You decide what each person can do.

The name: **J** is for Janelle, and **J:** looks like a drive on a computer (like C: or D:).

![The login page](docs/screenshots/login.png)

## Try It

Press **Try demo** on the login page. You get your own sample drive with folders, pictures, PDFs,
shares and links. Nobody else sees it, and it is deleted after 24 hours.

![The demo starts](docs/screenshots/demo-start.png)

## What It Does

### Your Account

- **Sign up** with your name, email and a password.
- J: Drive **emails you a 6-digit code**. Your account works only after you type it in, so the email must be real.
- Forgot your password? J: Drive emails you a code to make a new one.

![Check your email](docs/screenshots/check-email.png)

### Your Files

- **Upload** files with the Upload button, or drag them onto the page. Each file can be up to 100 MB.
- Make **folders**, and folders inside folders. **Rename** and **move** things.
- See your files as a **list** or a **grid** with small pictures. Sort them by name, date or size.
- **Download** a file, or a whole folder as one ZIP file.

![My Drive](docs/screenshots/my-drive.png)

![Pictures in a grid](docs/screenshots/photos-grid.png)

### Look Inside Files

- Open a file to **see it** without downloading: pictures, PDFs, text, video and sound.
- Every file keeps its **versions**. Upload a new version, or bring an old one back.

![A picture](docs/screenshots/picture.png)

![Text and versions](docs/screenshots/text-and-versions.png)

### Share with People

- Add people **by email** as **Viewer**, **Editor** or **Co-owner**:

  | Role | What they can do |
  |---|---|
  | Viewer | open and download |
  | Editor | also upload, rename, move and remove things |
  | Co-owner | also share it with others and choose who has access |
  | Owner | everything, and delete things for good |

- Sharing a folder shares everything inside it.
- They get an **email**. If they have no account yet, the invite waits until they sign up with that email.
- Files that others share with you are in **Shared With Me**.

![The Share page](docs/screenshots/share.png)

![Shared With Me](docs/screenshots/shared-with-me.png)

### Share with a Link

- Turn on a **link**: anyone with it can open the folder or file, without an account.
- **Link settings:** an end date, a password, downloads on or off.
- A **"Can upload" link** lets people send you files (like homework), without seeing what is in the folder.
- Make a new link at any time: the old one stops working.

![Link settings](docs/screenshots/link-settings.png)

![A visitor opens a link](docs/screenshots/visitor-folder.png)

![A visitor sends files](docs/screenshots/visitor-upload.png)

### Find Things

- **Search** by name and type, in your drive and in everything shared with you.
- **Recent** shows what you opened lately. **Starred** keeps your favourites.

![Search](docs/screenshots/search.png)

![Recent](docs/screenshots/recent.png)

![Starred](docs/screenshots/starred.png)

### Safety Net

- **Trash:** removed things wait 30 days before they are deleted for good. You can bring them back.
- **Space:** each account has 1 GB. The bar in the menu shows how much you use.

![The Trash](docs/screenshots/trash.png)

### Activity and Security

- **Activity:** see who uploaded, downloaded, shared or opened your files, and when.
- **Notifications:** the bell tells you when someone shares with you or sends you files.
- **Two-step sign-in:** use an authenticator app on your phone, so a stolen password is not enough.
- **Where you're logged in:** log out a device you don't know, or all other devices at once.

![Activity in a folder](docs/screenshots/folder-activity.png)

![All activity](docs/screenshots/activity.png)

![Notifications](docs/screenshots/notifications.png)

![Settings](docs/screenshots/settings.png)

### Dark Mode and Phones

Choose Light, Dark or your device's setting in **Settings**. J: Drive works on phones, and you can install it like an app.

![Dark mode grid](docs/screenshots/dark-grid.png)

![Dark mode Share page](docs/screenshots/dark-share.png)

| My Drive on a phone | A picture on a phone |
|---|---|
| ![My Drive on a phone](docs/screenshots/phone-drive.png) | ![A picture on a phone](docs/screenshots/phone-picture.png) |

## How It Keeps Files Safe

These are the main ideas this project practises:

- **Object storage.** The files are not in the database. They are "objects" in a **private bucket** (Backblaze B2), each under a random name like `b/3f2a…`. The database only knows the file's name, folder and owner.
- **Signed links.** Nobody can open the bucket directly. When you upload or download, J: Drive gives your browser a **signed link** for that one file. It works for a short time only (10 minutes to upload, 60 seconds to download). If anyone changes the link, it stops working.
- **Uploads go straight to the bucket.** Your browser sends the file to B2, not through the website. Then J: Drive checks that the file arrived with the right size before it shows it.
- **One check for everything.** Every page asks "what is this person's role here?" first. If you have no role, you get "Not Found", so you can't even tell that a file exists.
- **Secrets are never stored as they are.** Passwords, email codes, link secrets and recovery codes are kept only as hashes.

## Tools

| Tool | What it is | What J: Drive uses it for |
|---|---|---|
| Python 3.13 | a programming language | all the code on the server |
| Flask | a small web framework | the pages and the forms |
| SQLAlchemy + Alembic | a database library and its migrations | the tables and changing them safely |
| SQLite / PostgreSQL (Neon) | databases | SQLite on my computer, PostgreSQL on the live site |
| boto3 | the library for Amazon S3 and others like it | talking to the bucket and making signed links |
| Backblaze B2 | object storage (10 GB free) | keeping the files |
| Brevo | an email service | sign-up codes and invites |
| Pillow | a picture library | small pictures (thumbnails) and the demo's sample files |
| pyotp + segno | two-step codes and QR codes | two-step sign-in |
| HTML, CSS, JavaScript | the languages of web pages | the look, drag and drop, upload progress |
| pytest + moto | tests, and a pretend S3 | checking everything works (no internet needed) |
| Render | a hosting service | running the live site |
| GitHub Actions | automatic jobs on GitHub | the timer that cleans up every 15 minutes |

## Run It on Your Computer

```
cd C:\Users\USER\Documents\Workspace\file_storage_sharing_app
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\flask --app jdrive upgrade-db
.venv\Scripts\flask --app jdrive run --debug --port 5012
```

Open http://127.0.0.1:5012. On your computer, emails are not sent: the sign-up code is at http://127.0.0.1:5012/dev/outbox.

Run the tests with `.venv\Scripts\python -m pytest`.

To put it on the internet, follow [docs/DEPLOY.md](docs/DEPLOY.md). The plan is in [docs/PLAN.md](docs/PLAN.md).
