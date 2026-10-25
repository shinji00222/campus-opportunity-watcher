"""源码运行和 PyInstaller 打包的入口：python run_watcher.py --school gdut"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from campus_watcher.cli import main  # noqa: E402

raise SystemExit(main())
