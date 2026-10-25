# 校园机会提醒

高校竞赛、实习、出国机会定时提醒工具。学校和信息来源写在配置文件里，让 AI 读一遍 [AI_SETUP.md](AI_SETUP.md) 就能配好你的学校。（原本只有广工版，在某人的提醒下开了这个新仓库）

## 能做什么

- 每天定时检查学校官网和公开平台，汇总竞赛、实习、出国交流通知，检查完就退出，不常驻后台
- 有新通知时弹 Windows 通知；同一条通知改了标题或截止日期会再提醒一次
- 识别报名截止日期，过期的自动清理；没有截止日期的实习按发布日期 60 天清理
- 读取正文里的参赛对象、资格要求，按本机个人档案算匹配度
- 收藏、分类筛选、关键词搜索
- 内置国家大学生就业服务平台（可按省份筛选）、国际组织实习平台、蓝桥杯官网
- 系统代理开着就走代理，代理没开或连不上就自动直连

## 配置你的学校

已有学校：`schools/gdut`（广东工业大学）。

没有你的学校的话，把仓库交给 AI（Claude、Codex、Cursor 都行），跟它说：

> 读一下 AI_SETUP.md，帮我把这个工具配置成 XX 大学的。

它会帮你找通知页面、写 `schools/<学校>/school.toml`、试跑确认能抓到东西。也可以照着 `schools/_template` 自己写。

## 从源码运行

需要 Python 3.11 或更高版本。

```powershell
python -m pip install -r requirements.txt
python run_watcher.py --school gdut                 # 打开界面
python run_watcher.py --school gdut --probe         # 试跑所有来源，不写状态
python run_watcher.py --school gdut --scheduled     # 静默检查一次
```

装成每天自动检查（上午 8 点、晚上 8 点）：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\install.ps1 -School gdut -FromSource
```

## 打包 EXE

```powershell
python -m pip install pyinstaller
python -m PyInstaller --noconfirm --clean --onefile --windowed `
  --name CampusOpportunityWatcher --paths src --collect-all winotify `
  --add-data "schools;schools" run_watcher.py
```

发布包里放 `dist\CampusOpportunityWatcher.exe` 和 `scripts\` 下的安装、卸载脚本。

## 隐私

个人档案、收藏、检测记录和日志只存在本机 `%LOCALAPPDATA%\CampusOpportunityWatcher\<学校id>\`，不会上传。工具只读公开网页，不登录任何系统。

## 局限

只能抓公开网页。微信群、公众号、图片和附件里的要求、需要登录的平台都抓不到。网站改版也可能让某个来源失效，失效时检测结果会显示来源错误。报名前请以原通知为准。

## 许可

MIT
