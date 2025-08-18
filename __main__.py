from __future__ import annotations

import json
import os
import sys
import time
from typing import Any, Dict, List

from .protocol_types import RpcRequest, RpcResponse
from . import protocol_types as types
from . import tmux as tm
from . import registry
from . import inbox
from .utils import get_runtime_root


def _resp_ok(req_id: Any, result: Any) -> RpcResponse:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _resp_err(req_id: Any, code: int, message: str, data: Any = None) -> RpcResponse:
    err = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": req_id, "error": err}


def method_whoami(params: Dict[str, Any]) -> Dict[str, Any]:
    pane_id = os.environ.get("TMUX_PANE")
    if not pane_id:
        raise RuntimeError("TMUX_PANE is not set; must be run inside tmux pane")
    try:
        workdir = tm.display_current_path(pane_id)
    except Exception:
        workdir = os.getcwd()
    father = registry.get_father(pane_id)
    res: Dict[str, Any] = {"id": pane_id, "workdir": workdir}
    if father:
        res["father"] = father
    return res


def method_list(params: Dict[str, Any]) -> Any:
    mapping: Dict[str, Dict[str, str]] = {}
    panes = tm.list_panes_all()
    result = []
    # Build father mapping
    for pid, path, title in panes:
        father = registry.get_father(pid)
        obj: Dict[str, Any] = {"id": pid, "workdir": path, "title": title}
        if father:
            obj["father"] = father
        result.append(obj)
    return result


def method_start_new_session_and_get_return_id(params: Dict[str, Any]) -> Any:
    parent_pane = os.environ.get("TMUX_PANE")
    if not parent_pane:
        raise RuntimeError("TMUX_PANE is not set; must be run inside tmux pane")
    workdir = params.get("workdir")
    if not workdir:
        try:
            workdir = tm.display_current_path(parent_pane)
        except Exception:
            workdir = os.getcwd()
    # Apply strict workdir policy before doing anything dangerous
    policy = (params.get("workdir_policy") or "require_empty_existing").lower()
    workdir = _apply_workdir_policy(workdir, policy)
    # Default to launching Claude with permissive flag unless overridden
    cmd = os.environ.get("CLAUDE_CMD", "claude --dangerously-skip-permissions")
    # Fixed default split direction; no env coupling
    direction = "down"
    new_pane = tm.split_new_pane(parent_pane, workdir, cmd, direction=direction)
    # Assign a stable title based on pane id to avoid ambiguity
    try:
        tm.set_title(new_pane, f"agent:{new_pane}")
    except Exception:
        pass
    # After creating, ensure project-level Claude settings and MCP server config exist
    try:
        _ensure_claude_and_mcp_configs(workdir)
        # Optional hook management (strict; no fallbacks or %0)
        add_hook = bool(params.get("add_hook", False))
        if add_hook:
            hook_mode = (params.get("hook_mode") or "text").lower()
            called = params.get("calledagent")
            if hook_mode not in ("text", "screenshot"):
                raise RuntimeError("invalid hook_mode; must be 'text' or 'screenshot'")
            if not isinstance(called, str) or not called.strip():
                raise RuntimeError("calledagent is required when add_hook=True")
            if hook_mode == "text":
                base_text = params.get("text")
                if base_text is None:
                    raise RuntimeError("text is required when hook_mode='text'")
                commands = _build_stop_hook_commands_for_creation_strict(hook_mode, called, base_text, new_pane)
            else:
                commands = _build_stop_hook_commands_for_creation_strict(hook_mode, called, None, new_pane)
            _overwrite_settings_with_hooks(workdir, commands)
        else:
            _remove_claude_link_hooks(workdir)
    except Exception:
        # Do not fail the operation if writing configs fails
        pass
    registry.set_child(parent_pane, new_pane, workdir)
    return {"id": new_pane}


def method_get_screenshot_from(params: Dict[str, Any]) -> Any:
    target_id = params.get("target_id")
    if not target_id:
        raise RuntimeError("target_id is required")
    _validate_pane_id(target_id)
    max_lines = None
    try:
        text = tm.capture_text(target_id, max_lines=max_lines)
    except Exception as e:
        raise RuntimeError(f"tmux capture failed: {e}")
    return {"text": text}


def method_send_message_to(params: Dict[str, Any]) -> Any:
    target_id = params.get("target_id")
    text = params.get("text")
    if target_id is None or text is None:
        raise RuntimeError("target_id and text are required")
    sender = os.environ.get("TMUX_PANE", "unknown")
    stored_text = f"From {sender}: " + str(text)
    inbox.append_message(target_id, sender, stored_text)
    return {"ok": True}


def method_inject_input_to(params: Dict[str, Any]) -> Any:
    target_id = params.get("target_id")
    text = params.get("text")
    with_from = bool(params.get("with_from", False))
    prefix = params.get("prefix")
    submit = bool(params.get("submit", True))
    mode = params.get("mode", "append")
    if target_id is None or text is None:
        raise RuntimeError("target_id and text are required")
    _validate_pane_id(target_id)
    sender = os.environ.get("TMUX_PANE", "unknown")
    payload_text = str(text)
    if isinstance(prefix, str) and prefix != "":
        payload = f"{prefix}{payload_text}"
    elif with_from:
        payload = f"From {sender}: {payload_text}"
    else:
        payload = payload_text

    # Use tmux buffer-based paste to preserve exact text and bracketed paste
    buf_name = f"claude_link_{os.getpid()}_{int(time.time()*1e6)}"
    if mode == "replace":
        tm.send_clear_line(target_id)
    tm.set_buffer(buf_name, payload)
    tm.paste_buffer(buf_name, target_id, delete=True)
    if submit:
        time.sleep(0.1)
        tm.send_enter(target_id)
    return {"ok": True}


def method_add_callback_hook_when_completed(params: Dict[str, Any]) -> Any:
    """Append Stop-hook commands into the hooked agent's settings.local.json (strict mode).

    Required params:
      - hookedagent: pane id whose workdir hosts the settings
      - hooked_workdir: absolute project root path of the hooked agent
      - calledagent: pane id to receive injected messages
      - server_cmd: exact command to launch this MCP server (e.g., 'python3 -m claude_link')
      - mode: 'text' | 'screenshot'
      - text: required when mode == 'text'
    """
    hooked = params.get("hookedagent")
    called = params.get("calledagent")
    base_text = params.get("text")
    mode = (params.get("mode") or "text").lower()
    hooked_workdir = params.get("hooked_workdir")

    if not hooked or not called:
        raise RuntimeError("hookedagent and calledagent are required")
    if mode not in ("text", "screenshot"):
        raise RuntimeError("mode must be 'text' or 'screenshot'")
    if mode == "text" and base_text is None:
        raise RuntimeError("text is required when mode='text'")

    # Strictly resolve hooked agent's settings path: require explicit workdir (no fallbacks)
    if not (isinstance(hooked_workdir, str) and hooked_workdir.strip()):
        raise RuntimeError("'hooked_workdir' is required and must be the absolute path of the hooked agent's project root")
    target_workdir = hooked_workdir
    settings_path = os.path.join(target_workdir, ".claude", "settings.local.json")
    settings_dir = os.path.dirname(settings_path)
    os.makedirs(settings_dir, exist_ok=True)

    # Load existing JSON, tolerate non-object
    try:
        with open(settings_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            if not isinstance(data, dict):
                data = {}
    except FileNotFoundError:
        data = {}
    except Exception:
        # If unreadable/corrupt, back it up and start fresh
        try:
            os.replace(settings_path, settings_path + ".bak")
        except Exception:
            pass
        data = {}

    hooks_obj = data.get("hooks")
    if not isinstance(hooks_obj, dict):
        hooks_obj = {}
        data["hooks"] = hooks_obj

    stop_list = hooks_obj.get("Stop")
    if not isinstance(stop_list, list):
        stop_list = []
        hooks_obj["Stop"] = stop_list

    # Build commands (explicit pane ids; no %0 or env)
    mcp_client = "claude-link-call"
    # Prefer installed console script for path-agnostic startup
    _server_quoted = "'claude-link'"

    commands: List[Dict[str, Any]] = []

    if mode == "text":
        # Use TMUX_PANE of the executing agent to tag the sender dynamically
        default_prefix = "[msg from ${TMUX_PANE}] "
        p1 = json.dumps({"target_id": called, "text": default_prefix + str(base_text)}, ensure_ascii=False)
        cmd = f"{mcp_client} --server {_server_quoted} --method inject_input_to --params '{p1}' --output text"
        commands.append({"type": "command", "command": cmd})
    else:
        # mode == "screenshot": capture hooked's screenshot and inject as text to called
        py = (
            "import json,subprocess,os; "
            "base=['claude-link-call','--server','claude-link']; "
            "tid=os.environ.get('TMUX_PANE'); "
            "r=subprocess.run(base+['--method','get_screenshot_from','--params',json.dumps({'target_id': tid}),'--output','result'],capture_output=True,text=True,check=True); "
            "res=json.loads(r.stdout); body=res['text'] if isinstance(res,dict) else str(res); "
            f"tgt={json.dumps(called)}; "
            "prefix=f'[screenshot from ' + str(tid) + ']\\n'; "
            "params=json.dumps({'target_id': tgt, 'text': prefix + body}, ensure_ascii=False); "
            "subprocess.run(base+['--method','inject_input_to','--params',params,'--output','text'],check=True)"
        )
        commands.append({"type": "command", "command": f"python3 -c \"{py}\""})

    # Collect existing command strings to avoid duplicates
    existing_cmds: set[str] = set()
    for entry in stop_list:
        if isinstance(entry, dict) and isinstance(entry.get("hooks"), list):
            for h in entry["hooks"]:
                if isinstance(h, dict) and isinstance(h.get("command"), str):
                    existing_cmds.add(h["command"])

    new_hooks = [h for h in commands if h.get("command") not in existing_cmds]
    if new_hooks:
        stop_list.append({"hooks": new_hooks})
        # Write atomically
        tmp = settings_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, settings_path)

    return {
        "path": settings_path,
        "added": [h["command"] for h in new_hooks],
        "skipped": [h["command"] for h in commands if h not in new_hooks],
    }


def method_check_message_box(params: Dict[str, Any]) -> Any:
    pane_id = os.environ.get("TMUX_PANE")
    if not pane_id:
        raise RuntimeError("TMUX_PANE is not set; must be run inside tmux pane")
    since_id = int(params.get("since_id", 0))
    msgs, max_id = inbox.read_since(pane_id, since_id)
    return {"messages": msgs, "since_id": max_id}


def method_kill_pane_and_agent(params: Dict[str, Any]) -> Any:
    """Kill a tmux pane and any agent running inside it.

    Params:
      - target_id: pane id to kill (e.g., "%5")
    """
    target_id = params.get("target_id")
    if not target_id:
        raise RuntimeError("target_id is required")
    _validate_pane_id(target_id)
    try:
        tm.kill_pane(target_id)
    except Exception as e:
        raise RuntimeError(f"tmux kill-pane failed: {e}")
    return {"ok": True}


_METHODS = {
    "whoami": method_whoami,
    "list": method_list,
    "start_new_session_and_get_return_id": method_start_new_session_and_get_return_id,
    "get_screenshot_from": method_get_screenshot_from,
    "send_message_to": method_send_message_to,
    "inject_input_to": method_inject_input_to,
    "add_callback_hook_when_completed": method_add_callback_hook_when_completed,
    "kill_pane_and_agent": method_kill_pane_and_agent,
    "check_message_box": method_check_message_box,
}


# --- Minimal MCP compatibility layer ---
def _tool_specs() -> List[Dict[str, Any]]:
    # Return only canonical tool names to avoid duplicates. Aliases remain callable via tools/call.
    return [
        {"name": "whoami", "description": "Return caller pane id, workdir, and father if known.",
         "inputSchema": {"type": "object", "properties": {}, "required": []}},
        {"name": "list", "description": "List all tmux panes with workdir, title, and optional father.",
         "inputSchema": {"type": "object", "properties": {}, "required": []}},
        {"name": "start_new_session_and_get_return_id",
         "description": "Create a new Claude Code agent (tmux pane) next to the caller; returns pane id and sets title to 'agent:<id>'. 强烈建议：如需 Hook，请在创建时通过 add_hook 一次性写入；运行中的 Claude Code 不会实时应用你之后通过 MCP 或直接编辑 .claude 的变更，除非重启 pane。",
         "inputSchema": {"type": "object", "properties": {
             "workdir": {"type": "string"},
             "workdir_policy": {"type": "string", "enum": [
                 "require_empty_existing", "use_existing", "create_new", "create_or_empty"
             ], "default": "require_empty_existing"},
             "add_hook": {"type": "boolean", "default": False},
             "hook_mode": {"type": "string", "enum": ["text", "screenshot"], "default": "text"},
            "calledagent": {"type": "string", "description": "Target pane id like '%7' (names may be supported by your setup)."},
             "text": {"type": "string"}
         }, "required": []}},
        {"name": "get_screenshot_from",
         "description": "Capture the full text buffer (not an image) of a target tmux pane.",
         "inputSchema": {"type": "object", "properties": {"target_id": {"type": "string", "description": "Pane id like '%7' (names may be supported)."}}, "required": ["target_id"]}},
        {"name": "send_message_to",
         "description": "Passive delivery ONLY: enqueue a message into target inbox. Target must call check_message_box to see it. Use inject_input_to for immediate delivery.",
         "inputSchema": {"type": "object", "properties": {"target_id": {"type": "string"}, "text": {"type": "string"}}, "required": ["target_id", "text"]}},
        {"name": "inject_input_to",
         "description": "Active delivery (forced receive): write text to target input and optionally submit. Use when you must notify immediately. Optionally prepend 'From <sender_id>:' via with_from. Modes: append/replace. No sanitization.",
         "inputSchema": {"type": "object", "properties": {
             "target_id": {"type": "string", "description": "Pane id like '%7' (names may be supported)."}, "text": {"type": "string"},
             "with_from": {"type": "boolean", "default": False},
             "submit": {"type": "boolean", "default": True},
             "mode": {"type": "string", "enum": ["append", "replace"], "default": "append"}
         }, "required": ["target_id", "text"]}},
        {"name": "check_message_box",
         "description": "Pull inbox messages for caller pane since id. Call periodically to avoid stalls; send_message_to is passive and requires this pull.",
         "inputSchema": {"type": "object", "properties": {"since_id": {"type": "integer", "default": 0}}, "required": []}},
        {"name": "add_callback_hook_when_completed",
         "description": "Append a single project Stop-hook (text or screenshot) into settings.local.json with a default prefix. Use real pane ids (e.g. '%7'). 重要：对已运行的 Claude Code 注入/修改 Hook（无论通过 MCP 还是直接改 .claude）都不会影响当前会话；仅对后续新启动的会话生效。要让当前会话生效，请在创建时加 Hook，或重启该 pane。",
         "inputSchema": {"type": "object", "properties": {
             "hookedagent": {"type": "string", "description": "Pane id of the agent whose settings are modified (e.g. '%5'; names may be supported)."},
             "hooked_workdir": {"type": "string", "description": "Absolute project root of the hooked agent (contains .claude/)."},
             "calledagent": {"type": "string", "description": "Pane id to receive the callback (e.g. '%7'; names may be supported)."},
             "mode": {"type": "string", "enum": ["text", "screenshot"], "default": "text"},
             "text": {"type": "string"}
         }, "required": ["hookedagent", "hooked_workdir", "calledagent"]}},
        {"name": "kill_pane_and_agent",
         "description": "Kill a tmux pane by id; any running agent inside will be terminated by tmux.",
         "inputSchema": {"type": "object", "properties": {"target_id": {"type": "string", "description": "Pane id like '%7' (names may be supported)."}}, "required": ["target_id"]}},
    ]


def _validate_pane_id(pane_ref: str) -> None:
    # Allow either tmux pane ids (e.g. '%7') or future custom names; only enforce non-empty string.
    if not isinstance(pane_ref, str) or not pane_ref.strip():
        raise RuntimeError("target_id must be a non-empty string (e.g., '%7')")


def _ensure_claude_and_mcp_configs(workdir: str) -> None:
    """Ensure workdir/.claude/settings.local.json and workdir/.mcp.json contain required entries.

    - .claude/settings.local.json: merge {"enableAllProjectMcpServers": true}
    - .mcp.json: merge content from claude_link/.mcp.json (external, editable)
    """
    # .claude/settings.local.json
    claude_dir = os.path.join(workdir, ".claude")
    os.makedirs(claude_dir, exist_ok=True)
    settings_path = os.path.join(claude_dir, "settings.local.json")
    try:
        try:
            with open(settings_path, "r", encoding="utf-8") as f:
                settings = json.load(f)
                if not isinstance(settings, dict):
                    settings = {}
        except FileNotFoundError:
            settings = {}
        settings.setdefault("enableAllProjectMcpServers", True)
        tmp = settings_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(settings, f, ensure_ascii=False, indent=2)
            f.flush(); os.fsync(f.fileno())
        os.replace(tmp, settings_path)
    except Exception:
        pass


def _apply_workdir_policy(workdir: str, policy: str) -> str:
    """Enforce safe handling of workdir to minimize risk of corrupting useful folders.

    Policies:
      - require_empty_existing (default): workdir must exist and be empty; else error
      - use_existing: workdir must exist (any contents) and will be used; else error (DANGEROUS; explicit opt-in)
      - create_new: workdir must not exist; it will be created; else error
      - create_or_empty: if exists, must be empty; if missing, create
    """
    p = os.path.abspath(workdir)
    exists = os.path.isdir(p)
    def is_empty(dir_path: str) -> bool:
        try:
            entries = [name for name in os.listdir(dir_path) if name not in {'.DS_Store'}]
            return len(entries) == 0
        except FileNotFoundError:
            return True
    if policy == "require_empty_existing":
        if not exists:
            raise RuntimeError(f"workdir does not exist: {p}")
        if not is_empty(p):
            raise RuntimeError(f"workdir is not empty: {p}")
        return p
    if policy == "use_existing":
        if not exists:
            raise RuntimeError(f"workdir does not exist: {p}")
        return p
    if policy == "create_new":
        if exists:
            raise RuntimeError(f"workdir already exists: {p}")
        os.makedirs(p, exist_ok=False)
        return p
    if policy == "create_or_empty":
        if exists:
            if not is_empty(p):
                raise RuntimeError(f"workdir is not empty: {p}")
            return p
        os.makedirs(p, exist_ok=True)
        return p
    # Fallback to safest
    raise RuntimeError(f"invalid workdir_policy: {policy}")

    # .mcp.json merging from template file
    mcp_path = os.path.join(workdir, ".mcp.json")
    template_path = os.path.join(os.path.dirname(__file__), ".mcp.json")
    try:
        with open(template_path, "r", encoding="utf-8") as f:
            tpl = json.load(f)
            if not isinstance(tpl, dict):
                return
    except Exception:
        return

    try:
        try:
            with open(mcp_path, "r", encoding="utf-8") as f:
                mcp_cfg = json.load(f)
                if not isinstance(mcp_cfg, dict):
                    mcp_cfg = {}
        except FileNotFoundError:
            mcp_cfg = {}
        # Deep-merge mcpServers
        servers = mcp_cfg.get("mcpServers")
        if not isinstance(servers, dict):
            servers = {}
            mcp_cfg["mcpServers"] = servers
        tpl_servers = tpl.get("mcpServers") or {}
        for name, cfg in tpl_servers.items():
            existing = servers.get(name)
            if not isinstance(existing, dict):
                servers[name] = cfg
            else:
                # merge keys (shallow)
                for k, v in cfg.items():
                    if k == "env":
                        env = existing.get("env")
                        if not isinstance(env, dict):
                            existing["env"] = dict(v)
                        else:
                            for ek, ev in v.items():
                                env.setdefault(ek, ev)
                    elif k == "args":
                        if "args" not in existing:
                            existing["args"] = list(v)
                    elif k == "command":
                        existing.setdefault("command", v)
                    else:
                        existing.setdefault(k, v)
        tmp = mcp_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(mcp_cfg, f, ensure_ascii=False, indent=2)
            f.flush(); os.fsync(f.fileno())
        os.replace(tmp, mcp_path)
    except Exception:
        pass


def _build_stop_hook_commands_for_creation(mode: str, calledagent: Optional[str], base_text: Optional[str]) -> List[Dict[str, Any]]:
    # Removed legacy variant. Intentionally unused.
    return []


def _build_stop_hook_commands_for_creation_strict(
    mode: str,
    calledagent: str,
    base_text: Optional[str],
    hooked_id: str,
) -> List[Dict[str, Any]]:
    """Strict variant: console scripts only; no %0 or env fallbacks."""
    commands: List[Dict[str, Any]] = []
    mcp_client = "claude-link-call"
    _server_quoted = "'claude-link'"
    if mode == "text":
        if base_text is None:
            raise RuntimeError("text is required when mode='text'")
        text_val = f"[msg from {hooked_id}] {str(base_text)}"
        payload = json.dumps({"target_id": calledagent, "text": text_val}, ensure_ascii=False)
        cmd = f"{mcp_client} --server {_server_quoted} --method inject_input_to --params '{payload}' --output text"
        commands.append({"type": "command", "command": cmd})
    else:
        t_json = json.dumps(calledagent)
        h_json = json.dumps(hooked_id)
        py = (
            "import json,subprocess; "
            "server='claude-link'; "
            "base=['claude-link-call','--server',server]; "
            f"tid={h_json}; "
            "r=subprocess.run(base+['--method','get_screenshot_from','--params',json.dumps({'target_id': tid}),'--output','result'],capture_output=True,text=True,check=True); "
            "res=json.loads(r.stdout); body=res['text'] if isinstance(res,dict) else str(res); "
            f"tgt={t_json}; "
            "prefix=f'[screenshot from ' + str(tid) + ']\\n'; "
            "params=json.dumps({'target_id': tgt, 'text': prefix + body}, ensure_ascii=False); "
            "subprocess.run(base+['--method','inject_input_to','--params',params,'--output','text'],check=True)"
        )
        commands.append({"type": "command", "command": f"python3 -c \"{py}\""})
    return commands


def _overwrite_settings_with_hooks(workdir: str, commands: List[Dict[str, Any]]) -> None:
    """Overwrite settings.local.json with enableAllProjectMcpServers and provided Stop hook commands."""
    claude_dir = os.path.join(workdir, ".claude")
    os.makedirs(claude_dir, exist_ok=True)
    settings_path = os.path.join(claude_dir, "settings.local.json")
    data = {
        "enableAllProjectMcpServers": True,
        "hooks": {
            "Stop": [
                {"hooks": commands}
            ]
        }
    }
    tmp = settings_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.flush(); os.fsync(f.fileno())
    os.replace(tmp, settings_path)


def _remove_claude_link_hooks(workdir: str) -> None:
    """Remove any Stop hook entries whose command contains 'claude-link'."""
    settings_path = os.path.join(workdir, ".claude", "settings.local.json")
    try:
        with open(settings_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            if not isinstance(data, dict):
                return
    except FileNotFoundError:
        return
    except Exception:
        return
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        return
    stop_list = hooks.get("Stop")
    if not isinstance(stop_list, list):
        return
    changed = False
    new_stop: List[Dict[str, Any]] = []
    for entry in stop_list:
        if not isinstance(entry, dict):
            new_stop.append(entry)
            continue
        inner = entry.get("hooks")
        if not isinstance(inner, list):
            new_stop.append(entry)
            continue
        filtered: List[Dict[str, Any]] = []
        for h in inner:
            if isinstance(h, dict) and isinstance(h.get("command"), str):
                if "claude-link" in h["command"]:
                    changed = True
                    continue
            filtered.append(h)
        if filtered:
            if len(filtered) != len(inner):
                changed = True
            new_entry = dict(entry)
            new_entry["hooks"] = filtered
            new_stop.append(new_entry)
        else:
            changed = True
            # drop empty entry
    if changed:
        hooks["Stop"] = new_stop
        tmp = settings_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush(); os.fsync(f.fileno())
        os.replace(tmp, settings_path)


def method_initialize(params: Dict[str, Any]) -> Any:
    return {
        "protocolVersion": "2024-11-05",
        "serverInfo": {"name": "claude-link", "version": "0.1.0"},
        "capabilities": {"tools": {}},
    }


def method_tools_list(params: Dict[str, Any]) -> Any:
    return {"tools": _tool_specs()}


def method_tools_call(params: Dict[str, Any]) -> Any:
    name = params.get("name")
    arguments = params.get("arguments") or {}
    if name not in _METHODS:
        raise RuntimeError(f"Unknown tool: {name}")
    result = _METHODS[name](arguments)
    # Return plain text content per Claude's expected schema
    return {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}]}


_MCP_METHODS = {
    "initialize": method_initialize,
    "tools/list": method_tools_list,
    "tools/call": method_tools_call,
}


def main() -> None:
    # Ensure data root exists
    get_runtime_root()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req: RpcRequest = json.loads(line)
            if req.get("jsonrpc") != "2.0":
                raise ValueError("Invalid jsonrpc version")
            method = req.get("method")
            has_id = "id" in req
            req_id = req.get("id")
            params = req.get("params", {}) or {}
            # Route MCP methods, else direct method
            func = _MCP_METHODS.get(method or "") or _METHODS.get(method or "")
            if func is None:
                # For notifications, ignore silently
                if has_id:
                    resp = _resp_err(req_id, -32601, f"Method not found: {method}")
                else:
                    continue
            else:
                try:
                    result = func(params)
                    if has_id:
                        resp = _resp_ok(req_id, result)
                    else:
                        # Notification; no response
                        continue
                except Exception as e:
                    if has_id:
                        resp = _resp_err(req_id, -32000, str(e))
                    else:
                        continue
        except Exception as e:
            # Parse error
            resp = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error", "data": str(e)}}
        sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
