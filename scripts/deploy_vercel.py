"""Dereference the local shared-package symlink for a Vercel Services upload."""

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "cycling"
TARGET = ROOT / "backend" / "cycling"


def main() -> int:
    if not TARGET.is_symlink() or TARGET.readlink() != Path("../cycling"):
        raise SystemExit("Expected backend/cycling -> ../cycling source symlink")
    TARGET.unlink()
    try:
        shutil.copytree(
            SOURCE, TARGET, ignore=shutil.ignore_patterns("__pycache__", "*.pyc")
        )
        return subprocess.run(
            ["vercel", "deploy", *sys.argv[1:]], cwd=ROOT, check=False
        ).returncode
    finally:
        if TARGET.is_dir() and not TARGET.is_symlink():
            shutil.rmtree(TARGET)
        TARGET.symlink_to(Path("../cycling"), target_is_directory=True)


if __name__ == "__main__":
    raise SystemExit(main())
