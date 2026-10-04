import os
import runpy
import signal
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import db
from worker import BotWorker


APP_PATH = Path(__file__).resolve().parents[1] / "app.py"


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.original_paths = db.INSTANCE_DIR, db.LOGO_DIR, db.DB_PATH
        self.original_connection = db._conn
        self.addCleanup(self.restore_database)
        db.INSTANCE_DIR = Path(self.temp_dir.name)
        db.LOGO_DIR = db.INSTANCE_DIR / "logos"
        db.DB_PATH = db.INSTANCE_DIR / "test.sqlite3"
        db._conn = None

    def restore_database(self):
        if db._conn is not None:
            db._conn.close()
        db._conn = self.original_connection
        db.INSTANCE_DIR, db.LOGO_DIR, db.DB_PATH = self.original_paths

    def load_app(self, environment=None, run_name=None):
        with patch.dict(os.environ, environment or {}, clear=True), \
             patch("dotenv.load_dotenv"), \
             patch.object(BotWorker, "start"):
            return runpy.run_path(str(APP_PATH), run_name=run_name)

    def test_default_dashboard_remains_local_and_open(self):
        module = self.load_app()
        self.assertEqual("127.0.0.1", module["APP_HOST"])
        response = module["app"].test_client().get("/")
        self.assertEqual(200, response.status_code)
        self.assertIn(b"Connect the account", response.data)

    def test_network_binding_requires_password_or_explicit_override(self):
        for host in ("0.0.0.0", "::", "192.0.2.1"):
            for override in ("", "0", "true"):
                with self.subTest(host=host, override=override):
                    with self.assertRaises(SystemExit) as caught:
                        self.load_app({
                            "APP_HOST": host, "ALLOW_OPEN_DASHBOARD": override,
                        })
                    self.assertEqual(2, caught.exception.code)
        module = self.load_app({
            "APP_HOST": "0.0.0.0", "ALLOW_OPEN_DASHBOARD": "1",
        })
        self.assertEqual(200, module["app"].test_client().get("/").status_code)

    def test_password_protects_dashboard_and_forms_but_not_health(self):
        module = self.load_app({
            "APP_HOST": "0.0.0.0", "DASHBOARD_PASSWORD": "test-password-£",
            "ALLOW_OPEN_DASHBOARD": "1",
        })
        client = module["app"].test_client()
        for path in ("/", "/static/style.css", "/logos/missing.webp"):
            with self.subTest(path=path):
                response = client.get(path)
                self.assertEqual(401, response.status_code)
                self.assertIn("Basic", response.headers["WWW-Authenticate"])
        self.assertEqual(401, client.get(
            "/", auth=("any", "wrong-password")
        ).status_code)
        self.assertEqual(200, client.get(
            "/", auth=("any", "test-password-£")
        ).status_code)
        self.assertEqual(401, client.post("/settings/toggle-autopost").status_code)
        self.assertEqual(400, client.post(
            "/settings/toggle-autopost", auth=("any", "test-password-£")
        ).status_code)
        for ok in (True, False):
            state = {"ok": ok}
            with self.subTest(ok=ok), \
                 patch.object(module["worker"], "health", return_value=state):
                response = client.get("/health")
                self.assertEqual(200 if ok else 503, response.status_code)
                self.assertEqual(state, response.json)

    def test_main_honors_host_port_and_exits_cleanly_on_sigterm(self):
        with patch("flask.Flask.run") as run, \
             patch("signal.signal") as register_signal:
            self.load_app({
                "APP_HOST": "0.0.0.0", "APP_PORT": "5001",
                "ALLOW_OPEN_DASHBOARD": "1",
            }, run_name="__main__")
        run.assert_called_once_with(
            host="0.0.0.0", port=5001, debug=False,
            use_reloader=False, threaded=True,
        )
        self.assertEqual(signal.SIGTERM, register_signal.call_args.args[0])
        with self.assertRaises(SystemExit) as caught:
            register_signal.call_args.args[1](signal.SIGTERM, None)
        self.assertEqual(0, caught.exception.code)


if __name__ == "__main__":
    unittest.main()
