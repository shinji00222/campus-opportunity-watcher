import json
import tempfile
import threading
import unittest
from dataclasses import asdict
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from tests import ROOT  # noqa: F401
from campus_watcher import checker, net, state
from campus_watcher.config import load_school
from campus_watcher.models import Notice

TIME = "%Y-%m-%d %H:%M:%S"


class TempAppDir(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        previous = state.APP_DIR
        state.use_app_dir(Path(self._temp.name))
        self.addCleanup(state.use_app_dir, previous)
        env = patch.dict("os.environ", {"CAMPUS_WATCHER_NO_UPDATE_CHECK": "1"})
        env.start()
        self.addCleanup(env.stop)
        self.school = load_school("gdut")

    def save(self, data: dict) -> None:
        state.STATE_FILE.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def run_check(self, current, html="", errors=()):
        with (
            patch.object(checker, "check_all", return_value=(current, list(errors))),
            patch.object(net, "fetch", return_value=html) as fetch,
            patch.object(checker, "notify_windows") as notify,
        ):
            result = checker.run_check(self.school)
        return result, fetch, notify


class RunCheckTests(TempAppDir):
    def notice(self, notice_id="n1", title="2026年校内创新竞赛报名通知", **kwargs):
        return Notice(id=notice_id, title=title, url=f"https://example.edu.cn/{notice_id}", source="测试",
                      category="竞赛", **kwargs)

    def test_first_run_builds_baseline_then_only_new_items_notify(self):
        (new, _errors, first), _fetch, notify = self.run_check([self.notice("a")])
        self.assertTrue(first)
        self.assertEqual(new, [])
        notify.assert_not_called()
        (new, _errors, first), _fetch, notify = self.run_check([self.notice("a"), self.notice("b")])
        self.assertFalse(first)
        self.assertEqual([n.id for n in new], ["b"])
        notify.assert_called_once()

    def test_deadline_extension_on_seen_notice_notifies_once(self):
        old_deadline = date.today() + timedelta(days=3)
        new_deadline = date.today() + timedelta(days=10)
        stale_check = (datetime.now() - timedelta(days=8)).strftime(TIME)
        old = self.notice(detail_text=f"报名截止时间为{old_deadline:%Y年%m月%d日}，请按时提交。",
                          hard_conditions=["本科生"], detail_checked_at=stale_check)
        self.save({"initialized": True, "seen": [old.id], "notices": [asdict(old)], "favorites": [], "profile": {}})
        html = f"<p>报名截止时间调整为{new_deadline:%Y年%m月%d日}，请参赛队按新时间提交报名表和相关材料。</p>"
        (new, errors, _first), fetch, notify = self.run_check([self.notice()], html)
        self.assertEqual(errors, [])
        self.assertEqual([n.id for n in new], [old.id])
        notify.assert_called_once()
        fetch.assert_called_once()
        (new, _errors, _first), _fetch, notify = self.run_check([self.notice()], html)
        self.assertEqual(new, [])
        notify.assert_not_called()

    def test_first_detail_fetch_finding_a_deadline_does_not_renotify(self):
        seen = self.notice()
        self.save({"initialized": True, "seen": [seen.id], "notices": [asdict(seen)], "favorites": [], "profile": {}})
        deadline = date.today() + timedelta(days=10)
        html = f"<p>报名截止时间为{deadline:%Y年%m月%d日}，请各学院组织学生按时提交报名表和相关材料。</p>"
        (new, _errors, _first), fetch, notify = self.run_check([self.notice()], html)
        fetch.assert_called_once()
        self.assertEqual(new, [])
        notify.assert_not_called()

    def test_expired_notice_is_removed_with_favorite_and_not_refetched_daily(self):
        yesterday = date.today() - timedelta(days=1)
        expired = self.notice(detail_text=f"报名截止时间为{yesterday:%Y年%m月%d日}。", hard_conditions=["x"],
                              detail_checked_at=datetime.now().strftime(TIME))
        self.save({"initialized": True, "seen": [expired.id], "notices": [asdict(expired)],
                   "favorites": [expired.id], "profile": {}})
        self.run_check([self.notice()])
        saved = state.load_state()
        self.assertEqual(saved["notices"], [])
        self.assertEqual(saved["favorites"], [])
        self.assertIn(expired.id, saved["expired_checked_at"])
        (_new, _errors, _first), fetch, _notify = self.run_check([self.notice()])
        fetch.assert_not_called()

    def test_failed_detail_keeps_saved_content(self):
        old = self.notice(summary="旧摘要", detail_text="旧正文内容", hard_conditions=["旧条件"],
                          detail_checked_at=(datetime.now() - timedelta(days=8)).strftime(TIME))
        self.save({"initialized": True, "seen": [old.id], "notices": [asdict(old)], "favorites": [], "profile": {}})
        dynamic = '<div class="aContent"><section id="content1"></section></div>'
        with patch.object(state, "log"):
            self.run_check([self.notice()], dynamic)
        saved = state.load_state()["notices"][0]
        self.assertEqual((saved["summary"], saved["detail_text"]), ("旧摘要", "旧正文内容"))
        self.assertTrue(saved["detail_attempted_at"])

    def test_lifecycle_opening_notifies_and_detail_none_is_not_fetched(self):
        upcoming = self.notice("lq", "2026年蓝桥杯报名将于10月8日开始", lifecycle_stage="upcoming", detail="none")
        self.save({"initialized": True, "seen": ["lq"], "notices": [asdict(upcoming)], "favorites": [], "profile": {}})
        opened = self.notice("lq", "2026年蓝桥杯报名已开放", lifecycle_stage="open", detail="none")
        (new, _errors, _first), fetch, _notify = self.run_check([opened])
        self.assertEqual([n.id for n in new], ["lq"])
        fetch.assert_not_called()

    def test_profile_saved_during_check_is_kept(self):
        self.save({"initialized": True, "seen": [], "notices": [], "favorites": [], "profile": {}})

        def slow_check_all(_school):
            state.update_state(lambda s: s.update({"profile": {"major": "计算机"}, "favorites": ["x"]}))
            return [self.notice()], []

        with (
            patch.object(checker, "check_all", side_effect=slow_check_all),
            patch.object(net, "fetch", return_value=""),
            patch.object(checker, "notify_windows"),
            patch.object(state, "log"),
        ):
            checker.run_check(self.school)
        saved = state.load_state()
        self.assertEqual(saved["profile"], {"major": "计算机"})
        self.assertEqual(saved["favorites"], ["x"])

    def test_overlapping_check_is_skipped(self):
        with checker.check_mutex() as acquired:
            self.assertTrue(acquired)
            with patch.object(checker, "check_all") as check_all, patch.object(state, "log"):
                result = checker.run_check(self.school)
            check_all.assert_not_called()
        self.assertEqual(result, ([], [checker.CHECK_ALREADY_RUNNING], False))

    def test_detail_queue_prefers_untried_then_nearest_deadline(self):
        tried = (datetime.now() - timedelta(days=2)).strftime(TIME)
        far = self.notice("far", detail_text="报名截止时间为2099年1月1日。")
        near = self.notice("near", detail_text=f"报名截止时间为{date.today() + timedelta(days=2):%Y年%m月%d日}。")
        retry = self.notice("retry", detail_attempted_at=tried)
        selected = checker.select_detail_targets([retry, far, near])
        self.assertEqual([n.id for n in selected], ["near", "far", "retry"])


class StateFileTests(TempAppDir):
    def test_corrupt_state_is_preserved_and_reset(self):
        state.STATE_FILE.write_text("{not json", encoding="utf-8")
        with patch.object(state, "log"):
            self.assertEqual(state.load_state(), state.empty_state())
        self.assertEqual(len(list(state.APP_DIR.glob("state.corrupt-*.json"))), 1)

    def test_invalid_shape_is_preserved(self):
        self.save({"notices": "oops"})
        with patch.object(state, "log"):
            self.assertEqual(state.load_state(), state.empty_state())

    def test_concurrent_transactions_keep_both_updates(self):
        barrier = threading.Barrier(2)

        def update(key):
            barrier.wait()
            state.update_state(lambda s: s.setdefault("profile", {}).update({key: "1"}))

        threads = [threading.Thread(target=update, args=(key,)) for key in ("a", "b")]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(state.load_state()["profile"], {"a": "1", "b": "1"})


if __name__ == "__main__":
    unittest.main()
