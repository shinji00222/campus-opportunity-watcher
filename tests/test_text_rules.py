import unittest
from datetime import date, datetime, timedelta

from tests import ROOT  # noqa: F401  (adds src to sys.path)
from campus_watcher import text
from campus_watcher.rules import Rules, is_stale_abroad, is_stale_competition, is_stale_internship
from campus_watcher.sources import extract_link_notices, parse_lanqiao


class DeadlineTests(unittest.TestCase):
    def deadline(self, notice, today=date(2026, 9, 8)):
        return text.extract_registration_deadline(notice, today)

    def test_registration_window_uses_end_date(self):
        self.assertEqual(self.deadline({"detail_text": "报名时间：2026年9月1日至2026年9月10日。"}), date(2026, 9, 10))

    def test_same_month_range_uses_end_day(self):
        self.assertEqual(self.deadline({"detail_text": "报名时间：2026年9月1日至10日。"}), date(2026, 9, 10))

    def test_full_year_range_across_months(self):
        self.assertEqual(self.deadline({"detail_text": "报名时间：2026年9月1日至2026年10月10日。"}), date(2026, 10, 10))

    def test_yearless_range_uses_notice_year(self):
        notice = {"date": "2025-08-20", "detail_text": "报名时间：9月1日至10日。"}
        self.assertEqual(self.deadline(notice, date(2026, 8, 25)), date(2025, 9, 10))

    def test_cross_year_month_range_rolls_into_next_year(self):
        notice = {"date": "2026-11-20", "detail_text": "报名时间：12月20日至1月10日。"}
        self.assertEqual(self.deadline(notice, date(2026, 11, 20)), date(2027, 1, 10))

    def test_explicit_deadline_after_date(self):
        self.assertEqual(self.deadline({"detail_text": "请于2026年9月10日前提交报名表。"}), date(2026, 9, 10))

    def test_adjusted_deadline(self):
        self.assertEqual(self.deadline({"detail_text": "报名截止时间调整为2026年9月20日，请按时提交。"}), date(2026, 9, 20))

    def test_publish_date_near_registration_word_is_not_a_deadline(self):
        notice = {"title": "2026年9月1日 关于报名参加人工智能竞赛的通知", "date": "2026-09-01"}
        self.assertIsNone(self.deadline(notice))


class DetailExtractionTests(unittest.TestCase):
    def test_dynamic_placeholder_is_not_treated_as_content(self):
        html = '<div class="aContent"><section id="content123"></section></div><p>版权所有 某某大学 联系电话 12345678 地址某某路</p>'
        _summary, detail_text, conditions = text.extract_detail_data(html, "标题")
        self.assertEqual(detail_text, "")
        self.assertEqual(conditions, [])

    def test_condition_sentences_are_captured(self):
        html = ("<p>本次大赛面向全校学生，旨在提升创新能力，欢迎各学院积极组织学生报名参加。</p>"
                "<p>参赛对象：全日制在读本科生，以团队形式报名，每队不超过五人，需有一名指导老师。</p>")
        _summary, detail_text, conditions = text.extract_detail_data(html, "标题")
        self.assertIn("参赛对象", detail_text)
        self.assertTrue(any("参赛对象" in item for item in conditions))

    def test_english_eligibility_is_captured(self):
        html = "<p>Qualifications: applicants must be enrolled in a graduate or final-year bachelor programme.</p>"
        _summary, _detail, conditions = text.extract_detail_data(html, "Intern")
        self.assertTrue(any("enrolled" in item for item in conditions))

    def test_short_ncss_job_body_is_kept(self):
        html = '<pre class="mainContent">负责公司文件的校对工作</pre>'
        summary, detail_text, _conditions = text.extract_ncss_job_detail(html, "校对实习生")
        self.assertEqual(detail_text, "负责公司文件的校对工作")
        self.assertEqual(summary, "负责公司文件的校对工作")
        with self.assertRaises(ValueError):
            text.extract_ncss_job_detail('<pre class="mainContent"> <br> </pre>', "空岗位")


class RulesTests(unittest.TestCase):
    def test_extra_keywords_are_merged_with_defaults(self):
        rules = Rules.with_extras({"竞赛": ["某某杯"]}, ["内部培训"])
        self.assertEqual(rules.classify("2026年某某杯报名通知"), "竞赛")
        self.assertEqual(rules.classify("2026年数学建模校赛"), "竞赛")
        self.assertTrue(rules.is_ignored("内部培训竞赛通知"))

    def test_internship_wins_over_competition_and_english_intern(self):
        rules = Rules()
        self.assertEqual(rules.classify("创新创业实习生招募"), "实习")
        self.assertEqual(rules.classify("UNICEF Communication Intern"), "实习")
        self.assertIsNone(rules.classify("研究生联合培养基地介绍"))

    def test_source_default_needs_some_keyword(self):
        rules = Rules()
        self.assertIsNone(rules.classify("学院新闻：迎新晚会顺利举行", "竞赛"))

    def test_stale_rules(self):
        today = date(2026, 10, 13)
        self.assertTrue(is_stale_competition({"category": "竞赛", "title": "2025年大学生竞赛"}, today))
        self.assertFalse(is_stale_competition({"category": "竞赛", "title": "2025—2026学年竞赛"}, today))
        self.assertTrue(is_stale_competition({"category": "竞赛", "title": "2026年数学竞赛获奖名单"}, today))
        report = "“经”彩无限——经济学院2026年学生运动会暨校运会选拔赛成功举办"
        self.assertTrue(is_stale_competition({"category": "竞赛", "title": report}, today))
        self.assertTrue(is_stale_abroad({"category": "出国", "title": "2026年暑期访学项目"}, today))
        self.assertFalse(is_stale_abroad({"category": "出国", "title": "2027春季访学项目"}, today))
        self.assertTrue(is_stale_internship({"category": "实习", "title": "实习", "date": "2026-08-01"}, today))
        self.assertFalse(is_stale_internship({"category": "实习", "title": "实习", "date": "2026-09-01"}, today))


class LinkExtractionTests(unittest.TestCase):
    def test_links_are_classified_filtered_and_resolved(self):
        html = """
        <a href="/old">2019年广东省大学生竞赛</a>
        <a href="info/1.htm#top">2026年大学生数学竞赛报名通知</a>
        <a href="/nav">创新创业教育</a>
        <a href="javascript:void(0)">2026年程序设计竞赛</a>
        <a href="/abroad">2027春季访学项目报名</a>
        """
        notices = extract_link_notices("测试", "https://www.example.edu.cn/news/", html, Rules(), None, date(2026, 10, 13))
        self.assertEqual(
            [(n.title, n.category, n.url) for n in notices],
            [
                ("2026年大学生数学竞赛报名通知", "竞赛", "https://www.example.edu.cn/news/info/1.htm"),
                ("2027春季访学项目报名", "出国", "https://www.example.edu.cn/abroad"),
            ],
        )

    def test_page_without_links_is_an_error(self):
        with self.assertRaisesRegex(ValueError, "没有可解析的链接"):
            extract_link_notices("测试", "https://example.com", "<p>空</p>", Rules())


class LanqiaoTests(unittest.TestCase):
    HTML = "<div>报名时间：2026年10月8日 10:00 — 2026年12月18日 17:00</div>"

    def test_stage_changes_with_time_and_id_is_per_season(self):
        url = "https://dasai.lanqiao.cn/x"
        before = parse_lanqiao(self.HTML, "蓝桥杯", url, datetime(2026, 10, 8, 9, 59))
        opened = parse_lanqiao(self.HTML, "蓝桥杯", url, datetime(2026, 10, 8, 10, 0))
        self.assertEqual(before.lifecycle_stage, "upcoming")
        self.assertEqual(opened.lifecycle_stage, "open")
        self.assertEqual(before.id, opened.id)
        self.assertEqual(text.extract_registration_deadline(
            {"detail_text": opened.detail_text}, date(2026, 10, 9)), date(2026, 12, 18))

    def test_missing_window_returns_none(self):
        self.assertIsNone(parse_lanqiao("<p>敬请期待</p>", "蓝桥杯", "https://x", datetime.now() - timedelta(days=1)))


if __name__ == "__main__":
    unittest.main()
