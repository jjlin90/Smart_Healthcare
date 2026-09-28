from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    if not (ROOT / ".git").exists():
        print("当前目录还不是 Git 仓库，请先运行 git init。")
        return 1

    subprocess.run(
        ["git", "config", "core.hooksPath", ".githooks"],
        cwd=ROOT,
        check=True,
    )
    subprocess.run(
        ["git", "config", "--local", "medagent.python", Path(sys.executable).as_posix()],
        cwd=ROOT,
        check=True,
    )
    for hook in (ROOT / ".githooks").glob("*"):
        if hook.is_file():
            hook.chmod(hook.stat().st_mode | 0o111)
    print("Git hooks 已启用并绑定当前 Python：提交前快速检查，推送前完整检查。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
