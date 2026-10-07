# JDW Fix Proxy — V2

An Anthropic-compatible (`/v1/messages`) reverse proxy that repairs the broken
JustDoWork (JDW) upstream relay, plus a bilingual (EN/RU) web dashboard.

## What's new in V2 — First-launch client setup

On the **first launch** the proxy opens your browser to a setup wizard
(`/setup`). It:

1. Lists the supported AI clients, **numbered**, with live "installed /
   not found" detection:
   1. Claude Code
   2. Claude Desktop
   3. ChatGPT Codex
   4. OpenCode
   5. DeepSeek Harness
   6. Continue
   7. Aider
2. Lets you pick several at once — click them, or type their numbers with no
   spaces (e.g. `134` = clients 1, 3 and 4).
3. **Automatically finds each selected client's config file and edits it** to
   add a `jdw` provider pointing at this local proxy (base URL, endpoint
   format / wire API, model). Every file is backed up first
   (`*.jdw-bak-<timestamp>`), and existing settings are preserved.
4. If a config file needs elevated rights, offers a **"Retry as
   administrator"** button (Windows UAC prompt).
5. Clearly **warns you that you must supply your own JDW API key** — the setup
   wires the clients to the proxy but never invents a key for you. For clients
   that read the key from an environment variable it uses `JDW_API_KEY`
   (`JDW_PROXY_API_KEY` for DeepSeek Harness).

After setup (or "Skip for now") the flag `setup_completed` is written to
`config.json`; later launches open the dashboard (`/`) directly.

To re-run the wizard any time, open `http://127.0.0.1:8181/setup`, or set
`"setup_completed": false` in `config.json`.

Disable the automatic browser pop with the env var `JDW_NO_BROWSER=1`.

### Where each client is configured

| # | Client           | File                                         | Mechanism                               |
|---|------------------|----------------------------------------------|-----------------------------------------|
| 1 | Claude Code      | `~/.claude/settings.json`                    | `env.ANTHROPIC_BASE_URL` + token + model|
| 2 | Claude Desktop   | `%APPDATA%/Claude/claude_desktop_config.json`| endpoint stored (no native provider UI) |
| 3 | ChatGPT Codex    | `~/.codex/config.toml`                        | `[model_providers.jdw]` + default        |
| 4 | OpenCode         | `~/.config/opencode/opencode.jsonc`          | `provider.jdw` (`@ai-sdk/anthropic`)    |
| 5 | DeepSeek Harness | `~/.dsh/profiles/desktop/cordis.patch.yml`   | `llm-pi-ai` provider + default model    |
| 6 | Continue          | `~/.continue/config.yaml`                   | Anthropic model + `apiBase`             |
| 7 | Aider             | `~/.aider.conf.yml`                         | OpenAI-compatible `/v1/chat/completions`|

## Run

```
pip install -r requirements.txt
python jdw_proxy.py
```

## URLs

- Dashboard: http://127.0.0.1:8181/
- First-launch setup: http://127.0.0.1:8181/setup
- Anthropic endpoint: http://127.0.0.1:8181/v1

## Dashboard tools

- `pwsh`
- `fetch_image`
- `read`
- `write`
- `edit`
- `grep`
- `glob`


## V2.1 resilience changes

- Bounds upstream concurrency instead of allowing unlimited concurrent requests.
- Separate connect/write/pool/read deadlines; the default total upstream deadline is 90s.
- Retries are limited to 3 attempts and only target transient network/429/5xx conditions.
- Circuit breaker opens after repeated upstream failures, preventing a local request storm while JDW is down.
- `/health` exposes local proxy/circuit state; `/admin/health` performs a cached upstream probe for the dashboard.
- Added `/v1/chat/completions` as a local OpenAI-to-Anthropic compatibility bridge. This lets OpenAI-style clients such as Codex/Aider use the same local proxy without sending OpenAI traffic directly to JDW.
- `JDW_API_KEY` environment variable overrides the key in `config.json`, so production deployments can keep secrets out of disk.
- Dashboard now shows upstream health and exposes the resilience limits.
- The setup wizard now detects/configures Continue and Aider in addition to the existing clients.

### Recommended startup

Set the key outside the config file when possible:

Windows PowerShell:
```powershell
$env:JDW_API_KEY="your-key"
python .\jdw_proxy.py
```

macOS/Linux:
```bash
export JDW_API_KEY="your-key"
python ./jdw_proxy.py
```

Do not commit `config.json` containing a real API key.
