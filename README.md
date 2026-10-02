# Bluesky News Feed Bot

[![Tests](https://github.com/xXVampiricShadowXx/bsky-news-feed-bot/actions/workflows/tests.yml/badge.svg)](https://github.com/xXVampiricShadowXx/bsky-news-feed-bot/actions/workflows/tests.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

Automatically post world news from **public-service and non-profit newsrooms** to your Bluesky
account, with **correct credit on every post**. It runs on your own computer (Windows, macOS,
Linux or Docker) and is managed from a simple local dashboard.

See it running as ONI News at [@oninews.bsky.social](https://bsky.app/profile/oninews.bsky.social).

```text
EU agrees new sanctions package against Russia

(Source: DW News, with Reuters)

dw.com/en/eu-agrees-new-sanctions…   ← link card with the article preview
```

## Why this bot

- **Credit first.** Every post names the publisher and links to the original article. When a
  feed's byline shows the story came from a wire service (AP, Reuters, AFP…), the post credits
  both: `(Source: PBS News, with Associated Press)`. Headlines are never cut short; a story that
  can't fit with its full credit is not posted.
- **One-click starter pack.** 13 public-media and non-profit newsrooms: PBS, NPR, BBC, The
  Guardian, CBC, ABC (Australia), SBS, RNZ, RTÉ, DW, France 24, RFI and UN News. Each
  publisher's own icon is fetched automatically.
- **No spam, no repeats.** The bot never posts a backlog. The same article is posted only once,
  even across feeds, and the same story told by a second outlet within 18 hours is skipped.
- **Focused.** A built-in, transparent *geopolitics only* filter (switch it off for all news).
  Sport is always skipped.
- **Set and forget.** It restarts itself, has a health check and a watchdog, backs up its
  database daily, and can start automatically at sign-in or run as a Docker service.
- **Safe.** It uses a Bluesky *app password* (never your real password), the dashboard is local
  only by default, and it has CSRF and SSRF protection. See [SECURITY.md](SECURITY.md).

## Quick start

You need a Bluesky account and an **app password**: in Bluesky, open
**Settings → Privacy and Security → App Passwords → Add App Password**.

### Windows

1. Install [Python 3.10+](https://www.python.org/downloads/) (tick *Add python.exe to PATH*).
2. [Download this repo as a ZIP](https://github.com/xXVampiricShadowXx/bsky-news-feed-bot/archive/refs/heads/main.zip)
   and extract it, or `git clone` it.
3. In the folder, right-click `start.ps1` → **Run with PowerShell**. If Windows blocks it, run
   `powershell -ExecutionPolicy Bypass -File .\start.ps1`. The first run creates `.env`.
4. Open `.env` in Notepad, fill in `BLUESKY_HANDLE` and `BLUESKY_APP_PASSWORD`, save, and run
   `start.ps1` again.

### macOS / Linux

```bash
git clone https://github.com/xXVampiricShadowXx/bsky-news-feed-bot.git
cd bsky-news-feed-bot
./start.sh        # first run creates .env - edit it, then run ./start.sh again
```

### Docker

```bash
git clone https://github.com/xXVampiricShadowXx/bsky-news-feed-bot.git
cd bsky-news-feed-bot
cp .env.example .env      # fill in BLUESKY_HANDLE and BLUESKY_APP_PASSWORD
docker compose up -d --build
```

Data (database, logos, backups) lives in the `bot-data` volume. The container restarts
automatically and reports its health to Docker.

### Then, in the dashboard

Open **http://127.0.0.1:5000** and:

1. Click **Check connection**.
2. Click **Add starter sources**, or add your own feeds (see below).
3. Click **Start auto-posting**.

Posting starts paused and only covers stories published *after* this point. Nothing old is
posted.

## Settings (`.env`)

| Setting | Default | What it does |
| --- | --- | --- |
| `BLUESKY_HANDLE` | none | Your handle, e.g. `yournews.bsky.social` |
| `BLUESKY_APP_PASSWORD` | none | A Bluesky app password (not your main password) |
| `BOT_NAME` | `News Feed Bot` | Name shown on the dashboard |
| `APP_PORT` | `5000` | Dashboard port |
| `APP_HOST` | `127.0.0.1` | Set `0.0.0.0` to reach the dashboard from other devices (requires `DASHBOARD_PASSWORD`) |
| `DASHBOARD_PASSWORD` | none | Turns on a login prompt for the dashboard (any username + this password) |
| `BOT_DATA_DIR` | `./instance` | Where the database, logos and backups are stored |

Restart the bot after editing `.env`.

## Adding your own sources

1. **Add a source profile**: the publisher name exactly as it should appear in credits, plus
   its logo (PNG/JPG/WebP; re-encoded with metadata removed).
2. **Connect an RSS feed**: paste the RSS or Atom URL and pick the source. The app checks it and
   shows the latest headline.
3. Click **Enable**. Stories already in the feed become the starting point.

Several feeds can share one source profile. Want a source in the starter pack? Open a
[source suggestion](https://github.com/xXVampiricShadowXx/bsky-news-feed-bot/issues/new?template=source_suggestion.yml).

## How posting works

- Feeds are polled about every 90 seconds. Several new stories from one poll are spaced out
  instead of posted in a burst.
- Each post links to the article with a preview card (the article image, or the publisher's logo
  when there is none).
- **Duplicates.** URLs are normalised (tracking tags and fragments removed), and DW, ABC and RTÉ
  stories are matched by article id because those sites change the URL when retitling. A story
  is also skipped as a `duplicate` when another headline from the last 18 hours shares at least
  4 meaningful words and half its wording. The database notes which headline it matched.
- **Reliability.** Each story gets a stable Bluesky record key, so retries can never double-post.
  Temporary errors retry with backoff, up to 8 attempts. Stories found while posting is paused
  are skipped rather than queued, and so are stories found while the bot was offline.
- **Geopolitics filter.** `topics.py` scores the headline, summary and feed categories against
  transparent keyword lists: conflict, diplomacy, sanctions, international bodies, elections,
  leaders, and relations between countries. Skipped stories are kept with status `filtered` and a
  reason, so tuning is easy. Toggle it with **Geopolitics only** on the dashboard.

## Keeping it running

- `start.ps1`/`start.sh` run the bot under a supervisor that restarts it if it crashes or stalls
  (backoff from 5 seconds to 5 minutes). Logs go to `logs/bot-YYYY-MM-DD.log` and are kept for
  14 days.
- **Windows autostart** (runs hidden at sign-in):
  `powershell -ExecutionPolicy Bypass -File .\install_autostart.ps1`, then
  `Start-ScheduledTask -TaskName 'Bluesky News Feed Bot'`. Remove it with `-Uninstall`.
  Running several bots? Pass `-TaskName` and use a different `APP_PORT` for each.
- **Health:** `http://127.0.0.1:5000/health` returns JSON with HTTP 200 when the poller and
  publisher are alive, and 503 otherwise. An internal watchdog exits on a stall so the
  supervisor (or Docker) restarts the bot.
- **Backups:** the database is copied daily to `instance/backups` (or `/data/backups` in Docker),
  keeping the 7 most recent copies.

The bot posts only while the computer is on. For 24/7 posting, run the Docker setup on an
always-on machine or small server.

## Development

```bash
python -m unittest discover -s tests -v
```

The tests use temporary databases and fake network calls, and never post to Bluesky. CI runs them
on Linux, Windows and macOS, and builds and smoke-tests the Docker image. See
[CONTRIBUTING.md](CONTRIBUTING.md).

## Credits and disclaimer

This project is not affiliated with Bluesky or any of the publishers listed. Headlines, articles
and logos belong to their publishers. The bot shares headlines with a link and clear credit, as
RSS feeds are intended for, and every reader is sent to the original article. Publisher icons are
downloaded from each publisher's own website on your machine; none are included in this
repository.

Released under the [MIT License](LICENSE).
