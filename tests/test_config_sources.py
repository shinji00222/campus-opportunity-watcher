import json
import tempfile
import textwrap
import unittest
import urllib.error
import urllib.request
from email.message import Message
from pathlib import Path
from unittest.mock import patch

from tests import ROOT
from campus_watcher import config, net, sources
from campus_watcher.config import ConfigError, load_school, load_school_file


def write_school(directory: Path, toml: str, parsers: str | None = None) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "school.toml").write_text(textwrap.dedent(toml), encoding="utf-8")
    if parsers is not None:
        (directory / "parsers.py").write_text(textwrap.dedent(parsers), encoding="utf-8")
    return directory / "school.toml"


MINIMAL = """
[school]
id = "demo"
name = "示例大学"
short_name = "示例"

[[sources]]
name = "通知"
type = "html_links"
url = "https://www.example.edu.cn/tzgg.htm"
"""


class BundledSchoolTests(unittest.TestCase):
    def test_bundled_schools_are_valid(self):
        for school_id, path in config.available_schools().items():
            with self.subTest(school=school_id):
                school = load_school_file(path)
                self.assertEqual(school.id, school_id)

    def test_template_is_valid_but_not_listed(self):
        self.assertNotIn("_template", config.available_schools())
        template = load_school_file(ROOT / "schools" / "_template" / "school.toml")
        self.assertEqual(template.id, "example")

    def test_gdut_keeps_its_extra_keywords(self):
        school = load_school("gdut")
        self.assertEqual(school.rules.classify("关于提交2026年广东省工科大学生实验综..."), "竞赛")
        self.assertEqual(school.app_name, "广工机会提醒")


class ValidationTests(unittest.TestCase):
    def test_problems_are_all_reported_together(self):
        with tempfile.TemporaryDirectory() as temp:
            path = write_school(Path(temp) / "bad", """
                [school]
                id = "Bad School"
                name = ""
                short_name = "坏"

                [keywords]
                "讲座" = ["x"]

                [[sources]]
                name = "a"
                type = "html_links"

                [[sources]]
                name = "a"
                type = "custom"
                url = "https://x.edu.cn"
                parser = "missing"

                [[sources]]
                name = "b"
                type = "ncss_internships"
                area_code = "4"
            """)
            with self.assertRaises(ConfigError) as caught:
                load_school_file(path)
        message = str(caught.exception)
        for expected in ("school.id", "school.name", "keywords.讲座", "需要 http(s)", "来源名称重复", "missing", "area_code"):
            self.assertIn(expected, message)

    def test_toml_syntax_error_is_reported(self):
        with tempfile.TemporaryDirectory() as temp:
            path = write_school(Path(temp) / "x", "[school\nid=1")
            with self.assertRaisesRegex(ConfigError, "TOML"):
                load_school_file(path)

    def test_user_school_dir_overrides_bundled(self):
        with tempfile.TemporaryDirectory() as temp:
            write_school(Path(temp) / "schools" / "gdut", MINIMAL.replace('"demo"', '"gdut"'))
            with patch.object(config.state, "DATA_ROOT", Path(temp)):
                self.assertEqual(load_school("gdut").name, "示例大学")


class CustomParserTests(unittest.TestCase):
    def test_custom_parser_gets_context_and_partial_errors_keep_items(self):
        with tempfile.TemporaryDirectory() as temp:
            path = write_school(Path(temp) / "demo", MINIMAL + """
                [[sources]]
                name = "平台"
                type = "custom"
                parser = "platform"
                url = "https://p.example.edu.cn/"
                category = "竞赛"
            """, parsers="""
                def platform(ctx):
                    rows = ctx.fetch_json(ctx.url + "api")
                    items = [ctx.Notice(ctx.make_id(r["url"]), r["title"], r["url"], ctx.name, category=ctx.category)
                             for r in rows]
                    raise ctx.PartialSourceError(items, "第 2 页读取失败")
            """)
            school = load_school_file(path)
            payload = json.dumps([{"title": "2026年挑战杯校赛", "url": "https://p.example.edu.cn/1"}])
            with patch.object(net, "fetch", return_value=payload):
                items, error = sources.load_source(school, school.sources[1])
        self.assertEqual([n.title for n in items], ["2026年挑战杯校赛"])
        self.assertEqual(items[0].source, "平台")
        self.assertIn("第 2 页", error)

    def test_html_links_follows_same_host_pages_and_reports_failures(self):
        school = load_school("gdut")
        source = {"name": "分页", "type": "html_links", "url": "https://xsc.example.edu.cn/tzgg.htm",
                  "pages_pattern": r"tzgg/\d+\.htm$", "extra_pages": 3}
        first = ('<a href="tzgg/2.htm">2</a><a href="tzgg/3.htm">3</a>'
                 '<a href="https://other.edu.cn/tzgg/9.htm">x</a><a href="/a">2026年数学建模竞赛通知</a>')

        def fake_fetch(url, **_kwargs):
            if url.endswith("/2.htm"):
                return '<a href="/b">2026年程序设计竞赛通知</a>'
            if url.endswith("/3.htm"):
                raise OSError("timeout")
            return first

        with patch.object(net, "fetch", side_effect=fake_fetch) as fetch:
            items, error = sources.load_source(school, source)
        self.assertEqual(sorted(n.title for n in items), ["2026年数学建模竞赛通知", "2026年程序设计竞赛通知"])
        self.assertIn("分页读取失败", error)
        self.assertNotIn("other.edu.cn", " ".join(call.args[0] for call in fetch.call_args_list))

    def test_check_all_collects_errors_per_source(self):
        school = load_school("gdut")
        with (
            patch.object(net, "fetch", side_effect=OSError("down")),
            patch.object(sources.state, "log"),
        ):
            notices, errors = sources.check_all(school, only="教务处")
        self.assertEqual(notices, [])
        self.assertEqual(errors, ["教务处: down"])


class ProxyFallbackTests(unittest.TestCase):
    def setUp(self):
        net._proxy_unusable.clear()
        self.addCleanup(net._proxy_unusable.clear)

    def test_refused_proxy_falls_back_to_direct_with_a_fresh_request(self):
        request = urllib.request.Request("https://example.com/")
        refused = urllib.error.URLError(ConnectionRefusedError(10061, "refused"))
        with (
            patch.object(urllib.request, "getproxies", return_value={"https": "http://127.0.0.1:10808"}),
            patch.object(urllib.request, "urlopen", side_effect=refused) as via_proxy,
            patch.object(net._DIRECT_OPENER, "open", return_value="direct") as direct,
            patch.object(net.state, "log"),
        ):
            self.assertEqual(net.open_url(request), "direct")
            self.assertEqual(net.open_url(request), "direct")
        self.assertEqual(via_proxy.call_count, 1)
        self.assertEqual(direct.call_count, 2)
        self.assertIsNot(direct.call_args_list[0].args[0], request)

    def test_no_proxy_goes_direct_and_http_errors_are_not_retried(self):
        request = urllib.request.Request("https://example.com/")
        with (
            patch.object(urllib.request, "getproxies", return_value={}),
            patch.object(net._DIRECT_OPENER, "open", return_value="direct"),
        ):
            self.assertEqual(net.open_url(request), "direct")
        not_found = urllib.error.HTTPError(request.full_url, 404, "Not Found", Message(), None)
        with (
            patch.object(urllib.request, "getproxies", return_value={"https": "http://127.0.0.1:10808"}),
            patch.object(urllib.request, "urlopen", side_effect=not_found),
            patch.object(net._DIRECT_OPENER, "open") as direct,
        ):
            with self.assertRaises(urllib.error.HTTPError):
                net.open_url(request)
        direct.assert_not_called()


if __name__ == "__main__":
    unittest.main()
