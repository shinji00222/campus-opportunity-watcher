import json
import os
import tempfile
import unittest
import urllib.error
from datetime import datetime, timedelta
from email.message import Message
from pathlib import Path
from unittest.mock import patch

from tests import ROOT  # noqa: F401
from campus_watcher import checker, net, state, updates
from campus_watcher.config import load_school


def release(tag="v9.0.0", url=f"https://github.com/{updates.REPO}/releases/tag/v9.0.0"):
    return json.dumps({"tag_name": tag, "html_url": url})


class VersionTests(unittest.TestCase):
    def test_parse_and_compare(self):
        self.assertEqual(updates.parse_version("v1.2"), (1, 2, 0))
        self.assertIsNone(updates.parse_version("v1.2.0-beta"))
        self.assertTrue(updates.is_newer("0.1.1", "0.1.0"))
        self.assertTrue(updates.is_newer("v0.10.0", "0.9.9"))
        self.assertFalse(updates.is_newer("0.1.0", "0.1.0"))
        self.assertFalse(updates.is_newer("garbage", "0.1.0"))

    def test_foreign_release_url_falls_back_to_releases_page(self):
        with patch.object(net, "fetch", return_value=release(url="https://evil.example.com/x.exe")):
            self.assertEqual(updates.fetch_latest_release()["url"], updates.RELEASES_PAGE)

    def test_repo_without_release_returns_none(self):
        not_found = urllib.error.HTTPError(updates.RELEASES_API, 404, "Not Found", Message(), None)
        with patch.object(net, "fetch", side_effect=not_found):
            self.assertIsNone(updates.fetch_latest_release())


class MaybeCheckTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        previous = state.APP_DIR
        state.use_app_dir(Path(temp.name))
        self.addCleanup(state.use_app_dir, previous)
        env = patch.dict(os.environ)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop(updates.DISABLE_ENV, None)

    def test_new_version_is_announced_once_and_checks_are_rate_limited(self):
        announced = []
        with patch.object(net, "fetch", return_value=release()) as fetch:
            self.assertEqual(updates.maybe_check_for_update(announced.append)["version"], "9.0.0")
            updates.maybe_check_for_update(announced.append)
        self.assertEqual(len(announced), 1)
        self.assertEqual(fetch.call_count, 1)

        # 过了检查间隔再查：同一版本不重复提醒，但仍算待更新。
        state.update_state(lambda s: s.update({"update_checked_at": (datetime.now() - timedelta(days=1)).strftime(updates.TIME_FORMAT)}))
        with patch.object(net, "fetch", return_value=release()) as fetch:
            self.assertIsNotNone(updates.maybe_check_for_update(announced.append))
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(len(announced), 1)

    def test_current_version_is_not_announced(self):
        announced = []
        with patch.object(net, "fetch", return_value=release(tag=f"v{updates.__version__}")):
            self.assertIsNone(updates.maybe_check_for_update(announced.append))
        self.assertEqual(announced, [])

    def test_network_failure_is_logged_and_retried_next_time(self):
        with patch.object(net, "fetch", side_effect=OSError("offline")), patch.object(state, "log") as log:
            self.assertIsNone(updates.maybe_check_for_update(lambda _r: None))
        self.assertIn("检查更新失败", log.call_args.args[0])
        self.assertTrue(updates.check_due(state.load_state()))

    def test_disabled_by_environment(self):
        os.environ[updates.DISABLE_ENV] = "1"
        with patch.object(net, "fetch") as fetch:
            updates.maybe_check_for_update(lambda _r: None)
        fetch.assert_not_called()

    def test_update_failure_never_breaks_a_check(self):
        school = load_school("gdut")
        with (
            patch.object(checker, "check_all", return_value=([], [])),
            patch.object(updates, "maybe_check_for_update", side_effect=RuntimeError("boom")),
            patch.object(state, "log"),
        ):
            self.assertEqual(checker.run_check(school), ([], [], True))

    def test_announcement_opens_release_page_from_toast(self):
        school = load_school("gdut")
        with (
            patch.object(net, "fetch", return_value=release()),
            patch.object(checker, "notify_windows") as notify,
        ):
            checker.check_for_update(school)
        self.assertIn("v9.0.0", notify.call_args.args[1])
        self.assertEqual(notify.call_args.args[3], f"https://github.com/{updates.REPO}/releases/tag/v9.0.0")


if __name__ == "__main__":
    unittest.main()
