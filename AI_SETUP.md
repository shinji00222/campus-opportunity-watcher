# AI_SETUP：给 AI 看的学校配置指南

你（AI 助手）的任务：帮用户把这个工具配置成他们自己学校能用的版本。全程只读公开网页，不登录、不提交表单、不绕过验证码。

做完的标准：`schools/<学校id>/school.toml` 通过 `--validate`，`--probe` 至少有一个学校官网来源能抓到当前的竞赛或出国通知，并且所有来源都没有错误，或者剩下的错误你已经向用户解释清楚。

## 0. 先问用户

1. 学校全称和简称（如“中山大学 / 中大”）。
2. 所在省份，用来筛选实习岗位（见下方省级代码表）。
3. 关心哪些学院。学院官网经常单独发竞赛通知，学校主页上不一定有。
4. 用户用的是源码，还是下载的 EXE（第 6 步不一样）。

## 1. 建配置

```powershell
Copy-Item -Recurse schools\_template schools\<学校id>
```

学校 id 用小写拼音缩写或英文缩写（如 `sysu`、`scut`），必须和文件夹名一致。改 `[school]` 段的三个字段。

## 2. 找信息来源

从这些页面找（优先找“通知公告 / 通知 / 公告”这类列表页，不要用新闻首页的轮播）：

| 来源 | 常见名称 | 建议 category |
|---|---|---|
| 学校主页通知公告 | 通知公告、校内通知 | 不设 |
| 教务处 | 学科竞赛、通知公告 | `竞赛` |
| 学生工作处 / 团委 | 通知公告、第二课堂 | 不设 |
| 创新创业学院 | 竞赛通知、双创 | `竞赛` |
| 国际交流处 / 港澳台办 | 出国（境）交流、项目公告 | `出国` |
| 就业指导中心 | 赛事、实习 | 看页面 |
| 用户关心的学院 | 学院通知、学生工作 | `竞赛` |

每个候选页面都先跑一遍分析：

```powershell
python run_watcher.py --inspect "https://www.example.edu.cn/tzgg.htm"
python run_watcher.py --inspect "https://jwc.example.edu.cn/" --category 竞赛
```

看输出判断：

- **“会被收集的链接”里有当前的竞赛/出国通知** → 可以直接作为 `html_links` 来源。
- **命中太少，“未命中关键词的链接”里却有该收集的通知** → 给这个来源设 `category`，或者在 `[keywords]` 里补这所学校特有的赛事名。
- **混进了无关链接**（导航、后勤、结果公示）→ 在 `[keywords] ignore` 里加关键词。
- **提示“内容可能由脚本动态加载”** → 这个页面 `html_links` 抓不到，去浏览器开发者工具的 Network 面板找它请求的 JSON 接口，用 `custom` 写解析函数（第 4 步）。
- **“疑似分页链接”** → 如果较早的通知会落到第 2、3 页，写 `pages_pattern` 和 `extra_pages`。

## 3. school.toml 字段

```toml
[school]
id = "sysu"
name = "中山大学"
short_name = "中大"

[keywords]                      # 都是可选的，在内置关键词基础上追加
"竞赛" = ["某某杯"]
"出国" = []
"实习" = []
ignore = ["标题含这些词就跳过"]

[[sources]]
name = "教务处"                  # 每个来源唯一，会显示在界面和日志里
type = "html_links"
url = "https://jwc.example.edu.cn/"
category = "竞赛"                # 可选。页面默认分类；标题仍需含任一关键词才会收
pages_pattern = '(?:^|/)tzgg/\d+\.htm$'   # 可选。分页链接正则，只跟同域名
extra_pages = 3                  # 可选。1–10，默认 3
```

来源类型：

| type | 用途 | 参数 |
|---|---|---|
| `html_links` | 普通通知列表页，最常用 | `url`，可选 `category`、`pages_pattern`、`extra_pages` |
| `ncss_internships` | 国家大学生就业服务平台实习岗位 | `area_code`（省级代码，留空 `""` 表示全国），`max_pages` 1–5 |
| `ncss_international` | 国际组织实习平台 | `max_pages` 1–5 |
| `lanqiao` | 蓝桥杯官网报名时间 | 无 |
| `custom` | 结构特殊的页面或接口 | `url`、`parser`（parsers.py 里的函数名），可选 `category` |

内置关键词见 `src/campus_watcher/rules.py`，已经覆盖挑战杯、互联网+、数学建模、电子设计、蓝桥杯、ICPC 等常见全国赛事，以及交换、访学、联合培养、实习等词，不需要重复添加。

常用省级代码：北京 11、天津 12、河北 13、山西 14、内蒙古 15、辽宁 21、吉林 22、黑龙江 23、上海 31、江苏 32、浙江 33、安徽 34、福建 35、江西 36、山东 37、河南 41、湖北 42、湖南 43、广东 44、广西 45、海南 46、重庆 50、四川 51、贵州 52、云南 53、西藏 54、陕西 61、甘肃 62、青海 63、宁夏 64、新疆 65。

## 4. 写 custom 解析函数（只在必要时）

在 `schools/<学校id>/parsers.py` 写一个函数，参数是 `ctx`，返回通知列表：

```python
def my_platform(ctx):
    rows = ctx.fetch_json(ctx.url + "api/list?page=1")
    notices = []
    for row in rows:
        url = f"{ctx.url}detail?id={row['id']}"
        notices.append(ctx.Notice(
            id=ctx.make_id(url), title=ctx.squash(row["title"]), url=url,
            source=ctx.name, category=ctx.category or "竞赛",
        ))
    return [n for n in notices if not ctx.is_stale({"category": n.category, "title": n.title})]
```

`ctx` 提供：`name`、`url`、`category`、`source`（整段配置）、`today`、`rules`（`classify(title)`、`is_ignored(title)`）、`fetch(url, headers=, data=, method=)`、`fetch_json(...)`、`extract_notices(html, base_url=None, category=None)`（和 `html_links` 同一套规则）、`links(html)`、`visible_text(html)`、`squash(text)`、`is_stale(notice_dict)`、`make_id(*parts)`、`Notice`、`PartialSourceError`、`log(message)`。

规则：

- 以下划线开头的函数不会被当成解析函数，辅助函数这样命名。
- 多页数据中某一页失败时，`raise ctx.PartialSourceError(已拿到的通知, "第 2 页读取失败：…")`，不要吞掉错误。
- 整个来源都拿不到数据时直接抛 `ValueError("原因")`，不要返回空列表假装成功。
- 列表里已经有完整信息、不需要再抓详情页时，设 `detail="none"`。
- 需要特殊请求头时（比如租户代码），写注释说明它来自该网站前端自己的请求，不是凭据。
- 可以参考 `schools/gdut/parsers.py`：卡片式页面、标题不在链接里的页面、带分页的 JSON 接口各有一个例子。

## 5. 验证

```powershell
python run_watcher.py --school <学校id> --validate
python run_watcher.py --school <学校id> --probe
python run_watcher.py --school <学校id> --probe --source "教务处" --details 2 --show 10
```

`--probe` 只读试跑，不写状态、不发通知。检查这几件事：

- 每个来源的条数合理。0 条就回到第 2 步排查；条数特别多就看看是不是混进了无关链接。
- 抽几条标题和网址，确认是真实、当前的通知。
- 用 `--details` 确认能读到正文、识别出截止日期。正文 0 字说明详情页是动态加载的，需要在汇报时提醒用户。

全部跑完后，向用户汇报：用了哪些来源、各抓到多少条、哪些来源抓不到以及原因、建议用户自己留意哪些渠道（微信群、公众号、附件里的要求，这些本工具抓不到）。

## 6. 让用户用起来

源码用户：

```powershell
python -m pip install -r requirements.txt
python run_watcher.py --school <学校id>                  # 打开界面，点“立即检测”建立基线
powershell -ExecutionPolicy Bypass -File scripts\install.ps1 -School <学校id> -FromSource
```

EXE 用户：把 `schools\<学校id>` 整个文件夹复制到 `%LOCALAPPDATA%\CampusOpportunityWatcher\schools\`，然后在发布包里运行 `install.cmd <学校id>`。这里的配置优先于程序内置的同名学校。

注意：程序已经内置的学校（如 `gdut`）不要复制到上面这个目录，否则会一直用这份副本，新版程序里修好的来源就不会生效。

## 7. 不要做的事

- 不要配置需要登录才能看的页面（教务系统、学工系统、统一身份认证后的平台）。
- 不要在配置或代码里写账号、密码、Cookie、Token。
- 不要调高请求频率或页数去“扫全站”；每个来源只读最新一两页就够了。
- 不要改 `src/` 里的通用代码去适配某一所学校；学校专用的东西都放在 `schools/<学校id>/`。如果确实发现通用 bug，单独说明。
- 如果愿意分享，可以把 `schools/<学校id>/` 作为 PR 提交回本仓库。
