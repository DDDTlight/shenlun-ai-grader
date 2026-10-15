"""pytest 配置：把 server/ 和项目根加进 sys.path。

这样测试里可以直接 `import main` / `import config`，
不用在每个测试文件顶部重复改 sys.path。
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent          # server/tests
SERVER = HERE.parent                             # server
ROOT = SERVER.parent                             # 项目根

for p in (SERVER, ROOT):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))
