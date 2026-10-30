import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import token_capture as tc

FAKE = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ4In0.c2lnbmF0dXJl"
FAKE2 = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ5In0.c2lnbmF0dXJl"


class FakeCookie:
    def __init__(self, name, domain, value):
        self._n, self._d, self._v = name, domain, value

    def name(self):
        return self._n.encode()

    def domain(self):
        return self._d

    def value(self):
        return self._v.encode()


class FakeClipboard:
    def __init__(self):
        self.t = ""

    def text(self):
        return self.t


class FindJwtTests(unittest.TestCase):
    def test_plain_and_wrapped(self):
        self.assertEqual(tc.find_jwt(FAKE), FAKE)
        self.assertEqual(tc.find_jwt(f'  "{FAKE}"\n'), FAKE)
        self.assertEqual(tc.find_jwt(f"a=1; JWT={FAKE}; b=2"), FAKE)

    def test_rejects(self):
        for s in ("", None, "hello", "eyJabc.def", "x" * 9000, "a.b.c"):
            self.assertIsNone(tc.find_jwt(s))


class CookieTests(unittest.TestCase):
    def test_filters_name_and_domain(self):
        cookies = [
            FakeCookie("JWT", "evil.com", FAKE2),
            FakeCookie("JWT", "notwarframe.market", FAKE2),
            FakeCookie("session", ".warframe.market", FAKE2),
            FakeCookie("JWT", ".warframe.market", FAKE),
        ]
        self.assertEqual(tc.pick_jwt_cookie(cookies), FAKE)

    def test_none_when_absent_or_bad_value(self):
        self.assertIsNone(tc.pick_jwt_cookie([FakeCookie("JWT", "evil.com", FAKE)]))
        self.assertIsNone(tc.pick_jwt_cookie([FakeCookie("JWT", "api.warframe.market", "garbage")]))
        self.assertEqual(tc.pick_jwt_cookie([FakeCookie("JWT", "api.warframe.market", FAKE)]), FAKE)


class WatcherTests(unittest.TestCase):
    def test_fires_once_per_new_token_and_stops(self):
        cb, seen = FakeClipboard(), []
        w = tc.ClipboardWatcher(cb, seen.append)
        cb.t = "nothing"
        self.assertIsNone(w.check())
        cb.t = FAKE
        w.check()
        w.check()
        cb.t = FAKE2
        w.check()
        self.assertEqual(seen, [FAKE, FAKE2])
        w.stop()
        cb.t = FAKE
        self.assertIsNone(w.check())
        self.assertEqual(len(seen), 2)


class LazyImportTests(unittest.TestCase):
    def test_import_does_not_load_webengine(self):
        self.assertNotIn("PySide6.QtWebEngineWidgets", sys.modules)

    def test_missing_webengine(self):
        with mock.patch.dict(sys.modules, {"PySide6.QtWebEngineCore": None,
                                           "PySide6.QtWebEngineWidgets": None}):
            self.assertIsNone(tc.load_webengine())
            self.assertIsNone(tc.TokenCaptureDialog())


class SaveTests(unittest.TestCase):
    def test_validate_and_save(self):
        with tempfile.TemporaryDirectory() as d:
            env = Path(d) / ".env"
            with mock.patch.object(tc.auth_manager, "validate_wfm_token", return_value=False):
                ok, msg = tc.validate_and_save(FAKE, env)
            self.assertFalse(ok)
            self.assertFalse(env.exists())
            self.assertNotIn(FAKE, msg)
            with mock.patch.object(tc.auth_manager, "validate_wfm_token", return_value=True), \
                    mock.patch.object(tc.auth_manager.config, "WFM_TOKEN", "", create=True), \
                    mock.patch.dict(os.environ, {}, clear=False):
                ok, msg = tc.validate_and_save(FAKE, env)
                os.environ.pop("WFM_TOKEN", None)
            self.assertTrue(ok)
            self.assertIn("WFM_TOKEN=" + FAKE, env.read_text())
        ok, _ = tc.validate_and_save("junk")
        self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main()
