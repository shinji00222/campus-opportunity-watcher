# AGENTS.md

- 用户想给自己学校配置这个工具时，按 [AI_SETUP.md](AI_SETUP.md) 做。学校专用的东西只放在 `schools/<学校id>/`。
- 改 `src/` 通用代码时，跑 `python -m unittest discover -s tests -t .`，并用 `python run_watcher.py --school gdut --probe` 确认真实来源没坏。
- 代码只用标准库，加上 `winotify`（Windows 通知）。不要引入新依赖，除非用户同意。
- 不写入账号、密码、Cookie、Token；不配置需要登录的页面。
