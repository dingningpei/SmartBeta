# V13-E3 — L2 symbol-evidence selection and extraction (FROZEN 2026-10-09; user-approved)

- **Defect class:** specification gaps (planner) in plan §3, §4 and §6 (L2 / IM-P).
  1. **Selection:** E-1 `select_periodic_filings` takes the **earliest** ≤ 2 periodic
     filings in the key's interval. Cover-page `dei:TradingSymbol` tagging began around
     mid-2019, so most selected filings cannot carry symbol evidence. That would make
     IM-P evidence-starved artificially.
  2. **Extraction:** no task owns extracting `dei:TradingSymbol` from the L2 documents into
     the `SymbolEvidence` that `edgar_identity.audit_im_p` consumes.
- **Correction (binding; amends plan §3, §4 and §6):**
  1. **E-1-R2 (`edgar_fetch.py`):**
     - `build_l2_plan` selects, per sampled key, among the periodic filings
       (10-K, 10-Q, 20-F, 40-F; amendments excluded) inside the key's interval with
       `isInlineXBRL == 1`, the **latest 2** by `filingDate` (ties broken by accession);
     - only the primary document is fetched; the index JSON is no longer required;
     - a key with no inline-XBRL periodic filing in its interval gets **no request** and is
       recorded as `NO_INLINE_XBRL_FILING`.
     - The budget stays ≤ 1,000.
  2. **E-2-R2 (`edgar_identity.py`):**
     - new `extract_trading_symbols(document_bytes) -> tuple[str, ...]`. It collects every
       inline-XBRL fact whose `name` attribute is `dei:TradingSymbol`, matched
       case-insensitively, inside `ix:nonNumeric`. It strips inner tags and whitespace,
       uppercases, and normalizes by removing `.`, `-`, `/` and spaces. Empty values are
       dropped. It returns a sorted, distinct tuple;
     - new `build_symbol_evidence(raw_root, sample_rows) -> Mapping[security_key,
       SymbolEvidence]`, from the stored L2 documents at the E-1-R2 relpaths;
     - **verdict per key:**
       - **CONFIRMED** if the key's normalized D4 ticker is among the union of extracted
         symbols across its fetched documents. A multi-class filing that lists several
         symbols confirms any one of them;
       - **CONTRADICTED** if the union is non-empty and does not contain the ticker;
       - **NO_SYMBOL_EVIDENCE** otherwise, including `NO_INLINE_XBRL_FILING`.
     - IM-P (`audit_im_p`, with the V13-E1 seed and the §8 sufficiency rule) is unchanged.
  3. **Fixtures:** they use real-shape inline XBRL cover snippets with synthetic values,
     covering multi-class, BRK.B / BRK-B normalization, an absent tag, and a malformed
     tag.
  4. **New mutations:**
     - E1-M6: the earliest filings selected;
     - E1-M7: non-inline-XBRL filings selected;
     - E2-M6: the symbol is not normalized (BRK.B ≠ BRK-B);
     - E2-M7: an empty union treated as CONTRADICTED.
- **Expectation, recorded before any data is seen:** ended keys that delisted before
  inline-XBRL cover tagging have no evidence by construction. The ended stratum may
  therefore fall below the frozen 50-evidence floor. In that case IM-P = NOT CERTIFIED
  for the ended stratum, per plan §8. **No threshold or rule is tuned after the data is
  seen.**
