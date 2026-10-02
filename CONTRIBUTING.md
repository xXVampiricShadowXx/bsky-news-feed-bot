# Contributing

Thanks for helping! Bug reports, new public-media feeds, and code improvements are all welcome.

## Suggesting a news source

The starter pack (`starter_sources.json`) only includes **public-service, non-profit, or
trust-owned newsrooms** that publish a free English-language world-news RSS/Atom feed. Open a
"Suggest a source" issue with the feed URL and a link showing who owns the outlet.

## Development

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate    macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
python -m unittest discover -s tests -v
```

- Tests must not touch the network or your real database. See `tests/test_starter.py` for the
  temporary-database pattern and inject fakes for network calls.
- Keep credit correct: every post must name the publisher, and any wire service such as AP,
  Reuters or AFP that the feed's byline mentions. Changes to `publisher.attribution_line` or
  `feeds.wire_credit` need tests.
- Keep pull requests focused. CI runs the tests on Linux, Windows and macOS, and builds and
  smoke-tests the Docker image.
