"""Run the local checks required before code is committed or pushed."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
from collections.abc import Sequence


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
REQUIRED_FILES = (
    "Dockerfile.render",
    "render.yaml",
    ".env.example",
    "requirements.txt",
    "frontend/package.json",
    "start-render.sh",
)


def command_label(command: Sequence[str]) -> str:
    """Format a command for readable preflight output."""
    return " ".join(command)


def run_command(name: str, command: Sequence[str]) -> bool:
    """Run one check and report its result without stopping later checks."""
    print(f"\n[CHECK] {name}")
    print(f"$ {command_label(command)}")
    try:
        completed = subprocess.run(command, cwd=REPOSITORY_ROOT, check=False)
    except FileNotFoundError:
        print(f"[FAILED] Required command is not available: {command[0]}")
        return False

    if completed.returncode == 0:
        print(f"[PASSED] {name}")
        return True

    print(f"[FAILED] {name} (exit code {completed.returncode})")
    return False


def check_required_files() -> bool:
    """Ensure files needed to build and deploy production are present."""
    print("\n[CHECK] Required production files")
    missing_files = [path for path in REQUIRED_FILES if not (REPOSITORY_ROOT / path).is_file()]
    if missing_files:
        for path in missing_files:
            print(f"[MISSING] {path}")
        print("[FAILED] Required production files")
        return False

    print("[PASSED] Required production files")
    return True


def check_tracked_env_file() -> bool:
    """Reject a repository that tracks the root .env file."""
    print("\n[CHECK] .env is not tracked by Git")
    completed = subprocess.run(
        ["git", "ls-files", "--", ".env"],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        print(f"[FAILED] git ls-files returned exit code {completed.returncode}")
        if completed.stderr:
            print(completed.stderr, end="")
        return False

    tracked_files = completed.stdout.strip()
    if tracked_files:
        print("[FAILED] The following .env file is tracked by Git:")
        print(tracked_files)
        return False

    print("[PASSED] .env is not tracked by Git")
    return True


def npm_command() -> str:
    """Return the executable name that works for the host operating system."""
    return "npm.cmd" if os.name == "nt" else "npm"


def main() -> int:
    sys.stdout.reconfigure(line_buffering=True)
    print("=== AI SaaS preflight ===")
    results = [
        check_required_files(),
        check_tracked_env_file(),
        run_command(
            "Python dependency install",
            [sys.executable, "-m", "pip", "install", "-r", "requirements-dev.txt"],
        ),
        run_command("Python backend compilation", [sys.executable, "-m", "compileall", "app"]),
        run_command(
            "Backend tests",
            [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
        ),
        run_command("Frontend clean install", [npm_command(), "ci", "--prefix", "frontend"]),
        run_command("Frontend production build", [npm_command(), "run", "build", "--prefix", "frontend"]),
        run_command(
            "Production Docker build",
            ["docker", "build", "-f", "Dockerfile.render", "-t", "ai-saas-preflight", "."],
        ),
    ]

    if all(results):
        print("\nPREFLIGHT PASSED")
        return 0

    print("\nPREFLIGHT FAILED")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
