import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

import config
import db
import logos
import starter


def _png(size=(200, 200)) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", size, (10, 120, 200)).save(out, format="PNG")
    return out.getvalue()


class TempDbTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_paths = db.INSTANCE_DIR, db.LOGO_DIR, db.DB_PATH
        db.INSTANCE_DIR = Path(self.temp_dir.name)
        db.LOGO_DIR = db.INSTANCE_DIR / "logos"
        db.DB_PATH = db.INSTANCE_DIR / "test.sqlite3"
        db._conn = None
        db.init_db()

    def tearDown(self):
        if db._conn is not None:
            db._conn.close()
            db._conn = None
        db.INSTANCE_DIR, db.LOGO_DIR, db.DB_PATH = self.original_paths
        self.temp_dir.cleanup()


class LogoTests(TempDbTestCase):
    def test_store_logo_bytes_writes_webp(self):
        filename, alt = logos.store_logo_bytes(_png(), "RNZ")
        self.assertTrue(filename.endswith(".webp"))
        self.assertEqual(alt, "RNZ logo")
        with Image.open(db.LOGO_DIR / filename) as saved:
            self.assertEqual(saved.format, "WEBP")

    def test_store_logo_rejects_non_images(self):
        with self.assertRaises(ValueError):
            logos.store_logo_bytes(b"not an image", "X")
        with self.assertRaises(ValueError):
            logos.store_logo_bytes(b"", "X")

    def test_generated_logo_is_valid_png(self):
        with Image.open(io.BytesIO(logos.generated_logo("RTÉ News"))) as image:
            self.assertEqual((image.format, image.size), ("PNG", (400, 400)))

    def test_icon_candidates_prefer_large_apple_touch_icons(self):
        page = """
        <link rel="icon" href="/favicon-16.png" sizes="16x16">
        <link rel="icon" href="/logo.svg">
        <link rel="apple-touch-icon" sizes="180x180" href="/touch-180.png">
        <link rel="apple-touch-icon" sizes="512x512" href="https://cdn.example.org/touch-512.png?v=1&amp;x=2">
        <link rel="stylesheet" href="/site.css">
        """
        urls = logos._icon_candidates(page, "https://news.example.org/world/")
        self.assertEqual(urls[0], "https://cdn.example.org/touch-512.png?v=1&x=2")
        self.assertEqual(urls[1], "https://news.example.org/touch-180.png")
        self.assertNotIn("https://news.example.org/logo.svg", urls)
        self.assertNotIn("https://news.example.org/site.css", urls)
        self.assertEqual(urls[-1], "https://news.example.org/apple-touch-icon.png")


class StarterTests(TempDbTestCase):
    def test_bundled_list_is_well_formed(self):
        entries = starter.load_starter_sources()
        self.assertGreaterEqual(len(entries), 10)
        names = [entry["name"] for entry in entries]
        self.assertEqual(len(names), len(set(names)))
        for entry in entries:
            self.assertTrue(entry["feed"].startswith("https://"), entry)
            self.assertTrue(entry["website"].startswith("https://"), entry)

    def test_import_adds_activates_and_skips_existing(self):
        entries = [
            {"name": "Alpha News", "website": "https://alpha.example", "feed": "https://alpha.example/rss"},
            {"name": "Beta Radio", "website": "https://beta.example", "feed": "https://beta.example/rss"},
        ]
        activated = []
        icons = {"https://alpha.example": _png(), "https://beta.example": None}

        result = starter.import_starter_sources(activated.append, entries, discover=icons.get)
        self.assertEqual(result.added, ["Alpha News", "Beta Radio"])
        self.assertEqual(result.generated_logos, ["Beta Radio"])
        self.assertEqual(len(activated), 2)
        self.assertEqual(len(db.list_sources()), 2)

        again = starter.import_starter_sources(activated.append, entries, discover=icons.get)
        self.assertEqual(again.added, [])
        self.assertEqual(again.skipped, ["Alpha News", "Beta Radio"])
        self.assertEqual(len(activated), 2)

    def test_failed_activation_rolls_back_source_and_logo(self):
        entries = [{"name": "Broken", "website": "", "feed": "https://broken.example/rss"}]

        def fail(_feed_id):
            raise ValueError("feed unreachable")

        result = starter.import_starter_sources(fail, entries, discover=lambda _url: None)
        self.assertEqual(result.added, [])
        self.assertIn("feed unreachable", result.failed[0])
        self.assertEqual(db.list_sources(), [])
        self.assertEqual(db.list_feeds(), [])
        self.assertEqual(list(db.LOGO_DIR.glob("*.webp")), [])


class ConfigTests(unittest.TestCase):
    def test_bot_name_defaults_and_initials(self):
        with patch.dict("os.environ", {"BOT_NAME": ""}):
            self.assertEqual(config.bot_name(), config.DEFAULT_BOT_NAME)
        with patch.dict("os.environ", {"BOT_NAME": "OniNews"}):
            self.assertEqual(config.bot_initials(), "ON")
        with patch.dict("os.environ", {"BOT_NAME": "World Desk"}):
            self.assertEqual(config.bot_initials(), "WD")

    def test_user_agent_identifies_project(self):
        self.assertIn(config.REPO_URL, config.USER_AGENT)


if __name__ == "__main__":
    unittest.main()
