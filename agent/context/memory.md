# Memory

- The user loves Python.
- I am model Opus 4-7.
- **Core engineering principles (always apply):** Write serious code that follows:
  - **DRY** — Don't Repeat Yourself. Factor out duplication into well-named abstractions, but only once the duplication is real (not speculative).
  - **YAGNI** — You Aren't Gonna Need It. No speculative features, no "just in case" abstractions, no premature generality. Build what's needed now.
  - **KISS** — Keep It Simple, Stupid. Prefer the simplest design that solves the problem. Clarity > cleverness.
- **Codebase goal:** Build a simple but brilliantly effective agent harness. Optimize for simplicity and effectiveness — no bloat, no unnecessary abstractions, just a harness that works exceptionally well.

## OpenRouter API — key facts for building the harness

- **Base URL:** `https://openrouter.ai/api/v1` — primary endpoint: `POST /chat/completions`. Also `/responses` (OpenResponses format), `/models`, `/generation` (cost/stats lookup by id).
- **Auth:** `Authorization: Bearer <OPENROUTER_API_KEY>`. Optional attribution headers: `HTTP-Referer`, `X-OpenRouter-Title` (aka `X-Title`).
- **Schema:** OpenAI Chat Completions–compatible. Drop-in for the OpenAI Python SDK by setting `base_url="https://openrouter.ai/api/v1"`.
- **Docs as data:** every doc page has a `.md` variant; full index at `https://openrouter.ai/docs/llms.txt` and full content at `https://openrouter.ai/docs/llms-full.txt`. OpenAPI spec at `https://openrouter.ai/openapi.json` / `.yaml`.
- **Models:** `openrouter.ai/models`; filter via `?supported_parameters=tools|structured_outputs|...`. Model IDs are `vendor/model` (e.g. `anthropic/claude-sonnet-4.5`, `openai/gpt-5.2`). Variants via suffix: `:free`, `:nitro` (speed), `:thinking` (reasoning), `:online` (web search), `:extended` (context), `:exacto` (tool-calling quality).
- **Request body highlights:**
  - `messages` (OpenAI shape: `system|user|assistant|tool`, content can be string or content parts incl. `image_url`).
  - Standard params: `temperature`, `top_p`, `max_tokens`, `stop`, `seed`, `frequency_penalty`, `presence_penalty`, plus non-OpenAI ones (`top_k`, `min_p`, `top_a`, `repetition_penalty`) that get ignored if unsupported.
  - `tools` / `tool_choice` — standard OpenAI shape; transformed per-provider behind the scenes.
  - `response_format`: `{type:"json_object"}` or `{type:"json_schema", json_schema:{name, strict, schema}}` for strict structured outputs.
  - `stream: true` → SSE; usage is sent exactly once in the final chunk before `data: [DONE]`. Ignore SSE "comment" lines (lines starting with `:`).
  - **OpenRouter-only:** `models: string[]` (fallback list), `route: "fallback"`, `provider: {...}` (provider routing prefs incl. `order`, `allow_fallbacks`, `require_parameters`, `data_collection`, `sort: "price"|"throughput"|"latency"`), `plugins: [{id:"web"|"file-parser"|"response-healing"|"context-compression"}]`, `transforms: ["middle-out"]`, `user`.
- **Response:** `{id, model, choices:[{message|delta, finish_reason, ...}], usage:{prompt_tokens, completion_tokens, total_tokens}, created, object}`. Use the `id` against `GET /generation?id=...` for true cost.
- **Tool-calling loop:** standard OpenAI pattern — model returns `tool_calls`, you execute, append `{role:"tool", tool_call_id, content}`, call again. The `tools` array must be re-sent on every turn (router re-validates).
- **Reliability features worth leaning on:** automatic provider fallback on 5xx/rate limit, zero-completion insurance (no charge for empty responses), response caching, message transforms (`middle-out` compression).
- **Errors:** standard HTTP codes; `404` with `"no endpoints found that support tool use"` means the chosen model/provider combo doesn't support `tools` — fix via `provider.require_parameters: true` or pick a different model.
- **For our harness (Python, KISS):** use `httpx` directly against `/chat/completions` OR `openai` SDK with `base_url` override. Direct `httpx` keeps deps minimal and gives full access to OpenRouter-only fields (`models`, `provider`, `plugins`).
