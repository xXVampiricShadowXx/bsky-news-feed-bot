# OniNews Feed Bot

A local dashboard for connecting RSS or Atom news feeds to `oninews.bsky.social`. It posts each new story as:

```text
{full headline}

(Source: {publisher name}[, with {wire service}])

{article domain linking to the full article}
```

When a feed's byline shows the story was written by a wire service (for example PBS republishing "Jane Doe, Associated Press"), the post credits both: `(Source: PBS News, with Associated Press)`. Wire credit is read only from bylines, never from photo captions. Headlines are never shortened; a story that cannot fit with full credit is left unposted.

The post includes an article preview card with its image when available, falling back to the publisher's logo. Article blurbs can appear in the card description.

## Start the dashboard

1. Install Python 3.10 or newer.
2. In File Explorer, open this folder and run `start.ps1` in PowerShell. The first run creates a private Python environment, installs the dependencies, and creates `.env`.
3. Create a Bluesky app password in **Settings → Privacy and Security → App Passwords**. Do not use your regular account password.
4. Open `.env` in this folder and set `BLUESKY_APP_PASSWORD`. The handle is already set to `oninews.bsky.social`.
5. Restart `start.ps1`, open [http://127.0.0.1:5000](http://127.0.0.1:5000), and click **Check connection**.

Your Bluesky credentials stay in the local `.env` file and are not stored in the dashboard database. Keep `.env` private.

## Add a source and feed

1. In **Add a source profile**, enter the publisher's name and choose its logo image. The app accepts PNG, JPG, and WebP uploads and converts them to an optimized WebP with image metadata removed. Add meaningful alt text or let the app use `Publisher logo`.
2. In **Connect an RSS feed**, paste the feed URL and select that source profile. The app checks the feed and shows its latest headline.
3. Review the feed row and click **Enable**. The stories already in the feed become the starting point; they are not posted retroactively.
4. Click **Start auto-posting**. New feed items are polled about every 90 seconds and published to Bluesky. Feed polling continues even when posting or article previews are slow. Multiple new stories found in the same poll are spaced across the next polling interval.

Multiple feeds can use the same publisher profile and logo. Disable a feed to stop watching it. Remove a feed or source from the dashboard when you no longer want it.

## Safety controls and behavior

- The app listens on `127.0.0.1` only, so the dashboard is local to this computer.
- Automatic posting starts paused. You must test the Bluesky connection and turn it on in the dashboard.
- Stories discovered while auto-posting is paused are skipped. Resuming does not produce a backlog burst.
- On each launch, the first successful fetch of each enabled feed establishes a fresh baseline. Stories found while the bot was offline are not posted retroactively.
- A normalized article URL can only be posted once across all connected feeds, including common tracking-tag and fragment variants. This history stays in the local database even if a feed is removed. Stories published at different URLs are not assumed to be duplicates based only on similar headlines. DW and ABC Australia stories are matched by article id, since those sites change a story's URL slug when they retitle it.
- Each story uses a stable Bluesky record key, so a lost response or restart cannot create a duplicate post during a retry.
- Transient Bluesky errors retry with increasing delays, up to eight attempts. A story that exceeds Bluesky's post length limit is marked failed instead of silently shortening its headline.
- Feed errors and delivery results appear in the dashboard activity log.
- SQLite data and uploaded logos are stored under `instance/`. Back up this folder to preserve the setup. The `.env` file contains local credentials; neither it nor `instance/`, `logs/`, or `.vs/` belongs in Git.

## GitHub repository

The private GitHub repository backs up the tracked application source and provides change history. Changes made locally are not backed up there until they are committed and pushed. Keep the repository private unless you deliberately decide to publish the code, and never add `.env`, the database, logos, or logs to Git.

GitHub stores the source; it does not run the bot. The PowerShell window and dashboard process must remain running for automatic posting.

## Run the tests

After starting the bot once to create `.venv`, run `.venv\Scripts\python.exe -m unittest discover -s tests -v` in PowerShell. The tests use temporary databases and mocked feeds; they do not post to Bluesky.
GitHub Actions runs the same tests on Python 3.10 and 3.12 for pushes to `main` and pull requests.

## Example feed

PBS NewsHour publishes a headlines feed at:

```text
https://www.pbs.org/newshour/feeds/rss/headlines
```

Create a `PBS NewsHour` source profile and upload the logo image you want attached to those posts, then connect this URL to that profile.

## Local versus always-on hosting

This first version runs on your computer. It posts only while the PowerShell window and dashboard process are running. Hosting it on an always-on server can come later; keep the `.env` credentials private and keep the dashboard access restricted if you move it.
