from __future__ import annotations

import errno
import json
import os
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Dict, Iterator, Optional

import fcntl


DEFAULT_ROOT = os.environ.get("CLAUDE_LINK_ROOT", "/tmp/claude-link")


def ensure_dirs() -> str:
    root = DEFAULT_ROOT
    inbox_dir = os.path.join(root, "inbox")
    os.makedirs(inbox_dir, exist_ok=True)
    return root


@contextmanager
def file_lock(path: str) -> Iterator[None]:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a+") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            f.flush()
            os.fsync(f.fileno())
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)


def now_ts() -> float:
    return time.time()


def read_json(path: str, default: Any) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def write_json(path: str, data: Any) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def get_runtime_root() -> str:
    # Prefer XDG_RUNTIME_DIR if set, else CLAUDE_LINK_ROOT, else /tmp/claude-link
    xdg = os.environ.get("XDG_RUNTIME_DIR")
    if xdg:
        root = os.path.join(xdg, "claude-link")
        os.makedirs(root, exist_ok=True)
        os.makedirs(os.path.join(root, "inbox"), exist_ok=True)
        return root
    root = ensure_dirs()
    return root
