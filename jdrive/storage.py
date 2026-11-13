"""The bucket: where the files themselves live (object storage).

The database only knows about a file (its name, folder, owner, size). The
bytes are an "object" in a bucket, under a random key like "b/3f2a...". The
bucket is PRIVATE: nobody can read or write an object without a signed link.

A signed link (a "presigned URL") is an address with a signature in it. The
signature is an HMAC over the method, the key, the expiry time and the other
details, made with a secret only the server knows. The browser can use the
link until it runs out, but can't change anything in it: a different key, a
later expiry or another file name breaks the signature. So the server decides
who may do what, and the bucket only checks the signature.

Two buckets with the same methods:

- S3Bucket: Backblaze B2 on the live site, through its S3-compatible API and
  boto3, the library everyone uses for Amazon S3. B2 checks the signatures.
- LocalBucket: a folder (instance/bucket/) for your own computer and the
  tests. The app itself serves /_bucket/<key> and checks the signatures the
  same way, so everything else works exactly as it does live.

Uploads go straight from the browser to the bucket (presign_put), so big
files never pass through our server, and downloads come straight from it
(presign_get, valid for 60 seconds).
"""
import hashlib
import hmac
import json
import re
import secrets
import shutil
import time
from pathlib import Path
from urllib.parse import quote, urlsplit

import click
from flask import Blueprint, Response, abort, current_app, request, send_file, url_for
from flask.cli import with_appcontext

UPLOAD_LINK_SECONDS = 600     # 10 minutes to start the upload
DOWNLOAD_LINK_SECONDS = 60    # a download link works for one minute
# Keys look like "b/<32 hex>" (files) or "t/<32 hex>" (picture thumbnails). Nothing else is ever accepted.
KEY_PATTERN = re.compile(r"[bt]/[0-9a-f]{32}")

bp = Blueprint("bucket", __name__)


def new_key(prefix="b"):
    """A fresh random key. 128 random bits: nobody guesses one."""
    return f"{prefix}/{secrets.token_hex(16)}"


def content_disposition(filename, inline=False):
    """The header that tells the browser the file's name, and whether to show it
    (inline) or save it (attachment). Non-ASCII names need the filename* form."""
    ascii_name = "".join(ch if 32 <= ord(ch) < 127 and ch not in '"\\' else "_" for ch in filename) or "file"
    kind = "inline" if inline else "attachment"
    return f"{kind}; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename, safe='')}"


class Head(dict):
    """What the bucket says about an object: {"size": ..., "content_type": ...}."""


# ---------- Backblaze B2 (or any S3-compatible bucket) ----------

class S3Bucket:
    def __init__(self, endpoint, region, bucket, key_id, secret):
        import boto3
        from botocore.config import Config
        self.bucket = bucket
        self.endpoint = endpoint.rstrip("/")
        # Path-style addresses (https://endpoint/bucket/key): the browser then only
        # ever talks to one address, which the CSP allows (origin()).
        self.client = boto3.client(
            "s3", endpoint_url=self.endpoint, region_name=region,
            aws_access_key_id=key_id, aws_secret_access_key=secret,
            config=Config(signature_version="s3v4", s3={"addressing_style": "path"},
                          retries={"max_attempts": 3}),
        )

    def origin(self):
        parts = urlsplit(self.endpoint)
        return f"{parts.scheme}://{parts.netloc}"

    def presign_put(self, key, content_type, size, ttl=UPLOAD_LINK_SECONDS):
        """A link the browser can PUT exactly this file to, once, for 10 minutes.
        The type and length are part of the signature, and finish_upload checks
        both again afterwards (HEAD), so a different file can't sneak in."""
        url = self.client.generate_presigned_url(
            "put_object",
            Params={"Bucket": self.bucket, "Key": key, "ContentType": content_type, "ContentLength": size},
            ExpiresIn=ttl, HttpMethod="PUT",
        )
        return {"url": url, "method": "PUT", "headers": {"Content-Type": content_type}}

    def presign_get(self, key, filename, content_type, inline=False, ttl=DOWNLOAD_LINK_SECONDS):
        return self.client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": key,
                    "ResponseContentDisposition": content_disposition(filename, inline),
                    "ResponseContentType": content_type},
            ExpiresIn=ttl,
        )

    def head(self, key):
        from botocore.exceptions import ClientError
        try:
            answer = self.client.head_object(Bucket=self.bucket, Key=key)
        except ClientError as error:
            if error.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
                return None
            raise
        return Head(size=answer["ContentLength"], content_type=answer.get("ContentType", ""))

    def put_bytes(self, key, data, content_type):
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=content_type)

    def put_stream(self, key, stream, content_type, size=None, limit=None):
        # Flask's MAX_CONTENT_LENGTH already capped what the form could send.
        self.client.upload_fileobj(stream, self.bucket, key, ExtraArgs={"ContentType": content_type})
        return True

    def open(self, key):
        """The object's bytes as a stream (for thumbnails and folder ZIPs)."""
        return self.client.get_object(Bucket=self.bucket, Key=key)["Body"]

    def copy(self, source_key, key):
        self.client.copy_object(Bucket=self.bucket, Key=key, CopySource={"Bucket": self.bucket, "Key": source_key})

    def delete(self, key):
        self.client.delete_object(Bucket=self.bucket, Key=key)


# ---------- The local bucket (a folder) ----------

class LocalBucket:
    """Objects are files in a folder; a small .json beside each keeps its type
    (S3 keeps that with the object too)."""

    def __init__(self, folder, secret):
        self.folder = Path(folder)
        # Its own key, made from SECRET_KEY, so a bucket signature can never be
        # reused as any other signature in the app.
        self.secret = hmac.new(secret.encode(), b"jdrive-local-bucket", hashlib.sha256).digest()

    def origin(self):
        return None  # the same site as the app

    def path(self, key):
        if not KEY_PATTERN.fullmatch(key or ""):
            raise ValueError("not a bucket key")
        return self.folder / key

    def sign(self, *parts):
        message = "\n".join(str(part) for part in parts).encode()
        return hmac.new(self.secret, message, hashlib.sha256).hexdigest()

    def presign_put(self, key, content_type, size, ttl=UPLOAD_LINK_SECONDS):
        expires = int(time.time()) + ttl
        signature = self.sign("PUT", key, expires, content_type, size)
        url = url_for("bucket.put_object", key=key, exp=expires, size=size, sig=signature)
        return {"url": url, "method": "PUT", "headers": {"Content-Type": content_type}}

    def presign_get(self, key, filename, content_type, inline=False, ttl=DOWNLOAD_LINK_SECONDS):
        expires = int(time.time()) + ttl
        disposition = content_disposition(filename, inline)
        signature = self.sign("GET", key, expires, disposition, content_type)
        return url_for("bucket.get_object", key=key, exp=expires, disp=disposition, type=content_type, sig=signature)

    def head(self, key):
        path = self.path(key)
        if not path.exists():
            return None
        meta = json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
        return Head(size=path.stat().st_size, content_type=meta.get("content_type", ""))

    def _write_meta(self, key, content_type):
        self.path(key).with_suffix(".json").write_text(json.dumps({"content_type": content_type}), encoding="utf-8")

    def put_bytes(self, key, data, content_type):
        path = self.path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        self._write_meta(key, content_type)

    def put_stream(self, key, stream, content_type, size=None, limit=None):
        """Copy a stream into the bucket in pieces. With a limit, stop (and keep
        nothing) once more than that many bytes arrive."""
        path = self.path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        written = 0
        with path.open("wb") as out:
            while chunk := stream.read(1024 * 1024):
                written += len(chunk)
                if limit is not None and written > limit:
                    break
                out.write(chunk)
        if limit is not None and written > limit:
            path.unlink(missing_ok=True)
            return False
        self._write_meta(key, content_type)
        return True

    def open(self, key):
        return self.path(key).open("rb")

    def copy(self, source_key, key):
        target = self.path(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self.path(source_key), target)
        shutil.copyfile(self.path(source_key).with_suffix(".json"), target.with_suffix(".json"))

    def delete(self, key):
        path = self.path(key)
        path.unlink(missing_ok=True)
        path.with_suffix(".json").unlink(missing_ok=True)


def _check_signature(bucket, expected_parts):
    """Like S3: a link that ran out or was changed in any way is refused (403)."""
    try:
        expires = int(request.args.get("exp", ""))
    except ValueError:
        abort(403, "This link isn't valid.")
    signature = bucket.sign(*expected_parts(expires))
    if not hmac.compare_digest(signature, request.args.get("sig", "")):
        abort(403, "This link isn't valid.")
    if expires < time.time():
        abort(403, "This link has run out. Go back and try again.")


@bp.route("/_bucket/<path:key>", methods=["PUT"])
def put_object(key):
    """The browser's upload, straight into the local bucket."""
    bucket = get()
    if not isinstance(bucket, LocalBucket) or not KEY_PATTERN.fullmatch(key):
        abort(404)
    size_text = request.args.get("size", "")
    content_type = request.headers.get("Content-Type", "")
    _check_signature(bucket, lambda expires: ("PUT", key, expires, content_type, size_text))
    if request.content_length is None or str(request.content_length) != size_text:
        abort(400, "The file's size isn't the one the link was made for.")
    if not bucket.put_stream(key, request.stream, content_type, limit=int(size_text)):
        abort(400, "The file's size isn't the one the link was made for.")
    return Response(status=200)


@bp.get("/_bucket/<path:key>")
def get_object(key):
    """A download (or preview) from the local bucket."""
    bucket = get()
    if not isinstance(bucket, LocalBucket) or not KEY_PATTERN.fullmatch(key):
        abort(404)
    disposition = request.args.get("disp", "")
    content_type = request.args.get("type", "application/octet-stream")
    _check_signature(bucket, lambda expires: ("GET", key, expires, disposition, content_type))
    path = bucket.path(key)
    if not path.exists():
        abort(404)
    response = send_file(path, mimetype=content_type, conditional=True, max_age=0)
    response.headers["Content-Disposition"] = disposition
    response.headers["Cache-Control"] = "private, no-store"
    # A file from someone else must never act as a page of this site. Live, files
    # come from B2's own address anyway; here, "sandbox" makes the browser treat
    # it as coming from nowhere (no scripts, no cookies). PDFs are the exception:
    # browsers won't show a PDF inside a sandbox, and their viewers don't run page scripts.
    if content_type != "application/pdf":
        response.headers["Content-Security-Policy"] = "sandbox; default-src 'none'; img-src 'self'; media-src 'self'; style-src 'unsafe-inline'"
    else:
        response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'self'"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"  # previews show it in our own page
    return response


def get():
    return current_app.extensions["jdrive_bucket"]


def cors_rules(origin):
    """What the bucket must allow so browsers on our site can PUT uploads straight
    to it and GET previews (CORS: the browser asks the bucket first)."""
    return [{
        "AllowedOrigins": [origin],
        "AllowedMethods": ["PUT", "GET", "HEAD"],
        "AllowedHeaders": ["content-type"],
        "ExposeHeaders": ["ETag"],
        "MaxAgeSeconds": 3600,
    }]


@click.command("bucket-cors")
@with_appcontext
@click.argument("origin")
def bucket_cors_command(origin):
    """Let the site at ORIGIN (like https://j-drive.onrender.com) upload straight to the bucket."""
    bucket = get()
    if not isinstance(bucket, S3Bucket):
        raise click.ClickException("Set the S3_ settings first: this is for the live bucket (Backblaze B2).")
    origin = origin.rstrip("/")
    bucket.client.put_bucket_cors(Bucket=bucket.bucket, CORSConfiguration={"CORSRules": cors_rules(origin)})
    click.echo(f"The bucket now takes uploads from {origin}.")


def init_app(app):
    config = app.config
    if config.get("S3_BUCKET"):
        missing = [name for name in ("S3_ENDPOINT", "S3_REGION", "S3_KEY_ID", "S3_SECRET") if not config.get(name)]
        if missing:
            raise RuntimeError("Set these for the bucket too: " + ", ".join(missing))
        bucket = S3Bucket(config["S3_ENDPOINT"], config["S3_REGION"], config["S3_BUCKET"],
                          config["S3_KEY_ID"], config["S3_SECRET"])
    else:
        if config.get("ON_RENDER"):
            # Render forgets its own files on every restart: uploads would vanish.
            raise RuntimeError("Set S3_BUCKET (and the other S3_ settings) on Render: see docs/DEPLOY.md.")
        Path(config["BUCKET_DIR"]).mkdir(parents=True, exist_ok=True)
        bucket = LocalBucket(config["BUCKET_DIR"], config["SECRET_KEY"])
    app.extensions["jdrive_bucket"] = bucket
    app.register_blueprint(bp)
    app.cli.add_command(bucket_cors_command)
    return bucket
