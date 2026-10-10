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

## Erratum A2-E1 (2026-10-10; user-approved: "低强度推理" + "批准")

**Evidence** (the D13 live dry run, N = 3 planned, stub evaluator, disposable K-PIT copy,
allowlist network guard):
- the first call returned `finish_reason = length` with completion_tokens 8000, **all of
  them reasoning** (`reasoning_tokens = 8000`), and no content;
- `response.model` = `deepseek-flash` (the alias was confirmed);
- the executor stopped with GENERATOR_FAILURE after 1 call;
- the manifest showed `tokens_used = 0` and `n_used = 0`, although about 9,600 tokens were
  spent;
- the network log shows only `api.deepseek.com`; no key appeared in any output; the
  production K-PIT was unchanged.

**Corrections:**

1. **Thinking mode** (DeepSeek docs: on by default, effort `high`; `max_tokens` empirically
   includes reasoning):
   - the request passes **`reasoning_effort="low"`**, as a named parameter of
     `chat.completions.create` (verified present in the openai SDK 3.19.2);
   - **`max_tokens = 32000`**;
   - the `reasoning_effort` value is recorded in the settings and the call record.
2. **The token budget:** `DEFAULT_MAX_LLM_TOKEN_BUDGET` goes from 2,000,000 to
   **4,000,000** (a hard stop).
3. **Failed-call accounting (a defect):** the tokens of a failed generator call (when the
   provider returned usage, for example a `length` truncation) **must be added** to
   `tokens_used` and checked against the token hard stop. The manifest must record
   `failed_calls` (count) and `failed_call_tokens`. An API error without usage adds 0
   and is recorded as such.
4. **Wording correction (the planner's error):** §1 said that a `length` result is "a
   recorded failure that is counted in N and never retried". This is inconsistent with
   the frozen P4 stopping policy. **Corrected:** a `length` result is a typed generator
   failure; under the P4 / Phase-9 stopping policy the run **stops**
   (GENERATOR_FAILURE → RUN_INCOMPLETE). It is never retried, and no candidate is
   created.

## Erratum A2-E2 (2026-10-10; user-approved: "批准") — prompt template v2

**Evidence** (the N = 10 DeepSeek dry run at `bd23322`, plus an offline replay):
- 2 calls returned 14 entries; exact-N truncation was correct (10 counted, 4
  BUDGET_TRUNCATED);
- **all 10 counted entries were rejected**:
  - 12 of the 14 were `FAMILY_ESCAPE`: the model filled the prompt's optional
    `intended_family_id` with invented names, while the policy binds the family by
    PROGRAM_DECLARED;
  - 2 were `INVALID_FACTOR_SPEC`: `units: "indicator"`, because the prompt gives no
    per-role units.
- Replaying the earlier run2 responses through the same code still admits 21/21, so this
  is **not a code regression**.

**Correction:**

1. A **new** template file `smart_beta/research/prompts/campaign3_v2.txt` = v1 with exactly
   two changes:
   - (a) the `intended_family_id` line is removed from the OUTPUT FORMAT, plus one
     sentence stating that the family is assigned by the program and that the field must
     be omitted;
   - (b) the role table states each role's required `observation_period` and `units`,
     taken from the code's capabilities:

     | Role | observation_period | units |
     |---|---|---|
     | `tri` | INSTANT | ratio |
     | `ret` | PERIOD | fraction |
     | `dv` | PERIOD | currency |
     | `event_ear_k_kp1` | PERIOD | fraction |
     | `event_days_since_ea` | INSTANT | count |
     | `event_recent_ea_w<W>` | INSTANT | count |

     Sources: `trusted_inputs.py:1338-1370`, `pqv2_events.py:812-826`.

   The grammar, the feedback rendering, the history token and the firewall are
   unchanged.
2. `llm_generator_deepseek.py` renders with v2 (`render_prompt(…, template=v2_text)`) and
   reports `prompt_template_hash` = SHA-256(v2 bytes). C3-G and v1 are unchanged.
3. The Campaign-3 policy's `prompt_template_hash` is the DeepSeek generator's (v2) hash.
4. Re-run the N = 10 dry run after the merge.
