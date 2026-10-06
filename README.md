# JDW Fix Proxy

An **Anthropic-compatible** (`/v1/messages`) reverse proxy that sits in front of a
broken upstream relay and repairs its protocol violations on the fly, so any
standard Anthropic client (including coding agents) can talk to it reliably.

It exposes the normal Anthropic Messages API locally, forwards requests to the
upstream relay, and transparently fixes the bugs the relay introduces.

## Why?

The upstream relay (an Anthropic-style gateway) violates the protocol in several
ways that break tool-using clients. This proxy detects and repairs each one:

| # | Upstream bug | Fix applied by the proxy |
|---|--------------|--------------------------|
| 1 | **Streaming drops all text + `tool_use` blocks** (only `thinking` deltas survive). | Always calls the upstream in **non-stream** mode (where the body is intact) and **synthesizes a correct SSE stream** for the client. |
| 2 | **Client `tools` / `tool_choice` are discarded** — the model only sees the relay's own injected tools. | Injects the client's tools into the system prompt with a strict JSON `tool_call` output contract, then **parses the model's text back into real `tool_use` blocks** (`stop_reason=tool_use`). |
| 3 | **`max_tokens` ignored** (never returns `stop_reason=max_tokens`). | Truncates output to `max_tokens` and sets the stop reason correctly. |
| 4 | **`stop_sequences` ignored** (stop word leaks into output). | Cuts text before the stop sequence and reports it. |
| 5 | **~10.4k phantom context tokens inflate usage.** | Normalizes usage (subtracts the measured baseline). |
| 6 | **`thinking` blocks leak into content even when not requested.** | Optionally strips them. |
| 7 | **Fully-formed replies tagged with a 5xx status / truncated bodies / sporadic empty 403 responses.** | Recovers usable `content` from error-tagged bodies, salvages truncated JSON, and retries transient relay failures with back-off + multi-key fallback. |

Plus: local `/v1/messages/count_tokens`, `/v1/models` passthrough, proper
Anthropic-shaped errors, server-side image fetching for the model, and a web
dashboard at `/` for live request logs and runtime configuration.

## Requirements

- Python 3.10+
- See [`requirements.txt`](requirements.txt):
  - `fastapi>=0.110`
  - `uvicorn[standard]>=0.29`
  - `httpx>=0.27`

## Setup

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Create your config from the example and fill in your real values
cp config.example.json config.json
#   -> edit config.json: set upstream_base_url, api_key, (optional) fallback_api_keys, model

# 3. Run
python jdw_proxy.py
```

The proxy listens on `http://127.0.0.1:8181` by default (configurable).

## Configuration

All settings live in `config.json` (copy it from `config.example.json`). Key fields:

| Field | Meaning |
|-------|---------|
| `upstream_base_url` | Base URL of the upstream relay (e.g. `https://.../v1`). |
| `api_key` | Primary upstream API key. **Never commit this.** |
| `fallback_api_keys` | Optional list of backup keys; used automatically if the primary is rejected. |
| `listen_host` / `listen_port` | Where the proxy listens locally. |
| `model` | Upstream model name. |
| `upstream_timeout_s` | Upstream request timeout (seconds). |
| `features.*` | Toggle each repair/normalization behaviour (see below). |

### Feature flags (`features`)

| Flag | Default | Purpose |
|------|---------|---------|
| `tool_injection` | `true` | Inject client tools into the system prompt and parse them back. |
| `strict_tool_names` | `true` | Enforce exact tool-name matching. |
| `flatten_tool_history` | `true` | Flatten tool-call history into a form the relay accepts. |
| `strip_thinking` | `true` | Remove leaked `thinking` blocks from content. |
| `enforce_max_tokens` | `true` | Honour `max_tokens` and set `stop_reason`. |
| `enforce_stop_sequences` | `true` | Honour `stop_sequences`. |
| `usage_mode` | `normalized` | Usage reporting mode. |
| `usage_baseline_tokens` | `0` | Phantom-token baseline to subtract. |
| `compact_tool_schemas` / `ultra_compact_tools` | `true` | Shrink injected tool schemas to save context. |
| `max_history_messages` | `60` | Cap conversation history sent upstream (0 = no cap). |
| `max_tool_result_chars` | `0` | Cap tool-result size (0 = no cap). |
| `max_output_tokens` | `0` | Hard cap on output tokens (0 = off). |

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/v1/messages` | Anthropic Messages API (streaming + non-streaming). |
| `POST` | `/v1/messages/count_tokens` | Local token counting. |
| `GET`  | `/v1/models` | Upstream model passthrough. |
| `GET`  | `/` | Web dashboard (live logs + config). |
| `GET`  | `/admin/logs` | Recent request log (JSON). |
| `POST` | `/admin/logs/clear` | Clear the in-memory log. |

## Security notes

- **`config.json` is git-ignored** because it holds real API keys. Only
  `config.example.json` (with placeholders) is tracked.
- The proxy binds to `127.0.0.1` by default; do not expose it publicly without
  adding your own authentication.

## License

Personal/experimental project. Use at your own risk.
