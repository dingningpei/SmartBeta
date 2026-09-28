# D-SC — Security classification artifact (Discovery Dataset v1.1) — FROZEN TASK SPEC

- **Status:** FROZEN. The user authorized implementation on 2026-09-27.
- **Authority:**
  - `docs/discovery_dataset_v1_1_classification_amendment.md` (commit b6225b6;
    SHA-256 `f89a7128bb8113c6978fc6bca8bcaf6048cd0c7db1fe5ac2aaffa1c7d519c623`);
  - `worker_tasks/dataset_v1/dsc-vocabulary-v4.md` (cb811d0; `fd748432…`) and
    the v3, v2 and v1 vocabularies it references;
  - `dataset-v1-1-decisions.md`;
  - the Wave-2 interfaces.
- **Semantics are frozen. Do NOT redesign them.** Where this spec's appendix
  and the vocabulary appear to differ, **stop and report**.
- **Owned files (new):** `smart_beta/dataset_v1/classification.py`,
  `tests/test_dataset_v1_classification.py`, `tests/fixtures/dataset_v1/dsc/**`.
- **Must not modify** any existing file, D1–D5 artifacts, or the evidence
  files.
- **Provider or network calls: 0.**

## 1. Inputs (read-only; every file hash-verified and recorded)

| Input | Location (production) | Binding |
|---|---|---|
| D4 identity run | `<root>/derived/identity/<identity_id>/` (`identity_assignment.parquet`, `identity.json`) | verify `files_sha256` in `identity.json` |
| Wayback directory captures | `<captures_dir>` = `<root>/dsc_evidence/wayback-20260927/` | Files matching `^(nasdaqlisted\|otherlisted\|nasdaqtraded)_(\d{14})\.txt$` (127 in production). Each must match its line in `SHA256SUMS` |
| SEC full-index | `<sec_index_dir>` = `<root>/dsc_evidence/sec-regindex-20260927/raw/` | Files `master_YYYYQn.gz` (135 in production, 1993Q1–2026Q3); SHA-256 of each recorded |
| Amendment and vocabulary | repo paths above | their SHA-256 must equal the frozen values, else refuse |

## 2. Evidence parsing (normative)

**Captures:**
- Decode as latin-1. The first non-empty line is the header. Drop the row
  starting with `File Creation Time`. Fields are pipe-separated.
- **Capture date** = the first 8 digits of the 14-digit timestamp in the
  file name (UTC date). **Knowledge boundary** = the capture date.
- **Symbol and venue per file kind:**

  | Kind | Symbol | Venue |
  |---|---|---|
  | `nasdaqlisted` | `Symbol` | NASDAQ |
  | `otherlisted` | `ACT Symbol` with `.` → `-` | `Exchange` mapped by {N: NYSE, A: NYSE MKT, P: ARCA, Z: BATS, V: IEX} |
  | `nasdaqtraded` | `Symbol` with `.` → `-` | `Listing Exchange` mapped by {Q: NASDAQ, N: NYSE, A: NYSE MKT, P: ARCA, Z: BATS, V: IEX} |

- Fields used: `Security Name` (trimmed), `ETF`, `Test Issue` (trimmed; a
  missing field is treated as `N`).
- **For key K:** a capture row is **interval-valid** if the symbol equals K's
  ticker and `listed_start ≤ capture date ≤ listed_end` (unclipped D4
  dates).
  - It is **venue-admissible** if the venue equals K's exchange, or if K's
    exchange ∈ {AMEX, NYSE MKT} and the venue is NYSE MKT.
  - Interval-valid but venue-mismatched rows produce only
    EXCHANGE_MISMATCH_REVIEW evidence when their string class is explicit.

**SEC full-index:**
- Decompress with gzip and decode as latin-1. Data rows follow the first line
  that starts with `-----`. Each row is `CIK|Company Name|Form Type|Date
  Filed|Filename`.
- **Regulatory events:** rows whose Form Type ∈ {N-54A, N-54A/A, N-54C,
  N-54C/A, N-8A, N-8A/A, N-8F, N-8F/A}. The base form strips `/A`.
- **Regulatory CIK name set:** every Company Name that the CIK carries in
  **any** index row (all forms, all quarters).

## 3. Classification (normative behaviour = Appendix A, implementing v4)

Apply to D4 `INTERVAL_ASSIGNED` keys only; other keys are excluded upstream
by D4. For each key:
1. **Security class:**
   - apply the string grammar (A1) with the row flags (Test Issue = Y gives
     NON_COMMON(TEST_ISSUE); ETF = Y gives NON_COMMON(ETF)) to each
     venue-admissible row;
   - apply the when-issued filter (A3 `v3_points`);
   - the timeline is `state_at` (A2), with the static test-symbol list, the
     suffix rule, mismatch dates and unresolved points.
2. **Issuer mapping** (A5): `ISSUER_NAMES(K)` = `issuer()` of each
   venue-admissible row's Security Name. Normalize with `norm()`. The
   candidate CIKs are the regulatory CIKs having any normalized name in the
   set.
   - One candidate: MAPPED.
   - Several: AMBIGUOUS.
   - None: NO_MATCH.
   - No names: NO_NAME.
3. **Policy overlay** (A4): SEC events of the mapped CIK, the ambiguity flag,
   and directory-name points from venue-admissible rows.
   - The output label for "no exclusion" is
     **`NO_REGULATORY_EXCLUSION_OBSERVED`**.
4. **Constants:**
   - `TESTSYM = {NTEST, ATEST-G, ATEST-H, ATEST-L, PTEST-W, ZTEST, ZVZZT}`;
   - the suffix rule as in the v4 measurement (Nasdaq 5-letter alphabetic on
     NASDAQ with fifth letter in {W, R, U, P, M, N, O, G, H, I, V}; dash
     segments `P…`, `WS…`, `W`, `U`, `R`, `RT`, `WI`);
   - C = 2026-08-31.

## 4. Outputs

Written under `<root>/derived/classification/<dsc_id>/`, where
`dsc_id = "c-" +` the first 16 hex characters of the SHA-256 of
`canonical_json` of all input hashes plus the code, amendment and vocabulary
hashes.

| File | Content |
|---|---|
| `dsc.json` | canonical JSON; no timestamps; inputs and hashes; counts; `files_sha256`; `dsc_hash` = SHA-256(canonical_json(files_sha256)) |
| `evidence.parquet` | one row per interval-valid capture row per key: evidence_id, security_key, source_file, source_row (0-based data row), capture_date, venue, venue_admissible, security_name, etf_flag, test_flag, string_state, string_reason, name_overlay, when_issued_parallel |
| `regulatory_events.parquet` | cik, company_name, form, base_form, date_filed, filename, source_index |
| `issuer_mapping.parquet` | security_key, issuer_names_norm (JSON), candidate_ciks (JSON), mapping_state |
| `class_timeline.parquet` | segments: security_key, knowledge_date, state, reason, evidence_ids (JSON) |
| `policy_timeline.parquet` | segments: security_key, knowledge_date, policy_state, active_sources (JSON), evidence_refs (JSON) |
| `unresolved_conflict.parquet` | keys whose class at C is CLASS_UNRESOLVED or EVIDENCE_CONFLICT, or whose policy at C is ISSUER_STATUS_UNRESOLVED, with reasons |

**Segment semantics:**
- A segment's state applies to every query cutoff t with
  `knowledge_date ≤ t < next knowledge_date`.
- The first segment's `knowledge_date` = `listed_start`.
- New segments start only at evidence dates (capture dates or SEC Date Filed)
  where the computed state or reason changes.
- `class_state(K, f)` and `policy_state(K, f)` are answered from the segment
  containing cutoff f−1.
- Public pure functions: `class_state_at(timeline, key, cutoff)` and
  `policy_state_at(...)`.

**Reserve firewall:** no price, volume, return, liquidity, factor or
performance field in any output. Dates, identifiers, names, evidence classes,
provenance and counts are allowed.

Outputs are byte-deterministic. An existing output with different bytes
raises an error and is never overwritten.

## 5. Required tests

All tests use synthetic `tmp_path` fixtures; there is no real data_store
access and a socket guard is in place.

**Unit tests:** every Appendix-A rule, including each normative example in
v2, v3 and v4 (for example "Series A Common Stock Purchase Warrants" gives
WARRANT, "Class C-1 ADS (ADW)" gives WARRANT, the KKR L.P. string gives LP,
bare Petrobras ADS gives BARE_ADS, "Common Stock When-Issued" gives
WHEN_ISSUED).

**Adversarial fixtures:**
- embedded "Common Stock" inside warrant wording;
- a unit of common plus warrant;
- a right to purchase common;
- a preferred ADS;
- a bare ADS;
- REIT common;
- ambiguous SBI;
- suffix contradiction;
- exchange mismatch;
- LP → corporation transition;
- common → depositary preferred transition;
- a later when-issued parallel line (a CEG/SNDK-class case);
- a BDC whose listed instrument is Common Stock (a GLAD-class case: class
  COMMON throughout, and BDC/fund labels only affect the overlay);
- a registered fund with Common Stock;
- N-54A → N-54C (with and without N-8A);
- N-8A → N-8F;
- ambiguous issuer mapping;
- pre-EDGAR absence (a BDC-like name without the wording or forms stays
  unexcluded: a documented limitation, not a failure).

**PIT invariants:**
- for random cutoffs, adding **or** removing evidence dated after f−1 leaves
  `class_state` and `policy_state` at f identical;
- transitions are prospective only;
- neither name-based nor SEC evidence ever changes security class;
- a bare ADS never becomes COMMON without representation evidence.

**Other checks:**
- determinism (two runs are byte-identical);
- binding refusals (a hash mismatch, or a wrong amendment or vocabulary hash);
- reserve-firewall column scan;
- a credential sentinel is absent.

**Mutation tests** (must fail the suite):
- dropping the instrument precedence;
- accepting bare ADS;
- a WI parallel line causing a transition;
- backfilling a transition;
- letting N-8F end REGISTERED_FUND;
- letting N-54C certify unrestricted status;
- fuzzy/prefix matching;
- name overlay mutating class;
- allowing evidence dated on f to count.

## 6. Production reconciliation (planner barrier; reference from amendment §6)

At cutoff C, over the 10,353 INTERVAL_ASSIGNED keys:

| Item | Expected |
|---|---|
| COMMON_STOCK | 7,026 |
| NON_COMMON | 2,426 |
| CLASS_UNRESOLVED | 901 |
| EVIDENCE_CONFLICT | 0 |
| Genuine transitions (keys with exactly two explicit runs and never in conflict) | 10 |
| Overlay EXCLUDED | 224 |
| Overlay ISSUER_STATUS_UNRESOLVED | 40 |

Any difference **stops the merge** for classification into: implementation
defect, serialization difference, reference defect, or specification
ambiguity. Rules are never changed to match the counts.

## 7. Report

- files changed;
- test counts and a test-to-item mapping;
- the commit SHA;
- the line `sealed files modified: NONE`.

## Appendix A — Normative reference behaviour

This code is the exact algorithm behind the v4 reference measurement. The
implementation must reproduce its behaviour. It may restructure the code but
must not change the semantics.

### A1 — string grammar (dsc_v4_grammar)

```python
"""dsc-vocab-v2 string grammar (worker_tasks/dataset_v1/dsc-vocabulary-v2.md §1)."""
import re
I=re.I
OVR=[(r"\btest (stock|issue|symbol)\b","TEST_ISSUE"),(r"\bexchange[- ]traded (fund|note)s?\b|\betf\b|\betn\b","ETF"),
     (r"\btracking stock\b","TRACKING_STOCK"),(r"\bwhen[- ](issued|distributed)\b","WHEN_ISSUED")]
TERM=r"\b(Inc|Incorporated|Corp|Corporation|Ltd|Limited|plc|PLC|N\.V|S\.A|L\.P|LLC|Co|Company|Trust|Group|Holdings|AG|SE)\b\.?,?"
REF=r",?\s*\b(each|representing|represents|exercisable|consisting|to (purchase|acquire|buy|subscribe)|convertible (into|for)|included as part of|underlying|entitling)\b"
NON=[("LP_OR_MLP_UNIT",r"limited partner(ship)? interests?|partnership units?|\bcommon units?\b"),
     ("NONCOMMON_TRUST_OR_UNIT",r"units? of beneficial interest|\btrust units?\b"),
     ("DEPOSITARY_PREFERRED",r"depositary shares?.*\bpreferred\b|\bpreferred\b.*depositary"),
     ("PREFERRED",r"\bpreferred\b|\bpreference shares?\b"),
     ("NOTE_OR_DEBT",r"\bnotes?\b|\bdebentures?\b|\bbonds?\b|\bsenior\b|\bsubordinated\b"),
     ("WARRANT",r"\bwarrants?\b|\badws?\b"),("RIGHT",r"\bcontingent value rights?\b|\brights?\b"),
     ("UNIT",r"\bsub-?units?\b|\bcorporate units?\b|\btangible equity units?\b|\bunits?\b")]
DEP=r"american deposit[ao]ry (shares?|receipts?)|\bads\b|\badrs?\b|sponsored adr|\bdepositary shares?\b"
AMDEP=r"american deposit[ao]ry (shares?|receipts?)|\bads\b|\badrs?\b|sponsored adr"
REIT=r"\breit\b|real estate investment trust"
def classify(name):
    n=re.sub(r"\s+"," ",name or "").strip()
    for p,r in OVR:
        if re.search(p,n,I): return ("NON",r)
    dash=" - " in n
    cand=n.rsplit(" - ",1)[1] if dash else n
    m=re.search(REF,cand,I); pre=cand[:m.start()] if m else cand; ref=cand[m.end():] if m else ""
    if not dash:
        ms=list(re.finditer(TERM,pre)); inst=pre[ms[-1].end():] if ms else pre
    else:
        inst=pre
    inst=inst.strip(" -,")
    if not inst: return ("UNR","ISSUER_ONLY")
    for r,p in NON:
        if re.search(p,inst,I): return ("NON",r)
    if re.search(DEP,inst,I):
        if re.search(r"preferred|preference",ref,I): return ("NON","DEPOSITARY_PREFERRED")
        if re.search(r"\b(common (stock|shares?)|ordinary (shares?|equity))\b",ref,I): return ("COM","ADR_ADS")
        return ("UNR","BARE_ADS") if re.search(AMDEP,inst,I) else ("UNR","GENERIC_SHARES")
    if re.search(r"beneficial interest",inst,I):
        return ("COM","REIT_COMMON") if (re.search(REIT,n,I) and re.search(r"\bcommon\b",inst,I)) else ("UNR","SBI_AMBIGUOUS")
    if re.search(r"\bseries [a-z] (?!common\b)[a-z][\w&.\- ]* common (stock|shares?)\b",inst,I): return ("UNR","POSSIBLE_TRACKING_STOCK")
    if re.search(r"\b(new )?common (stock|shares?)\b|\bordinary shares?\b",inst,I):
        return ("COM","REIT_COMMON") if re.search(REIT,n,I) else ("COM","COMMON")
    if re.search(r"\bsubordinate voting shares\b|\bclass [a-z] shares\b|\bshares\b",inst,I): return ("UNR","GENERIC_SHARES")
    if not re.search(r"\b(stock|shares?|units?|notes?|interests?)\b",inst,I): return ("UNR","ISSUER_ONLY")
    return ("UNR","UNRECOGNIZED")
```

### A2 — PIT class timeline (state_at)

```python
"""dsc-vocab-v2 §4 point-in-time timeline."""
def state_at(points, cutoff, static_test=False, suffix=None, mism_dates=(), unres=()):
    """points: sorted list of (date, G in {'COM','NON'}, detail); returns (state, detail)."""
    if static_test: return ("NON_COMMON","TEST_ISSUE")
    pts=[p for p in points if p[0]<=cutoff]
    if not pts:
        if suffix: return ("NON_COMMON",suffix)
        if any(d<=cutoff for d in mism_dates): return ("CLASS_UNRESOLVED","EXCHANGE_MISMATCH_REVIEW")
        u=[r for d,r in unres if d<=cutoff]
        return ("CLASS_UNRESOLVED", u[-1] if u else "NO_ADMISSIBLE_EVIDENCE")
    # concurrent contradiction: same date, different G
    byd={}
    for d,g,_ in pts: byd.setdefault(d,set()).add(g)
    if any(len(s)>1 for s in byd.values()): return ("EVIDENCE_CONFLICT","CONCURRENT")
    runs=[]
    for d,g,det in pts:
        if runs and runs[-1][0]==g: runs[-1][1].append((d,det))
        else: runs.append((g,[(d,det)]))
    if len(runs)>=3: return ("EVIDENCE_CONFLICT","ALTERNATING")
    g,run=runs[-1]
    if g=="COM":
        dets={x for _,x in run}; det="ADR_ADS" if "ADR_ADS" in dets else "REIT_COMMON" if "REIT_COMMON" in dets else "COMMON"
        if suffix: return ("EVIDENCE_CONFLICT","SUFFIX_CONTRADICTION")
        return ("COMMON_STOCK",det)
    return ("NON_COMMON",run[-1][1])
```

### A3 — when-issued parallel-line filter + SEC-form status machine

```python
"""dsc-vocab-v3 §2.2 when-issued correction + §3.3 regulatory overlay."""
from dsc_v2_timeline import state_at
def v3_points(points):
    """Drop (flag) WHEN_ISSUED points dated on/after any COMMON point."""
    com=[d for d,g,_ in points if g=="COM"]; first=min(com) if com else None
    kept=[];flag=[]
    for p in points:
        if p[1]=="NON" and p[2]=="WHEN_ISSUED" and first is not None and p[0]>=first: flag.append(p)
        else: kept.append(p)
    return kept,flag
def overlay(events, cutoff, ambiguous=False):
    """events: list of (date, base_form) for a mapped CIK."""
    if ambiguous: return "ISSUER_STATUS_UNRESOLVED","AMBIGUOUS_MAPPING"
    ev=[e for e in events if e[0]<=cutoff]
    bdc=[e for e in ev if e[1] in("N-54A","N-54C")]
    last=max(bdc)[1] if bdc else None
    if last=="N-54A": return "BDC","N-54A"
    if any(f=="N-8A" for _,f in ev): return "REGISTERED_FUND","N-8A"
    if last=="N-54C": return "ISSUER_STATUS_UNRESOLVED","WITHDRAWN_NO_SUCCESSOR_STATUS"
    return "NO_REGULATORY_EXCLUSION",None
```

### A4 — directory-name overlay + combination

```python
"""dsc-vocab-v4 §3.2 name overlay + §3.3 combination."""
import re
from dsc_v3_core import overlay as sec_overlay
def name_evidence(name, etf_flag):
    if etf_flag=="Y": return None
    if re.search(r"\bbusiness development (company|corporation)\b",name,re.I) or re.search(r"\bBDC\b",name): return "NAME_BASED_BDC"
    if re.search(r"\bclosed[- ]end fund\b",name,re.I) or re.search(r"\bfund\b",name,re.I): return "NAME_BASED_REGISTERED_FUND"
    return None
def policy(cut, sec_events, ambiguous, name_pts):
    """name_pts: list of (date, 'NAME_BASED_BDC'|'NAME_BASED_REGISTERED_FUND'). Returns (status, sources)."""
    s,why=sec_overlay(sec_events,cut,ambiguous)
    src=set()
    if s=="BDC": src.add("SEC_FORM_BDC")
    if s=="REGISTERED_FUND": src.add("SEC_FORM_REGISTERED_FUND")
    if not ambiguous and any(f=="N-8A" for d,f in sec_events if d<=cut): src.add("SEC_FORM_REGISTERED_FUND")
    for d,k in name_pts:
        if d<=cut: src.add(k)
    if src: return "EXCLUDED",frozenset(src)
    if s=="ISSUER_STATUS_UNRESOLVED": return "ISSUER_STATUS_UNRESOLVED",frozenset([why])
    return "NO_REGULATORY_EXCLUSION",frozenset()
```

### A5 — issuer-name extraction and normalization for SEC mapping

```python
SUFX={"inc","incorporated","corp","corporation","co","company","ltd","limited","llc","lp","plc","sa","nv","ag","se","the"}
def norm(s):
    s=(s or "").lower().replace("&"," and ")
    s=re.sub(r"[/\\][a-z]{2,3}[/\\]?\s*$","",s.strip())
    s=re.sub(r"[^a-z0-9 ]"," ",s); t=s.split()
    if t and t[0]=="the": t=t[1:]
    while t and t[-1] in SUFX: t=t[:-1]
    t=[x for x in t if x not in ("l","p")] if t[-2:]==["l","p"] else t
    return " ".join(t)
TERM=r"\b(Inc|Incorporated|Corp|Corporation|Ltd|Limited|plc|PLC|N\.V|S\.A|L\.P|LLC|Co|Company|Trust|Group|Holdings|AG|SE)\b\.?,?"
REF=r",?\s*\b(each|representing|represents|exercisable|consisting|to (purchase|acquire|buy|subscribe)|convertible (into|for)|included as part of|underlying|entitling)\b"
CLASSW=r"\s+(?=(?:\d+(?:\.\d+)?%|common|ordinary|class [a-z]\b|units?\b|warrants?\b|rights?\b|preferred|notes?\b|shares?\b|depositary|american depositary|series [a-z]\b|reit\b|closed end fund|business development company))"
def issuer(name):
    n=re.sub(r"\s+"," ",name).strip()
    if " - " in n: return n.rsplit(" - ",1)[0]
    m=re.search(REF,n,re.I); P=n[:m.start()] if m else n
    ms=list(re.finditer(TERM,P))
    if ms: return P[:ms[-1].end()]
    return re.split(CLASSW,P,maxsplit=1,flags=re.I)[0]
```
