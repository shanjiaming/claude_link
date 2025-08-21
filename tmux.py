from __future__ import annotations

import os
import shlex
import subprocess
from typing import List, Optional, Tuple


class TmuxError(RuntimeError):
    pass


def _run(args: List[str], capture: bool = True) -> str:
    try:
        completed = subprocess.run(
            ["tmux", *args],
            check=True,
            capture_output=True,
            text=True,
        )
        return completed.stdout
    except subprocess.CalledProcessError as e:
        raise TmuxError(e.stderr.strip() or str(e))


def display_current_path(pane_id: Optional[str] = None) -> str:
    args = ["display", "-p", "#{pane_current_path}"]
    if pane_id:
        args.extend(["-t", pane_id])
    out = _run(args)
    return out.strip()


def list_panes_all() -> List[Tuple[str, str, str]]:
    # Output: %7<TAB>/path<TAB>title
    fmt = "#{pane_id}\t#{pane_current_path}\t#{pane_title}"
    out = _run(["list-panes", "-a", "-F", fmt])
    panes: List[Tuple[str, str, str]] = []
    for line in out.splitlines():
        if not line:
            continue
        # Expect exactly two tabs; tolerate missing fields
        parts = line.split("\t")
        pid = parts[0].strip() if len(parts) > 0 else ""
        path = parts[1].strip() if len(parts) > 1 else ""
        title = parts[2].strip() if len(parts) > 2 else ""
        panes.append((pid, path, title))
    return panes


def get_current_command(pane_id: str) -> str:
    out = _run(["display", "-p", "#{pane_current_command}", "-t", pane_id])
    return out.strip()


def history_limit() -> int:
    try:
        out = _run(["show", "-gv", "history-limit"])
        return int(out.strip())
    except Exception:
        return int(os.environ.get("CLAUDE_LINK_HISTORY_LINES", "2000"))


def capture_text(pane_id: str, max_lines: Optional[int] = None) -> str:
    lines = max_lines if max_lines is not None else history_limit()
    # Capture with escapes and join wrapped lines, from -lines to bottom
    out = _run(["capture-pane", "-p", "-e", "-J", "-S", f"-{lines}", "-t", pane_id])
    return out


def split_new_pane(parent_pane: str, workdir: str, command: str, direction: str = "down") -> str:
    # Always target the specified parent pane so this works even when
    # the caller process itself is not inside tmux.
    args = [
        "split-window",
        "-P",
        "-F",
        "#{pane_id}",
        "-c",
        workdir,
        "-t",
        parent_pane,
    ]
    if direction == "right":
        args.insert(1, "-h")
    args.append(command)
    out = _run(args)
    return out.strip()


def send_clear_line(pane_id: str) -> None:
    _run(["send-keys", "-t", pane_id, "C-u"])


def set_buffer(name: str, data: str) -> None:
    _run(["set-buffer", "-b", name, "--", data])


def paste_buffer(name: str, target_pane: str, delete: bool = True) -> None:
    args = ["paste-buffer", "-b", name, "-t", target_pane]
    if delete:
        args.append("-d")
    _run(args)


def send_enter(pane_id: str) -> None:
    _run(["send-keys", "-t", pane_id, "Enter"]) 


def send_ctrl_c(pane_id: str) -> None:
    _run(["send-keys", "-t", pane_id, "C-c"]) 


def send_keys(pane_id: str, keys: List[str]) -> None:
    """Send one or more tmux key names to a pane.

    Examples of key names:
    - "Enter", "C-m" (Enter)
    - "C-c", "C-u"
    - "Down", "Up", "Left", "Right"
    - "BSpace" (Backspace), "DC" (Delete)

    Keys are passed through to tmux without modification.
    """
    if not isinstance(keys, list) or not keys:
        return
    _run(["send-keys", "-t", pane_id, *keys])


def respawn_pane(pane_id: str, command: str, kill_before: bool = True) -> None:
    args = ["respawn-pane"]
    if kill_before:
        args.append("-k")
    args.extend(["-t", pane_id, command])
    _run(args)


def kill_pane(pane_id: str, force: bool = False) -> None:
    """Kill a tmux pane by id. If force is True, use -K to keep layout stable if possible."""
    args = ["kill-pane", "-t", pane_id]
    # Note: tmux kill-pane doesn't have a generic force flag; -a/-t variants exist.
    # We keep the signature for future extension.
    _run(args)


def set_title(pane_id: str, title: str) -> None:
    _run(["select-pane", "-t", pane_id, "-T", title])


def get_title(pane_id: str) -> str:
    out = _run(["display", "-p", "#{pane_title}", "-t", pane_id])
    return out.strip()
