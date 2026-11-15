# Local core API contract fixtures

Shared by the TypeScript server tests (`core/packages/local/test/server.test.ts`, which asserts the server emits exactly these bodies) and the Rust client tests (`desktop/crates/shell-core/tests/contract.rs`, which asserts the client parses every file here). Change a fixture and both suites must pass.

The local core binds to loopback only. On start it prints one line, `ready-line.json`, to stdout and reads the bearer token from stdin. Every endpoint, `GET /v1/health` included, needs `Authorization: Bearer <token>`.

| Endpoint | Request fixture | Response fixture |
|---|---|---|
| `GET /v1/health` | none | `health.json` |
| `GET /v1/settings` | none | `settings.json` |
| `PUT /v1/settings` | `settings-put-request.json` | `settings.json` |
| `POST /v1/secrets/load` | `secrets-load-request.json` | `secrets-load-response.json` |
| `POST /v1/test-connection` | none | `test-connection-ok.json`, `test-connection-model-key-rejected.json` |
| any, on failure | none | `error-unauthorized.json` |

Secret values appear only in `secrets-load-request.json`, and only as placeholders. No response carries a secret value.
