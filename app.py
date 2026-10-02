"""Local dashboard for the OniNews RSS-to-Bluesky bot."""

from __future__ import annotations

import hmac
import io
import os
import secrets
import sys
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit

if os.name == "nt":
    import msvcrt
else:
    import fcntl

from dotenv import load_dotenv
from flask import (
    Flask,
    abort,
    flash,
    redirect,
    render_template,
    request,
    send_from_directory,
    session,
    url_for,
)
from PIL import Image, ImageOps, UnidentifiedImageError

import db
from feeds import fetch_snapshot
from publisher import BlueskyPublisher
from worker import BotWorker


BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")
db.init_db()

_instance_lock_handle = None
ALREADY_RUNNING_EXIT_CODE = 10  # run_bot.ps1 stops instead of restarting on this code

def _ensure_single_instance() -> None:
    """Hold an OS-managed lock so concurrent starts cannot run duplicate workers."""
    global _instance_lock_handle
    lock_path = db.INSTANCE_DIR / "bot.pid"
    db.INSTANCE_DIR.mkdir(parents=True, exist_ok=True)
    lock_path.touch(exist_ok=True)
    _instance_lock_handle = lock_path.open("r+b")
    _instance_lock_handle.seek(0, os.SEEK_END)
    if _instance_lock_handle.tell() == 0:
        _instance_lock_handle.write(b"\0")
        _instance_lock_handle.flush()
    _instance_lock_handle.seek(0)
    try:
        if os.name == "nt":
            msvcrt.locking(_instance_lock_handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            fcntl.flock(_instance_lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        _instance_lock_handle.close()
        _instance_lock_handle = None
        print("Another copy of this bot is already running. Close that window first.", file=sys.stderr)
        sys.exit(ALREADY_RUNNING_EXIT_CODE)
    _instance_lock_handle.seek(0)
    _instance_lock_handle.write(str(os.getpid()).encode("ascii").ljust(16, b" "))
    _instance_lock_handle.flush()


_ensure_single_instance()


def _ensure_dashboard_secret() -> str:
    """Reuse DASHBOARD_SECRET from .env, or create and save one on first run.

    Without this, a new random secret_key was generated on every restart, which
    silently invalidated the session/CSRF token behind any dashboard tab that was
    still open from before the restart (its next form submit would fail with
    "Form expired. Reload the page and try again.").
    """
    existing = os.getenv("DASHBOARD_SECRET")
    if existing:
        return existing
    new_secret = secrets.token_urlsafe(32)
    env_path = BASE_DIR / ".env"
    try:
        with env_path.open("a", encoding="utf-8") as handle:
            handle.write(f"\nDASHBOARD_SECRET={new_secret}\n")
    except OSError:
        pass  # Still usable for this run; just won't survive a restart.
    os.environ["DASHBOARD_SECRET"] = new_secret
    return new_secret


app = Flask(__name__)
app.secret_key = _ensure_dashboard_secret()
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
publisher = BlueskyPublisher()
worker = BotWorker()
worker.start()


def csrf_value() -> str:
    token = session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["csrf_token"] = token
    return token


@app.before_request
def protect_local_forms() -> None:
    if request.method == "POST":
        expected = session.get("csrf_token", "")
        supplied = request.form.get("csrf_token", "")
        if not expected or not hmac.compare_digest(expected, supplied):
            abort(400, "Form expired. Reload the page and try again.")


@app.context_processor
def inject_template_helpers() -> dict:
    return {"csrf_token": csrf_value}


def _store_logo(file_storage, source_name: str) -> tuple[str, str]:
    if not file_storage or not file_storage.filename:
        raise ValueError("Choose a logo image for this source.")
    raw = file_storage.read()
    if not raw:
        raise ValueError("The selected logo file is empty.")
    if len(raw) > 10 * 1024 * 1024:
        raise ValueError("Logo files must be 10 MB or smaller.")

    try:
        with Image.open(io.BytesIO(raw)) as opened:
            if opened.format not in {"PNG", "JPEG", "WEBP"}:
                raise ValueError("Use a PNG, JPG, or WebP logo file.")
            image = ImageOps.exif_transpose(opened).copy()
    except UnidentifiedImageError as exc:
        raise ValueError("That file is not a supported image. Use PNG, JPG, or WebP.") from exc

    image.thumbnail((1600, 1600), Image.Resampling.LANCZOS)
    if "A" not in image.getbands():
        image = image.convert("RGB")
    else:
        image = image.convert("RGBA")

    # Re-encoding strips camera metadata. Keep the result below Bluesky's current 2 MB image cap.
    output = io.BytesIO()
    for quality in (94, 88, 82, 76, 68):
        output.seek(0)
        output.truncate(0)
        image.save(output, format="WEBP", quality=quality, method=6)
        if output.tell() <= 1_900_000:
            break
    if output.tell() > 1_900_000:
        image.thumbnail((1000, 1000), Image.Resampling.LANCZOS)
        output.seek(0)
        output.truncate(0)
        image.save(output, format="WEBP", quality=70, method=6)
    if output.tell() > 1_900_000:
        raise ValueError("The logo could not be reduced below the Bluesky image limit.")

    filename = f"source-{uuid.uuid4().hex}.webp"
    target = db.LOGO_DIR / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(output.getvalue())
    return filename, f"{source_name} logo"


@app.get("/")
def index():
    return render_template(
        "index.html",
        sources=db.list_sources(),
        feeds=db.list_feeds(),
        events=db.list_events(),
        recent_posts=db.recent_posts(),
        counts=db.counts(),
        bluesky_handle=publisher.handle,
        bluesky_configured=publisher.configured,
        autopost_enabled=db.setting("autopost_enabled", "0") == "1",
    )


@app.post("/sources/add")
def add_source():
    name = " ".join(request.form.get("name", "").split())
    logo_alt = " ".join(request.form.get("logo_alt", "").split())
    if not name or len(name) > 80:
        flash("Enter a source name up to 80 characters long.", "error")
        return redirect(url_for("index"))
    if not logo_alt:
        logo_alt = f"{name} logo"
    if len(logo_alt) > 300:
        flash("Logo alt text must be 300 characters or shorter.", "error")
        return redirect(url_for("index"))

    filename = None
    try:
        filename, default_alt = _store_logo(request.files.get("logo"), name)
        db.add_source(name, filename, logo_alt or default_alt)
        db.add_event("info", f"Added source profile: {name}.")
        flash(f"Added {name}. You can now connect one or more feeds to it.", "success")
    except (ValueError, OSError) as exc:
        if filename:
            (db.LOGO_DIR / filename).unlink(missing_ok=True)
        flash(str(exc), "error")
    return redirect(url_for("index"))


@app.post("/sources/<int:source_id>/delete")
def remove_source(source_id: int):
    source = db.get_source(source_id)
    filename = db.delete_source(source_id)
    if filename:
        (db.LOGO_DIR / filename).unlink(missing_ok=True)
        db.add_event("info", f"Removed source profile: {source['name'] if source else source_id}.")
        flash("Source and its connected feeds were removed.", "success")
    else:
        flash("Source not found.", "error")
    return redirect(url_for("index"))


@app.post("/feeds/add")
def add_feed():
    source_id_raw = request.form.get("source_id", "")
    feed_url = request.form.get("url", "").strip()
    try:
        source_id = int(source_id_raw)
    except ValueError:
        flash("Choose a source profile first.", "error")
        return redirect(url_for("index"))

    source = db.get_source(source_id)
    if not source:
        flash("That source profile no longer exists.", "error")
        return redirect(url_for("index"))

    parsed_url = urlsplit(feed_url)
    if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
        flash("Enter a complete RSS or Atom URL starting with http:// or https://.", "error")
        return redirect(url_for("index"))

    try:
        snapshot = fetch_snapshot(feed_url)
        if not snapshot.entries:
            raise ValueError("The feed is valid but has no entries with both a headline and article link.")
        feed_id = db.add_feed(source_id, feed_url)
        dated_entries = [entry for entry in snapshot.entries if entry.get("published_at")]
        latest = max(dated_entries, key=lambda entry: entry["published_at"]) if dated_entries else snapshot.entries[0]
        db.add_event("info", f"Added a feed for {source['name']}.")
        flash(
            f"Feed saved for {source['name']}. Latest headline: {latest['headline']}. "
            "It is still paused; enable it after checking the preview.",
            "success",
        )
        db.update_feed_fetch(
            feed_id,
            etag=snapshot.etag,
            last_modified=snapshot.last_modified,
        )
    except ValueError as exc:
        flash(str(exc), "error")
    except Exception as exc:
        flash(f"Could not add feed: {exc}", "error")
    return redirect(url_for("index"))


@app.post("/feeds/<int:feed_id>/toggle")
def toggle_feed(feed_id: int):
    feed = db.get_feed(feed_id)
    if not feed:
        flash("Feed not found.", "error")
    elif feed["enabled"]:
        db.disable_feed(feed_id)
        db.add_event("info", f"Paused the {feed['source_name']} feed.")
        flash(f"Paused {feed['source_name']} feed.", "success")
    else:
        try:
            worker.activate_feed(feed_id)
            flash(
                f"Enabled {feed['source_name']}. Existing stories were skipped; new stories will be picked up automatically.",
                "success",
            )
        except Exception as exc:
            flash(f"Could not enable feed: {exc}", "error")
    return redirect(url_for("index"))


@app.post("/feeds/<int:feed_id>/delete")
def remove_feed(feed_id: int):
    feed = db.get_feed(feed_id)
    db.delete_feed(feed_id)
    if feed:
        db.add_event("info", f"Removed the {feed['source_name']} feed.")
        flash("Feed removed.", "success")
    else:
        flash("Feed not found.", "error")
    return redirect(url_for("index"))


@app.post("/settings/test-bluesky")
def test_bluesky():
    try:
        connected_as = publisher.check_connection()
        flash(f"Bluesky connection works for {connected_as}.", "success")
    except Exception as exc:
        flash(f"Bluesky connection failed: {exc}", "error")
    return redirect(url_for("index"))


@app.post("/settings/toggle-autopost")
def toggle_autopost():
    enabled = db.setting("autopost_enabled", "0") == "1"
    if enabled:
        db.set_setting("autopost_enabled", "0")
        db.pause_pending_items()
        db.add_event("info", "Automatic posting was paused.")
        flash("Automatic posting is paused. Stories found while paused will be skipped.", "success")
    else:
        try:
            connected_as = publisher.check_connection()
            db.set_setting("autopost_enabled", "1")
            db.add_event("info", f"Automatic posting started for {connected_as}.")
            flash(f"Automatic posting is on for {connected_as}.", "success")
        except Exception as exc:
            flash(
                f"Could not start automatic posting: {exc} Check the local .env file and restart the app.",
                "error",
            )
    return redirect(url_for("index"))


@app.get("/logos/<path:filename>")
def logo_file(filename: str):
    return send_from_directory(db.LOGO_DIR, filename, max_age=3600)


@app.get("/health")
def health():
    state = worker.health()
    feeds = db.list_feeds()
    state["feeds"] = {
        "enabled": sum(1 for f in feeds if f["enabled"]),
        "erroring": [f["source_name"] for f in feeds if f["enabled"] and f["last_error"]],
    }
    state["autopost_enabled"] = db.setting("autopost_enabled", "0") == "1"
    return state, (200 if state["ok"] else 503)


def _self_watchdog() -> None:
    """Exit if a worker loop dies or hangs, so the supervisor (run_bot.ps1) restarts
    the bot cleanly instead of leaving a dashboard that silently stopped posting."""
    while True:
        time.sleep(60)
        state = worker.health()
        if not state["ok"]:
            try:
                db.add_event("error", f"Worker unhealthy, restarting: {state}")
            finally:
                print(f"Worker unhealthy, exiting for restart: {state}", flush=True)
                os._exit(3)


@app.errorhandler(413)
def too_large(_error):
    flash("Upload is too large. Logo files must be 10 MB or smaller.", "error")
    return redirect(url_for("index"))


if __name__ == "__main__":
    port = int(os.getenv("APP_PORT", "5000"))
    host = "127.0.0.1"
    threading.Thread(target=_self_watchdog, name="self-watchdog", daemon=True).start()
    print(f"OniNews feed bot dashboard: http://{host}:{port}", flush=True)
    try:
        from waitress import serve
    except ImportError:
        app.run(host=host, port=port, debug=False, use_reloader=False, threaded=True)
    else:
        serve(app, host=host, port=port, threads=8, ident="OniNews")
