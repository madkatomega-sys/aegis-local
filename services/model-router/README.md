# model-router

OpenAI-compatible router that maps stable model aliases (`aegis-reasoner`, `aegis-fast`,
`aegis-extractor`) to llama-server tiers running on the host.

- Routing rules: [`config/router.yaml`](../../config/router.yaml)
- Selection logic (pure, unit-tested): [`aegis_router/routing.py`](aegis_router/routing.py)
- HTTP layer: [`aegis_router/app.py`](aegis_router/app.py)

Endpoints: `POST /v1/chat/completions` (streaming and non-streaming), `GET /v1/models`,
`GET /status`, `GET /healthz`. Responses carry `X-Aegis-Tier` and `X-Aegis-Route` headers so
every routing decision is visible.

The container listens on port 8000, published to `127.0.0.1:4000` by Compose.
