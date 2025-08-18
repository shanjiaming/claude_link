from __future__ import annotations

import json
import os
from typing import Dict, List, Tuple

from .utils import get_runtime_root, file_lock, now_ts, read_json, write_json


def _inbox_path(pane_id: str) -> str:
    return os.path.join(get_runtime_root(), "inbox", f"{pane_id}.jsonl")


def _meta_path(pane_id: str) -> str:
    return os.path.join(get_runtime_root(), "inbox", f"{pane_id}.meta.json")


def _lock_path(pane_id: str) -> str:
    return os.path.join(get_runtime_root(), "inbox", f".{pane_id}.lock")


def _ensure_meta(pane_id: str) -> Dict[str, int]:
    meta = read_json(_meta_path(pane_id), default={"next_id": 1})
    if "next_id" not in meta or not isinstance(meta["next_id"], int):
        meta["next_id"] = 1
    return meta


def _save_meta(pane_id: str, meta: Dict[str, int]) -> None:
    write_json(_meta_path(pane_id), meta)


def append_message(target_pane: str, from_pane: str, text: str) -> int:
    lockfile = _lock_path(target_pane)
    with file_lock(lockfile):
        meta = _ensure_meta(target_pane)
        msg_id = meta["next_id"]
        meta["next_id"] = msg_id + 1
        _save_meta(target_pane, meta)

        record = {
            "id": msg_id,
            "from": from_pane,
            "text": text,
            "ts": now_ts(),
        }
        path = _inbox_path(target_pane)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False))
            f.write("\n")
        return msg_id


def read_since(pane_id: str, since_id: int) -> Tuple[List[Dict], int]:
    lockfile = _lock_path(pane_id)
    max_id = since_id
    messages: List[Dict] = []
    with file_lock(lockfile):
        path = _inbox_path(pane_id)
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.rstrip("\n")
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except Exception:
                        continue
                    mid = int(obj.get("id", 0))
                    if mid > since_id:
                        messages.append(obj)
                        if mid > max_id:
                            max_id = mid
        except FileNotFoundError:
            pass
    messages.sort(key=lambda m: int(m.get("id", 0)))
    return messages, max_id
