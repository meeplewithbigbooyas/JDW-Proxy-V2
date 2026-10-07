# JDW Fix Proxy V2.1

## Stability
- Added upstream concurrency cap.
- Added separate connection/write/pool/read timeouts.
- Reduced retries to a bounded transient-only policy.
- Added circuit breaker and health endpoints.
- Added cached upstream health status to dashboard.

## Compatibility
- Added local `/v1/chat/completions` OpenAI-compatible bridge.
- Codex/Aider can use the local bridge instead of hitting JDW's blocked OpenAI endpoint.
- Added Continue and Aider auto-configuration.

## Security
- `JDW_API_KEY` environment variable overrides the config-file key.
- Delivered `config.json` no longer contains the previously stored secret.
