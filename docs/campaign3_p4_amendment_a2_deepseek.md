# Campaign-3 P4 — amendment A2: generator provider DeepSeek (FROZEN 2026-10-10)

- **User decision (2026-10-10):** "D13还是用deepseek". Clarified: **"正式运行也换 DeepSeek"**.
- **What this replaces:** P4 design decision **D4** (Claude), and the C3-G model literal
  `claude-opus-5-5` in `docs/campaign3_p4_implementation_spec.md` §3/§4.
- **Unchanged:** all other P4 literals (N = 100, k = 5, the control slot, seed 20261012,
  the 2,000,000-token budget, the T-A evaluation, the information tiers, every invariant
  and mutation).

## 1. Frozen literals

| Item | Value |
|---|---|
| Provider | `deepseek` |
| Endpoint | the OpenAI-compatible base URL `https://api.deepseek.com` (DeepSeek API docs, fetched 2026-10-10) |
| SDK | the official `openai` Python SDK (present in the venv): `OpenAI(api_key=…, base_url=…, max_retries=0)`, then `client.chat.completions.create(model=…, messages=[system, user], max_tokens=…, stream=False)`. A client object is injected; tests use a fake client. No other SDK features |
| Model literal | **`deepseek-v4-flash`**, the same pair the project's Pi workers use (CLAUDE.md §2). The **`response.model` string returned by the API is recorded** per call, because the provider documents name aliasing |
| `max_tokens` | **8000**. The model may spend output tokens on reasoning; `finish_reason` is recorded, and `length` → a recorded failure that is counted in N and never retried |
| Temperature | no override (provider default; recorded as such) |
| Raw artifact | `choices[0].message.content` verbatim. A provider `reasoning_content` field, if present, is **not** stored in the artifact or shown to the history. Only its token count is accounted |
| Credentials | `DEEPSEEK_API_KEY` is read only when the real client is constructed. It is never logged or stored; the Phase-9 secret scan applies |
| Accounting | `tokens_used` = `usage.prompt_tokens + usage.completion_tokens`; `cost_used` only with a run-time price table, and **no price literal** |
| `generator_identity` | `deepseek:deepseek-v4-flash` |
| Prompt | the unchanged `smart_beta/research/prompts/campaign3_v1.txt` (provider-neutral; its hash is unchanged) |

## 2. Implementation (Pi task C3-D)

- **New file:** `smart_beta/research/llm_generator_deepseek.py`, with the same callable
  interface as C3-G (`(visible, feedback) -> GenerationOutput`), reusing C3-G's
  `GenerationOutput`, `PriceTable` and prompt rendering by import. **C3-G is not
  modified.**
- **Amended:** `smart_beta/campaign/campaign3_generation.py`.
  - `CAMPAIGN3_GENERATOR_IDENTITY` becomes `deepseek:deepseek-v4-flash`.
  - The executor's default generator factory selects C3-D.
  - No other behavioural change; every existing C3-X test still passes, with fixture
    identity updates only.
- **Tests (fake client, no network):**
  - the request shape;
  - usage accounting;
  - `response.model` recorded;
  - `finish_reason=length` → a typed failure;
  - `reasoning_content` excluded from the artifact;
  - a secret in the content → rejection;
  - an API error → a typed failure with no retry.
- **Mutations:**
  - D3-M1: reasoning text stored in the artifact;
  - D3-M2: `response.model` not recorded;
  - D3-M3: a silent retry;
  - D3-M4: the key written to the artifact;
  - D3-M5: a `length` truncation accepted as success;
  - D3-M6: the identity still `anthropic:…`.

## 3. Disclosure

The Claude adapter (C3-G) remains merged and tested but is unused by Campaign-3. A
provider switch after this freeze would be a new amendment.
