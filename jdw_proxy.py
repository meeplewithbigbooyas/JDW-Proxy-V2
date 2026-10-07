#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
JDW Fix Proxy
=============
An Anthropic-compatible (`/v1/messages`) reverse proxy that sits in front of a
broken upstream relay (api.justwoker.icu, model `claude-opus-4-8`) and repairs
the protocol violations documented in JDW_PROTOCOL_AUDIT.md.

What the upstream does wrong (and what this proxy fixes):

  1. Streaming drops all text + tool_use blocks (only thinking deltas survive).
     -> We ALWAYS call the upstream in non-stream mode (where the full content
        is intact) and SYNTHESIZE a correct SSE stream for the client.

  2. Client-supplied `tools` / `tool_choice` are discarded; the model only knows
     its own injected tools (read_tabular / system_todo_write).
     -> We inject the client's tools into the bottom of the system prompt with a
        strict JSON "tool_call" output contract, then PARSE the model's text and
        REWRITE it into real Anthropic `tool_use` blocks (stop_reason=tool_use).

  3. max_tokens ignored (never returns stop_reason=max_tokens).
     -> We truncate output to max_tokens and set stop_reason correctly.

  4. stop_sequences ignored (stop word leaks into output).
     -> We cut the text before the stop sequence and report it.

  5. ~10.4k phantom context tokens inflate usage.
     -> We normalize usage (subtract the measured baseline).

  6. thinking blocks leak into content even when not requested.
     -> Optionally stripped.

  Plus: local /v1/messages/count_tokens, /v1/models passthrough, proper
  Anthropic-shaped errors, and a bilingual (EN/RU) web dashboard at `/`.

Run:  python jdw_proxy.py
"""

import json
import os
import re
import time
import uuid
import html as _html
import base64
import asyncio
import threading
from collections import deque
from urllib.parse import unquote, urlparse, parse_qs
from typing import Any, Dict, List, Optional, Tuple

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse, HTMLResponse

# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(HERE, "config.json")

DEFAULT_CONFIG: Dict[str, Any] = {
    "upstream_base_url": "https://api.justwoker.icu/v1",
    "api_key": "",
    # Optional extra keys tried automatically only if the primary key is
    # genuinely rejected (401/403) after its retries are exhausted.
    "fallback_api_keys": [],
    "listen_host": "127.0.0.1",
    "listen_port": 8181,
    "model": "claude-opus-4-8",
    "upstream_timeout_s": 90,
    "upstream_connect_timeout_s": 8,
    "upstream_write_timeout_s": 30,
    "upstream_pool_timeout_s": 5,
    "max_concurrent_upstream": 8,
    "retry_max_attempts": 3,
    "retry_backoff_s": [0.8, 2.0],
    "circuit_breaker_failures": 4,
    "circuit_breaker_cooldown_s": 20,
    "features": {
        "tool_injection": True,
        "strict_tool_names": True,
        # Keep a fenced/bare tool_call as TEXT when it is clearly documentation
        # (placeholder name like '...'/'tool_name', or preceded by example cues)
        # rather than a real call. Prevents the model's own explanations about
        # tool calls from being truncated into a broken tool_use block.
        "guard_tool_examples": True,
        # Remove the upstream harness's OWN tool JSON (system_todo_write /
        # read_tabular) when it leaks into visible text as a raw blob. A tool
        # the client actually offered under the same name is never stripped.
        "strip_harness_tools": True,
        "flatten_tool_history": True,
        "strip_thinking": True,
        "enforce_max_tokens": True,
        "enforce_stop_sequences": True,
        "usage_mode": "normalized",       # "normalized" | "raw" | "estimated"
        "usage_baseline_tokens": 10380,
        # --- Token-saving controls (reduce what is actually sent upstream) ---
        # Compact tool schemas: send a short description + field names only,
        # instead of the full JSON schema (saves thousands of tokens/request).
        "compact_tool_schemas": True,
        # Ultra-compact: one line per tool (name + fields only). Max tool saving.
        "ultra_compact_tools": True,
        # Keep only the last N messages of history (0 = unlimited / keep all).
        "max_history_messages": 0,
        # Truncate any single tool-result text longer than this many characters
        # before sending upstream (0 = no truncation).
        "max_tool_result_chars": 0,
        # Hard cap on the model's OUTPUT tokens per turn. When > 0 this OVERRIDES
        # whatever max_tokens the client sent (clamped to this value), capping
        # generation cost. 0 = OFF (honour the client's requested max_tokens).
        "max_output_tokens": 0,
        # SAFETY FLOOR for the upstream max_tokens budget. If a client asks for a
        # small max_tokens, the model can get cut off MID tool_call (the fenced
        # JSON is left unterminated -> can't be parsed into a tool_use, so the
        # call silently "stops half way"). We never send upstream less than this,
        # so long tool calls (e.g. system_todo_write) have room to finish. Our
        # own enforce_max_tokens step still trims pure-text replies afterwards.
        "min_tool_output_tokens": 8192,
        # --- Server-side tools (executed BY the proxy, not the client) ---
        # When on, the proxy injects its own tools (image fetch + internet
        # access), runs them itself, and feeds the results back to the model,
        # looping until the model gives a final answer. This is the master
        # switch; the three sub-flags below toggle each capability.
        "web_tools_enabled": True,
        # fetch_image: download an image URL (or scrape the image out of an
        # HTML share page) and hand it to the model as a real image block.
        "image_fetch_enabled": True,
        # web_search: query the web (DuckDuckGo HTML endpoint, no API key) and
        # return the top result titles + snippets + links.
        "web_search_enabled": True,
        # fetch_url: open an http(s) page and return its readable text content
        # (HTML stripped) so the model can read docs / articles / raw files.
        "web_fetch_enabled": True,
        # Max server-side tool round-trips per request (safety against loops).
        "web_tools_max_iters": 4,
    },
    "ui_lang": "en",
    # First-launch client setup wizard. When False, launching the proxy opens
    # the browser straight to the /setup page that configures AI clients.
    "setup_completed": False,
}

_config_lock = threading.RLock()
_UPSTREAM_SEMAPHORE = asyncio.Semaphore(8)
_CIRCUIT_LOCK = threading.Lock()
_CIRCUIT = {"failures": 0, "opened_at": 0.0, "last_error": "", "last_status": 0}
_HEALTH_CACHE = {"ts": 0.0, "ok": False, "status": 0, "latency_ms": None, "error": ""}


def _reset_runtime_limits() -> None:
    global _UPSTREAM_SEMAPHORE
    limit = max(1, int(CONFIG.get("max_concurrent_upstream", 8) or 8))
    _UPSTREAM_SEMAPHORE = asyncio.Semaphore(limit)


def _circuit_open() -> bool:
    with _CIRCUIT_LOCK:
        opened = float(_CIRCUIT.get("opened_at", 0) or 0)
        cooldown = float(CONFIG.get("circuit_breaker_cooldown_s", 20) or 20)
        return opened > 0 and (time.time() - opened) < cooldown


def _circuit_success() -> None:
    with _CIRCUIT_LOCK:
        _CIRCUIT.update({"failures": 0, "opened_at": 0.0, "last_error": "", "last_status": 0})


def _circuit_failure(status: int = 0, error: str = "") -> None:
    with _CIRCUIT_LOCK:
        _CIRCUIT["failures"] = int(_CIRCUIT.get("failures", 0)) + 1
        _CIRCUIT["last_status"] = status
        _CIRCUIT["last_error"] = str(error)[:300]
        threshold = max(1, int(CONFIG.get("circuit_breaker_failures", 4) or 4))
        if _CIRCUIT["failures"] >= threshold:
            _CIRCUIT["opened_at"] = time.time()



def load_config() -> Dict[str, Any]:
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))  # deep copy
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                disk = json.load(f)
            # shallow+features merge
            for k, v in disk.items():
                if k == "features" and isinstance(v, dict):
                    cfg["features"].update(v)
                else:
                    cfg[k] = v
        except Exception as e:
            print(f"[config] failed to read {CONFIG_PATH}: {e}")
    # Prefer an environment key when configured; this keeps secrets out of config.json.
    env_key = os.environ.get("JDW_API_KEY", "").strip()
    if env_key:
        cfg["api_key"] = env_key
    return cfg


def save_config(cfg: Dict[str, Any]) -> None:
    with _config_lock:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)


CONFIG = load_config()
_reset_runtime_limits()

# Opt-in debug: when JDW_DEBUG_TOOL_LEAK=1 the proxy scans the FINAL content it
# is about to return and logs a `tool_leak` event if a raw tool-call (an
# un-parsed ```tool_call fence, native <invoke> XML, or a truncated
# {"name": ...} object) leaked into a plain text block instead of becoming a
# real tool_use. This never alters the response -- it only records evidence so
# a real occurrence of the "raw tool_call shows up as text" bug can be caught.
DEBUG_TOOL_LEAK = os.environ.get("JDW_DEBUG_TOOL_LEAK") == "1"


# --------------------------------------------------------------------------- #
# In-memory request log (for the dashboard)
# --------------------------------------------------------------------------- #

LOG: "deque[Dict[str, Any]]" = deque(maxlen=200)
_log_lock = threading.Lock()


def log_event(entry: Dict[str, Any]) -> None:
    entry["ts"] = time.time()
    with _log_lock:
        LOG.appendleft(entry)


def _loads_tool_json(raw: str) -> Optional[Dict[str, Any]]:
    """Parse the JSON inside a tool_call fence as robustly as possible.

    The model very often emits tool arguments (edit old_string/new_string,
    write content, etc.) that contain *literal* newlines, tabs and other
    control characters INSIDE the JSON string values. Strict json.loads
    rejects those with 'Invalid control character', which used to make the
    whole tool_call fall back to being shown as a plain ```code``` block that
    never executed. We try progressively more lenient strategies:

      1. strict json.loads (fast path, already-valid JSON)
      2. json.loads(..., strict=False) -> allows literal control chars in
         strings (handles raw newlines/tabs in old_string/new_string/content)
      3. a last-ditch pass that escapes stray control chars then retries
    """
    if not raw:
        return None
    # 1) strict
    try:
        obj = json.loads(raw)
        return obj if isinstance(obj, dict) else None
    except Exception:
        pass
    # 2) lenient: permit literal control characters inside strings
    try:
        obj = json.loads(raw, strict=False)
        return obj if isinstance(obj, dict) else None
    except Exception:
        pass
    # 3) escape stray control chars (newline/tab/cr) that sit inside the JSON
    #    and retry. We only touch raw control bytes, never already-escaped
    #    sequences, so valid JSON is unchanged.
    fixed = (raw.replace("\r\n", "\\n")
                .replace("\n", "\\n")
                .replace("\r", "\\n")
                .replace("\t", "\\t"))
    try:
        obj = json.loads(fixed, strict=False)
        return obj if isinstance(obj, dict) else None
    except Exception:
        pass
    # 4) brace/bracket balancing: the model sometimes appends EXTRA closing
    #    brackets ('..."}]}]}') or leaves some open. Walk the string tracking
    #    {} and [] depth (while respecting quoted strings/escapes) and cut the
    #    text at the point the FIRST top-level object closes -> ignores any
    #    trailing junk. If it never closes, append the missing closers.
    for candidate in (raw, fixed):
        extracted = _extract_balanced_json(candidate)
        if extracted is not None:
            try:
                obj = json.loads(extracted, strict=False)
                if isinstance(obj, dict):
                    return obj
            except Exception:
                continue
    return None


def _extract_balanced_json(s: str) -> Optional[str]:
    """Return a cleaned, balanced {...} object extracted from `s`.

    Repairs two common model mistakes:
      * EXTRA / mismatched closing brackets (e.g. '..."}]}]}') -> stray
        closers that don't match the current open bracket are DROPPED, so the
        rebuilt object is valid JSON instead of over-closed junk.
      * MISSING closers -> appended in the right order at the end.
    String contents and escapes are respected so braces inside strings never
    affect the depth count.
    """
    start = s.find("{")
    if start < 0:
        return None
    pairs = {"}": "{", "]": "["}
    buf: List[str] = []
    stack: List[str] = []
    in_str = False
    esc = False
    for i in range(start, len(s)):
        ch = s[i]
        if in_str:
            buf.append(ch)
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
            buf.append(ch)
        elif ch in "{[":
            stack.append(ch)
            buf.append(ch)
        elif ch in "}]":
            if stack and stack[-1] == pairs[ch]:
                stack.pop()
                buf.append(ch)
                if not stack:
                    # root object fully + correctly closed -> done
                    return "".join(buf)
            else:
                # stray / mismatched closer -> drop it (the model's extra
                # ']' or '}' that caused the whole call to fail to parse)
                continue
        else:
            buf.append(ch)
    # never closed -> append the missing closers in reverse order
    if stack:
        for c in reversed(stack):
            buf.append("}" if c == "{" else "]")
        return "".join(buf)
    return None


# --------------------------------------------------------------------------- #
# Tool-injection: build the system-prompt addendum + parse tool_call blocks
# --------------------------------------------------------------------------- #

# Match a fenced block with ANY (or no) language label -- the model often
# tags a tool call with the SHELL language it is invoking (```powershell,
# ```bash, ```sh, ```pwsh) or a bare ``` fence, not just ```tool_call /
# ```json. We still only ACCEPT a block as a tool call later if its JSON
# body actually carries a "name" (+ optional "input"), so a real code block
# is never misread as a call.
TOOL_CALL_FENCE_RE = re.compile(
    r"```[A-Za-z0-9_+.\-]*[ \t]*\r?\n(\{.*?\})[ \t]*\r?\n?```",
    re.DOTALL,
)
# Fallback for a BARE JSON tool call emitted with no fence at all: a top-level
# object that contains a "name" key. Used only when no fenced call was found.
# We locate candidates by scanning for a '{' that starts a brace-balanced span
# (string-aware, so braces inside "command": "... { } ..." don't confuse it).
_NAME_HINT_RE = re.compile(r"\{\s*\"name\"\s*:")


def _iter_balanced_json_objects(text: str):
    """Yield (start, end, substring) for every brace-balanced {...} span in
    `text` that begins with a {"name": ... hint, honouring JSON string quoting
    and escapes so braces inside string values are ignored."""
    for hint in _NAME_HINT_RE.finditer(text):
        start = hint.start()
        depth = 0
        in_str = False
        esc = False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    yield start, i + 1, text[start:i + 1]
                    break

# The underlying model was trained on Anthropic's NATIVE XML tool-call syntax
# and frequently falls back to it (optionally with an `antml:` namespace),
# ignoring our ```tool_call fence instruction. It also sometimes MIXES the two
# (a JSON fence followed by stray </parameter></invoke> tags). We parse this
# form too so the tool ALWAYS executes instead of leaking as a code block.
INVOKE_XML_RE = re.compile(
    r"<(?:antml:)?invoke\s+name\s*=\s*\"([^\"]+)\"\s*>(.*?)</(?:antml:)?invoke>",
    re.DOTALL,
)
PARAM_XML_RE = re.compile(
    r"<(?:antml:)?parameter\s+name\s*=\s*\"([^\"]+)\"\s*>(.*?)</(?:antml:)?parameter>",
    re.DOTALL,
)
# Opening-tag matchers used by the tolerant parser. We deliberately do NOT
# require closing tags, because the model very often OMITS the closing
#  on the last parameter (which used to make that whole parameter
# -- e.g. file_path -- vanish and the tool fail with "missing required
# property").
INVOKE_NAME_RE = re.compile(r"<(?:antml:)?invoke\s+name\s*=\s*\"([^\"]+)\"\s*>")
PARAM_OPEN_RE = re.compile(r"<(?:antml:)?parameter\s+name\s*=\s*\"([^\"]+)\"\s*>")


def _coerce_param_value(raw: str) -> Any:
    """Convert an XML <parameter> text body into a Python value. Parameter
    bodies are always text; we try JSON first (so numbers/bools/arrays/objects
    arrive typed) and fall back to the raw string (the common case for code in
    old_string/new_string/content)."""
    s = raw.strip()
    if s and (s[0] in "[{" or s in ("true", "false", "null")
              or re.fullmatch(r"-?\d+(?:\.\d+)?", s)):
        try:
            return json.loads(s)
        except Exception:
            pass
    return raw


def _parse_invoke_xml(segment: str) -> Optional[Dict[str, Any]]:
    """Parse one <invoke name="X">...<parameter...>...</invoke> segment into
    {"name": X, "input": {...}}. Returns None if no name found."""
    nm = INVOKE_NAME_RE.search(segment)
    if not nm:
        return None
    name = nm.group(1).strip()
    body = segment[nm.end():]
    close = re.search(r"</(?:antml:)?invoke>", body)
    if close:
        body = body[:close.start()]
    inp: Dict[str, Any] = {}
    opens = list(PARAM_OPEN_RE.finditer(body))
    for i, pm in enumerate(opens):
        key = pm.group(1).strip()
        val_start = pm.end()
        val_end = opens[i + 1].start() if i + 1 < len(opens) else len(body)
        val = body[val_start:val_end]
        val = re.sub(r"</(?:antml:)?parameter>\s*$", "", val)
        val = re.sub(r"</(?:antml:)?invoke>\s*$", "", val)
        inp[key] = _coerce_param_value(val)
    return {"name": name, "input": inp}

TOOL_PREAMBLE_EN = (
    "## IMPORTANT — Tool access override\n"
    "The hosting provider misconfigured native tool support on this endpoint, so "
    "the normal Anthropic `tools` mechanism is not delivered to you. A compatibility "
    "proxy restores it for you here. You DO have access to the tools listed below, "
    "and you MUST use them when the task requires it.\n\n"
    "To call a tool, emit a fenced code block tagged `tool_call` containing a single "
    "JSON object with exactly two keys: `name` (the tool name) and `input` (the "
    "arguments object matching that tool's JSON schema). Example:\n\n"
    "```tool_call\n"
    "{\"name\": \"read_file\", \"input\": {\"path\": \"src/app.ts\"}}\n"
    "```\n\n"
    "Rules:\n"
    "- Emit the tool_call block and then STOP; do not predict the tool's result.\n"
    "- You may write a short sentence before the block explaining what you are about to do.\n"
    "- Use ONE tool_call block per turn unless the task clearly needs several.\n"
    "- The `input` must be valid JSON and conform to the tool's schema.\n"
    "- Call tools ONLY by a name that appears in the list below. Never invent a\n"
    "  tool name or call one that is not listed; if a capability is not listed,\n"
    "  solve the task without a tool.\n\n"
    "If the user attaches images (e.g. a screenshot), you can see them directly; "
    "describe or act on their visual content as needed.\n\n"
    "If an image tool (e.g. `fetch_image`) is listed below, you CAN fetch an image "
    "by URL and see it directly. Use it whenever you need to look at an image at a "
    "specific URL.\n\n"
    "### Available tools\n"
)

TOOL_PREAMBLE_FORCED_EN = (
    "\nThe caller REQUIRES you to call the tool named `{name}` on this turn. "
    "Respond with exactly one `tool_call` block for `{name}` and nothing else.\n"
)


def _compact_schema_fields(schema: Dict[str, Any], depth: int = 1) -> str:
    """Render a tool schema as a short one-line field summary instead of the
    full JSON schema. Keeps names + types + required flags, drops verbose
    descriptions -> massive token savings.

    `depth` controls how far nested object/array<object> fields are expanded
    inline (depth=1 expands one level, so array<object> shows its sub-fields
    like questions*:array<{id*,question*,options}>). Expanding one level is
    what keeps tool CALLS correct under ultra-compact: the model can see the
    shape of nested arguments instead of guessing.
    """
    if not isinstance(schema, dict):
        return ""
    props = schema.get("properties")
    if not isinstance(props, dict) or not props:
        return ""
    required = set(schema.get("required") or [])
    parts = []
    for pname, pdef in props.items():
        ptype = ""
        sub = ""
        if isinstance(pdef, dict):
            ptype = pdef.get("type") or ""
            if ptype == "array" and isinstance(pdef.get("items"), dict):
                items = pdef["items"]
                it = items.get("type") or ""
                if it == "object" and depth > 0:
                    inner = _compact_schema_fields(items, depth - 1)
                    sub = f"array<{{{inner}}}>" if inner else "array<object>"
                else:
                    sub = f"array<{it}>" if it else "array"
                ptype = sub
            elif ptype == "object" and depth > 0:
                inner = _compact_schema_fields(pdef, depth - 1)
                ptype = f"object{{{inner}}}" if inner else "object"
        star = "*" if pname in required else ""
        parts.append(f"{pname}{star}:{ptype}" if ptype else f"{pname}{star}")
    return ", ".join(parts)


def _tool_capability_hint(name: str, desc: str = "") -> str:
    """Return a SHORT, concrete capability note tailored to THIS tool, keyed off
    its name/description. Tells the model, in plain terms, the useful things it
    can actually do with the tool (e.g. 'with me you can push to GitHub') instead
    of a generic blurb. Empty string if we have no specific hint."""
    n = (name or "").lower()
    d = (desc or "").lower()
    def has(*subs: str) -> bool:
        return any(s in n or s in d for s in subs)
    # Shell / command runners
    if has("pwsh", "powershell", "bash", "shell", "terminal", "command", "exec", "run_command"):
        return ("With me you run real commands on this machine. You CAN: `git add/commit/push` "
                "to upload a project to GitHub, install packages, move/zip files, start servers. "
                "Put the WHOLE command (even multi-line git here-strings) in `input` and CALL me — "
                "never just print the command as a code block.")
    # Image fetch / vision by URL
    if has("fetch_image", "image", "screenshot", "vision", "img"):
        return ("With me you download an image from a URL and SEE it directly, so you can read "
                "text in it, describe it, or compare screenshots.")
    # URL / web fetch
    if has("fetch_url", "web_fetch", "open_url", "browse", "http_get"):
        return ("With me you open a URL and read its page content as text — use me to pull docs, "
                "API responses, or a raw file off the web.")
    # Web search
    if has("web_search", "search"):
        return ("With me you search the web for current info and get back result links/snippets "
                "to fetch next.")
    # Write / create files
    if has("write", "create_file", "new_file"):
        return ("With me you create or fully overwrite a file on disk — pass the full final "
                "content, not a diff.")
    # Edit files
    if has("edit", "replace", "patch", "apply_diff"):
        return ("With me you edit an existing file by replacing exact text — read it first so "
                "your old-text match is precise.")
    # Read files
    if has("read", "cat", "open_file", "view"):
        return ("With me you read a file's exact contents from disk before changing or quoting it.")
    # Search in files
    if has("grep", "ripgrep", "find_in"):
        return ("With me you search file CONTENTS by regex to locate code/text fast.")
    # Glob / find files
    if has("glob", "find", "list_files"):
        return ("With me you find files by path pattern (e.g. **/*.py).")
    return ""


def build_tool_system_block(tools: List[Dict[str, Any]],
                            tool_choice: Optional[Dict[str, Any]],
                            compact: bool = False,
                            ultra: bool = False) -> str:
    lines = [TOOL_PREAMBLE_EN]
    for t in tools:
        name = t.get("name", "?")
        desc = t.get("description", "") or ""
        schema = t.get("input_schema") or t.get("inputSchema") or {}
        if ultra:
            # Ultra-compact: one line per tool, name + field names only.
            fields = _compact_schema_fields(schema)
            short = desc.strip().split("\n", 1)[0][:80]
            line = f"- `{name}`"
            if short:
                line += f": {short}"
            if fields:
                line += f"  [{fields}]"
            hint = _tool_capability_hint(name, desc)
            if hint:
                line += f"\n    → {hint}"
            lines.append(line + "\n")
        elif compact:
            # short description (first line / first ~200 chars) + field summary
            short = desc.strip().split("\n", 1)[0][:200]
            fields = _compact_schema_fields(schema)
            lines.append(f"\n#### `{name}`\n{short}\n")
            hint = _tool_capability_hint(name, desc)
            if hint:
                lines.append(f"What you can do with it: {hint}\n")
            if fields:
                lines.append(f"Params ( * = required ): {fields}\n")
        else:
            lines.append(f"\n#### `{name}`\n{desc}\n")
            hint = _tool_capability_hint(name, desc)
            if hint:
                lines.append(f"What you can do with it: {hint}\n")
            try:
                schema_str = json.dumps(schema, ensure_ascii=False, indent=2)
            except Exception:
                schema_str = str(schema)
            lines.append(f"Input JSON schema:\n```json\n{schema_str}\n```\n")

    # Make the closed set of valid names explicit so the model can't invent one.
    valid_names = [t.get("name") for t in tools if t.get("name")]
    if valid_names:
        lines.append(
            "\n### Valid tool names (the ONLY names you may call)\n"
            + ", ".join(f"`{n}`" for n in valid_names)
            + "\n"
        )

    block = "".join(lines)

    if tool_choice:
        ctype = tool_choice.get("type")
        if ctype == "tool" and tool_choice.get("name"):
            block += TOOL_PREAMBLE_FORCED_EN.format(name=tool_choice["name"])
        elif ctype == "any":
            block += ("\nThe caller requires you to call one of the available tools "
                      "on this turn. Respond with a `tool_call` block.\n")
    return block


def extract_system_text(system: Any) -> str:
    """Anthropic `system` may be a string or a list of blocks."""
    if system is None:
        return ""
    if isinstance(system, str):
        return system
    if isinstance(system, list):
        parts = []
        for b in system:
            if isinstance(b, dict) and b.get("type") == "text":
                parts.append(b.get("text", ""))
            elif isinstance(b, str):
                parts.append(b)
        return "\n".join(parts)
    return str(system)


# --------------------------------------------------------------------------- #
# History flattening: turn tool_use / tool_result blocks into text the upstream
# model can actually read (since it never saw the real tool protocol).
# --------------------------------------------------------------------------- #

def flatten_content_blocks(content: Any, role: str,
                           max_tool_result_chars: int = 0) -> str:
    """Convert a message's content (string or block list) into plain text,
    rendering tool_use / tool_result into the JSON contract format.

    Tool-result text longer than max_tool_result_chars (when > 0) is truncated
    with a short marker, to cap upstream token cost from huge tool outputs."""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return str(content)

    parts: List[str] = []
    for b in content:
        if not isinstance(b, dict):
            parts.append(str(b))
            continue
        btype = b.get("type")
        if btype == "text":
            parts.append(b.get("text", ""))
        elif btype == "thinking":
            # don't feed stale thinking back
            continue
        elif btype == "tool_use":
            payload = {"name": b.get("name"), "input": b.get("input", {})}
            parts.append(
                "```tool_call\n" + json.dumps(payload, ensure_ascii=False) + "\n```"
            )
        elif btype == "tool_result":
            tcid = b.get("tool_use_id", "")
            inner = b.get("content", "")
            if isinstance(inner, list):
                texts = []
                for ib in inner:
                    if isinstance(ib, dict):
                        if ib.get("type") == "text":
                            texts.append(ib.get("text", ""))
                        elif ib.get("type") == "image":
                            texts.append("[image]")
                    else:
                        texts.append(str(ib))
                inner = "\n".join(texts)
            is_err = b.get("is_error")
            tag = "TOOL ERROR" if is_err else "TOOL RESULT"
            # Cap huge tool outputs to save upstream tokens.
            if (max_tool_result_chars and isinstance(inner, str)
                    and len(inner) > max_tool_result_chars):
                keep = max_tool_result_chars
                omitted = len(inner) - keep
                inner = (inner[:keep]
                         + f"\n...[truncated {omitted} chars to save tokens]")
            parts.append(f"[{tag} for {tcid}]\n{inner}")
        elif btype == "image":
            # keep images as proper blocks elsewhere; here mark presence
            parts.append("[image]")
        else:
            parts.append(json.dumps(b, ensure_ascii=False))
    return "\n\n".join(p for p in parts if p != "")


def _block_has_image(b: Any) -> bool:
    """True if a single content block is an image, or a tool_result that carries
    one or more nested image blocks."""
    if not isinstance(b, dict):
        return False
    if b.get("type") == "image":
        return True
    if b.get("type") == "tool_result" and isinstance(b.get("content"), list):
        return any(isinstance(ib, dict) and ib.get("type") == "image"
                   for ib in b["content"])
    return False


def message_has_image(content: Any) -> bool:
    if isinstance(content, list):
        return any(_block_has_image(b) for b in content)
    return False


def flatten_messages(messages: List[Dict[str, Any]],
                     flatten_tools: bool,
                     max_tool_result_chars: int = 0) -> List[Dict[str, Any]]:
    """Produce an upstream-friendly messages array.

    If a message contains images we KEEP the structured block list (upstream
    supports image blocks per the audit), but still rewrite tool blocks to text.
    Otherwise we collapse to a plain string.
    """
    out: List[Dict[str, Any]] = []
    for m in messages:
        role = m.get("role", "user")
        content = m.get("content")

        if not flatten_tools:
            out.append({"role": role, "content": content})
            continue

        if message_has_image(content) and isinstance(content, list):
            new_blocks = []
            for b in content:
                if isinstance(b, dict) and b.get("type") == "image":
                    new_blocks.append(b)
                elif isinstance(b, dict) and b.get("type") == "tool_result" \
                        and _block_has_image(b):
                    # Split a tool_result that carries image(s): keep the text
                    # part as a flattened text block, then pass the image
                    # block(s) through untouched so the model actually sees
                    # them (previously they were dropped as the literal
                    # string "[image]").
                    inner = b.get("content") or []
                    text_parts = []
                    image_blocks = []
                    for ib in inner:
                        if isinstance(ib, dict) and ib.get("type") == "image":
                            image_blocks.append(ib)
                        elif isinstance(ib, dict) and ib.get("type") == "text":
                            text_parts.append(ib.get("text", ""))
                        else:
                            text_parts.append(str(ib))
                    tcid = b.get("tool_use_id", "")
                    tag = "TOOL ERROR" if b.get("is_error") else "TOOL RESULT"
                    head = "\n".join(p for p in text_parts if p)
                    label = f"[{tag} for {tcid}]" + (f"\n{head}" if head else "")
                    new_blocks.append({"type": "text", "text": label})
                    new_blocks.extend(image_blocks)
                else:
                    txt = flatten_content_blocks([b], role, max_tool_result_chars)
                    if txt:
                        new_blocks.append({"type": "text", "text": txt})
            out.append({"role": role, "content": new_blocks})
        else:
            out.append({"role": role,
                        "content": flatten_content_blocks(content, role,
                                                           max_tool_result_chars)})
    return out


def _msg_is_empty(content: Any) -> bool:
    """True if a message has no meaningful content (empty string / no blocks)."""
    if content is None:
        return True
    if isinstance(content, str):
        return content.strip() == ""
    if isinstance(content, list):
        for b in content:
            if isinstance(b, dict):
                if b.get("type") == "image":
                    return False
                if (b.get("text") or "").strip():
                    return False
            elif str(b).strip():
                return False
        return True
    return False


def _merge_two(a: Any, b: Any) -> Any:
    """Merge the content of two same-role messages into one."""
    if isinstance(a, list) or isinstance(b, list):
        la = a if isinstance(a, list) else [{"type": "text", "text": str(a)}]
        lb = b if isinstance(b, list) else [{"type": "text", "text": str(b)}]
        return la + lb
    return (str(a) + "\n\n" + str(b)).strip()


def normalize_alternating(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Make a messages array satisfy the upstream's strict contract:
      1. no empty messages,
      2. the list STARTS with a 'user' message,
      3. roles strictly ALTERNATE user/assistant (consecutive same-role
         messages are merged into one).
    This is what prevents the recurring 400 'messages must have alternating
    user and assistant roles / message 0 is not a user message' error after
    history trimming.
    """
    # 1) drop empties
    cleaned = [m for m in messages if not _msg_is_empty(m.get("content"))]
    # 2) drop any leading non-user turns (orphaned assistant / tool_result)
    while cleaned and cleaned[0].get("role") != "user":
        cleaned.pop(0)
    # 3) merge consecutive same-role messages
    merged: List[Dict[str, Any]] = []
    for m in cleaned:
        role = m.get("role", "user")
        if merged and merged[-1].get("role") == role:
            merged[-1]["content"] = _merge_two(merged[-1].get("content"),
                                                m.get("content"))
        else:
            merged.append({"role": role, "content": m.get("content")})
    # 4) a trailing assistant message would leave the model nothing to answer;
    #    it's allowed by the API, so we keep it. But guarantee non-empty list.
    return merged


# --------------------------------------------------------------------------- #
# Parse the model's text output -> real content blocks (text + tool_use)
# --------------------------------------------------------------------------- #

# Placeholder "names" the model writes when it is DOCUMENTING a tool call in
# prose ("```tool_call {\"name\": \"...\"}") rather than actually calling one.
# A real tool is never named any of these, so they must never become a
# tool_use block -- doing so truncates the explanation at the fence.
_PLACEHOLDER_TOOL_NAMES = {
    "", "...", "name", "tool", "tool_name", "toolname", "your_tool",
    "tool_here", "example", "example_tool", "some_tool", "the_tool",
    "read_file",  # the literal name used in OUR OWN preamble example
}
_VALID_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.\-]*$")

# Prose cues that a following fence is an EXAMPLE / explanation, not a call.
# Checked only against the short run of text immediately before the fence.
_EXAMPLE_CUE_RE = re.compile(
    r"(?:example|for instance|e\.g\.|looks? like|such as|like this|"
    r"syntax|format|the preamble|documentation|illustrat|placeholder|"
    r"пример|например|выглядит|синтаксис|формат|вид[ае])",
    re.IGNORECASE,
)


def _is_plausible_tool_name(name: Optional[str]) -> bool:
    """True only if `name` could be a REAL tool name (not a prose placeholder
    like '...' or 'tool_name', and a syntactically valid identifier)."""
    if not name or not isinstance(name, str):
        return False
    if name.strip().lower() in _PLACEHOLDER_TOOL_NAMES:
        return False
    return bool(_VALID_NAME_RE.match(name.strip()))


def _looks_like_example_context(preceding_text: str) -> bool:
    """True if the text right before a fence signals an example/explanation
    (so the fence is documentation, not an actual tool call)."""
    if not preceding_text:
        return False
    tail = preceding_text[-160:]
    return bool(_EXAMPLE_CUE_RE.search(tail))


def parse_tool_calls_from_text(text: str,
                               valid_names: Optional[set] = None,
                               strict: bool = True,
                               guard_examples: bool = True) -> List[Dict[str, Any]]:
    """Split raw model text into Anthropic content blocks, converting fenced
    tool_call JSON into tool_use blocks.

    If `strict` is True and `valid_names` is provided, a tool_call whose name
    is NOT in valid_names is left as plain text instead of being emitted as a
    tool_use block. This prevents the client from ever receiving an
    "unknown tool" call (the model occasionally invents or mistypes a name).
    """
    # --- NORMALIZE native XML tool calls into ```tool_call fences --------- #
    # The model frequently emits Anthropic's native <invoke>/<parameter> XML
    # instead of (or mixed with) our fence. Rewrite every complete <invoke>
    # block into an equivalent tool_call fence BEFORE the main parse, so a
    # single code path handles everything.
    if "invoke" in text and "<" in text:
        openers = list(INVOKE_NAME_RE.finditer(text))
        if openers:
            rebuilt: List[str] = []
            cursor = 0
            for i, om in enumerate(openers):
                rebuilt.append(text[cursor:om.start()])
                seg_end = openers[i + 1].start() if i + 1 < len(openers) else len(text)
                segment = text[om.start():seg_end]
                obj = _parse_invoke_xml(segment)
                if obj:
                    try:
                        js = json.dumps(obj, ensure_ascii=False)
                        rebuilt.append("\n```tool_call\n" + js + "\n```\n")
                    except Exception:
                        rebuilt.append(segment)
                else:
                    rebuilt.append(segment)
                cursor = seg_end
            rebuilt.append(text[cursor:])
            text = "".join(rebuilt)
    # Strip any ORPHAN closing tags left behind when the model mixed a JSON
    # fence with trailing </parameter></invoke> noise (the exact corruption
    # that made calls leak as code blocks).
    if "</" in text:
        text = re.sub(r"</(?:antml:)?(?:parameter|invoke)>", "", text)
        text = re.sub(r"<(?:antml:)?(?:parameter|invoke)\b[^>]*>", "", text)

    blocks: List[Dict[str, Any]] = []
    last = 0
    _match_count = 0
    for m in TOOL_CALL_FENCE_RE.finditer(text):
        _match_count += 1
        pre = text[last:m.start()].strip()
        raw = m.group(1).strip()
        parsed = _loads_tool_json(raw)
        name = parsed.get("name") if isinstance(parsed, dict) else None
        # Only treat as a tool call if it has a name key AND (when strict) the
        # name is one the client actually offered.
        name_ok = bool(name)
        if strict and valid_names is not None:
            name_ok = name in valid_names
        # GUARD 1: a prose placeholder name ('...', 'tool_name', etc.) is never
        # a real call -> keep as text. Skip this guard when the name IS a known
        # offered tool (so a real tool that happens to collide is still called).
        is_offered = bool(valid_names) and name in valid_names
        if guard_examples and not is_offered and not _is_plausible_tool_name(name):
            name_ok = False
        # GUARD 2: the fence is preceded by example/explanation cues AND its name
        # isn't an offered tool -> it's documentation, keep as text. A genuine
        # call to an offered tool is never blocked by this.
        if (guard_examples and name_ok and not is_offered
                and _looks_like_example_context(text[last:m.start()])):
            name_ok = False
            log_event({"tool_example_guard": True, "name_seen": name,
                       "note": "fence looks like a documented example -> kept as text"})
        if isinstance(parsed, dict) and name_ok:
            if pre:
                blocks.append({"type": "text", "text": pre})
            tool_input = parsed.get("input", parsed.get("arguments", {}))
            if not isinstance(tool_input, dict):
                tool_input = {}
            blocks.append({
                "type": "tool_use",
                "id": "toolu_" + uuid.uuid4().hex[:24],
                "name": name,
                "input": tool_input,
            })
            last = m.end()
        elif isinstance(parsed, dict) and name and not name_ok:
            # Unknown tool name -> keep the whole fenced block as literal text
            # so the client never sees an invalid tool_use. Log it for the admin.
            log_event({"unknown_tool": name,
                       "note": "model called an unlisted tool -> kept as text"})
            # leave `last` as-is so the fence text falls into the tail/pre of
            # the next iteration; advance only past the preamble text we consumed
        # else: not a tool call -> leave as text (handled by tail)
        else:
            # DIAGNOSTIC: a fenced block matched the tool_call regex but we could
            # NOT turn it into a tool_use (parse failed, or no name). This is
            # exactly the "shows as code block" case. Capture the raw so we can
            # see what the model actually emitted.
            log_event({"parse_miss": True,
                       "parsed_ok": isinstance(parsed, dict),
                       "name_seen": name,
                       "raw_head": raw[:600],
                       "raw_tail": raw[-200:] if len(raw) > 200 else "",
                       "note": "fence matched but not emitted as tool_use"})
    # FALLBACK: the model emitted a BARE JSON tool call with no fence at all.
    # Only engage when NO fenced tool_use was produced, and only accept an
    # object whose "name" is a real offered tool -- so ordinary prose or JSON
    # data is never mistaken for a call.
    produced_tool = any(b.get("type") == "tool_use" for b in blocks)
    if not produced_tool and '"name"' in text:
        for cstart, cend, cand in _iter_balanced_json_objects(text):
            parsed = _loads_tool_json(cand.strip())
            name = parsed.get("name") if isinstance(parsed, dict) else None
            if not name:
                continue
            if strict and valid_names is not None and name not in valid_names:
                continue
            # Same placeholder guard as the fenced path: a bare {"name": "..."}
            # that is a prose placeholder is never a real call. (An offered tool
            # name is always allowed through.)
            is_offered = bool(valid_names) and name in valid_names
            if guard_examples and not is_offered and not _is_plausible_tool_name(name):
                continue
            tool_input = parsed.get("input", parsed.get("arguments", {})) if isinstance(parsed, dict) else {}
            if not isinstance(tool_input, dict):
                tool_input = {}
            pre = text[last:cstart].strip()
            if pre:
                blocks.append({"type": "text", "text": pre})
            blocks.append({
                "type": "tool_use",
                "id": "toolu_" + uuid.uuid4().hex[:24],
                "name": name,
                "input": tool_input,
            })
            last = cend
            produced_tool = True
            break

    tail = text[last:].strip()
    if tail:
        blocks.append({"type": "text", "text": tail})
    if not blocks:
        blocks.append({"type": "text", "text": text})
    # DIAGNOSTIC: the regex found NO fence at all, yet the text clearly contains
    # a tool-call-ish fenced block. That means the FENCE REGEX failed to match
    # (not the JSON parser). Capture the raw so we can fix the pattern.
    if _match_count == 0 and ("```tool_call" in text or '"name"' in text):
        snip = text
        idx = text.find("```")
        if idx > 0:
            snip = text[max(0, idx - 40):]
        log_event({"regex_miss": True,
                   "text_len": len(text),
                   "snip_head": snip[:700],
                   "note": "fence present but TOOL_CALL_FENCE_RE matched nothing"})
    return blocks


# --------------------------------------------------------------------------- #
# Debug: detect a raw tool_call that leaked into a final TEXT block
# --------------------------------------------------------------------------- #

# A truncated/!unparsed tool call sitting in plain text looks like one of:
#   * a ```tool_call (or ```json {"name": ...}) fence
#   * native <invoke name="..."> / <invoke ...> XML
#   * a bare {"name": "...", "input": ...} object (possibly cut off)
_LEAK_FENCE_RE = re.compile(r"```[A-Za-z0-9_+.\-]*[ \t]*\r?\n\s*\{\s*\"name\"",
                            re.IGNORECASE)
_LEAK_INVOKE_RE = re.compile(r"<(?:antml:)?invoke\s+name\s*=", re.IGNORECASE)
_LEAK_BARE_RE = re.compile(r"\{\s*\"name\"\s*:\s*\"[^\"]+\"")


def detect_tool_leak(content_out: List[Dict[str, Any]]) -> None:
    """Log a `tool_leak` event if a raw tool-call pattern survived into a final
    text block. Diagnostic only -- does not modify the content. Gated by the
    JDW_DEBUG_TOOL_LEAK env flag at the call site."""
    for b in content_out:
        if not isinstance(b, dict) or b.get("type") != "text":
            continue
        txt = b.get("text") or ""
        if not txt:
            continue
        kind = None
        if _LEAK_FENCE_RE.search(txt):
            kind = "fence"
        elif _LEAK_INVOKE_RE.search(txt):
            kind = "invoke_xml"
        elif _LEAK_BARE_RE.search(txt):
            kind = "bare_json"
        if kind:
            idx = txt.find("```")
            if idx < 0:
                idx = txt.find("<")
            if idx < 0:
                idx = txt.find("{")
            snip = txt[max(0, idx - 40):idx + 400] if idx >= 0 else txt[:400]
            log_event({"tool_leak": True, "kind": kind,
                       "text_len": len(txt),
                       "snippet": snip,
                       "note": "raw tool_call leaked into final text block"})


# --------------------------------------------------------------------------- #
# Strip the upstream HARNESS's own tool JSON that leaks into visible text
# --------------------------------------------------------------------------- #

# The upstream relay is an "analysis agent" harness with its OWN private tools
# (system_todo_write, read_tabular). On long step-by-step tasks the model under
# the harness reflexively calls them, and when it writes the call as BARE JSON
# in the text (no fence), it isn't one of the client's tools, so it survives as
# a raw {"name": "system_todo_write", "input": {...}} blob dumped to the user.
# We cut those blobs out of visible text. (Real client tool_use blocks and any
# tool the client ACTUALLY offered are never touched -- see the guard below.)
HARNESS_TOOL_NAMES = {"system_todo_write", "read_tabular"}

# A fenced OR bare harness-tool call, matched so we can delete it from text.
_HARNESS_FENCE_RE = re.compile(
    r"```[A-Za-z0-9_+.\-]*[ \t]*\r?\n\s*\{\s*\"name\"\s*:\s*\"(system_todo_write|read_tabular)\".*?\}[ \t]*\r?\n?```",
    re.DOTALL,
)


def strip_harness_tool_text(text: str, client_offered: set) -> str:
    """Remove leaked harness-tool JSON (fenced or bare) from a text string.
    A harness name the CLIENT actually offered is left intact (the client wants
    it as a real tool, handled elsewhere)."""
    if not text:
        return text
    targets = HARNESS_TOOL_NAMES - set(client_offered or ())
    if not targets:
        return text
    if not any(t in text for t in targets):
        return text
    # 1) fenced form
    def _fence_sub(m: "re.Match") -> str:
        return "" if m.group(1) in targets else m.group(0)
    out = _HARNESS_FENCE_RE.sub(_fence_sub, text)
    # 2) bare brace-balanced form: walk every {"name": ...} object and drop the
    #    ones whose name is a leaked harness tool.
    removals: List[Tuple[int, int]] = []
    for cstart, cend, cand in _iter_balanced_json_objects(out):
        parsed = _loads_tool_json(cand.strip())
        if isinstance(parsed, dict) and parsed.get("name") in targets:
            removals.append((cstart, cend))
    for cstart, cend in reversed(removals):
        out = out[:cstart] + out[cend:]
    # tidy up leftover blank lines from the cut
    out = re.sub(r"\n[ \t]*\n[ \t]*\n+", "\n\n", out)
    return out.strip()


def strip_harness_tools(content_out: List[Dict[str, Any]],
                        client_offered: set) -> List[Dict[str, Any]]:
    """Apply strip_harness_tool_text to every text block; drop blocks that end
    up empty. tool_use / image / thinking blocks pass through untouched."""
    cleaned: List[Dict[str, Any]] = []
    for b in content_out:
        if isinstance(b, dict) and b.get("type") == "text":
            new_text = strip_harness_tool_text(b.get("text", ""), client_offered)
            if new_text:
                cleaned.append({"type": "text", "text": new_text})
            else:
                log_event({"harness_tool_stripped": True,
                           "note": "removed leaked harness-tool JSON from text"})
        else:
            cleaned.append(b)
    return cleaned


# --------------------------------------------------------------------------- #
# Post-processing of the upstream non-stream response
# --------------------------------------------------------------------------- #

def approx_tokens(text: str) -> int:
    # rough heuristic ~4 chars/token
    return max(1, int(len(text) / 4))


def apply_stop_sequences(text: str,
                         stop_sequences: Optional[List[str]]) -> Tuple[str, Optional[str]]:
    if not stop_sequences:
        return text, None
    cut_idx = None
    matched = None
    for s in stop_sequences:
        if not s:
            continue
        i = text.find(s)
        if i != -1 and (cut_idx is None or i < cut_idx):
            cut_idx = i
            matched = s
    if cut_idx is not None:
        return text[:cut_idx], matched
    return text, None


def normalize_usage(raw_usage: Dict[str, Any],
                    feats: Dict[str, Any],
                    output_text_len_tokens: int) -> Dict[str, Any]:
    mode = feats.get("usage_mode", "normalized")
    baseline = int(feats.get("usage_baseline_tokens", 0) or 0)
    raw_in = int(raw_usage.get("input_tokens", 0) or 0)
    raw_out = int(raw_usage.get("output_tokens", 0) or 0)

    if mode == "raw":
        return {"input_tokens": raw_in, "output_tokens": raw_out}
    if mode == "estimated":
        return {"input_tokens": max(0, raw_in - baseline),
                "output_tokens": raw_out or output_text_len_tokens}
    # normalized: subtract baseline from input, keep real output
    norm_in = raw_in
    # upstream sometimes reports input_tokens as the big combined number
    if raw_in >= baseline:
        norm_in = raw_in - baseline
    return {"input_tokens": max(0, norm_in),
            "output_tokens": raw_out or output_text_len_tokens}


def process_upstream_message(upstream: Dict[str, Any],
                             client_req: Dict[str, Any],
                             feats: Dict[str, Any]) -> Dict[str, Any]:
    """Turn the upstream non-stream message into a clean Anthropic message,
    applying all fixes. Returns a dict ready to serialize / to stream."""
    content_in = upstream.get("content", []) or []

    # 1) Collect raw text, drop/stash thinking, keep upstream-native tool_use.
    text_segments: List[str] = []
    native_tool_blocks: List[Dict[str, Any]] = []
    had_thinking = False
    for b in content_in:
        if not isinstance(b, dict):
            continue
        bt = b.get("type")
        if bt == "text":
            text_segments.append(b.get("text", ""))
        elif bt == "thinking":
            had_thinking = True
            if not feats.get("strip_thinking", True):
                # keep thinking as its own block (verbatim) at the front
                native_tool_blocks.append(b)
        elif bt == "tool_use":
            # upstream emitted its OWN tool (e.g. system_todo_write) — keep it,
            # client asked for its own tools so this is rare; pass through.
            native_tool_blocks.append(b)

    joined_text = "\n\n".join(t for t in text_segments if t != "")

    # 2) stop_sequences
    stop_seq_matched = None
    if feats.get("enforce_stop_sequences", True):
        joined_text, stop_seq_matched = apply_stop_sequences(
            joined_text, client_req.get("stop_sequences"))

    # 3) parse tool_call fences from text -> blocks
    client_tools = client_req.get("tools") or []
    client_has_tools = bool(client_tools) and feats.get("tool_injection", True)
    # Closed set of names the client actually offered.
    valid_names = {t.get("name") for t in client_tools if t.get("name")}
    # Only enforce the closed set when the client gave us a list to check against.
    strict = feats.get("strict_tool_names", True) and bool(valid_names)
    if client_has_tools:
        parsed_blocks = parse_tool_calls_from_text(
            joined_text, valid_names=valid_names, strict=strict,
            guard_examples=feats.get("guard_tool_examples", True))
    else:
        parsed_blocks = [{"type": "text", "text": joined_text}] if joined_text else []

    # If strict, drop any upstream-native tool_use whose name isn't offered by
    # the client (prevents the client receiving an "unknown tool" block).
    if strict:
        kept = []
        for b in native_tool_blocks:
            if b.get("type") == "tool_use" and b.get("name") not in valid_names:
                log_event({"unknown_tool": b.get("name"),
                           "note": "upstream-native tool not offered by client -> dropped"})
                continue
            kept.append(b)
        native_tool_blocks = kept

    # merge: thinking (if kept) + native tool blocks come first, then parsed
    content_out: List[Dict[str, Any]] = []
    for b in native_tool_blocks:
        content_out.append(b)
    content_out.extend(parsed_blocks)
    if not content_out:
        content_out = [{"type": "text", "text": ""}]

    # Strip the upstream harness's OWN leaked tool JSON (system_todo_write /
    # read_tabular) from visible text -- unless the client itself offered a
    # tool by that name. This removes the raw {"name": ...} blobs the harness
    # dumps into text on long step-by-step tasks.
    if feats.get("strip_harness_tools", True):
        before = len(content_out)
        content_out = strip_harness_tools(content_out, valid_names)
        if not content_out:
            content_out = [{"type": "text", "text": ""}]
        _ = before  # (kept for clarity; count change is logged inside)

    # DEBUG (opt-in): catch a raw tool_call that leaked into a text block.
    if DEBUG_TOOL_LEAK:
        detect_tool_leak(content_out)

    # 4) determine stop_reason
    has_tool_use = any(b.get("type") == "tool_use" for b in content_out)
    stop_reason = "end_turn"
    stop_sequence_val = None
    if has_tool_use:
        stop_reason = "tool_use"
    if stop_seq_matched is not None:
        stop_reason = "stop_sequence"
        stop_sequence_val = stop_seq_matched

    # 5) max_tokens enforcement (only meaningful for text)
    max_tokens = client_req.get("max_tokens")
    if (feats.get("enforce_max_tokens", True) and isinstance(max_tokens, int)
            and max_tokens > 0 and stop_reason == "end_turn" and not has_tool_use):
        # truncate the LAST text block by approx token budget
        total = 0
        truncated = False
        new_content = []
        for b in content_out:
            if b.get("type") == "text":
                words = b["text"]
                tk = approx_tokens(words)
                if total + tk > max_tokens:
                    budget_chars = max(0, (max_tokens - total) * 4)
                    b = {"type": "text", "text": words[:budget_chars]}
                    truncated = True
                    total = max_tokens
                    new_content.append(b)
                    break
                total += tk
            new_content.append(b)
        if truncated:
            content_out = new_content
            stop_reason = "max_tokens"

    # 6) usage
    out_tok_est = approx_tokens(joined_text)
    usage = normalize_usage(upstream.get("usage", {}) or {}, feats, out_tok_est)

    result = {
        "id": upstream.get("id", "msg_" + uuid.uuid4().hex[:16]),
        "type": "message",
        "role": "assistant",
        "model": client_req.get("model", upstream.get("model", CONFIG["model"])),
        "content": content_out,
        "stop_reason": stop_reason,
        "stop_sequence": stop_sequence_val,
        "usage": {
            "input_tokens": usage["input_tokens"],
            "output_tokens": usage["output_tokens"],
        },
    }
    result["_meta"] = {
        "had_thinking": had_thinking,
        "has_tool_use": has_tool_use,
        "client_tools": len(client_req.get("tools") or []),
    }
    return result


# --------------------------------------------------------------------------- #
# Server-side web tools: executed BY the proxy (no client involvement).
# The proxy injects these tool definitions, the model emits a tool_call,
# we run it here (DuckDuckGo HTML endpoint -> no API key), then feed the
# result back as a user turn and loop until the model answers normally.
# --------------------------------------------------------------------------- #

# Tool schemas injected into the system prompt (same shape as a client tool).
# Each entry carries a private "_feature" key naming the config flag that
# enables it, so build_upstream_payload can inject only the ones turned on.
# (The key is stripped before the schema is rendered into the prompt.)
SERVER_WEB_TOOLS: List[Dict[str, Any]] = [
    {
        "_feature": "image_fetch_enabled",
        "name": "fetch_image",
        "description": (
            "Fetch an image by URL so you can SEE and analyse it. Returns the "
            "image to you directly. Use to describe, read, or reason about an "
            "image available online. The URL may point straight at an image "
            "file OR at an HTML page that displays/shares one (e.g. a share "
            "link) -- in the latter case the proxy finds the image for you."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "Absolute http(s) URL of the image or the page showing it."},
            },
            "required": ["url"],
        },
    },
    {
        "_feature": "web_search_enabled",
        "name": "web_search",
        "description": (
            "Search the web for current, up-to-date information and get back a "
            "ranked list of result titles, snippets and links. Use this when "
            "you need facts you don't know, recent events, or to find a page "
            "to open with fetch_url."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "The search query."},
                "max_results": {"type": "integer", "description": "How many results to return (default 5, max 10)."},
            },
            "required": ["query"],
        },
    },
    {
        "_feature": "web_fetch_enabled",
        "name": "fetch_url",
        "description": (
            "Open an http(s) URL and return its readable page content as plain "
            "text (HTML tags/scripts stripped). Use to read docs, articles, API "
            "responses or a raw file off the web, e.g. a link returned by "
            "web_search."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "Absolute http(s) URL of the page to read."},
                "max_chars": {"type": "integer", "description": "Truncate the returned text to this many characters (default 6000)."},
            },
            "required": ["url"],
        },
    },
]

SERVER_WEB_TOOL_NAMES = {t["name"] for t in SERVER_WEB_TOOLS}

# Headers specifically for image requests. Some CDNs (notably Wikimedia) block
# any request whose User-Agent is not a descriptive bot UA carrying contact
# info (their published robot policy returns 403 "respect our robot policy"
# otherwise). We therefore advertise a descriptive UA with a contact URL plus
# an image-first Accept header. This satisfies Wikimedia and ordinary CDNs.
_IMAGE_HEADERS = {
    "User-Agent": ("jdw-fix-proxy/1.0 "
                   "(+https://github.com/jdw-fix-proxy; contact: admin@localhost) "
                   "httpx"),
    "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,ar;q=0.8",
}

# Browser-like headers for ordinary web pages (search + fetch_url). A real
# desktop User-Agent avoids the trivial bot blocks many sites apply to the
# default httpx UA.
_WEB_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/125.0.0.0 Safari/537.36"),
    "Accept": ("text/html,application/xhtml+xml,application/xml;q=0.9,"
               "image/avif,image/webp,*/*;q=0.8"),
    "Accept-Language": "en-US,en;q=0.9,ru;q=0.8",
}

_IMG_CTYPES = ("image/jpeg", "image/png", "image/gif", "image/webp")


def _ctype_from_ext(url: str) -> Optional[str]:
    """Best-effort image media-type from a URL's file extension."""
    low = url.lower().split("?", 1)[0]
    if low.endswith((".jpg", ".jpeg")):
        return "image/jpeg"
    if low.endswith(".png"):
        return "image/png"
    if low.endswith(".gif"):
        return "image/gif"
    if low.endswith(".webp"):
        return "image/webp"
    return None


# <meta property="og:image" content="..."> (either attribute order) and <img src>
_OG_IMAGE_RE = re.compile(
    r"""<meta[^>]+(?:property|name)\s*=\s*["'](?:og:image|twitter:image)["'][^>]*>""",
    re.IGNORECASE,
)
_CONTENT_ATTR_RE = re.compile(r"""content\s*=\s*["']([^"']+)["']""", re.IGNORECASE)
_IMG_SRC_RE = re.compile(r"""<img[^>]+src\s*=\s*["']([^"']+)["']""", re.IGNORECASE)


def _scrape_image_url(html: str, base_url: str) -> Optional[str]:
    """Find the best image URL inside an HTML page: prefer og:image/twitter:image
    (what share pages expose), else the first <img src>. Relative URLs are
    resolved against base_url."""
    candidate: Optional[str] = None
    m = _OG_IMAGE_RE.search(html)
    if m:
        cm = _CONTENT_ATTR_RE.search(m.group(0))
        if cm:
            candidate = cm.group(1).strip()
    if not candidate:
        im = _IMG_SRC_RE.search(html)
        if im:
            candidate = im.group(1).strip()
    if not candidate:
        return None
    candidate = _html.unescape(candidate)
    # resolve protocol-relative and root/relative URLs
    if candidate.startswith("//"):
        scheme = urlparse(base_url).scheme or "https"
        return f"{scheme}:{candidate}"
    if candidate.startswith("http://") or candidate.startswith("https://"):
        return candidate
    pr = urlparse(base_url)
    if candidate.startswith("/"):
        return f"{pr.scheme}://{pr.netloc}{candidate}"
    base_dir = pr.path.rsplit("/", 1)[0]
    return f"{pr.scheme}://{pr.netloc}{base_dir}/{candidate}"


_TAG_RE = re.compile(r"<[^>]+>")
_SCRIPT_STYLE_RE = re.compile(r"<(script|style)\b[^>]*>.*?</\1>",
                              re.IGNORECASE | re.DOTALL)
_WS_RE = re.compile(r"[ \t\r\f]+")
_BLANKLINES_RE = re.compile(r"\n\s*\n\s*\n+")


def _html_to_text(html: str) -> str:
    """Strip an HTML document down to readable plain text."""
    txt = _SCRIPT_STYLE_RE.sub(" ", html)
    # turn block-level tags into newlines so structure survives
    txt = re.sub(r"<\s*(br|/p|/div|/li|/h[1-6]|/tr)\s*/?>", "\n", txt,
                 flags=re.IGNORECASE)
    txt = _TAG_RE.sub(" ", txt)
    txt = _html.unescape(txt)
    txt = _WS_RE.sub(" ", txt)
    txt = _BLANKLINES_RE.sub("\n\n", txt)
    return txt.strip()


async def _fetch_image_block(client: httpx.AsyncClient, url: str) -> Dict[str, Any]:
    """Fetch an image and return an Anthropic image content block (base64).

    If the URL turns out to be an HTML page (a share/landing page rather than a
    direct image file), scrape its og:image / <img> and fetch THAT instead, so
    links like image-host share pages work transparently.
    """
    # Build image-appropriate headers, adding a same-origin Referer which some
    # CDNs (Wikimedia, etc.) require before they serve the bytes.
    hdrs = dict(_IMAGE_HEADERS)
    try:
        pr = urlparse(url)
        if pr.scheme and pr.netloc:
            hdrs["Referer"] = f"{pr.scheme}://{pr.netloc}/"
    except Exception:
        pass
    try:
        r = await client.get(url, headers=hdrs, follow_redirects=True)
    except Exception as e:
        return {"_error": f"fetch_image error: {e}"}
    if r.status_code >= 400:
        return {"_error": f"fetch_image HTTP {r.status_code} for {url}"}
    ctype = (r.headers.get("content-type", "") or "").split(";")[0].strip().lower()
    if ctype not in _IMG_CTYPES:
        ctype = _ctype_from_ext(url) or ctype
    # Not a direct image -> if it's an HTML page, scrape an image URL out of it
    # and fetch that (one hop only, to avoid loops).
    if ctype not in _IMG_CTYPES:
        looks_html = ("text/html" in ctype or "application/xhtml" in ctype
                      or r.content[:200].lstrip().lower().startswith(b"<!doctype html")
                      or b"<html" in r.content[:500].lower())
        if looks_html:
            try:
                page = r.content.decode(r.encoding or "utf-8", "replace")
            except Exception:
                page = r.text
            img_url = _scrape_image_url(page, str(r.url))
            if not img_url:
                return {"_error": f"fetch_image: no image found on page {url}"}
            try:
                r2 = await client.get(img_url, headers=hdrs, follow_redirects=True)
            except Exception as e:
                return {"_error": f"fetch_image error following page image: {e}"}
            if r2.status_code >= 400:
                return {"_error": f"fetch_image HTTP {r2.status_code} for {img_url}"}
            ctype = (r2.headers.get("content-type", "") or "").split(";")[0].strip().lower()
            if ctype not in _IMG_CTYPES:
                ctype = _ctype_from_ext(img_url) or ""
            if ctype not in _IMG_CTYPES:
                return {"_error": f"fetch_image: page image had unsupported type '{ctype}'"}
            b64 = base64.b64encode(r2.content).decode("ascii")
            return {"type": "image",
                    "source": {"type": "base64", "media_type": ctype, "data": b64},
                    "_resolved_url": img_url}
        return {"_error": f"fetch_image: unsupported content-type '{ctype}'"}
    b64 = base64.b64encode(r.content).decode("ascii")
    return {
        "type": "image",
        "source": {"type": "base64", "media_type": ctype, "data": b64},
    }


# DuckDuckGo's HTML endpoint needs no API key. Results are plain anchor tags
# with class "result__a" (title+link) and "result__snippet" (snippet).
_DDG_RESULT_RE = re.compile(
    r'<a[^>]+class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)
_DDG_SNIPPET_RE = re.compile(
    r'<a[^>]+class="result__snippet"[^>]*>(.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)


def _ddg_clean_link(href: str) -> str:
    """DuckDuckGo wraps result links as /l/?uddg=<encoded>. Unwrap to the real
    target URL when present."""
    try:
        if "uddg=" in href:
            qs = parse_qs(urlparse(href).query)
            if qs.get("uddg"):
                return unquote(qs["uddg"][0])
    except Exception:
        pass
    if href.startswith("//"):
        return "https:" + href
    return href


async def _web_search(client: httpx.AsyncClient, query: str,
                      max_results: int = 5) -> str:
    """Run a DuckDuckGo HTML search and return a compact, numbered text list of
    title / link / snippet triples the model can read and cite."""
    max_results = max(1, min(int(max_results or 5), 10))
    try:
        r = await client.post(
            "https://html.duckduckgo.com/html/",
            headers=_WEB_HEADERS,
            data={"q": query},
            follow_redirects=True,
        )
    except Exception as e:
        return f"[web_search error: {e}]"
    if r.status_code >= 400:
        return f"[web_search HTTP {r.status_code}]"
    html = r.text
    titles = _DDG_RESULT_RE.findall(html)
    snippets = _DDG_SNIPPET_RE.findall(html)
    if not titles:
        return f"[web_search: no results for '{query}']"
    lines = [f"Search results for: {query}\n"]
    for i, (href, title_html) in enumerate(titles[:max_results]):
        title = _html.unescape(_TAG_RE.sub("", title_html)).strip()
        link = _ddg_clean_link(href)
        snip = ""
        if i < len(snippets):
            snip = _html.unescape(_TAG_RE.sub("", snippets[i])).strip()
        lines.append(f"{i + 1}. {title}\n   {link}")
        if snip:
            lines.append(f"   {snip}")
    return "\n".join(lines)


async def _fetch_url_text(client: httpx.AsyncClient, url: str,
                          max_chars: int = 6000) -> str:
    """Fetch a page and return its readable text (HTML stripped), truncated."""
    max_chars = max(500, min(int(max_chars or 6000), 50000))
    try:
        r = await client.get(url, headers=_WEB_HEADERS, follow_redirects=True)
    except Exception as e:
        return f"[fetch_url error: {e}]"
    if r.status_code >= 400:
        return f"[fetch_url HTTP {r.status_code} for {url}]"
    ctype = (r.headers.get("content-type", "") or "").split(";")[0].strip().lower()
    if ctype and not (ctype.startswith("text/") or "html" in ctype
                      or "xml" in ctype or "json" in ctype
                      or "javascript" in ctype):
        return f"[fetch_url: non-text content-type '{ctype}' at {url}]"
    body = r.text
    if "html" in ctype or "<html" in body[:500].lower():
        text = _html_to_text(body)
    else:
        text = body
    text = text.strip()
    if len(text) > max_chars:
        text = text[:max_chars] + f"\n...[truncated at {max_chars} chars]"
    return f"Content of {url}:\n\n{text}" if text else f"[fetch_url: empty page at {url}]"


async def run_server_web_tool(client: httpx.AsyncClient,
                              name: str,
                              tool_input: Dict[str, Any],
                              feats: Dict[str, Any]) -> Dict[str, Any]:
    """Execute ONE server-side web tool. Returns a dict:
        {"text": str}                      -> feed back as text
        {"blocks": [image_block, ...]}     -> feed back as content blocks
    """
    if name == "fetch_image":
        url = (tool_input.get("url") or "").strip()
        if not url:
            return {"text": "[fetch_image: empty url]"}
        block = await _fetch_image_block(client, url)
        if block.get("_error"):
            return {"text": "[" + block["_error"] + "]"}
        resolved = block.pop("_resolved_url", None)
        caption = (f"Fetched image from {resolved} (found on page {url}):"
                   if resolved else f"Fetched image from {url}:")
        return {"blocks": [
            {"type": "text", "text": caption},
            block,
        ]}
    if name == "web_search":
        query = (tool_input.get("query") or "").strip()
        if not query:
            return {"text": "[web_search: empty query]"}
        text = await _web_search(client, query,
                                 tool_input.get("max_results", 5))
        return {"text": text}
    if name == "fetch_url":
        url = (tool_input.get("url") or "").strip()
        if not url:
            return {"text": "[fetch_url: empty url]"}
        text = await _fetch_url_text(client, url,
                                     tool_input.get("max_chars", 6000))
        return {"text": text}
    return {"text": f"[unknown server tool: {name}]"}


def _raw_assistant_text(upstream: Dict[str, Any]) -> str:
    """Join the plain-text blocks of an upstream message (ignoring thinking)."""
    segs: List[str] = []
    for b in (upstream.get("content") or []):
        if isinstance(b, dict) and b.get("type") == "text":
            segs.append(b.get("text", ""))
    return "\n\n".join(s for s in segs if s)


def _find_server_tool_call(text: str) -> Optional[Dict[str, Any]]:
    """Return the FIRST server-tool tool_use parsed from the model's text, or
    None. Server tools are always valid names here (strict against our set)."""
    if not text:
        return None
    blocks = parse_tool_calls_from_text(
        text, valid_names=SERVER_WEB_TOOL_NAMES, strict=True)
    for b in blocks:
        if b.get("type") == "tool_use" and b.get("name") in SERVER_WEB_TOOL_NAMES:
            return b
    return None


async def resolve_server_tools(payload: Dict[str, Any],
                              feats: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
    """Call upstream; if the model invokes one of OUR server web tools, execute
    it here, feed the result back, and loop -- until the model answers without
    a server-tool call (or we hit the iteration cap). The returned upstream
    message is then processed normally for the client.

    When web tools are disabled this is a thin pass-through to call_upstream.
    """
    status, upstream = await call_upstream(payload)
    if not feats.get("web_tools_enabled", False):
        return status, upstream
    if status >= 400 or not isinstance(upstream, dict):
        return status, upstream

    max_iters = int(feats.get("web_tools_max_iters", 4) or 4)
    timeout = httpx.Timeout(
        timeout=float(CONFIG.get("upstream_timeout_s", 90) or 90),
        connect=float(CONFIG.get("upstream_connect_timeout_s", 8) or 8),
        write=float(CONFIG.get("upstream_write_timeout_s", 30) or 30),
        pool=float(CONFIG.get("upstream_pool_timeout_s", 5) or 5),
    )
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as web_client:
        for _it in range(max_iters):
            raw_text = _raw_assistant_text(upstream)
            call = _find_server_tool_call(raw_text)
            if not call:
                break  # model answered (or called a CLIENT tool) -> hand off
            name = call.get("name")
            tin = call.get("input") or {}
            log_event({"server_tool": name, "input": tin, "iter": _it,
                       "note": "proxy executing server-side web tool"})
            try:
                res = await run_server_web_tool(web_client, name, tin, feats)
            except Exception as e:
                res = {"text": f"[{name} failed: {e}]"}
            # Append the model's own turn, then the tool result as a user turn,
            # preserving start-with-user + strict alternation.
            payload["messages"].append({"role": "assistant", "content": raw_text})
            if res.get("blocks"):
                user_content: Any = res["blocks"]
            else:
                user_content = (res.get("text") or "")
            payload["messages"].append({"role": "user", "content": user_content})
            status, upstream = await call_upstream(payload)
            if status >= 400 or not isinstance(upstream, dict):
                return status, upstream
        else:
            # Loop ran to the cap without the model finishing: nudge it to answer
            # using what it already gathered (no more tool calls).
            payload["messages"].append({
                "role": "user",
                "content": ("You have reached the web-tool limit. Answer now "
                            "using the information already gathered; do not call "
                            "any more tools."),
            })
            status, upstream = await call_upstream(payload)
    return status, upstream


# --------------------------------------------------------------------------- #
# Build the upstream payload
# --------------------------------------------------------------------------- #

def build_upstream_payload(client_req: Dict[str, Any],
                           feats: Dict[str, Any]) -> Dict[str, Any]:
    model = client_req.get("model") or CONFIG["model"]

    # system prompt (+ tool injection)
    system_text = extract_system_text(client_req.get("system"))
    tools = client_req.get("tools") or []
    tool_choice = client_req.get("tool_choice")
    addendum = ""
    # Server-side web tools are injected IN ADDITION to the client's tools when
    # enabled -- but NOT when the client forces one specific tool this turn.
    forcing_tool = (isinstance(tool_choice, dict) and tool_choice.get("type") == "tool"
                    and bool(tool_choice.get("name")))
    web_on = bool(feats.get("web_tools_enabled", False)) and not forcing_tool
    if feats.get("tool_injection", True) and (tools or web_on):
        # If the client forces ONE specific tool, only inject that tool's schema
        # (big saving vs. injecting all N tool schemas every request).
        inject_tools = list(tools)
        if forcing_tool:
            only = [t for t in tools if t.get("name") == tool_choice["name"]]
            if only:
                inject_tools = only
        elif web_on:
            # Append our server tools, skipping any the client already defines
            # under the same name (client definition wins) and any whose
            # per-feature flag is turned off. The private "_feature" key is
            # stripped so it never leaks into the rendered prompt.
            client_names = {t.get("name") for t in tools}
            for st in SERVER_WEB_TOOLS:
                if st["name"] in client_names:
                    continue
                flag = st.get("_feature")
                if flag and not feats.get(flag, True):
                    continue
                inject_tools.append({k: v for k, v in st.items()
                                     if k != "_feature"})
        addendum = build_tool_system_block(
            inject_tools, tool_choice,
            compact=feats.get("compact_tool_schemas", True),
            ultra=feats.get("ultra_compact_tools", False))
        system_text = (system_text + "\n\n" + addendum) if system_text else addendum

    # messages (flatten tool blocks for upstream comprehension)
    all_msgs = client_req.get("messages", []) or []
    # Optionally keep only the last N messages to cap history token cost.
    # IMPORTANT: the upstream requires the message list to START with a
    # 'user' message and alternate roles. A naive messages[-N:] slice can
    # start on an 'assistant' (or an orphaned tool_result) and get rejected
    # with a 400. So we keep the FIRST user message (original task) and the
    # last N, and we make sure the trimmed tail begins on a user turn.
    max_hist = int(feats.get("max_history_messages", 0) or 0)
    if max_hist > 0 and len(all_msgs) > max_hist:
        tail = all_msgs[-max_hist:]
        # Drop leading turns until the tail starts with a 'user' message
        # (also drops orphaned tool_result blocks that lost their tool_use).
        while tail and tail[0].get("role") != "user":
            tail = tail[1:]
        # Preserve the very first user message (the original task/context)
        # if it is not already included in the tail.
        head = []
        if all_msgs and all_msgs[0].get("role") == "user" and all_msgs[0] not in tail:
            head = [all_msgs[0]]
        all_msgs = head + tail
    # Final guard: whatever we send must begin with a user message.
    while all_msgs and all_msgs[0].get("role") != "user":
        all_msgs = all_msgs[1:]
    msgs = flatten_messages(all_msgs,
                            feats.get("flatten_tool_history", True),
                            int(feats.get("max_tool_result_chars", 0) or 0))
    # Enforce the upstream's strict contract (start-with-user + strict
    # alternation). This is the definitive fix for the recurring 400
    # 'messages must have alternating user and assistant roles' error that
    # history trimming / tool-result flattening can otherwise trigger.
    msgs = normalize_alternating(msgs)

    # max_tokens: never send a tiny budget upstream, otherwise the model can
    # get cut off MID tool_call (the fenced JSON is left unterminated and can't
    # be parsed into a tool_use -> the tool silently "stops half way"). We
    # raise the upstream budget to a safe floor so tool calls always complete;
    # our own enforce_max_tokens step still trims pure-text replies afterwards.
    _client_max = client_req.get("max_tokens", 4096) or 4096
    _floor = int(feats.get("min_tool_output_tokens", 8192) or 0)
    _upstream_max = _client_max if _client_max >= _floor else _floor
    # Admin hard cap on OUTPUT tokens. When set (>0) it OVERRIDES the client's
    # request and clamps generation to this value, capping cost per turn.
    _hard_cap = int(feats.get("max_output_tokens", 0) or 0)
    if _hard_cap > 0:
        _upstream_max = _hard_cap
    payload: Dict[str, Any] = {
        "model": model,
        "max_tokens": _upstream_max,
        "messages": msgs,
        "stream": False,  # ALWAYS non-stream upstream
    }
    if system_text:
        payload["system"] = system_text
    # pass temperature/top_p/top_k through (they work per audit)
    for k in ("temperature", "top_p", "top_k"):
        if k in client_req and client_req[k] is not None:
            payload[k] = client_req[k]
    # NOTE: we intentionally do NOT forward client tools/tool_choice/stop_sequences
    # to the upstream (it discards them); we handle them ourselves.

    # Approx token breakdown so the dashboard can show WHERE the input cost is
    # (tools vs. history vs. base system) -> helps diagnose high usage.
    tools_tok = approx_tokens(addendum) if (feats.get("tool_injection", True) and tools) else 0
    base_sys_tok = approx_tokens(system_text) - tools_tok
    hist_tok = approx_tokens(json.dumps(msgs, ensure_ascii=False))
    payload["_breakdown"] = {
        "tools_tok": max(0, tools_tok),
        "system_tok": max(0, base_sys_tok),
        "history_tok": max(0, hist_tok),
        "history_msgs": len(msgs),
    }
    return payload


# --------------------------------------------------------------------------- #
# SSE synthesis
# --------------------------------------------------------------------------- #

def sse(event: str, data: Dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def synthesize_sse(message: Dict[str, Any], emit_start: bool = True):
    """Yield a spec-correct Anthropic SSE stream from a finished message dict.

    When `emit_start` is False the leading `message_start` + `ping` events are
    skipped (used by stream_with_keepalive, which already opened the stream).
    """
    usage = message.get("usage", {})
    msg_id = message.get("id")
    model = message.get("model")

    if emit_start:
        # message_start (with real input usage)
        start_msg = {
            "type": "message_start",
            "message": {
                "id": msg_id,
                "type": "message",
                "role": "assistant",
                "model": model,
                "content": [],
                "stop_reason": None,
                "stop_sequence": None,
                "usage": {
                    "input_tokens": usage.get("input_tokens", 0),
                    "output_tokens": 0,
                },
            },
        }
        yield sse("message_start", start_msg)
        yield sse("ping", {"type": "ping"})

    content = message.get("content", [])
    for idx, block in enumerate(content):
        btype = block.get("type")
        if btype == "text":
            yield sse("content_block_start", {
                "type": "content_block_start",
                "index": idx,
                "content_block": {"type": "text", "text": ""},
            })
            text = block.get("text", "")
            # chunk text so clients render progressively
            for chunk in chunk_text(text, 60):
                yield sse("content_block_delta", {
                    "type": "content_block_delta",
                    "index": idx,
                    "delta": {"type": "text_delta", "text": chunk},
                })
                await asyncio.sleep(0)
            yield sse("content_block_stop",
                      {"type": "content_block_stop", "index": idx})

        elif btype == "tool_use":
            yield sse("content_block_start", {
                "type": "content_block_start",
                "index": idx,
                "content_block": {
                    "type": "tool_use",
                    "id": block.get("id"),
                    "name": block.get("name"),
                    "input": {},
                },
            })
            partial = json.dumps(block.get("input", {}), ensure_ascii=False)
            for chunk in chunk_text(partial, 60):
                yield sse("content_block_delta", {
                    "type": "content_block_delta",
                    "index": idx,
                    "delta": {"type": "input_json_delta", "partial_json": chunk},
                })
                await asyncio.sleep(0)
            yield sse("content_block_stop",
                      {"type": "content_block_stop", "index": idx})

        elif btype == "thinking":
            yield sse("content_block_start", {
                "type": "content_block_start",
                "index": idx,
                "content_block": {"type": "thinking", "thinking": ""},
            })
            for chunk in chunk_text(block.get("thinking", ""), 60):
                yield sse("content_block_delta", {
                    "type": "content_block_delta",
                    "index": idx,
                    "delta": {"type": "thinking_delta", "thinking": chunk},
                })
                await asyncio.sleep(0)
            if block.get("signature"):
                yield sse("content_block_delta", {
                    "type": "content_block_delta",
                    "index": idx,
                    "delta": {"type": "signature_delta",
                              "signature": block["signature"]},
                })
            yield sse("content_block_stop",
                      {"type": "content_block_stop", "index": idx})

    # message_delta with final stop_reason + output usage
    yield sse("message_delta", {
        "type": "message_delta",
        "delta": {
            "stop_reason": message.get("stop_reason"),
            "stop_sequence": message.get("stop_sequence"),
        },
        "usage": {"output_tokens": usage.get("output_tokens", 0)},
    })
    yield sse("message_stop", {"type": "message_stop"})


def _salvage_truncated(upstream: Any) -> str:
    """Recover assistant text from a relay body that was truncated mid-JSON.

    Heavy requests (large context + thinking + stream) sometimes get their
    response cut off by the relay, leaving an unparseable body like
    `{"content": [{"text": "real answer ...` with no closing braces. Rather
    than fail the whole turn, pull the partial text out so the user still gets
    the answer that was already generated.

    Returns the recovered text, or "" if nothing usable could be extracted.
    """
    if not isinstance(upstream, dict):
        return ""
    raw = upstream.get("_raw")
    if not isinstance(raw, str) or not raw.strip():
        return ""
    # Only salvage bodies that actually look like a (partial) assistant reply.
    if '"text"' not in raw:
        return ""
    parts: List[str] = []
    # Walk every `"text": "..."` chunk, decoding JSON string escapes. The LAST
    # one may be unterminated (truncation point) -> take whatever is there.
    i = 0
    marker = '"text":'
    while True:
        j = raw.find(marker, i)
        if j < 0:
            break
        k = raw.find('"', j + len(marker))
        if k < 0:
            break
        # scan the string body, honouring backslash escapes, until an
        # unescaped closing quote OR end-of-buffer (truncated).
        buf = []
        p = k + 1
        n = len(raw)
        closed = False
        while p < n:
            c = raw[p]
            if c == "\\" and p + 1 < n:
                buf.append(raw[p:p + 2]); p += 2; continue
            if c == '"':
                closed = True; break
            buf.append(c); p += 1
        frag = "".join(buf)
        try:
            frag = json.loads('"' + frag + '"')
        except Exception:
            # last fragment truncated inside an escape -> best-effort cleanup
            try:
                frag = json.loads('"' + frag.rstrip("\\") + '"')
            except Exception:
                frag = frag.encode("utf-8", "ignore").decode("unicode_escape", "ignore")
        if frag:
            parts.append(frag)
        i = p + 1 if closed else n
    text = "".join(parts).strip()
    return text


async def stream_with_keepalive(payload: Dict[str, Any],
                                client_req: Dict[str, Any],
                                feats: Dict[str, Any],
                                t0: float,
                                keepalive_s: float = 2.0,
                                breakdown: Optional[Dict[str, Any]] = None):
    """Streaming path that eliminates the "dead air" while the (non-stream)
    upstream is still composing its full reply.

    It opens the SSE stream to the client IMMEDIATELY (so the client sees bytes
    and never times out / feels stalled), emits a `ping` every `keepalive_s`
    seconds while call_upstream runs concurrently in the background, and only
    then streams the real content via synthesize_sse.
    """
    # Open the stream right away with a spec-correct message_start FIRST, so
    # the client sees bytes immediately and the ordering stays valid.
    provisional_id = "msg_" + uuid.uuid4().hex[:16]
    yield sse("message_start", {
        "type": "message_start",
        "message": {
            "id": provisional_id,
            "type": "message",
            "role": "assistant",
            "model": payload.get("model") or CONFIG["model"],
            "content": [],
            "stop_reason": None,
            "stop_sequence": None,
            "usage": {"input_tokens": 0, "output_tokens": 0},
        },
    })
    yield sse("ping", {"type": "ping"})

    # Run the upstream call (incl. any server-side web-tool round-trips)
    # concurrently with the keepalive pings.
    task = asyncio.ensure_future(resolve_server_tools(payload, feats))
    try:
        while not task.done():
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=keepalive_s)
            except asyncio.TimeoutError:
                # still waiting -> send a keepalive ping and loop
                yield sse("ping", {"type": "ping"})
            except Exception:
                # the task raised; break out and handle below via task.result()
                break

        status, upstream = task.result()
    except Exception as e:
        log_event({"model": payload.get("model"), "stream": True,
                   "error": f"upstream connection: {e}"})
        err = {"type": "error",
               "error": {"type": "api_error",
                         "message": f"Upstream connection failed: {e}"}}
        yield sse("error", err)
        return

    # CASE A: the relay returned a FULLY-FORMED assistant message but tagged it
    # with an error HTTP status (seen: a real {"content":[...]} body carrying a
    # 502/5xx code). The payload is perfectly usable -> treat it as success and
    # process it normally instead of throwing the good answer away.
    if status >= 400 and isinstance(upstream, dict) \
            and isinstance(upstream.get("content"), list) and upstream.get("content"):
        log_event({"model": payload.get("model"), "stream": True, "status": status,
                   "note": "usable content on error status -> recovered as success",
                   "recovered_blocks": len(upstream["content"])})
        status = 200

    if status >= 400:
        # LAST-RESORT SALVAGE: a heavy request (big context + thinking) can come
        # back with its JSON truncated mid-stream by the relay. If the truncated
        # body still carries real assistant text, recover that text and stream it
        # to the client instead of failing the whole turn.
        salvaged = _salvage_truncated(upstream)
        if salvaged:
            log_event({"model": payload.get("model"), "stream": True,
                       "status": 200, "note": "salvaged truncated upstream body",
                       "salvaged_chars": len(salvaged)})
            message = {"id": provisional_id, "type": "message", "role": "assistant",
                       "model": payload.get("model") or CONFIG["model"],
                       "content": [{"type": "text", "text": salvaged}],
                       "stop_reason": "end_turn", "stop_sequence": None,
                       "usage": {"input_tokens": 0, "output_tokens": 0}}
            async for evt in synthesize_sse(message, emit_start=False):
                yield evt
            return
        msg = ""
        if isinstance(upstream, dict):
            msg = (upstream.get("error", {}) or {}).get("message") \
                  or upstream.get("message") \
                  or upstream.get("_raw") \
                  or json.dumps(upstream, ensure_ascii=False)[:500]
        else:
            msg = str(upstream)[:500]
        log_event({"model": payload.get("model"), "stream": True,
                   "status": status, "error": (msg or "")[:200], "attempts": 5})
        etype = "invalid_request_error" if status == 400 else "api_error"
        yield sse("error", {"type": "error",
                            "error": {"type": etype,
                                      "message": f"Upstream {status} (after 5 attempt(s)): {msg}"}})
        return

    message = process_upstream_message(upstream, client_req, feats)
    meta = message.pop("_meta", {})
    dt = round(time.time() - t0, 2)
    log_event({
        "model": message.get("model"), "stream": True, "status": status,
        "latency_s": dt, "client_tools": meta.get("client_tools", 0),
        "tool_use": meta.get("has_tool_use", False),
        "had_thinking": meta.get("had_thinking", False),
        "stop_reason": message.get("stop_reason"),
        "usage": message.get("usage"),
        "breakdown": breakdown or {},
    })

    # Now stream the real content. message_start was already emitted above,
    # so skip it here to keep the SSE event ordering spec-correct.
    async for evt in synthesize_sse(message, emit_start=False):
        yield evt


def chunk_text(text: str, size: int):
    if not text:
        return
    for i in range(0, len(text), size):
        yield text[i:i + size]


# --------------------------------------------------------------------------- #
# FastAPI app
# --------------------------------------------------------------------------- #

app = FastAPI(title="JDW Fix Proxy")


def anthropic_error(status: int, err_type: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"type": "error", "error": {"type": err_type, "message": message}},
    )


def _clean_upstream_error(raw_text: str, status: int, ctype: str = "") -> str:
    """Turn an ugly upstream error body (often a full Cloudflare HTML page) into
    a short, human-readable reason. Prevents '<!DOCTYPE html> <!--[if lt IE 7'
    from being dumped into the dashboard / client error field."""
    txt = (raw_text or "").strip()
    looks_html = ("html" in (ctype or "").lower()
                  or txt[:15].lower().startswith("<!doctype html")
                  or txt[:5].lower() == "<html")
    if looks_html:
        # Cloudflare edge pages carry the real reason in a known phrase set.
        low = txt.lower()
        cf = {
            502: "upstream is down (bad gateway)",
            503: "upstream temporarily unavailable",
            504: "upstream timed out (gateway timeout)",
            520: "upstream returned an unknown error",
            521: "upstream server is down",
            522: "connection to upstream timed out",
            523: "upstream is unreachable",
            524: "upstream took too long to respond",
        }
        if status in cf:
            return f"{cf[status]} (HTTP {status})"
        for phrase in ("bad gateway", "service temporarily unavailable",
                       "gateway timeout", "web server is down",
                       "connection timed out", "origin is unreachable"):
            if phrase in low:
                return f"{phrase} (HTTP {status})"
        return f"upstream returned an HTML error page (HTTP {status})"
    # Non-HTML: collapse whitespace and cap length.
    oneline = re.sub(r"\s+", " ", txt)
    return oneline[:200] if oneline else f"upstream error (HTTP {status})"


def _upstream_auth_headers(api_key: str, *, json_body: bool = True) -> Dict[str, str]:
    """Build upstream request headers. CRUCIAL: when the key is empty we must
    OMIT the auth headers entirely -- an empty 'Authorization: Bearer ' value
    (trailing space) is an illegal HTTP header value and makes httpx/h11 raise
    'Illegal header value b\\'Bearer \\'' on every request instead of a clean
    401. We send both x-api-key and Bearer when a key is present (some relays
    check one, some the other)."""
    headers: Dict[str, str] = {"anthropic-version": "2023-06-01"}
    if json_body:
        headers["Content-Type"] = "application/json"
    key = (api_key or "").strip()
    if key:
        headers["x-api-key"] = key
        headers["Authorization"] = f"Bearer {key}"
    return headers


async def _try_one_key(client: httpx.AsyncClient, url: str, api_key: str,
                       payload: Dict[str, Any]) -> Tuple[int, Dict[str, Any], Optional[Exception]]:
    """Run the full retry loop for a SINGLE api key.

    Returns (status, data, last_exc). status == 0 means every attempt hit a
    network-level exception (last_exc carries the reason).
    """
    # Send BOTH auth styles: some upstreams expect Anthropic-native `x-api-key`,
    # others expect `Authorization: Bearer`. Sending both is safe and fixes
    # sporadic "API key is invalid" when the relay checks the other header.
    headers = _upstream_auth_headers(api_key, json_body=True)
    # NOTE: 401/403 are NOT retried here -- a real key rejection is handled ONCE
    # by call_upstream (it moves to the fallback key). Retrying auth errors here
    # would multiply requests (3x per key x 2 keys = 6) and hammer an already
    # overloaded relay. We only retry genuine transient/capacity errors.
    retryable = {408, 409, 425, 429, 500, 502, 503, 504, 529}
    # Longer, growing backoff for capacity errors: hammering a relay that just
    # said "no available channel" only makes the shortage worse. Give it room
    # to recover between attempts instead of adding load.
    # Heavy requests (large context + thinking + stream) sometimes get their
    # response truncated mid-JSON by the relay. That is transient, so we retry
    # several times with growing back-off to give the relay time to free a slot.
    backoff = [float(x) for x in (CONFIG.get("retry_backoff_s") or [0.8, 2.0])]
    max_attempts = max(1, int(CONFIG.get("retry_max_attempts", 3) or 3))
    status = 0
    data: Dict[str, Any] = {}
    last_exc: Optional[Exception] = None
    for attempt in range(1, max_attempts + 1):
        if attempt > 1:
            delay = backoff[min(attempt - 2, len(backoff) - 1)]
            log_event({"retry": attempt, "after_status": status,
                       "sleep_s": delay, "note": "retry upstream"})
            await asyncio.sleep(delay)
        try:
            r = await client.post(url, headers=headers, json=payload)
        except Exception as e:  # network error -> retry
            last_exc = e
            status = 0
            data = {"error": {"type": "api_error",
                              "message": f"upstream connection: {e}"}}
            continue
        last_exc = None
        status = r.status_code
        raw_text = r.text or ""
        bad_body = False
        if raw_text.strip() == "":
            # Upstream dropped the connection / returned an empty body.
            # This is a transient relay failure (seen as {"_raw": ""}),
            # NOT a real key error -> retry it.
            bad_body = True
            data = {"_raw": "",
                    "error": {"type": "api_error",
                              "message": "upstream returned an empty response body"}}
        else:
            try:
                data = r.json()
            except Exception as _pe:
                # Unparseable body: preserve RAW so the reason is visible,
                # and treat as transient -> retry.
                bad_body = True
                log_event({"parse_fail": True, "status": status,
                           "raw_len": len(raw_text),
                           "raw_head": raw_text[:120],
                           "raw_tail": raw_text[-200:],
                           "parse_err": str(_pe)[:200],
                           "ctype": r.headers.get("content-type", "")})
                data = {"_raw": raw_text,
                        "error": {"type": "api_error",
                                  "message": _clean_upstream_error(
                                      raw_text, status,
                                      r.headers.get("content-type", ""))}}
            else:
                # Parsed OK but a 2xx with no usable content is also a
                # transient relay glitch -> retry.
                if status < 400 and isinstance(data, dict) \
                        and not data.get("content") and not data.get("error"):
                    bad_body = True
                # "No available channel" (and similar capacity messages) mean the
                # relay has no free upstream slot right now -> transient, retry it
                # instead of passing the error straight through to the client.
                if "no available channel" in raw_text.lower() \
                        or "no channel" in raw_text.lower():
                    bad_body = True

        if bad_body:
            log_event({"attempt": attempt, "status": status,
                       "note": "empty/invalid upstream body -> retry"})
            if attempt < max_attempts:
                continue
            # last attempt: an empty/invalid body is ALWAYS a transient relay
            # glitch (e.g. sporadic 403/empty or "no available channel"), never
            # a real key rejection -> surface as 502 so call_upstream does not
            # misclassify it as an auth failure / "invalid key".
            status = 502
            return status, data, last_exc

        if status < 400 or status not in retryable:
            return status, data, last_exc
        # retryable status -> loop again
    return status, data, last_exc


async def call_upstream(payload: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
    url = CONFIG["upstream_base_url"].rstrip("/") + "/messages"
    keys: List[str] = []
    primary = CONFIG.get("api_key", "") or ""
    if primary:
        keys.append(primary)
    for k in (CONFIG.get("fallback_api_keys") or []):
        k = (k or "").strip()
        if k and k not in keys:
            keys.append(k)
    if not keys:
        # No key anywhere (config.json empty AND no JDW_API_KEY env var). Fail
        # fast with a clear, actionable message instead of firing keyless
        # requests that the relay rejects (and which used to crash on the empty
        # 'Bearer ' header). This is the exact state the dashboard shows as a
        # masked-but-empty key.
        log_event({"model": payload.get("model"), "status": 401,
                   "error": "no API key configured"})
        return 401, {"error": {"type": "authentication_error",
                               "message": ("No JDW API key configured. Open the "
                                           "dashboard and paste your key into the "
                                           "'API key' field, then Save.")}}

    timeout = httpx.Timeout(
        timeout=float(CONFIG.get("upstream_timeout_s", 90) or 90),
        connect=float(CONFIG.get("upstream_connect_timeout_s", 8) or 8),
        write=float(CONFIG.get("upstream_write_timeout_s", 30) or 30),
        pool=float(CONFIG.get("upstream_pool_timeout_s", 5) or 5),
    )
    auth_reject = {401, 403}
    status = 0
    data: Dict[str, Any] = {}
    last_exc: Optional[Exception] = None

    if _circuit_open():
        raise RuntimeError("upstream circuit breaker is open; retry shortly")

    limit = max(2, int(CONFIG.get("max_concurrent_upstream", 8) or 8))
    async with _UPSTREAM_SEMAPHORE:
        async with httpx.AsyncClient(
            timeout=timeout,
            limits=httpx.Limits(
                max_connections=limit,
                max_keepalive_connections=limit,
            ),
        ) as client:
            for idx, key in enumerate(keys):
                if idx > 0:
                    log_event({"fallback_key": idx, "after_status": status,
                               "note": "previous key rejected -> trying fallback key"})
                status, data, last_exc = await _try_one_key(client, url, key, payload)
                if status < 400 or status not in auth_reject:
                    if last_exc is not None and status == 0:
                        if idx < len(keys) - 1:
                            continue
                        raise last_exc
                    _circuit_success()
                    if status < 400:
                        log_event({"key_used": "primary" if idx == 0 else f"fallback#{idx}",
                                   "key_masked": masked_key(key), "status": status,
                                   "note": "upstream OK with this key"})
                    return status, data

@app.post("/v1/messages")
async def v1_messages(request: Request):
    feats = CONFIG["features"]
    try:
        client_req = await request.json()
    except Exception:
        return anthropic_error(400, "invalid_request_error", "Request body is not valid JSON.")

    if not isinstance(client_req, dict) or "messages" not in client_req:
        return anthropic_error(400, "invalid_request_error", "Missing required field: messages.")

    wants_stream = bool(client_req.get("stream"))
    payload = build_upstream_payload(client_req, feats)
    # Pull the diagnostic breakdown out so it is NOT sent upstream.
    breakdown = payload.pop("_breakdown", {})

    t0 = time.time()

    # STREAMING PATH: open the SSE stream immediately and run the upstream call
    # concurrently with keepalive pings, so the client never sees dead air.
    if wants_stream:
        return StreamingResponse(
            stream_with_keepalive(payload, client_req, feats, t0, breakdown=breakdown),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive",
                     "X-Accel-Buffering": "no"},
        )

    # NON-STREAMING PATH: wait for the full upstream reply, then return JSON.
    try:
        status, upstream = await resolve_server_tools(payload, feats)
    except Exception as e:
        log_event({"model": payload.get("model"), "stream": wants_stream,
                   "error": f"upstream connection: {e}"})
        return anthropic_error(502, "api_error", f"Upstream connection failed: {e}")

    # CASE A (non-stream): fully-formed assistant body tagged with an error HTTP
    # status -> the content is usable, treat it as success.
    if status >= 400 and isinstance(upstream, dict) \
            and isinstance(upstream.get("content"), list) and upstream.get("content"):
        log_event({"model": payload.get("model"), "stream": wants_stream, "status": status,
                   "note": "usable content on error status -> recovered as success",
                   "recovered_blocks": len(upstream["content"])})
        status = 200

    if status >= 400:
        # LAST-RESORT SALVAGE (non-stream path): recover partial text from a
        # relay body truncated mid-JSON instead of failing the whole turn.
        salvaged = _salvage_truncated(upstream)
        if salvaged:
            log_event({"model": payload.get("model"), "stream": wants_stream,
                       "status": 200, "note": "salvaged truncated upstream body",
                       "salvaged_chars": len(salvaged)})
            return JSONResponse(status_code=200, content={
                "id": "msg_" + uuid.uuid4().hex[:16], "type": "message",
                "role": "assistant",
                "model": payload.get("model") or CONFIG["model"],
                "content": [{"type": "text", "text": salvaged}],
                "stop_reason": "end_turn", "stop_sequence": None,
                "usage": {"input_tokens": 0, "output_tokens": 0}})
        # Wrap upstream errors in Anthropic shape, surfacing the RAW reason so
        # the real upstream cause is visible (not a vague "invalid key").
        msg = ""
        if isinstance(upstream, dict):
            msg = (upstream.get("error", {}) or {}).get("message") \
                  or upstream.get("message") \
                  or upstream.get("_raw") \
                  or json.dumps(upstream, ensure_ascii=False)[:500]
        else:
            msg = str(upstream)[:500]
        log_event({"model": payload.get("model"), "stream": wants_stream,
                   "status": status, "error": (msg or "")[:200],
                   "attempts": 5})
        etype = "invalid_request_error" if status == 400 else "api_error"
        return anthropic_error(status, etype,
                               f"Upstream {status} (after 5 attempt(s)): {msg}")

    message = process_upstream_message(upstream, client_req, feats)
    meta = message.pop("_meta", {})
    dt = round(time.time() - t0, 2)

    log_event({
        "model": message.get("model"),
        "stream": wants_stream,
        "status": status,
        "latency_s": dt,
        "client_tools": meta.get("client_tools", 0),
        "tool_use": meta.get("has_tool_use", False),
        "had_thinking": meta.get("had_thinking", False),
        "stop_reason": message.get("stop_reason"),
        "usage": message.get("usage"),
        "breakdown": breakdown,
    })

    # (The streaming path returned earlier via stream_with_keepalive.)
    return JSONResponse(content=message)


@app.get("/health")
async def health():
    with _CIRCUIT_LOCK:
        circuit = dict(_CIRCUIT)
    return JSONResponse(content={
        "ok": not _circuit_open(),
        "service": "jdw-proxy",
        "upstream": CONFIG.get("upstream_base_url"),
        "circuit": circuit,
        "limits": {
            "max_concurrent_upstream": CONFIG.get("max_concurrent_upstream", 8),
            "timeout_s": CONFIG.get("upstream_timeout_s", 90),
        },
    })


@app.get("/admin/health")
async def admin_health():
    now = time.time()
    if now - float(_HEALTH_CACHE.get("ts", 0)) < 10:
        return JSONResponse(content=dict(_HEALTH_CACHE))
    started = time.perf_counter()
    url = CONFIG["upstream_base_url"].rstrip("/") + "/models"
    key = CONFIG.get("api_key", "") or ""
    headers = _upstream_auth_headers(key, json_body=False)
    result = {"ts": now, "ok": False, "status": 0, "latency_ms": None, "error": ""}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(8, connect=4)) as client:
            r = await client.get(url, headers=headers)
            result["status"] = r.status_code
            result["ok"] = r.status_code < 400
            result["latency_ms"] = round((time.perf_counter() - started) * 1000, 1)
            if not result["ok"]:
                result["error"] = (r.text or "")[:180]
    except Exception as e:
        result["error"] = str(e)[:180]
        result["latency_ms"] = round((time.perf_counter() - started) * 1000, 1)
    _HEALTH_CACHE.update(result)
    return JSONResponse(content=result)



def _openai_to_anthropic(req: Dict[str, Any]) -> Dict[str, Any]:
    messages = []
    system_parts = []
    for m in req.get("messages") or []:
        role, content = m.get("role"), m.get("content")
        if role == "system":
            system_parts.append(content if isinstance(content, str) else json.dumps(content, ensure_ascii=False))
        elif role in ("user", "assistant"):
            messages.append({"role": role, "content": content if content is not None else ""})
        elif role == "tool":
            messages.append({"role": "user", "content": [{
                "type": "tool_result", "tool_use_id": m.get("tool_call_id", "toolu_unknown"),
                "content": content if content is not None else "",
            }]})
    out = {"model": req.get("model") or CONFIG["model"], "messages": messages,
           "stream": bool(req.get("stream")),
           "max_tokens": req.get("max_tokens") or req.get("max_completion_tokens") or 4096}
    if system_parts: out["system"] = "\n\n".join(system_parts)
    for k in ("temperature", "top_p"):
        if k in req and req[k] is not None: out[k] = req[k]
    if req.get("stop") is not None:
        out["stop_sequences"] = req["stop"] if isinstance(req["stop"], list) else [req["stop"]]
    tools=[]
    for t in req.get("tools") or []:
        fn=t.get("function") or {}
        if fn.get("name"):
            tools.append({"name":fn["name"],"description":fn.get("description",""),
                          "input_schema":fn.get("parameters") or {"type":"object"}})
    if tools: out["tools"]=tools
    tc=req.get("tool_choice")
    if tc=="auto": out["tool_choice"]={"type":"auto"}
    elif tc=="none": out["tool_choice"]={"type":"none"}
    elif isinstance(tc,dict) and isinstance(tc.get("function"),dict):
        out["tool_choice"]={"type":"tool","name":tc["function"].get("name","")}
    return out


def _anthropic_to_openai(message: Dict[str, Any], request_model: str) -> Dict[str, Any]:
    text=[]; tool_calls=[]
    for b in message.get("content",[]) or []:
        if b.get("type")=="text": text.append(b.get("text",""))
        elif b.get("type")=="tool_use":
            tool_calls.append({"id":b.get("id") or ("call_"+uuid.uuid4().hex[:12]),
                               "type":"function","function":{"name":b.get("name",""),
                               "arguments":json.dumps(b.get("input") or {},ensure_ascii=False)}})
    finish={"end_turn":"stop","max_tokens":"length","tool_use":"tool_calls","stop_sequence":"stop"}.get(
        message.get("stop_reason"),"stop")
    u=message.get("usage") or {}; inp=int(u.get("input_tokens",0) or 0); out=int(u.get("output_tokens",0) or 0)
    msg={"role":"assistant","content":"\n\n".join(text) if text else None}
    if tool_calls: msg["tool_calls"]=tool_calls
    return {"id":message.get("id") or ("chatcmpl_"+uuid.uuid4().hex[:16]),
            "object":"chat.completion","created":int(time.time()),
            "model":request_model or message.get("model") or CONFIG["model"],
            "choices":[{"index":0,"message":msg,"finish_reason":finish}],
            "usage":{"prompt_tokens":inp,"completion_tokens":out,"total_tokens":inp+out}}


async def _openai_stream(message: Dict[str, Any], model: str):
    data=_anthropic_to_openai(message,model); choice=data["choices"][0]; msg=choice["message"]
    content=msg.get("content")
    if content:
        for chunk in chunk_text(content,80):
            yield "data: "+json.dumps({"id":data["id"],"object":"chat.completion.chunk","created":data["created"],
                "model":data["model"],"choices":[{"index":0,"delta":{"role":"assistant","content":chunk},"finish_reason":None}],
                },ensure_ascii=False)+"\n\n"
            await asyncio.sleep(0)
    if msg.get("tool_calls"):
        for tc in msg["tool_calls"]:
            yield "data: "+json.dumps({"id":data["id"],"object":"chat.completion.chunk","created":data["created"],
                "model":data["model"],"choices":[{"index":0,"delta":{"tool_calls":[tc]},"finish_reason":None}]},
                ensure_ascii=False)+"\n\n"
    yield "data: "+json.dumps({"id":data["id"],"object":"chat.completion.chunk","created":data["created"],
        "model":data["model"],"choices":[{"index":0,"delta":{},"finish_reason":choice["finish_reason"]}]},
        ensure_ascii=False)+"\n\n"
    yield "data: [DONE]\n\n"


@app.post("/v1/chat/completions")
async def openai_chat_completions(request: Request):
    try: req=await request.json()
    except Exception:
        return JSONResponse(status_code=400,content={"error":{"type":"invalid_request_error","message":"Request body is not valid JSON."}})
    if not isinstance(req,dict) or not req.get("messages"):
        return JSONResponse(status_code=400,content={"error":{"type":"invalid_request_error","message":"Missing required field: messages."}})
    anth_req=_openai_to_anthropic(req); payload=build_upstream_payload(anth_req,CONFIG["features"]); payload.pop("_breakdown",None)
    try: status,upstream=await resolve_server_tools(payload,CONFIG["features"])
    except Exception as e:
        return JSONResponse(status_code=503,content={"error":{"type":"api_error","message":str(e)}})
    if status>=400:
        msg=(upstream.get("error",{}) or {}).get("message",str(upstream)) if isinstance(upstream,dict) else str(upstream)
        return JSONResponse(status_code=status,content={"error":{"type":"api_error","message":msg[:500]}})
    message=process_upstream_message(upstream,anth_req,CONFIG["features"])
    if req.get("stream"):
        return StreamingResponse(_openai_stream(message,req.get("model") or CONFIG["model"]),
                                 media_type="text/event-stream",
                                 headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no"})
    return JSONResponse(content=_anthropic_to_openai(message,req.get("model") or CONFIG["model"]))


@app.post("/v1/messages/count_tokens")
async def count_tokens(request: Request):
    try:
        req = await request.json()
    except Exception:
        return anthropic_error(400, "invalid_request_error", "Body is not valid JSON.")
    total = 0
    total += approx_tokens(extract_system_text(req.get("system")))
    for m in req.get("messages", []):
        total += approx_tokens(flatten_content_blocks(m.get("content"), m.get("role", "user")))
    for t in req.get("tools") or []:
        total += approx_tokens(json.dumps(t, ensure_ascii=False))
    return JSONResponse(content={"input_tokens": total})


@app.get("/v1/models")
async def models():
    url = CONFIG["upstream_base_url"].rstrip("/") + "/models"
    headers = _upstream_auth_headers(CONFIG.get("api_key", ""), json_body=False)
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(url, headers=headers)
            return JSONResponse(status_code=r.status_code, content=r.json())
    except Exception:
        # fallback to configured model
        return JSONResponse(content={
            "data": [{"type": "model", "id": CONFIG["model"],
                      "display_name": CONFIG["model"]}]
        })


# --------------------------------------------------------------------------- #
# Admin / dashboard API
# --------------------------------------------------------------------------- #

def masked_key(k: str) -> str:
    if not k:
        return ""
    if len(k) <= 10:
        return "*" * len(k)
    return k[:7] + "..." + k[-4:]


@app.get("/admin/config")
async def get_admin_config():
    c = json.loads(json.dumps(CONFIG))
    c["api_key_masked"] = masked_key(c.get("api_key", ""))
    c.pop("api_key", None)
    # expose fallback keys masked, so they can be shown but not leaked in full
    c["fallback_api_keys_masked"] = [masked_key(k) for k in (c.get("fallback_api_keys") or [])]
    c.pop("fallback_api_keys", None)
    return JSONResponse(content=c)


@app.post("/admin/config")
async def set_admin_config(request: Request):
    global CONFIG
    body = await request.json()
    with _config_lock:
        feats = body.get("features", {})
        if isinstance(feats, dict):
            CONFIG["features"].update(feats)
        for k in ("upstream_base_url", "model", "ui_lang", "upstream_timeout_s", "upstream_connect_timeout_s", "upstream_write_timeout_s", "upstream_pool_timeout_s", "max_concurrent_upstream", "retry_max_attempts", "retry_backoff_s", "circuit_breaker_failures", "circuit_breaker_cooldown_s"):
            if k in body and body[k] is not None:
                CONFIG[k] = body[k]
        # only overwrite api key if a non-masked value is provided
        newkey = body.get("api_key")
        if newkey and "..." not in newkey:
            CONFIG["api_key"] = newkey
        # fallback keys: replace the whole list only if a clean (non-masked)
        # list is sent. Masked entries contain "..." -> means "keep current".
        fb = body.get("fallback_api_keys")
        if isinstance(fb, list) and not any("..." in str(k) for k in fb):
            CONFIG["fallback_api_keys"] = [str(k).strip() for k in fb if str(k).strip()]
        save_config(CONFIG)
    return await get_admin_config()


@app.get("/admin/logs")
async def get_logs():
    with _log_lock:
        return JSONResponse(content={"logs": list(LOG)})


@app.post("/admin/logs/clear")
async def clear_logs():
    with _log_lock:
        LOG.clear()
    return JSONResponse(content={"ok": True})


@app.post("/admin/shutdown")
async def admin_shutdown():
    """Gracefully stop the proxy process (so the user can stop it from the
    dashboard without opening a terminal). We schedule the exit shortly after
    responding so the HTTP reply reaches the browser first."""
    import threading
    import os
    import signal as _signal

    def _stop():
        time.sleep(0.4)
        # Try a clean SIGINT/SIGTERM first; fall back to os._exit.
        try:
            os.kill(os.getpid(), getattr(_signal, "SIGTERM", _signal.SIGINT))
        except Exception:
            pass
        time.sleep(0.6)
        os._exit(0)

    threading.Thread(target=_stop, daemon=True).start()
    return JSONResponse(content={"ok": True, "message": "Proxy is shutting down"})


@app.get("/", response_class=HTMLResponse)
async def dashboard():
    return HTMLResponse(content=DASHBOARD_HTML)


# --------------------------------------------------------------------------- #
# First-launch client setup wizard
# --------------------------------------------------------------------------- #

import jdw_setup  # noqa: E402


def _proxy_base_url() -> str:
    """The Anthropic base URL clients should point at (this proxy)."""
    host = CONFIG.get("listen_host", "127.0.0.1")
    # Clients can't reach 0.0.0.0; advertise loopback instead.
    if host in ("0.0.0.0", "::", ""):
        host = "127.0.0.1"
    port = int(CONFIG.get("listen_port", 8181))
    return f"http://{host}:{port}/v1"


@app.get("/setup", response_class=HTMLResponse)
async def setup_page():
    return HTMLResponse(content=SETUP_HTML)


@app.get("/setup/clients")
async def setup_clients():
    """Return the numbered client list with live install detection + the
    endpoint/model/key-env the proxy will write into each client."""
    return JSONResponse(content={
        "clients": jdw_setup.list_clients(),
        "base_url": _proxy_base_url(),
        "model": CONFIG.get("model", "claude-opus-4-8"),
        "api_key_env": jdw_setup.API_KEY_ENV,
        "setup_completed": bool(CONFIG.get("setup_completed")),
    })


@app.post("/setup/apply")
async def setup_apply(request: Request):
    """Patch the selected clients' config files to use the JDW proxy.

    Body: {"selection": "134" | [1,3,4], "write_key": false,
           "api_key": "<optional inline key>"}
    By default we do NOT write the key (the user supplies it themselves); if
    write_key is true and the stored proxy key is available we pass it through
    to clients whose format needs an inline key.
    """
    try:
        body = await request.json()
    except Exception:
        return anthropic_error(400, "invalid_request_error", "Body is not valid JSON.")
    selection = body.get("selection")
    if selection in (None, "", []):
        return anthropic_error(400, "invalid_request_error", "No clients selected.")
    api_key = ""
    if body.get("write_key"):
        api_key = (body.get("api_key") or CONFIG.get("api_key") or "").strip()
    report = jdw_setup.apply_clients(
        selection, _proxy_base_url(), CONFIG.get("model", "claude-opus-4-8"), api_key)
    # Mark setup as completed as soon as at least one client was processed.
    if report.get("results"):
        with _config_lock:
            CONFIG["setup_completed"] = True
            save_config(CONFIG)
    return JSONResponse(content=report)


@app.post("/setup/elevate")
async def setup_elevate(request: Request):
    """Retry the selected clients with administrator rights (UAC prompt).
    Used when a plain apply returned needs_admin for a client."""
    try:
        body = await request.json()
    except Exception:
        return anthropic_error(400, "invalid_request_error", "Body is not valid JSON.")
    selection = body.get("selection")
    if selection in (None, "", []):
        return anthropic_error(400, "invalid_request_error", "No clients selected.")
    api_key = ""
    if body.get("write_key"):
        api_key = (body.get("api_key") or CONFIG.get("api_key") or "").strip()
    # Elevation blocks on a UAC prompt + child process; run it off the loop.
    report = await asyncio.to_thread(
        jdw_setup.run_elevated_apply, selection, _proxy_base_url(),
        CONFIG.get("model", "claude-opus-4-8"), api_key)
    if report.get("results"):
        with _config_lock:
            CONFIG["setup_completed"] = True
            save_config(CONFIG)
    return JSONResponse(content=report)


@app.post("/setup/skip")
async def setup_skip():
    """Mark the first-launch wizard as done without configuring any client."""
    with _config_lock:
        CONFIG["setup_completed"] = True
        save_config(CONFIG)
    return JSONResponse(content={"ok": True})


# Dashboard + setup HTML are defined in a separate module and appended below.
from dashboard_html import DASHBOARD_HTML  # noqa: E402
from setup_html import SETUP_HTML  # noqa: E402


# --------------------------------------------------------------------------- #
# Entrypoint
# --------------------------------------------------------------------------- #

def _open_browser_when_ready(url: str, host: str, port: int) -> None:
    """Wait until the HTTP server accepts connections, then open the default
    browser once. Runs in a background thread so it never blocks startup."""
    import socket
    import webbrowser
    probe_host = "127.0.0.1" if host in ("0.0.0.0", "::", "") else host
    deadline = time.time() + 15
    while time.time() < deadline:
        try:
            with socket.create_connection((probe_host, port), timeout=0.5):
                break
        except OSError:
            time.sleep(0.25)
    try:
        webbrowser.open(url)
    except Exception as e:
        print(f"[setup] could not open browser automatically: {e}")
        print(f"[setup] open this URL manually: {url}")


if __name__ == "__main__":
    import uvicorn
    host = CONFIG.get("listen_host", "127.0.0.1")
    port = int(CONFIG.get("listen_port", 8181))
    disp_host = "127.0.0.1" if host in ("0.0.0.0", "::", "") else host
    first_launch = not bool(CONFIG.get("setup_completed"))
    landing = "/setup" if first_launch else "/"
    url = f"http://{disp_host}:{port}{landing}"

    print(f"JDW Proxy V2 listening on http://{host}:{port}")
    print(f"  Anthropic endpoint : http://{disp_host}:{port}/v1")
    print(f"  Dashboard          : http://{disp_host}:{port}/")
    if first_launch:
        print(f"  First-launch setup : {url}")
    print(f"  Upstream           : {CONFIG['upstream_base_url']}")

    # Open the browser automatically: the setup wizard on first launch, or the
    # dashboard on subsequent launches. Disable with JDW_NO_BROWSER=1.
    if os.environ.get("JDW_NO_BROWSER") != "1":
        threading.Thread(target=_open_browser_when_ready,
                         args=(url, host, port), daemon=True).start()

    uvicorn.run(app, host=host, port=port, log_level="info")
