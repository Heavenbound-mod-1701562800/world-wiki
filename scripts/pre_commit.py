"""git pre-commit：按暂存文件跑 pylint / 前端 eslint。失败则拒绝 commit。"""

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


def _staged_paths() -> list[str]:
    result = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise SystemExit(result.returncode or 1)
    return [
        line.strip().replace("\\", "/")
        for line in result.stdout.splitlines()
        if line.strip()
    ]


def _needs_pylint(paths: list[str]) -> bool:
    for path in paths:
        if not path.endswith(".py"):
            continue
        if path.split("/")[0] == "tests":
            continue
        return True
    return False


def _needs_eslint(paths: list[str]) -> bool:
    return any(path == "frontend" or path.startswith("frontend/") for path in paths)


def main() -> None:
    """作为 hook 按改动跑 lint，或 `install` 写入 .git/hooks。"""
    if len(sys.argv) > 1 and sys.argv[1] == "install":
        install_hook()
        return

    paths = _staged_paths()
    run_pylint = _needs_pylint(paths)
    run_eslint = _needs_eslint(paths)
    if not run_pylint and not run_eslint:
        return

    python = sys.executable
    if run_pylint:
        _run([python, "-m", "pylint", "api", "libs", "models", "scripts", "config.py"])
    if run_eslint:
        npm = "npm.cmd" if sys.platform == "win32" else "npm"
        _run([npm, "run", "lint"], cwd=ROOT / "frontend")


if __name__ == "__main__":
    main()
