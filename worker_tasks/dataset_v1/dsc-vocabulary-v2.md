# D-SC classification vocabulary — version `dsc-vocab-v2` (PLANNING; FROZEN BEFORE REMEASUREMENT)

- **Status:** written and committed **before** the single v2 offline
  remeasurement, per the user's v2 correction brief of 2026-09-27.
- **D-SC implementation is not authorized.**
- **Supersedes** `dsc-vocab-v1` (commit 58a7489), which is kept as history.
- **Unchanged from v1 unless stated below:** the policy basis
  (`dataset-v1-1-decisions.md` §1, including the REIT/SBI clarification),
  the states, the directory-flag rule, the admissibility rules, the
  exchange-transfer rule (EXCHANGE_MISMATCH_REVIEW never certifies or
  excludes) and the SEC XBRL-context extraction requirement.

## A. Semantic changes from v1

1. **Instrument-phrase grammar replaces head-first/tail-first position
   matching** (§1). An explicit non-common instrument term in the **listed
   instrument segment** always takes precedence over common or ADS words.
   Reference-clause wording (the underlying security) never classifies the
   listed instrument.
2. **Suffix contradiction:** explicit COMMON evidence together with an
   applicable NON_COMMON suffix gives **EVIDENCE_CONFLICT**, not COMMON (§3).
3. **ADR/ADS:** bare ADS/ADR evidence gives CLASS_UNRESOLVED(BARE_ADS).
   COMMON_STOCK(ADR_ADS) requires representation wording naming common or
   ordinary equity, or a context-bound SEC class title. A preferred reference
   gives NON_COMMON(DEPOSITARY_PREFERRED) (§1.5).
4. **Point-in-time temporal classification** replaces whole-key
   classification (§4). Classification is a timeline, queried at f using only
   evidence dated ≤ f−1.

## 1. String → string-class grammar (deterministic)

All matching is case-insensitive, on whitespace-collapsed text.

**1.1 Whole-name overrides.** These are v1 O1–O7, unchanged and in the same
order, applied to the whole name:
- test stock/issue/symbol: TEST_ISSUE;
- closed-end fund: CLOSED_END_FUND;
- business development company/corporation: BDC;
- exchange-traded fund/note, ETF, ETN: ETF;
- `\bfund\b`: FUND;
- tracking stock: TRACKING_STOCK;
- when-issued / when-distributed: WHEN_ISSUED.

**1.2 Candidate text.** If the name contains `" - "`, the candidate text is
the text after the last `" - "`. Otherwise it is the whole name.

**1.3 Reference split, then instrument segment.**
- **Split** the candidate text at the **first** reference marker:
  `,?\s*\b(each|representing|represents|exercisable|consisting|to (purchase|acquire|buy|subscribe)|convertible (into|for)|included as part of|underlying|entitling)\b`
  - Text after the marker is the **reference segment (Rf)**.
  - Text before it is the **pre-reference text (P)**.
- **Issuer part** (only for names without `" - "`): the **instrument segment
  (I)** is the text of P after the **last** issuer-terminator token **within
  P**:
  `\b(Inc|Incorporated|Corp|Corporation|Ltd|Limited|plc|PLC|N\.V|S\.A|L\.P|LLC|Co|Company|Trust|Group|Holdings|AG|SE)\b\.?,?`
  If P has no terminator, I = P. For dashed names, I = P.
  - Terminator-like words inside Rf (e.g. "Limited Partnership") never cut
    the phrase, because the listed instrument precedes its reference clause.
- An empty I gives CLASS_UNRESOLVED(ISSUER_ONLY).
- Parenthesized abbreviations are part of I; for example `(ADW)` is a
  warrant token.

**1.4 Non-common instrument terms in I.** If any term below matches in I,
the result is NON_COMMON, regardless of any common, ordinary or ADS words in
I or Rf. When several match, the first reason in this list is reported:

| Reason | Pattern in I |
|---|---|
| LP_OR_MLP_UNIT | `limited partner(ship)? interests?\|partnership units?\|\bcommon units?\b` |
| NONCOMMON_TRUST_OR_SBI | `units? of beneficial interest\|\btrust units?\b` |
| DEPOSITARY_PREFERRED | `depositary shares?.*\bpreferred\b\|\bpreferred\b.*depositary` |
| PREFERRED | `\bpreferred\b\|\bpreference shares?\b` |
| NOTE_OR_DEBT | `\bnotes?\b\|\bdebentures?\b\|\bbonds?\b\|\bsenior\b\|\bsubordinated\b` |
| WARRANT | `\bwarrants?\b\|\badws?\b` |
| RIGHT | `\bcontingent value rights?\b\|\brights?\b` |
| UNIT | `\bsub-?units?\b\|\bcorporate units?\b\|\btangible equity units?\b\|\bunits?\b` |

**1.5 Depositary evidence.** Applied only if 1.4 did not match, where I
matches `american deposit[ao]ry (shares?|receipts?)|\bads\b|\badrs?\b|sponsored adr|\bdepositary shares?\b`:
- Rf matches `preferred|preference`: NON_COMMON(DEPOSITARY_PREFERRED).
- Rf matches `\b(common (stock|shares?)|ordinary (shares?|equity))\b`:
  COMMON_STOCK(ADR_ADS).
- Otherwise:
  - bare American ADS/ADR gives CLASS_UNRESOLVED(BARE_ADS);
  - bare "Depositary Shares" gives CLASS_UNRESOLVED(GENERIC_SHARES).

**1.6 Beneficial interest (REIT/SBI rule, unchanged).** Where I matches
`beneficial interest`:
- if the whole name matches `\breit\b|real estate investment trust` **and**
  I matches `\bcommon\b`: COMMON_STOCK(REIT_COMMON);
- otherwise CLASS_UNRESOLVED(SBI_AMBIGUOUS).

**1.7 Possible tracking stock (unchanged).** I matching
`\bseries [a-z] (?!common\b)[a-z][\w&.\- ]* common (stock|shares?)\b` gives
CLASS_UNRESOLVED(POSSIBLE_TRACKING_STOCK).

**1.8 Common equity.** I matching `\b(new )?common (stock|shares?)\b|\bordinary shares?\b`:
- COMMON_STOCK(REIT_COMMON) if the whole name matches
  `\breit\b|real estate investment trust`;
- otherwise COMMON_STOCK(COMMON).

**1.9 Otherwise:**
- I matching `\bsubordinate voting shares\b|\bclass [a-z] shares\b|\bshares\b`:
  CLASS_UNRESOLVED(GENERIC_SHARES);
- I matching no stock/share/unit/note/interest word:
  CLASS_UNRESOLVED(ISSUER_ONLY);
- anything else: CLASS_UNRESOLVED(UNRECOGNIZED).

**Normative examples:**

| String | Result |
|---|---|
| "Series A Common Stock Purchase Warrants" | NON_COMMON(WARRANT) |
| "Class C-1 ADS (ADW)" | NON_COMMON(WARRANT) |
| "Units, each consisting of one share of Class A common stock and one warrant" | NON_COMMON(UNIT) |
| "Rights, each entitling the holder to one-tenth of one ordinary share" | NON_COMMON(RIGHT) |
| "American Depositary Shares, each representing one Ordinary Share" | COMMON_STOCK(ADR_ADS) |
| "Petrobras American Depositary Shares" | CLASS_UNRESOLVED(BARE_ADS) |
| "XYZ Limited American Depositary Shares, each representing one Class A Ordinary Share" | COMMON_STOCK(ADR_ADS) |
| "Depositary Shares, each representing 1/1,000th interest in a share of Preferred Stock" | NON_COMMON(DEPOSITARY_PREFERRED) |
| "KKR & Co. L.P. Common Units Representing Limited Partnership Interest" | NON_COMMON(LP_OR_MLP_UNIT) |
| "KKR & Co. Inc. Class A Common Stock" | COMMON_STOCK(COMMON) |
| "Preferred Apartment Communities, Inc. Common Stock" | COMMON_STOCK(COMMON) |
| "Common Stock When-Issued" | NON_COMMON(WHEN_ISSUED) |
| "AT&T Inc." | CLASS_UNRESOLVED(ISSUER_ONLY) |

## 2. Evidence points

- Each admissible capture row (v1 §3: interval-dated, symbol-matched,
  venue-admissible) yields one **dated evidence point**.
- A row flag overrides the name in the same row: Test Issue = Y gives
  NON_COMMON(TEST_ISSUE); ETF = Y gives NON_COMMON(ETF).
- A point is **explicit** if it is COMMON_STOCK or NON_COMMON. Unresolved
  points are recorded but never negate explicit points.
- Static evidence known at listing:
  - the authoritative test-symbol list gives NON_COMMON(TEST_ISSUE) for the
    whole interval;
  - the applicable suffix (v1 §3 list) gives NON_COMMON(reason) for the whole
    interval, as exclusion evidence only.

## 3. Suffix contradiction

If K has an applicable NON_COMMON suffix, then for every f at which the §4
timeline would yield COMMON_STOCK, the result is **EVIDENCE_CONFLICT
(SUFFIX_CONTRADICTION)**. A ticker suffix is static over the key, so no
temporal explanation is admissible for it.

Where the timeline yields NON_COMMON or nothing explicit, the suffix yields
NON_COMMON(reason).

## 4. Point-in-time temporal classification

**Query:** `class_state(K, f)` uses only the explicit points of K dated
**≤ f−1** (together with the static evidence of §2 and §3).

1. **No explicit point ≤ f−1:**
   - static test symbol: NON_COMMON(TEST_ISSUE);
   - applicable suffix: NON_COMMON(reason);
   - venue-mismatched explicit evidence ≤ f−1 exists:
     CLASS_UNRESOLVED(EXCHANGE_MISMATCH_REVIEW);
   - otherwise CLASS_UNRESOLVED (the latest unresolved reason ≤ f−1, or
     NO_ADMISSIBLE_EVIDENCE).
2. **Eligibility group.** Explicit points are compared by eligibility group
   G ∈ {COMMON, NON}. Different NON reasons are the same group, and so are
   different COMMON subtypes.
3. **Concurrent contradiction:** two explicit points with the **same date**
   and different G. The result is EVIDENCE_CONFLICT(CONCURRENT) for every f
   whose knowable points include that date.
4. **Switch count.** Let the explicit points ≤ f−1 be ordered by date and
   reduced to runs of equal G.
   - **One run:** the state is that G (with its subtype or reason).
   - **Two runs (documented temporal transition):** the state is the G of the
     second run. That state begins at the date of the **first point of the
     second run**, which is the evidence boundary. It is never backfilled
     before that date.
   - **Three or more runs (alternating evidence):**
     EVIDENCE_CONFLICT(ALTERNATING), from the date of the point that created
     the third run.
5. **Uncertain transition window.** Suppose the last old-group point is at
   date a and the first new-group point at date b. For f−1 in [a, b), the
   knowable evidence is only the old group, so the state is the old group.
   - The new state is **not** backfilled into [a, b).
   - The window is **not** retroactively marked CLASS_UNRESOLVED, because
     doing so would let evidence dated b change eligibility at f ≤ b, which
     violates "future class evidence must never change eligibility at an
     earlier f".

   **Interpretation choice flagged for user confirmation:** a literal
   "uncertain period is CLASS_UNRESOLVED" requires future knowledge of the
   transition, so v2 applies the PIT-strict form above.
6. **Subtype and reason.** These come from the latest point of the governing
   run. A COMMON subtype of ADR_ADS or REIT_COMMON persists if any point of
   the run gives it.
7. **Suffix.** §3 is applied last.

**Key-level reporting classes** (for summary only; eligibility is always per
f):
- `EVER_COMMON`;
- `EVER_NON_COMMON`;
- `ALWAYS_UNRESOLVED`;
- `EVER_CONFLICT`;
- `TRANSITION` (two runs observed by C).

## 5. Scope and non-use

- Only `INTERVAL_ASSIGNED` keys are classified.
- SEC evidence is not part of this offline measurement: no context-extracted
  SEC data exists yet, and no new requests are authorized.
- Current directories and current Tiingo metadata are never decisive.
- No price, volume value, return or performance data is used.
