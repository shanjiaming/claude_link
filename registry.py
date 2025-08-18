from __future__ import annotations

import os
from typing import Dict, Optional

from .utils import get_runtime_root, read_json, write_json, file_lock


_REGISTRY_PATH = os.path.join(get_runtime_root(), "registry.json")
_LOCK_PATH = os.path.join(get_runtime_root(), ".registry.lock")


def _load() -> Dict[str, Dict[str, str]]:
    return read_json(_REGISTRY_PATH, default={"children": {}})


def _save(data: Dict[str, Dict[str, str]]) -> None:
    write_json(_REGISTRY_PATH, data)


def set_child(parent_id: str, child_id: str, workdir: str) -> None:
    with file_lock(_LOCK_PATH):
        data = _load()
        children = data.setdefault("children", {})
        children[child_id] = {"father": parent_id, "workdir": workdir}
        _save(data)


def get_father(child_id: str) -> Optional[str]:
    with file_lock(_LOCK_PATH):
        data = _load()
        child = data.get("children", {}).get(child_id)
        return child.get("father") if child else None


def get_child_workdir(child_id: str) -> Optional[str]:
    with file_lock(_LOCK_PATH):
        data = _load()
        child = data.get("children", {}).get(child_id)
        return child.get("workdir") if child else None
