"""The bucket: signed links that run out and can't be changed, and the S3 code (against moto)."""
import io
import time
from urllib.parse import parse_qs, urlsplit

import pytest

from jdrive import storage


def local(app):
    return app.extensions["jdrive_bucket"]


def put(client, link, data, content_type=None):
    return client.put(link["url"], data=data, headers={"Content-Type": content_type or link["headers"]["Content-Type"]})


def test_a_signed_put_and_get_work(app, client):
    bucket = local(app)
    key = storage.new_key()
    with app.test_request_context():
        link = bucket.presign_put(key, "text/plain", 5)
    assert put(client, link, b"hello").status_code == 200
    assert bucket.head(key) == {"size": 5, "content_type": "text/plain"}
    with app.test_request_context():
        url = bucket.presign_get(key, "notes.txt", "text/plain")
    response = client.get(url)
    assert response.status_code == 200 and response.data == b"hello"
    assert 'attachment; filename="notes.txt"' in response.headers["Content-Disposition"]
    assert response.headers["Cache-Control"] == "private, no-store"
    # A file from someone else can never act as a page of this site.
    assert response.headers["Content-Security-Policy"].startswith("sandbox")
    assert response.headers["X-Content-Type-Options"] == "nosniff"


def test_a_changed_link_is_refused(app, client):
    bucket = local(app)
    key = storage.new_key()
    bucket.put_bytes(key, b"secret", "text/plain")
    with app.test_request_context():
        url = bucket.presign_get(key, "a.txt", "text/plain")
    other = storage.new_key()
    bucket.put_bytes(other, b"other", "text/plain")
    for changed in (url.replace(key, other),                            # another object
                    url.replace("exp=", "exp=9"),                        # a later expiry
                    url.replace("type=text/plain", "type=text/html"),    # another type
                    url.replace("attachment", "inline"),                 # shown instead of saved
                    url.split("&sig=")[0] + "&sig=" + "0" * 64):         # a made-up signature
        assert client.get(changed).status_code == 403, changed
    assert client.get(url).status_code == 200


def test_a_link_runs_out(app, client, monkeypatch):
    bucket = local(app)
    key = storage.new_key()
    bucket.put_bytes(key, b"x", "text/plain")
    with app.test_request_context():
        url = bucket.presign_get(key, "a.txt", "text/plain", ttl=60)
    assert client.get(url).status_code == 200
    real_time = time.time
    monkeypatch.setattr(storage.time, "time", lambda: real_time() + 61)
    assert client.get(url).status_code == 403


def test_a_put_must_be_the_promised_size_and_type(app, client):
    bucket = local(app)
    key = storage.new_key()
    with app.test_request_context():
        link = bucket.presign_put(key, "image/png", 4)
    assert put(client, link, b"12345").status_code == 400              # bigger than promised
    assert put(client, link, b"1234", content_type="text/html").status_code == 403  # another type
    assert bucket.head(key) is None
    assert put(client, link, b"1234").status_code == 200


def test_only_real_keys_are_accepted(app, client):
    bucket = local(app)
    for bad in ("../secret", "b/../../x", "b/short", "x/" + "a" * 32):
        with pytest.raises(ValueError):
            bucket.path(bad)
    assert client.get("/_bucket/b/../../instance/test.sqlite").status_code in (403, 404)


def test_non_ascii_names_download_with_their_name():
    header = storage.content_disposition("Résumé — 2026.pdf")
    assert 'filename="R_sum_ _ 2026.pdf"' in header
    assert "filename*=UTF-8''R%C3%A9sum%C3%A9%20%E2%80%94%202026.pdf" in header
    assert storage.content_disposition('evil"; x.html').count('"') == 2


@pytest.fixture
def s3():
    """A pretend S3 (moto) with the same calls B2 answers. moto only catches
    Amazon's own addresses, so the tests use one (never the internet)."""
    from moto import mock_aws
    with mock_aws():
        bucket = storage.S3Bucket("https://s3.us-east-1.amazonaws.com", "us-east-1", "j-drive", "testing", "testing")
        bucket.client.create_bucket(Bucket="j-drive")
        yield bucket


def test_s3_presigned_links_carry_a_signature_and_expiry(s3):
    link = s3.presign_put("b/" + "a" * 32, "image/png", 1234)
    parts = urlsplit(link["url"])
    query = parse_qs(parts.query)
    assert parts.netloc == "s3.us-east-1.amazonaws.com"
    assert parts.path == "/j-drive/b/" + "a" * 32  # path style: one address for the CSP
    assert query["X-Amz-Expires"] == ["600"] and "X-Amz-Signature" in query
    assert "content-length" in query["X-Amz-SignedHeaders"][0] and "content-type" in query["X-Amz-SignedHeaders"][0]
    assert link["headers"] == {"Content-Type": "image/png"}
    url = s3.presign_get("b/" + "a" * 32, "Photo.png", "image/png")
    query = parse_qs(urlsplit(url).query)
    assert query["X-Amz-Expires"] == ["60"]
    assert query["response-content-disposition"][0].startswith('attachment; filename="Photo.png"')
    assert s3.origin() == "https://s3.us-east-1.amazonaws.com"


def test_s3_head_put_copy_open_delete(s3):
    key, copy = storage.new_key(), storage.new_key()
    assert s3.head(key) is None
    s3.put_stream(key, io.BytesIO(b"hello there"), "text/plain", 11)
    assert s3.head(key) == {"size": 11, "content_type": "text/plain"}
    s3.copy(key, copy)
    assert s3.open(copy).read() == b"hello there"
    s3.delete(key)
    assert s3.head(key) is None


def test_the_live_site_needs_a_real_bucket(tmp_path, monkeypatch):
    from jdrive import create_app
    monkeypatch.setenv("RENDER", "true")
    with pytest.raises(RuntimeError, match="S3_BUCKET"):
        create_app({"SECRET_KEY": "x", "DATABASE_URL": "sqlite:///" + str(tmp_path / "x.sqlite")})


def test_the_csp_allows_the_bucket_address(tmp_path):
    from jdrive import create_app
    app = create_app({"TESTING": True, "SECRET_KEY": "x", "DATABASE": str(tmp_path / "x.sqlite"),
                      "S3_BUCKET": "j-drive", "S3_ENDPOINT": "https://s3.us-west-004.backblazeb2.com",
                      "S3_REGION": "us-west-004", "S3_KEY_ID": "k", "S3_SECRET": "s"})
    policy = app.test_client().get("/login").headers["Content-Security-Policy"]
    assert "connect-src 'self' https://s3.us-west-004.backblazeb2.com" in policy
    assert "script-src 'self';" in policy  # scripts still only from this site


def test_the_bucket_cors_command(s3, app, monkeypatch):
    from click.testing import CliRunner

    from jdrive.storage import bucket_cors_command
    app.extensions["jdrive_bucket"] = s3
    with app.app_context():
        result = CliRunner().invoke(bucket_cors_command, ["https://j-drive.onrender.com/"], obj=app)
    assert result.exit_code == 0, result.output
    rules = s3.client.get_bucket_cors(Bucket="j-drive")["CORSRules"]
    assert rules[0]["AllowedOrigins"] == ["https://j-drive.onrender.com"]
    assert "PUT" in rules[0]["AllowedMethods"]
