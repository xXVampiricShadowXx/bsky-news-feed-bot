# Security policy

## Reporting a vulnerability

Please **do not** open a public issue for security problems. Use GitHub's
[private vulnerability reporting](https://github.com/xXVampiricShadowXx/bsky-news-feed-bot/security/advisories/new)
instead. You'll get a reply as soon as possible, and fixes are released on `main`.

## How the bot protects your account

- It uses a Bluesky **app password**, never your main password. You can revoke it any time in
  Bluesky under Settings → Privacy and Security → App Passwords.
- `.env` (credentials) and `instance/` (database, logos, backups) are git-ignored. Never commit them.
- The dashboard listens on `127.0.0.1` only. It refuses to listen on a network address unless
  `DASHBOARD_PASSWORD` is set, and every form is CSRF-protected.
- Article previews and logos are fetched only from public internet addresses. Private and
  loopback addresses and redirects to them are blocked (SSRF protection), and downloaded images
  are re-encoded before upload.

If you think your app password has leaked, revoke it in Bluesky right away and create a new one.
