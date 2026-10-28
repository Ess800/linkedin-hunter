#!/usr/bin/env python3
"""
Minimal .env loader — no third-party dependency.

Secrets live in <project>/.env (mode 600, never committed). Values already set
in the environment win, so systemd units can override.
"""

import os
from pathlib import Path
from typing import Dict

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = PROJECT_ROOT / ".env"


def read_env_file(path: Path = ENV_PATH) -> Dict[str, str]:
    values: Dict[str, str] = {}
    try:
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            values[key.strip().removeprefix("export ").strip()] = val.strip().strip("'\"")
    except Exception:
        pass
    return values


def load_project_env() -> Dict[str, str]:
    values = read_env_file()
    for key, val in values.items():
        if val and not os.environ.get(key):
            os.environ[key] = val
    return values
