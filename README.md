# Bsky News Feed Bot

A local dashboard for connecting RSS or Atom news feeds to `oninews.bsky.social`. It posts each new story as:

```text
{full headline}

(Source: {publisher name})

{article link}
```

The post attaches the publisher's logo. Feed blurbs and article photos are ignored.

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
4. Click **Start auto-posting**. New feed items are polled about every 90 seconds and published to Bluesky.

Multiple feeds can use the same publisher profile and logo. Disable a feed to stop watching it. Remove a feed or source from the dashboard when you no longer want it.

## Safety controls and behavior

- The app listens on `127.0.0.1` only, so the dashboard is local to this computer.
- Automatic posting starts paused. You must test the Bluesky connection and turn it on in the dashboard.
- Stories discovered while auto-posting is paused are skipped. Resuming does not produce a backlog burst.
- A normalized article URL can only be posted once across all connected feeds, including common tracking-tag and fragment variants. This history stays in the local database even if a feed is removed. Stories published at different URLs are not assumed to be duplicates based only on similar headlines.
- Transient Bluesky errors retry with increasing delays, up to eight attempts. A story that exceeds Bluesky's post length limit is marked failed instead of silently shortening its headline.
- Feed errors and delivery results appear in the dashboard activity log.
- SQLite data and uploaded logos are stored under `instance/`. Back up this folder to preserve the setup.

## Example feed

PBS NewsHour publishes a headlines feed at:

```text
https://www.pbs.org/newshour/feeds/rss/headlines
```

Create a `PBS NewsHour` source profile and upload the logo image you want attached to those posts, then connect this URL to that profile.

## Local versus always-on hosting

This first version runs on your computer. It posts only while the PowerShell window and dashboard process are running. Hosting it on an always-on server can come later; keep the `.env` credentials private and keep the dashboard access restricted if you move it.
