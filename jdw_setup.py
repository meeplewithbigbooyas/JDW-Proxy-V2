#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
JDW Proxy V2 - Client auto-configuration
========================================
Detects installed AI clients and patches their configuration files so they
talk to the local JDW proxy instead of the broken upstream relay directly.

For every supported client we register:
  * a human-readable name,
  * the config file path (resolved from the user's home / APPDATA),
  * a `detect()` heuristic (is this client installed?),
  * a `patch(cfg_path, base_url, model, api_key_env, api_key)` function that
    merges a "jdw" provider into the existing config WITHOUT destroying the
    user's other settings (every file is backed up first).

The proxy exposes a tiny HTTP API around this module; the browser setup page
collects the user's numbered selection (e.g. "134") and calls apply_clients().

NOTE on secrets: we never write the JDW API key into shared/committed files
unless the client's own format requires an inline key. By default the key is
stored in an environment-variable reference and the UI tells the user they
must supply the key themselves.

All functions are defensive: a failure for one client never aborts the others.
"""

import io
import os
import json
import re
import sys
import time
import shutil
import subprocess
from typing import Any, Dict, List, Optional, Tuple

try:
    import yaml  # PyYAML - used for DeepSeek Harness profile patches
except Exception:  # pragma: no cover
    yaml = None


# --------------------------------------------------------------------------- #
# Paths / environment helpers
# --------------------------------------------------------------------------- #

HOME = os.path.expanduser("~")
APPDATA = os.environ.get("APPDATA") or os.path.join(HOME, "AppData", "Roaming")
LOCALAPPDATA = os.environ.get("LOCALAPPDATA") or os.path.join(HOME, "AppData", "Local")
IS_WINDOWS = os.name == "nt"

# The provider id/name written into every client config.
PROVIDER_ID = "jdw"
PROVIDER_DISPLAY = "JustDoWork (JDW Proxy)"
# Environment variable the clients will read the key from (so the raw key is
# not committed in plain config files for the clients that support env refs).
API_KEY_ENV = "JDW_API_KEY"


def _p(*parts: str) -> str:
    return os.path.normpath(os.path.join(*parts))


def _ensure_parent(path: str) -> None:
    d = os.path.dirname(path)
    if d and not os.path.isdir(d):
        os.makedirs(d, exist_ok=True)


def _backup(path: str) -> Optional[str]:
    """Copy an existing file next to itself as *.jdw-bak-<ts>. Returns bak path."""
    if not os.path.isfile(path):
        return None
    bak = f"{path}.jdw-bak-{time.strftime('%Y%m%d-%H%M%S')}"
    try:
        shutil.copy2(path, bak)
        return bak
    except Exception:
        return None


def _read_text(path: str) -> str:
    with io.open(path, "r", encoding="utf-8") as f:
        return f.read()


def _write_text(path: str, text: str) -> None:
    _ensure_parent(path)
    with io.open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def _read_json_lenient(path: str) -> Dict[str, Any]:
    """Read a .json / .jsonc file, tolerating // and /* */ comments and
    trailing commas (OpenCode uses .jsonc). Returns {} if the file is
    missing or unparseable."""
    if not os.path.isfile(path):
        return {}
    try:
        raw = _read_text(path)
    except Exception:
        return {}
    return _parse_jsonc(raw)


def _parse_jsonc(raw: str) -> Dict[str, Any]:
    if not raw or not raw.strip():
        return {}
    try:
        return json.loads(raw)
    except Exception:
        pass
    # strip /* */ block comments
    s = re.sub(r"/\*.*?\*/", "", raw, flags=re.DOTALL)
    # strip // line comments (not inside strings - best effort)
    out_lines = []
    for line in s.splitlines():
        in_str = False
        esc = False
        cut = None
        for i, ch in enumerate(line):
            if esc:
                esc = False
                continue
            if ch == "\\":
                esc = True
                continue
            if ch == '"':
                in_str = not in_str
                continue
            if ch == "/" and not in_str and i + 1 < len(line) and line[i + 1] == "/":
                cut = i
                break
        out_lines.append(line if cut is None else line[:cut])
    s = "\n".join(out_lines)
    # remove trailing commas
    s = re.sub(r",(\s*[}\]])", r"\1", s)
    try:
        return json.loads(s)
    except Exception:
        return {}


# --------------------------------------------------------------------------- #
# TOML (Codex) - minimal reader via tomllib + hand-rolled merge writer
# --------------------------------------------------------------------------- #

_CODEX_BEGIN = "# >>> JDW Proxy provider (managed by JDW setup) >>>"
_CODEX_END = "# <<< JDW Proxy provider (managed by JDW setup) <<<"


def _strip_marked_block(text: str, begin: str, end: str) -> str:
    """Remove a previously written block delimited by begin/end sentinel lines
    (inclusive). Safe no-op if the markers are absent."""
    if begin not in text:
        return text
    out: List[str] = []
    skipping = False
    for line in text.splitlines():
        if line.strip() == begin.strip():
            skipping = True
            continue
        if skipping and line.strip() == end.strip():
            skipping = False
            continue
        if not skipping:
            out.append(line)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out))


def _toml_quote(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _strip_toml_table(text: str, header: str) -> str:
    """Remove an existing [header] table (and its body up to the next
    top-level/other table header) from a TOML document. Used so we can
    re-write the jdw provider cleanly without duplicating it."""
    lines = text.splitlines()
    out: List[str] = []
    skipping = False
    hdr_re = re.compile(r"^\s*\[\[?\s*(.+?)\s*\]\]?\s*$")
    target = header.strip()
    for line in lines:
        m = hdr_re.match(line)
        if m:
            cur = m.group(1).strip()
            if cur == target:
                skipping = True
                continue
            else:
                skipping = False
        if not skipping:
            out.append(line)
    # collapse 3+ blank lines
    txt = "\n".join(out)
    txt = re.sub(r"\n{3,}", "\n\n", txt)
    return txt


# --------------------------------------------------------------------------- #
# Per-client PATCH functions
# Each returns a dict: {ok, path, backup, note, needs_admin}
# --------------------------------------------------------------------------- #

def _result(ok: bool, path: str, backup: Optional[str] = None,
            note: str = "", needs_admin: bool = False) -> Dict[str, Any]:
    return {"ok": ok, "path": path, "backup": backup, "note": note,
            "needs_admin": needs_admin}


def _write_guarded(path: str, text: str) -> Tuple[bool, bool, str]:
    """Try to write; return (ok, needs_admin, note).
    needs_admin is True when the failure looks like a permission problem."""
    try:
        _ensure_parent(path)
        bak = _backup(path)
        _write_text(path, text)
        return True, False, (f"backup: {os.path.basename(bak)}" if bak else "created")
    except PermissionError as e:
        return False, True, f"permission denied: {e}"
    except OSError as e:
        # EACCES / EPERM style
        needs = getattr(e, "errno", None) in (1, 13)
        return False, needs, f"write failed: {e}"
    except Exception as e:
        return False, False, f"write failed: {e}"


# ---- Claude Code -----------------------------------------------------------

def patch_claude_code(base_url: str, model: str, api_key: str) -> Dict[str, Any]:
    """~/.claude/settings.json -> env.ANTHROPIC_BASE_URL + auth token + model.
    Claude Code reads ANTHROPIC_BASE_URL / ANTHROPIC_AUTH_TOKEN from the `env`
    block of settings.json. We also set ANTHROPIC_MODEL so the proxy model is
    used by default."""
    path = _p(HOME, ".claude", "settings.json")
    data = _read_json_lenient(path)
    if not isinstance(data, dict):
        data = {}
    env = data.get("env")
    if not isinstance(env, dict):
        env = {}
    env["ANTHROPIC_BASE_URL"] = base_url
    # Claude Code accepts an inline auth token; it is required for the client
    # to send the key, so we must write it (the UI warns the user about this).
    if api_key:
        env["ANTHROPIC_AUTH_TOKEN"] = api_key
    env["ANTHROPIC_MODEL"] = model
    env.setdefault("CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC", "1")
    data["env"] = env
    text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    ok, needs, note = _write_guarded(path, text)
    return _result(ok, path, note=note, needs_admin=needs)


# ---- Claude Desktop --------------------------------------------------------

def patch_claude_desktop(base_url: str, model: str, api_key: str) -> Dict[str, Any]:
    """Claude Desktop (%APPDATA%\\Claude\\claude_desktop_config.json).
    The desktop app has no public custom-base-URL field; the file is used for
    MCP servers. We register the JDW proxy as an MCP-style launch entry that
    exposes the proxy env to any MCP tooling, and record the endpoint so the
    user can wire it manually. This is best-effort / informational."""
    path = _p(APPDATA, "Claude", "claude_desktop_config.json")
    data = _read_json_lenient(path)
    if not isinstance(data, dict):
        data = {}
    servers = data.get("mcpServers")
    if not isinstance(servers, dict):
        servers = {}
    servers[PROVIDER_ID] = {
        "command": "npx",
        "args": ["-y", "@anthropic-ai/mcp-server-fetch"],
        "env": {
            "ANTHROPIC_BASE_URL": base_url,
            "ANTHROPIC_AUTH_TOKEN": api_key or "",
            "ANTHROPIC_MODEL": model,
        },
    }
    data["mcpServers"] = servers
    text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    ok, needs, note = _write_guarded(path, text)
    extra = "Claude Desktop has no native custom-provider field; endpoint stored for manual use."
    return _result(ok, path, note=(note + " | " + extra), needs_admin=needs)


# ---- ChatGPT Codex (CLI) ---------------------------------------------------

def patch_codex(base_url: str, model: str, api_key: str) -> Dict[str, Any]:
    """~/.codex/config.toml -> [model_providers.jdw] + default provider.
    Codex talks the OpenAI protocol; the JDW proxy also serves an Anthropic
    endpoint, so we point Codex's provider base_url at the proxy root and let
    the proxy translate. Key is read from the JDW_API_KEY env var."""
    path = _p(HOME, ".codex", "config.toml")
    base_root = base_url.rstrip("/")
    # Codex expects an OpenAI-style base ending in /v1.
    if not base_root.endswith("/v1"):
        base_root = base_root + "/v1"
    existing = ""
    if os.path.isfile(path):
        try:
            existing = _read_text(path)
        except Exception:
            existing = ""
    # Remove any previous JDW-managed block (delimited by sentinel markers) so
    # re-running setup never duplicates keys/tables.
    cleaned = _strip_marked_block(existing, _CODEX_BEGIN, _CODEX_END)
    # Also drop stray prior jdw tables from older versions (no markers).
    cleaned = _strip_toml_table(cleaned, f"model_providers.{PROVIDER_ID}")
    cleaned_lines = []
    for ln in cleaned.splitlines():
        s = ln.strip()
        if s.startswith("model_provider") and PROVIDER_ID in s:
            continue
        cleaned_lines.append(ln)
    cleaned = "\n".join(cleaned_lines).strip()

    block = (
        f"{_CODEX_BEGIN}\n"
        f"model_provider = {_toml_quote(PROVIDER_ID)}\n"
        f"model = {_toml_quote(model)}\n"
        f"\n[model_providers.{PROVIDER_ID}]\n"
        f"name = {_toml_quote(PROVIDER_DISPLAY)}\n"
        f"base_url = {_toml_quote(base_root)}\n"
        f"env_key = {_toml_quote(API_KEY_ENV)}\n"
        f"wire_api = \"chat\"\n"
        f"{_CODEX_END}\n"
    )
    text = (cleaned + "\n\n" + block).lstrip("\n")
    if not text.endswith("\n"):
        text += "\n"
    ok, needs, note = _write_guarded(path, text)
    note += f" | set env {API_KEY_ENV} to your JDW key"
    return _result(ok, path, note=note, needs_admin=needs)


# ---- OpenCode --------------------------------------------------------------

def patch_opencode(base_url: str, model: str, api_key: str) -> Dict[str, Any]:
    """~/.config/opencode/opencode.json(c) -> provider.jdw (Anthropic SDK).
    Writes to the .jsonc file if it exists, otherwise opencode.json."""
    base = _p(HOME, ".config", "opencode")
    path_jsonc = _p(base, "opencode.jsonc")
    path_json = _p(base, "opencode.json")
    path = path_jsonc if os.path.isfile(path_jsonc) else (
        path_json if os.path.isfile(path_json) else path_jsonc)
    data = _read_json_lenient(path)
    if not isinstance(data, dict):
        data = {}
    data.setdefault("$schema", "https://opencode.ai/config.json")
    prov = data.get("provider")
    if not isinstance(prov, dict):
        prov = {}
    base_root = base_url.rstrip("/")
    # OpenCode's @ai-sdk/anthropic appends /v1/messages itself; give the root.
    if base_root.endswith("/v1"):
        base_root = base_root[:-3]
    prov[PROVIDER_ID] = {
        "name": PROVIDER_DISPLAY,
        "npm": "@ai-sdk/anthropic",
        "options": {
            "baseURL": base_root + "/v1",
            "apiKey": api_key or ("{env:" + API_KEY_ENV + "}"),
        },
        "models": {
            model: {"name": model},
        },
    }
    data["provider"] = prov
    text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    ok, needs, note = _write_guarded(path, text)
    return _result(ok, path, note=note, needs_admin=needs)


# ---- DeepSeek Harness ------------------------------------------------------

def patch_deepseek_harness(base_url: str, model: str, api_key: str) -> Dict[str, Any]:
    """~/.dsh/profiles/desktop/cordis.patch.yml -> llm-pi-ai provider jdw +
    default model. Also registers the key under refs in ~/.dsh/.credentials.yaml
    (as an env-style ref the profile points at)."""
    if yaml is None:
        return _result(False, _p(HOME, ".dsh"), note="PyYAML not installed")
    base = _p(HOME, ".dsh", "profiles", "desktop")
    path = _p(base, "cordis.patch.yml")
    if not os.path.isfile(path):
        return _result(False, path, note="DSH desktop profile not found")
    try:
        raw = _read_text(path)
        patches = yaml.safe_load(raw)
    except Exception as e:
        return _result(False, path, note=f"parse failed: {e}")
    if not isinstance(patches, list):
        patches = []

    base_root = base_url.rstrip("/")
    if base_root.endswith("/v1"):
        base_root = base_root[:-3]
    key_env = "JDW_PROXY_API_KEY"

    prov_entry = {
        "displayName": PROVIDER_DISPLAY,
        "apiKeyEnv": key_env,
        "api": "anthropic-messages",
        "baseURL": base_root,
        "models": [{"id": model, "name": model}],
    }

    def _find(pid: str) -> Optional[Dict[str, Any]]:
        for p in patches:
            if isinstance(p, dict) and p.get("id") == pid:
                return p
        return None

    # 1) llm-pi-ai providers.jdw
    pi = _find("llm-pi-ai")
    if pi is None:
        pi = {"id": "llm-pi-ai", "name": "@deepseek-ai/dsh-llm-pi-ai",
              "config": {"providers": {}}}
        patches.append(pi)
    pi.setdefault("config", {})
    pi["config"].setdefault("providers", {})
    pi["config"]["providers"][PROVIDER_ID] = prov_entry

    # 2) default model -> jdw
    dm = _find("agent-default-model")
    if dm is None:
        dm = {"id": "agent-default-model",
              "name": "@deepseek-ai/dsh-agent-default-model", "config": {}}
        patches.append(dm)
    dm.setdefault("config", {})
    dm["config"]["provider"] = PROVIDER_ID
    dm["config"]["model"] = model

    header = ("# Patched by JDW setup: added the 'jdw' provider pointing at the\n"
              "# local JDW proxy and made it the default agent model.\n")
    try:
        body = yaml.safe_dump(patches, sort_keys=False, allow_unicode=True,
                              default_flow_style=False)
    except Exception as e:
        return _result(False, path, note=f"serialize failed: {e}")
    ok, needs, note = _write_guarded(path, header + body)

    # 3) credentials ref (best-effort; don't fail the whole op)
    cred_note = ""
    cred_path = _p(HOME, ".dsh", ".credentials.yaml")
    try:
        cred = {}
        if os.path.isfile(cred_path):
            cred = yaml.safe_load(_read_text(cred_path)) or {}
        if not isinstance(cred, dict):
            cred = {}
        cred.setdefault("refs", {})
        if api_key:
            cred["refs"][key_env] = api_key
        else:
            cred["refs"].setdefault(key_env, "REPLACE_WITH_YOUR_JDW_KEY")
        _backup(cred_path)
        _write_text(cred_path, yaml.safe_dump(cred, sort_keys=False,
                    allow_unicode=True, default_flow_style=False))
        cred_note = f"key ref {key_env} written to .credentials.yaml"
    except Exception as e:
        cred_note = f"could not update credentials ({e}); set {key_env} manually"

    return _result(ok, path, note=(note + " | " + cred_note), needs_admin=needs)


# --------------------------------------------------------------------------- #
# Client registry
# --------------------------------------------------------------------------- #

def _claude_code_detect() -> bool:
    return (os.path.isdir(_p(HOME, ".claude"))
            or os.path.isfile(_p(HOME, ".claude.json"))
            or shutil.which("claude") is not None)


def _claude_desktop_detect() -> bool:
    return (os.path.isdir(_p(APPDATA, "Claude"))
            or os.path.isfile(_p(APPDATA, "Claude", "claude_desktop_config.json"))
            or os.path.isdir(_p(LOCALAPPDATA, "AnthropicClaude"))
            or os.path.isdir(_p(LOCALAPPDATA, "Programs", "claude")))


def _codex_detect() -> bool:
    return (os.path.isdir(_p(HOME, ".codex"))
            or shutil.which("codex") is not None)


def _opencode_detect() -> bool:
    base = _p(HOME, ".config", "opencode")
    return (os.path.isdir(base)
            or shutil.which("opencode") is not None)


def _dsh_detect() -> bool:
    return os.path.isdir(_p(HOME, ".dsh"))


# Ordered list -> the numbers shown to the user are 1-based indices here.
CLIENTS: List[Dict[str, Any]] = [
    {"key": "claude_code", "name": "Claude Code",
     "detect": _claude_code_detect, "patch": patch_claude_code,
     "config_hint": "~/.claude/settings.json",
     "inline_key": True},
    {"key": "claude_desktop", "name": "Claude Desktop",
     "detect": _claude_desktop_detect, "patch": patch_claude_desktop,
     "config_hint": "%APPDATA%/Claude/claude_desktop_config.json",
     "inline_key": True},
    {"key": "codex", "name": "ChatGPT Codex",
     "detect": _codex_detect, "patch": patch_codex,
     "config_hint": "~/.codex/config.toml",
     "inline_key": False},
    {"key": "opencode", "name": "OpenCode",
     "detect": _opencode_detect, "patch": patch_opencode,
     "config_hint": "~/.config/opencode/opencode.jsonc",
     "inline_key": False},
    {"key": "deepseek_harness", "name": "DeepSeek Harness",
     "detect": _dsh_detect, "patch": patch_deepseek_harness,
     "config_hint": "~/.dsh/profiles/desktop/cordis.patch.yml",
     "inline_key": False},
]


def list_clients() -> List[Dict[str, Any]]:
    """Return the numbered client list with live detection results."""
    out = []
    for i, c in enumerate(CLIENTS, start=1):
        try:
            installed = bool(c["detect"]())
        except Exception:
            installed = False
        out.append({
            "number": i,
            "key": c["key"],
            "name": c["name"],
            "installed": installed,
            "config_hint": c["config_hint"],
            "inline_key": c["inline_key"],
        })
    return out


def _resolve_selection(selection: Any) -> List[int]:
    """Accept '134', [1,3,4], '1,3,4', '1 3 4' -> [1,3,4] (valid indices)."""
    nums: List[int] = []
    if isinstance(selection, (list, tuple)):
        for x in selection:
            try:
                nums.append(int(x))
            except Exception:
                pass
    elif isinstance(selection, str):
        toks = re.split(r"[,\s]+", selection.strip())
        if len(toks) == 1 and toks[0].isdigit():
            # compact form "134" -> each digit (clients are single-digit indexed)
            if len(CLIENTS) < 10:
                nums = [int(ch) for ch in toks[0]]
            else:
                nums = [int(toks[0])]
        else:
            for t in toks:
                if t.isdigit():
                    nums.append(int(t))
    elif isinstance(selection, int):
        nums = [selection]
    # de-dup, keep order, keep only valid 1..N
    seen = set()
    valid = []
    for n in nums:
        if 1 <= n <= len(CLIENTS) and n not in seen:
            seen.add(n)
            valid.append(n)
    return valid


def apply_clients(selection: Any, base_url: str, model: str,
                  api_key: str = "") -> Dict[str, Any]:
    """Patch every selected client. Returns a per-client report. One failing
    client never stops the others."""
    chosen = _resolve_selection(selection)
    results: List[Dict[str, Any]] = []
    any_admin = False
    for n in chosen:
        c = CLIENTS[n - 1]
        entry = {"number": n, "key": c["key"], "name": c["name"]}
        try:
            r = c["patch"](base_url, model, api_key)
        except Exception as e:
            r = _result(False, c.get("config_hint", ""), note=f"exception: {e}")
        entry.update(r)
        if r.get("needs_admin"):
            any_admin = True
        results.append(entry)
    return {
        "selection": chosen,
        "results": results,
        "needs_admin": any_admin,
        "api_key_env": API_KEY_ENV,
    }


# --------------------------------------------------------------------------- #
# Administrator elevation (Windows) - re-run a single client patch elevated
# --------------------------------------------------------------------------- #

def run_elevated_apply(selection: Any, base_url: str, model: str,
                       api_key: str = "") -> Dict[str, Any]:
    """Re-launch this module with admin rights to retry writes that failed with
    a permission error. Uses PowerShell Start-Process -Verb RunAs on Windows
    (shows the UAC prompt). On non-Windows we just re-run apply directly."""
    chosen = _resolve_selection(selection)
    if not IS_WINDOWS:
        return apply_clients(chosen, base_url, model, api_key)

    payload = {
        "selection": chosen,
        "base_url": base_url,
        "model": model,
        "api_key": api_key,
    }
    tmp = _p(os.environ.get("TEMP", HOME),
             f"jdw-setup-{int(time.time())}.json")
    out = tmp + ".out"
    _write_text(tmp, json.dumps(payload, ensure_ascii=False))
    script = os.path.abspath(__file__)
    py = sys.executable
    # Child invocation: python jdw_setup.py --elevated <tmp> <out>
    ps = (
        "Start-Process -FilePath {py} "
        "-ArgumentList @('{script}','--elevated','{inp}','{outp}') "
        "-Verb RunAs -WindowStyle Hidden -Wait"
    ).format(
        py=py.replace("'", "''"),
        script=script.replace("'", "''"),
        inp=tmp.replace("'", "''"),
        outp=out.replace("'", "''"),
    )
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-Command", ps],
            check=True, capture_output=True, timeout=180,
        )
    except subprocess.CalledProcessError as e:
        return {"ok": False, "error": "elevation cancelled or failed",
                "detail": (e.stderr or b"").decode("utf-8", "ignore")[:400],
                "selection": chosen}
    except Exception as e:
        return {"ok": False, "error": f"elevation failed: {e}",
                "selection": chosen}
    # read the elevated child's result
    try:
        res = json.loads(_read_text(out))
    except Exception as e:
        res = {"ok": False, "error": f"no result from elevated run: {e}"}
    finally:
        for f in (tmp, out):
            try:
                os.remove(f)
            except Exception:
                pass
    return res


def _elevated_main(inp: str, outp: str) -> None:
    """Entry for the elevated child process."""
    try:
        data = json.loads(_read_text(inp))
        res = apply_clients(data.get("selection"), data.get("base_url", ""),
                            data.get("model", ""), data.get("api_key", ""))
        res["elevated"] = True
    except Exception as e:
        res = {"ok": False, "error": f"elevated apply failed: {e}"}
    try:
        _write_text(outp, json.dumps(res, ensure_ascii=False))
    except Exception:
        pass


if __name__ == "__main__":
    if len(sys.argv) >= 4 and sys.argv[1] == "--elevated":
        _elevated_main(sys.argv[2], sys.argv[3])
    else:
        # Simple CLI for manual testing / headless setup.
        print("JDW client setup - detected clients:")
        for c in list_clients():
            mark = "installed" if c["installed"] else "not found"
            print(f"  {c['number']}. {c['name']:<18} [{mark}]  {c['config_hint']}")
        print("\nRun the proxy and open the browser setup page to configure,")
        print("or import this module and call apply_clients('134', base_url, model).")
