"""git pre-commit：pylint + 前端 eslint。失败则拒绝 commit。"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _run(cmd: list[str], *, cwd: Path = ROOT) -> None:
    print("+", " ".join(cmd), flush=True)
    result = subprocess.run(cmd, cwd=cwd, check=False)
    if result.returncode != 0:
        raise SystemExit(result.returncode)


def install_hook() -> None:
    """把 .githooks/pre-commit 拷到 .git/hooks/pre-commit。"""
    git_dir = ROOT / ".git"
    if not git_dir.is_dir():
        raise SystemExit("不是 git 仓库，无法安装 hook")
    src = ROOT / ".githooks" / "pre-commit"
    dst = git_dir / "hooks" / "pre-commit"
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)
    try:
        dst.chmod(dst.stat().st_mode | 0o111)
    except OSError:
        pass
    print(f"installed git hook: {dst}")


def main() -> None:
    """作为 hook 跑两套 lint，或 `install` 写入 .git/hooks。"""
    if len(sys.argv) > 1 and sys.argv[1] == "install":
        install_hook()
        return
    python = sys.executable
    _run([python, "-m", "pylint", "api", "libs", "models", "scripts", "config.py"])
    npm = "npm.cmd" if sys.platform == "win32" else "npm"
    _run([npm, "run", "lint"], cwd=ROOT / "frontend")


if __name__ == "__main__":
    main()
