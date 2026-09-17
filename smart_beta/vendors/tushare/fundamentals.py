"""Tushare as-reported fundamentals mapping (Phase 4D-B, task P4DB-6).

This is the *only* module in the Tushare adapter that turns raw statement
payloads into ``FUNDAMENTALS_FACT_SCHEMA`` rows, plus the adapter-owned
extra columns Phase 4D-B policy 9 requires. It produces raw, fully
timestamped vintage facts only. It **never** resolves an as-of query, never
computes a cumulative-to-discrete subtraction, and never calls or
reimplements :func:`smart_beta.pit.fundamentals.latest_known_value` -- see
``worker_tasks/phase4d_b/phase4d-b-plan.md``'s "Why no ``pit/*`` task is
needed" for the architectural boundary this module sits below.

Scope
-----
``get_fundamentals`` / ``get_uncertain_observations`` are exposed both as
module-level functions (the primary API for P4DB-8's assembly) and through
the thin :class:`TushareFundamentals` wrapper, whose method signatures match
the task spec's ``get_fundamentals(start, end, fields)`` shape. The module
functions additionally take the transport-neutral
:class:`~smart_beta.vendors.tushare.client.TushareClient` and the explicit
``ts_codes`` universe, because Tushare (through this proxy) rejects a
period-only bulk query -- every statement request is per-``ts_code`` and
per-``period``. That is an interface-driven fetch decision, not a mapping
rule.

Frozen policies implemented here (all from ``phase4d-b-plan.md``)
---------------------------------------------------------------
* **Policy 3, knowledge date (fail-closed, five lettered cases A-E).**
  ``f_ann_date`` wins when present and valid; ``ann_date`` is used only
  when ``f_ann_date`` is genuinely missing/null AND ``ann_date`` is valid
  AND ``report_type in {1,2,3,6,7,8}``. A malformed ``f_ann_date`` is never
  coerced. Everything else is dropped and recorded per-observation in the
  uncertainty side table with a named reason -- never a fabricated
  ``knowledge_date``.

* **Policy 6, blank-out.** An empty-string value where a number is expected
  is emitted as ``is_blank_out=True`` with ``value=NaN``; the row is kept,
  never coerced to ``0.0`` and never dropped.

* **Policy 2, vintage / ``is_restatement``.** ``True`` only when directly
  evidenced: (a) the row shares ``(stock_id, report_period_end, field)``
  with an *earlier* vintage (a strictly larger ``knowledge_date``), or (b)
  the row's own ``ann_date != f_ann_date``. ``False`` means "no restatement
  evidence found", never "verified original".

* **Policies 7/8, reporting basis and the CH3 join.** ``income``/``cashflow``
  ``report_type`` 1/4/5/9/10/11/12 -> ``CUMULATIVE_YTD``; 2/3/7/8 ->
  ``SINGLE_QUARTER``; ``balancesheet`` always ``INSTANT``;
  ``fina_indicator``'s ``profit_dedt`` -> ``CUMULATIVE_YTD``. A
  ``ni_ex_nonrecurring`` fact is emitted only when all three frozen
  conditions hold, otherwise it is suppressed and recorded with
  ``reason="ch3_vintage_join_not_certified"`` -- silence, never a guessed
  vintage.

* **Proxy provenance.** Every fact carries ``source_vendor="Tushare"``,
  ``access_path="proxy:pcd.mobcvb.cn"`` and ``retrieved_at``. This is
  proxy-observed evidence and does not certify direct official-Tushare
  behavior.

Duplicate handling and a real schema-key collision (reportable finding)
----------------------------------------------------------------------
``FUNDAMENTALS_FACT_SCHEMA``'s key is
``(stock_id, report_period_end, field, knowledge_date)``. Tushare emits
``update_flag`` duplicate pairs sharing all four key columns with
byte-identical values in every specimen recorded for this task; those are
de-duplicated here deterministically, choosing the higher ``update_flag``
and recording every observed raw ``update_flag`` in ``vintage_id``.

Separately, a *genuine* same-key, different-value collision was found in a
real specimen that is **not** an ``update_flag`` pair: ``002450.SZ`` FY2015
``income`` has ``report_type=1`` and ``report_type=4`` rows that both carry
``f_ann_date=20210228`` (identical ``knowledge_date``) but different
``total_revenue``/``n_income``. Because ``PanelSchema`` rejects duplicate
keys and neither value is safe to discard silently, this module raises
:class:`TushareConflictingVintageError` rather than guessing. The CH3 join
for that same period is suppressed independently (a ``report_type=4/5`` row
exists), so this does not affect the CH3 path.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Mapping, Sequence

import numpy as np
import pandas as pd

from smart_beta.pit.schema import (
    FIELD_COL,
    FUNDAMENTALS_FACT_SCHEMA,
    IS_RESTATEMENT_COL,
    KNOWLEDGE_DATE_COL,
    REPORT_PERIOD_END_COL,
    STOCK_COL,
    VALUE_COL,
    validate_panel,
)
from smart_beta.vendors.tushare.client import TushareClient

__all__ = [
    "ACCESS_PATH",
    "AMBIGUOUS_FIELD_REPORT_TYPES",
    "CH3_FIELD",
    "CUMULATIVE_YTD",
    "ELIGIBLE_FALLBACK_REPORT_TYPES",
    "FINA_INDICATOR_ENDPOINT",
    "INSTANT",
    "INELIGIBLE_REPORT_TYPES",
    "KnowledgeDateDecision",
    "REASON_BOTH_DATES_MISSING",
    "REASON_CH3_VINTAGE_JOIN_NOT_CERTIFIED",
    "REASON_F_ANN_DATE_MALFORMED",
    "REASON_INELIGIBLE_REPORT_TYPE",
    "SINGLE_QUARTER",
    "SOURCE_VENDOR",
    "TushareFundamentals",
    "TushareFundamentalsError",
    "TushareAmbiguousFieldError",
    "TushareConflictingVintageError",
    "TushareUnknownFieldError",
    "TushareValueParseError",
    "assemble_ch3_facts",
    "get_fundamentals",
    "get_uncertain_observations",
    "infer_reporting_basis",
    "knowledge_date_decision",
    "map_statement_payload",
    "resolve_field_endpoint",
]

# ---------------------------------------------------------------------------
# Endpoint / field registry
# ---------------------------------------------------------------------------
INCOME_ENDPOINT = "income"
BALANCESHEET_ENDPOINT = "balancesheet"
CASHFLOW_ENDPOINT = "cashflow"
FINA_INDICATOR_ENDPOINT = "fina_indicator"

#: Common non-value columns that are never emitted as a fact field.
_METADATA_FIELDS = frozenset(
    {
        "ts_code",
        "ann_date",
        "f_ann_date",
        "end_date",
        "report_type",
        "comp_type",
        "end_type",
        "update_flag",
    }
)

_INCOME_FIELDS = frozenset(
    {
        "adj_lossgain", "admin_exp", "amodcost_fin_assets", "ass_invest_income", "asset_disp_income",
        "assets_impair_loss", "basic_eps", "biz_tax_surchg", "capit_comstock_div", "comm_exp", "comm_income",
        "compens_payout", "compens_payout_refu", "compr_inc_attr_m_s", "compr_inc_attr_p", "comshare_payable_dvd",
        "continued_net_profit", "credit_impa_loss", "diluted_eps", "distable_profit", "distr_profit_shrhder", "div_payt",
        "ebit", "ebitda", "end_net_profit", "fin_exp", "fin_exp_int_exp", "fin_exp_int_inc",
        "forex_gain", "fv_value_chg_gain", "income_tax", "insur_reser_refu", "insurance_exp", "int_exp",
        "int_income", "invest_income", "minority_gain", "n_asset_mg_income", "n_commis_income", "n_income",
        "n_income_attr_p", "n_oth_b_income", "n_oth_income", "n_sec_tb_income", "n_sec_uw_income", "nca_disploss",
        "net_after_nr_lp_correct", "net_expo_hedging_benefits", "non_oper_exp", "non_oper_income", "oper_cost", "oper_exp",
        "operate_profit", "oth_b_income", "oth_compr_income", "oth_impair_loss_assets", "oth_income", "other_bus_cost",
        "out_prem", "prem_earned", "prem_income", "prem_refund", "prfshare_payable_dvd", "rd_exp",
        "reins_cost_refund", "reins_exp", "reins_income", "reser_insur_liab", "revenue", "sell_exp",
        "t_compr_income", "total_cogs", "total_opcost", "total_profit", "total_revenue", "transfer_housing_imprest",
        "transfer_oth", "transfer_surplus_rese", "undist_profit", "une_prem_reser", "withdra_biz_devfund", "withdra_legal_pubfund",
        "withdra_legal_surplus", "withdra_oth_ersu", "withdra_rese_fund", "workers_welfare",
    }
)

_BALANCESHEET_FIELDS = frozenset(
    {
        "acc_exp", "acc_receivable", "accounts_pay", "accounts_receiv", "accounts_receiv_bill", "acct_payable",
        "acting_trading_sec", "acting_uw_sec", "adv_receipts", "agency_bus_liab", "amor_exp",
        "bond_payable", "cap_rese", "cash_reser_cb", "cb_borr", "cip", "cip_total",
        "client_depos", "client_prov", "comm_payable", "const_materials", "contract_assets", "contract_liab",
        "cost_fin_assets", "debt_invest", "decr_in_disbur", "defer_inc_non_cur_liab", "defer_tax_assets", "defer_tax_liab",
        "deferred_inc", "depos", "depos_ib_deposits", "depos_in_oth_bfi", "depos_oth_bfi", "depos_received",
        "deriv_assets", "deriv_liab", "div_payable", "div_receiv", "estimated_liab", "fa_avail_for_sale",
        "fair_value_fin_assets", "fix_assets", "fix_assets_total", "fixed_assets_disp", "forex_differ", "goodwill",
        "hfs_assets", "hfs_sales", "htm_invest", "indem_payable", "indep_acct_assets", "indept_acc_liab",
        "int_payable", "int_receiv", "intan_assets", "inventories", "invest_as_receiv", "invest_loss_unconf",
        "invest_real_estate", "lease_liab", "lending_funds", "loan_oth_bank", "loanto_oth_bank_fi", "long_pay_total",
        "lt_amor_exp", "lt_borr", "lt_eqt_invest", "lt_payable", "lt_payroll_payable", "lt_rec",
        "minority_int", "money_cap", "nca_within_1y", "non_cur_liab_due_1y", "notes_payable", "notes_receiv",
        "oil_and_gas_assets", "ordin_risk_reser", "oth_assets", "oth_comp_income", "oth_cur_assets", "oth_cur_liab",
        "oth_debt_invest", "oth_eq_invest", "oth_eq_ppbond", "oth_eqt_tools", "oth_eqt_tools_p_shr", "oth_illiq_fin_assets",
        "oth_liab", "oth_nca", "oth_ncl", "oth_pay_total", "oth_payable", "oth_rcv_total",
        "oth_receiv", "payable_to_reinsurer", "payables", "payroll_payable", "ph_invest", "ph_pledge_loans",
        "pledge_borr", "policy_div_payable", "prec_metals", "prem_receiv_adva", "premium_receiv", "prepayment",
        "produc_bio_assets", "pur_resale_fa", "r_and_d", "receiv_financing", "refund_cap_depos", "refund_depos",
        "reinsur_receiv", "reinsur_res_receiv", "reser_lins_liab", "reser_lthins_liab", "reser_outstd_claims", "reser_une_prem",
        "rr_reins_lins_liab", "rr_reins_lthins_liab", "rr_reins_outstd_cla", "rr_reins_une_prem", "rsrv_insur_cont", "sett_rsrv",
        "sold_for_repur_fa", "special_rese", "specific_payables", "st_bonds_payable", "st_borr", "st_fin_payable",
        "surplus_rese", "taxes_payable", "time_deposits", "total_assets", "total_cur_assets", "total_cur_liab",
        "total_hldr_eqy_exc_min_int", "total_hldr_eqy_inc_min_int", "total_liab", "total_liab_hldr_eqy", "total_nca", "total_ncl",
        "total_share", "trad_asset", "trading_asset", "trading_fl", "transac_seat_fee", "treasury_share",
        "undistr_porfit", "use_right_assets",
    }
)

_CASHFLOW_FIELDS = frozenset(
    {
        "amort_intang_assets", "beg_bal_cash", "beg_bal_cash_equ", "c_cash_equ_beg_period", "c_cash_equ_end_period", "c_disp_withdrwl_invest",
        "c_fr_oth_operate_a", "c_fr_sale_sg", "c_inf_fr_operate_a", "c_paid_for_taxes", "c_paid_goods_s", "c_paid_invest",
        "c_paid_to_for_empl", "c_pay_acq_const_fiolta", "c_pay_claims_orig_inco", "c_pay_dist_dpcp_int_exp", "c_prepay_amt_borr", "c_recp_borrow",
        "c_recp_cap_contrib", "c_recp_return_invest", "conv_copbonds_due_within_1y", "conv_debt_into_cap", "credit_impa_loss", "decr_def_inc_tax_assets",
        "decr_deferred_exp", "decr_inventories", "decr_oper_payable", "depr_fa_coga_dpba", "eff_fx_flu_cash", "end_bal_cash",
        "end_bal_cash_equ", "fa_fnc_leases", "finan_exp", "free_cashflow", "ifc_cash_incr", "im_n_incr_cash_equ",
        "im_net_cashflow_oper_act", "incl_cash_rec_saims", "incl_dvd_profit_paid_sc_ms", "incr_acc_exp", "incr_def_inc_tax_liab", "incr_oper_payable",
        "invest_loss", "loss_disp_fiolta", "loss_fv_chg", "loss_scr_fa", "lt_amort_deferred_exp", "n_cap_incr_repur",
        "n_cash_flows_fnc_act", "n_cashflow_act", "n_cashflow_inv_act", "n_depos_incr_fi", "n_disp_subs_oth_biz", "n_inc_borr_oth_fi",
        "n_incr_cash_cash_equ", "n_incr_clt_loan_adv", "n_incr_dep_cbob", "n_incr_disp_faas", "n_incr_disp_tfa", "n_incr_insured_dep",
        "n_incr_loans_cb", "n_incr_loans_oth_bank", "n_incr_pledge_loan", "n_recp_disp_fiolta", "n_recp_disp_sobu", "n_reinsur_prem",
        "net_cash_rece_sec", "net_dism_capital_add", "net_profit", "oth_cash_pay_oper_act", "oth_cash_recp_ral_fnc_act", "oth_cashpay_ral_fnc_act",
        "oth_loss_asset", "oth_pay_ral_inv_act", "oth_recp_ral_inv_act", "others", "pay_comm_insur_plcy", "pay_handling_chrg",
        "prem_fr_orig_contr", "proc_issue_bonds", "prov_depr_assets", "recp_tax_rends", "st_cash_out_act", "stot_cash_in_fnc_act",
        "stot_cashout_fnc_act", "stot_inflows_inv_act", "stot_out_inv_act", "uncon_invest_loss", "use_right_asset_dep",
    }
)

#: ``(endpoint, value-field)`` registry. ``credit_impa_loss`` legitimately
#: appears in both ``income`` and ``cashflow``; a request for it is refused
#: as ambiguous rather than silently picked.
_FIELD_SETS: dict[str, frozenset[str]] = {
    INCOME_ENDPOINT: _INCOME_FIELDS,
    BALANCESHEET_ENDPOINT: _BALANCESHEET_FIELDS,
    CASHFLOW_ENDPOINT: _CASHFLOW_FIELDS,
}

#: The adapter's canonical CH3 output field and its ``fina_indicator`` source.
CH3_FIELD = "ni_ex_nonrecurring"
_PROFIT_DEDT_FIELD = "profit_dedt"

# ---------------------------------------------------------------------------
# Policy constants
# ---------------------------------------------------------------------------
CUMULATIVE_YTD = "CUMULATIVE_YTD"
SINGLE_QUARTER = "SINGLE_QUARTER"
INSTANT = "INSTANT"

#: ``report_type`` codes eligible for the ``ann_date`` knowledge-date fallback.
ELIGIBLE_FALLBACK_REPORT_TYPES = frozenset({"1", "2", "3", "6", "7", "8"})

#: The adjusted / pre-adjustment families -- never eligible for the fallback,
#: and the exact code set policy 7's CH3 condition 2 checks for.
INELIGIBLE_REPORT_TYPES = frozenset({"4", "5", "9", "10", "11", "12"})

_CUMULATIVE_REPORT_TYPES = frozenset({"1", "4", "5", "9", "10", "11", "12"})
_SINGLE_QUARTER_REPORT_TYPES = frozenset({"2", "3", "7", "8"})

#: Reporting-basis families, kept distinct from the fallback-eligibility sets
#: above because the two are different questions asked of the same code.
AMBIGUOUS_FIELD_REPORT_TYPES = INELIGIBLE_REPORT_TYPES

REASON_INELIGIBLE_REPORT_TYPE = "ineligible_report_type_for_fallback"
REASON_F_ANN_DATE_MALFORMED = "f_ann_date_malformed"
REASON_BOTH_DATES_MISSING = "both_dates_missing_or_invalid"
REASON_CH3_VINTAGE_JOIN_NOT_CERTIFIED = "ch3_vintage_join_not_certified"

SOURCE_VENDOR = "Tushare"
ACCESS_PATH = "proxy:pcd.mobcvb.cn"

# ---------------------------------------------------------------------------
# Output column names
# ---------------------------------------------------------------------------
REPORTING_BASIS_COL = "reporting_basis"
REPORT_TYPE_COL = "report_type"
VINTAGE_ID_COL = "vintage_id"
IS_BLANK_OUT_COL = "is_blank_out"
SOURCE_VENDOR_COL = "source_vendor"
ACCESS_PATH_COL = "access_path"
RETRIEVED_AT_COL = "retrieved_at"
SOURCE_ENDPOINT_COL = "source_endpoint"
RAW_ANN_DATE_COL = "raw_ann_date"
RAW_F_ANN_DATE_COL = "raw_f_ann_date"
RAW_FINA_ANN_DATE_COL = "raw_fina_ann_date"
REASON_COL = "reason"

_FACT_COLUMNS: tuple[str, ...] = (
    STOCK_COL,
    REPORT_PERIOD_END_COL,
    FIELD_COL,
    KNOWLEDGE_DATE_COL,
    VALUE_COL,
    IS_RESTATEMENT_COL,
    REPORTING_BASIS_COL,
    REPORT_TYPE_COL,
    VINTAGE_ID_COL,
    IS_BLANK_OUT_COL,
    SOURCE_VENDOR_COL,
    ACCESS_PATH_COL,
    RETRIEVED_AT_COL,
)

_UNCERTAIN_COLUMNS: tuple[str, ...] = (
    STOCK_COL,
    REPORT_PERIOD_END_COL,
    FIELD_COL,
    REPORT_TYPE_COL,
    RAW_ANN_DATE_COL,
    RAW_F_ANN_DATE_COL,
    RAW_FINA_ANN_DATE_COL,
    REASON_COL,
    SOURCE_ENDPOINT_COL,
    RETRIEVED_AT_COL,
)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------
class TushareFundamentalsError(Exception):
    """Base class for this module's fail-closed mapping errors."""


class TushareUnknownFieldError(TushareFundamentalsError):
    """A requested field is not a mapped value field of any statement endpoint."""


class TushareAmbiguousFieldError(TushareFundamentalsError):
    """A requested field maps to more than one statement endpoint."""


class TushareValueParseError(TushareFundamentalsError):
    """A non-empty, non-numeric value was found where a float is required."""


class TushareConflictingVintageError(TushareFundamentalsError):
    """Two distinct vintages collapse to the same ``FUNDAMENTALS_FACT_SCHEMA``
    key with *different* values and no rule can honestly choose between them.

    This is deliberately loud. Silently picking one would be exactly the
    guessed-vintage behavior policies 2/3/7 forbid. See the module docstring
    for the real ``002450.SZ`` FY2015 specimen that triggers it.
    """

    def __init__(self, key: tuple, records: Sequence[Mapping]) -> None:
        self.key = key
        self.records = [dict(r) for r in records]
        super().__init__(
            "Conflicting Tushare vintages collapse to one "
            f"FUNDAMENTALS_FACT_SCHEMA key {key!r}: {self.records!r}"
        )


# ---------------------------------------------------------------------------
# Primitive helpers
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class KnowledgeDateDecision:
    """The outcome of applying frozen policy 3 to one raw statement row.

    ``knowledge_date`` is ``None`` exactly when ``drop_reason`` is set; a
    dropped row is never assigned a fabricated date.
    """

    knowledge_date: pd.Timestamp | None
    drop_reason: str | None


def _classify_date(value: object) -> tuple[str, pd.Timestamp | None]:
    """Classify a raw Tushare date value as ``missing`` / ``valid`` / ``malformed``.

    ``None``, NaN, and blank strings are *missing* (genuinely absent); a
    non-empty value that fails to parse is *malformed*. Keeping these
    distinct is required by policy 3 (a missing ``f_ann_date`` may fall back,
    a malformed one may not).
    """
    if value is None:
        return "missing", None
    if isinstance(value, float) and np.isnan(value):
        return "missing", None
    if isinstance(value, (pd.Timestamp,)):
        if pd.isna(value):
            return "missing", None
        return "valid", value
    text = str(value).strip()
    if text == "" or text.lower() in {"nan", "nat", "none", "null"}:
        return "missing", None
    try:
        parsed = pd.Timestamp(text)
    except (ValueError, TypeError, OverflowError):
        return "malformed", None
    if pd.isna(parsed):
        return "missing", None
    return "valid", parsed


def normalize_report_type(value: object) -> str | None:
    """Normalize Tushare's ``report_type`` (string in ``income``, int in
    ``balancesheet``/``cashflow``) to a canonical string, or ``None``."""
    if value is None:
        return None
    if isinstance(value, float) and np.isnan(value):
        return None
    text = str(value).strip()
    if text == "" or text.lower() in {"nan", "none", "null"}:
        return None
    return text


def knowledge_date_decision(row: Mapping) -> KnowledgeDateDecision:
    """Apply frozen policy 3 to one raw statement row.

    Exactly the lettered cases:

    * **A** -- valid ``f_ann_date`` -> ``knowledge_date = f_ann_date``.
    * **D** -- present but malformed ``f_ann_date`` -> dropped,
      ``f_ann_date_malformed`` (never coerced, never falls back).
    * **B** -- ``f_ann_date`` missing, ``ann_date`` valid and
      ``report_type in {1,2,3,6,7,8}`` -> ``knowledge_date = ann_date``.
    * **C** -- ``f_ann_date`` missing, ``ann_date`` valid but ineligible
      ``report_type`` -> dropped, ``ineligible_report_type_for_fallback``.
    * **E** -- neither date usable -> dropped,
      ``both_dates_missing_or_invalid``.
    """
    f_status, f_value = _classify_date(row.get("f_ann_date"))
    if f_status == "valid":
        return KnowledgeDateDecision(f_value, None)  # case A
    if f_status == "malformed":
        return KnowledgeDateDecision(None, REASON_F_ANN_DATE_MALFORMED)  # case D

    a_status, a_value = _classify_date(row.get("ann_date"))
    if a_status == "valid":
        report_type = normalize_report_type(row.get("report_type"))
        if report_type in ELIGIBLE_FALLBACK_REPORT_TYPES:
            return KnowledgeDateDecision(a_value, None)  # case B
        return KnowledgeDateDecision(None, REASON_INELIGIBLE_REPORT_TYPE)  # case C
    return KnowledgeDateDecision(None, REASON_BOTH_DATES_MISSING)  # case E


def infer_reporting_basis(endpoint: str, report_type: object) -> str:
    """Return the policy-8 ``reporting_basis`` for a statement row.

    ``balancesheet`` is always ``INSTANT`` (stock quantities, not flows).
    ``income``/``cashflow`` split on ``report_type``. An unrecognized
    ``report_type`` fails closed rather than guessing a basis.
    """
    if endpoint == BALANCESHEET_ENDPOINT:
        return INSTANT
    normalized = normalize_report_type(report_type)
    if endpoint in (INCOME_ENDPOINT, CASHFLOW_ENDPOINT):
        if normalized in _CUMULATIVE_REPORT_TYPES:
            return CUMULATIVE_YTD
        if normalized in _SINGLE_QUARTER_REPORT_TYPES:
            return SINGLE_QUARTER
        raise TushareValueParseError(
            f"Unknown report_type={report_type!r} for endpoint {endpoint!r}; "
            "refusing to guess a reporting_basis."
        )
    raise TushareValueParseError(
        f"No reporting_basis rule for endpoint {endpoint!r}; refusing to guess."
    )


def resolve_field_endpoint(field: str) -> str:
    """Resolve one requested value field to exactly one statement endpoint.

    Unknown fields and fields mapping to more than one endpoint are refused
    (fail-closed) rather than silently sourced from an arbitrary endpoint.
    """
    if field == CH3_FIELD:
        return FINA_INDICATOR_ENDPOINT
    if field in _METADATA_FIELDS:
        raise TushareUnknownFieldError(
            f"{field!r} is raw statement metadata, not a fact value field."
        )
    candidates = [endpoint for endpoint, fields in _FIELD_SETS.items() if field in fields]
    if not candidates:
        raise TushareUnknownFieldError(
            f"{field!r} is not a mapped value field of any Tushare statement endpoint."
        )
    if len(candidates) > 1:
        raise TushareAmbiguousFieldError(
            f"{field!r} appears in endpoints {candidates!r}; refusing to guess."
        )
    return candidates[0]


def _clean_raw(value: object) -> str | None:
    """Preserve a raw provenance value as a trimmed string (or ``None``)."""
    if value is None:
        return None
    if isinstance(value, float) and np.isnan(value):
        return None
    text = str(value)
    return text


def _rows_from_payload(payload: Mapping) -> list[dict]:
    """Expand a ``{"fields": [...], "items": [[...]]}`` payload into row dicts."""
    fields = list(payload.get("fields") or [])
    return [dict(zip(fields, item)) for item in payload.get("items") or []]


def _is_nan(value: object) -> bool:
    return isinstance(value, float) and np.isnan(value)


def _values_conflict(values: Sequence[object]) -> bool:
    reference = values[0]
    for other in values[1:]:
        if _is_nan(reference) and _is_nan(other):
            continue
        if _is_nan(reference) != _is_nan(other):
            return True
        if reference != other:
            return True
    return False


def _update_flag_int(record: Mapping) -> int:
    raw = record.get("_update_flag")
    try:
        return int(str(raw))
    except (TypeError, ValueError):
        return -1


def _ann_ne_f(record: Mapping) -> bool:
    """Signal (b): both dates valid and different."""
    ann_status, ann = _classify_date(record.get("_ann_date"))
    f_status, f = _classify_date(record.get("_f_ann_date"))
    return (
        ann_status == "valid"
        and f_status == "valid"
        and ann != f
    )


# ---------------------------------------------------------------------------
# Statement mapping (policies 2, 3, 6, 8)
# ---------------------------------------------------------------------------
def _map_statement_records(
    payload: Mapping,
    endpoint: str,
    fields: Sequence[str],
    *,
    retrieved_at: pd.Timestamp,
) -> tuple[list[dict], list[dict]]:
    """Map one raw statement payload to fact/uncertainty record dicts.

    Fact records carry private ``_ann_date``/``_f_ann_date``/``_update_flag``
    keys so the later cross-row de-duplication and restatement flagging can
    see the raw vintage evidence. Those keys are dropped when the records are
    materialized into the public DataFrame.
    """
    requested = [f for f in dict.fromkeys(fields) if f != CH3_FIELD]
    records: list[dict] = []
    uncertain: list[dict] = []

    for row in _rows_from_payload(payload):
        stock_id = _clean_raw(row.get("ts_code"))
        period_status, period_end = _classify_date(row.get("end_date"))
        if stock_id is None or period_status != "valid":
            raise TushareValueParseError(
                "Statement row is missing a usable ts_code/end_date: "
                f"{row!r}"
            )

        report_type = normalize_report_type(row.get("report_type"))
        decision = knowledge_date_decision(row)

        if decision.knowledge_date is None:
            for field in requested:
                raw = row.get(field)
                if raw is None:
                    continue
                uncertain.append(
                    {
                        STOCK_COL: stock_id,
                        REPORT_PERIOD_END_COL: period_end,
                        FIELD_COL: field,
                        REPORT_TYPE_COL: report_type,
                        RAW_ANN_DATE_COL: _clean_raw(row.get("ann_date")),
                        RAW_F_ANN_DATE_COL: _clean_raw(row.get("f_ann_date")),
                        RAW_FINA_ANN_DATE_COL: None,
                        REASON_COL: decision.drop_reason,
                        SOURCE_ENDPOINT_COL: endpoint,
                        RETRIEVED_AT_COL: retrieved_at,
                    }
                )
            continue

        basis = infer_reporting_basis(endpoint, row.get("report_type"))
        for field in requested:
            if field not in row:
                continue
            raw = row.get(field)
            if raw is None:
                continue  # genuinely not reported for this row
            is_blank = isinstance(raw, str) and raw.strip() == ""
            if is_blank:
                value = float("nan")
            else:
                try:
                    value = float(raw)
                except (TypeError, ValueError) as exc:
                    raise TushareValueParseError(
                        f"Non-numeric value for field {field!r} on "
                        f"{stock_id} {period_end.date()}: {raw!r}"
                    ) from exc
            records.append(
                {
                    STOCK_COL: stock_id,
                    REPORT_PERIOD_END_COL: period_end,
                    FIELD_COL: field,
                    KNOWLEDGE_DATE_COL: decision.knowledge_date,
                    VALUE_COL: value,
                    IS_RESTATEMENT_COL: False,
                    REPORTING_BASIS_COL: basis,
                    REPORT_TYPE_COL: report_type,
                    IS_BLANK_OUT_COL: is_blank,
                    SOURCE_VENDOR_COL: SOURCE_VENDOR,
                    ACCESS_PATH_COL: ACCESS_PATH,
                    RETRIEVED_AT_COL: retrieved_at,
                    "_ann_date": row.get("ann_date"),
                    "_f_ann_date": row.get("f_ann_date"),
                    "_update_flag": row.get("update_flag"),
                }
            )

    return records, uncertain


def map_statement_payload(
    payload: Mapping,
    endpoint: str,
    fields: Sequence[str],
    *,
    retrieved_at: pd.Timestamp,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Public DataFrame wrapper over :func:`_map_statement_records`.

    Applies the knowledge-date rule per row (dropping and recording cases
    C/D/E), the blank-out rule per value, and the reporting-basis tag.
    Restatement flagging is deferred to :func:`_finalize_facts` because it is
    a cross-row, cross-vintage property (so the returned fact frame's
    ``is_restatement`` column is a placeholder until finalized).
    """
    records, uncertain = _map_statement_records(
        payload, endpoint, fields, retrieved_at=retrieved_at
    )
    return _records_to_frame(records), _records_to_uncertain_frame(uncertain)


# ---------------------------------------------------------------------------
# CH3 join (policies 7 / 9)
# ---------------------------------------------------------------------------
def _ch3_suppression(
    *,
    stock_id: str | None,
    period_end: pd.Timestamp | None,
    report_type: str | None,
    anchor: Mapping | None,
    fina: Mapping | None,
    retrieved_at: pd.Timestamp,
    source_endpoint: str = f"{INCOME_ENDPOINT}+{BALANCESHEET_ENDPOINT}+{FINA_INDICATOR_ENDPOINT}",
) -> dict:
    return {
        STOCK_COL: stock_id,
        REPORT_PERIOD_END_COL: period_end,
        FIELD_COL: CH3_FIELD,
        REPORT_TYPE_COL: report_type,
        RAW_ANN_DATE_COL: _clean_raw(anchor.get("ann_date")) if anchor else None,
        RAW_F_ANN_DATE_COL: _clean_raw(anchor.get("f_ann_date")) if anchor else None,
        RAW_FINA_ANN_DATE_COL: _clean_raw(fina.get("ann_date")) if fina else None,
        REASON_COL: REASON_CH3_VINTAGE_JOIN_NOT_CERTIFIED,
        SOURCE_ENDPOINT_COL: source_endpoint,
        RETRIEVED_AT_COL: retrieved_at,
    }


def _assemble_ch3_records(
    income_payload: Mapping,
    balancesheet_payload: Mapping,
    fina_indicator_payload: Mapping,
    *,
    stock_id: str | None = None,
    period_end: pd.Timestamp | None = None,
    retrieved_at: pd.Timestamp,
) -> tuple[list[dict], list[dict]]:
    """Core CH3 join returning fact/uncertainty record dicts.

    Conditions: (1) the anchor ``income`` ``report_type=1`` row has
    ``ann_date == f_ann_date``; (2) no ``income``/``balancesheet`` row has
    ``report_type in {4,5,9,10,11,12}`` for the period; (3) ``fina_indicator``
    exposes its own ``ann_date`` and it equals the anchor's ``ann_date``.

    Any failure -- including ``fina_indicator`` exposing no comparable date
    -- yields **no** fact row and exactly one uncertainty entry with
    ``reason="ch3_vintage_join_not_certified"``. There is no degraded
    two-condition path.
    """
    income_rows = _rows_from_payload(income_payload)
    balancesheet_rows = _rows_from_payload(balancesheet_payload)
    fina_rows = _rows_from_payload(fina_indicator_payload)

    if stock_id is None and income_rows:
        stock_id = _clean_raw(income_rows[0].get("ts_code"))
    if period_end is None and income_rows:
        status, parsed = _classify_date(income_rows[0].get("end_date"))
        period_end = parsed if status == "valid" else None

    anchors = [r for r in income_rows if normalize_report_type(r.get("report_type")) == "1"]
    anchor = anchors[0] if anchors else None
    anchor_report_type = normalize_report_type(anchor.get("report_type")) if anchor else None

    fina = fina_rows[0] if fina_rows else None

    def suppress() -> tuple[list[dict], list[dict]]:
        return [], [
            _ch3_suppression(
                stock_id=stock_id,
                period_end=period_end,
                report_type=anchor_report_type,
                anchor=anchor,
                fina=fina,
                retrieved_at=retrieved_at,
            )
        ]

    if not anchors:
        return suppress()

    # Condition 1: every anchor row must have valid, equal ann/f dates, and
    # there must be exactly one distinct (ann, f) vintage.
    anchor_pairs: set[tuple[pd.Timestamp, pd.Timestamp]] = set()
    for row in anchors:
        ann_status, ann = _classify_date(row.get("ann_date"))
        f_status, f = _classify_date(row.get("f_ann_date"))
        if ann_status != "valid" or f_status != "valid" or ann != f:
            return suppress()
        anchor_pairs.add((ann, f))
    if len(anchor_pairs) != 1:
        return suppress()
    anchor_date = next(iter(anchor_pairs))[0]

    # Condition 2: no adjusted/pre-adjustment vintage anywhere for the period.
    for row in [*income_rows, *balancesheet_rows]:
        if normalize_report_type(row.get("report_type")) in INELIGIBLE_REPORT_TYPES:
            return suppress()

    # Condition 3: fina_indicator's own ann_date must exist and match.
    if not fina_rows:
        return suppress()
    fina_dates = set()
    for row in fina_rows:
        status, parsed = _classify_date(row.get("ann_date"))
        if status == "valid":
            fina_dates.add(parsed)
    if not fina_dates or anchor_date not in fina_dates:
        return suppress()

    # Value: profit_dedt must be present and single-valued.
    profits = []
    for row in fina_rows:
        raw = row.get(_PROFIT_DEDT_FIELD)
        if raw is None or (isinstance(raw, str) and raw.strip() == ""):
            continue
        try:
            profits.append(float(raw))
        except (TypeError, ValueError):
            return suppress()
    if not profits or _values_conflict(profits):
        return suppress()

    record = {
        STOCK_COL: stock_id,
        REPORT_PERIOD_END_COL: period_end,
        FIELD_COL: CH3_FIELD,
        KNOWLEDGE_DATE_COL: anchor_date,
        VALUE_COL: profits[0],
        IS_RESTATEMENT_COL: False,
        REPORTING_BASIS_COL: CUMULATIVE_YTD,
        REPORT_TYPE_COL: anchor_report_type,
        VINTAGE_ID_COL: (
            f"{anchor_report_type}:{anchor_date.date().isoformat()}:"
            f"{anchor_date.date().isoformat()}"
        ),
        IS_BLANK_OUT_COL: False,
        SOURCE_VENDOR_COL: SOURCE_VENDOR,
        ACCESS_PATH_COL: ACCESS_PATH,
        RETRIEVED_AT_COL: retrieved_at,
        "_ann_date": anchor.get("ann_date"),
        "_f_ann_date": anchor.get("f_ann_date"),
        "_update_flag": anchor.get("update_flag"),
    }
    return [record], []


def assemble_ch3_facts(
    income_payload: Mapping,
    balancesheet_payload: Mapping,
    fina_indicator_payload: Mapping,
    *,
    stock_id: str | None = None,
    period_end: pd.Timestamp | None = None,
    retrieved_at: pd.Timestamp,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Public DataFrame wrapper over :func:`_assemble_ch3_records`.

    Emit ``ni_ex_nonrecurring`` only when all three frozen conditions hold;
    otherwise return an empty fact frame and record the suppression with
    ``reason="ch3_vintage_join_not_certified"``.
    """
    records, uncertain = _assemble_ch3_records(
        income_payload,
        balancesheet_payload,
        fina_indicator_payload,
        stock_id=stock_id,
        period_end=period_end,
        retrieved_at=retrieved_at,
    )
    return _records_to_frame(records), _records_to_uncertain_frame(uncertain)


# ---------------------------------------------------------------------------
# De-duplication + restatement flagging
# ---------------------------------------------------------------------------
def _finalize_facts(records: Sequence[Mapping]) -> pd.DataFrame:
    """De-duplicate on the schema key and set ``is_restatement``.

    Raises :class:`TushareConflictingVintageError` when two distinct vintages
    collapse onto one key with different values.
    """
    if not records:
        return _records_to_frame([])

    groups: dict[tuple, list[dict]] = {}
    for index, record in enumerate(records):
        key = (
            record[STOCK_COL],
            record[REPORT_PERIOD_END_COL],
            record[FIELD_COL],
            record[KNOWLEDGE_DATE_COL],
        )
        entry = dict(record)
        entry["_index"] = index
        groups.setdefault(key, []).append(entry)

    deduped: list[dict] = []
    for key, group in groups.items():
        values = [entry[VALUE_COL] for entry in group]
        if _values_conflict(values):
            raise TushareConflictingVintageError(key, group)
        # Higher update_flag wins (the vendor's own "more final" marker);
        # earliest input order breaks ties deterministically. Every observed
        # update_flag specimen was byte-identical in value, so this is an
        # audit choice, not a data choice.
        representative = max(group, key=lambda e: _update_flag_int(e))
        representative = dict(representative)
        report_types = sorted(
            {str(e[REPORT_TYPE_COL]) for e in group if e.get(REPORT_TYPE_COL) is not None}
        )
        update_flags = sorted(
            {str(e.get("_update_flag")) for e in group if e.get("_update_flag") is not None}
        )
        ann = _clean_raw(representative.get("_ann_date"))
        f_ann = _clean_raw(representative.get("_f_ann_date"))
        representative[VINTAGE_ID_COL] = (
            f"{'|'.join(report_types)}:{ann}:{f_ann}:{'|'.join(update_flags)}"
        )
        deduped.append(representative)

    min_knowledge: dict[tuple, pd.Timestamp] = {}
    for record in deduped:
        key = (record[STOCK_COL], record[REPORT_PERIOD_END_COL], record[FIELD_COL])
        current = min_knowledge.get(key)
        if current is None or record[KNOWLEDGE_DATE_COL] < current:
            min_knowledge[key] = record[KNOWLEDGE_DATE_COL]

    for record in deduped:
        key = (record[STOCK_COL], record[REPORT_PERIOD_END_COL], record[FIELD_COL])
        signal_a = record[KNOWLEDGE_DATE_COL] > min_knowledge[key]
        record[IS_RESTATEMENT_COL] = bool(signal_a or _ann_ne_f(record))

    return _records_to_frame(deduped)


# ---------------------------------------------------------------------------
# Frame construction
# ---------------------------------------------------------------------------
def _records_to_frame(records: Sequence[Mapping]) -> pd.DataFrame:
    frame = pd.DataFrame.from_records(list(records), columns=_FACT_COLUMNS)
    frame[STOCK_COL] = frame[STOCK_COL].astype("string")
    frame[FIELD_COL] = frame[FIELD_COL].astype("string")
    frame[REPORT_PERIOD_END_COL] = pd.to_datetime(frame[REPORT_PERIOD_END_COL])
    frame[KNOWLEDGE_DATE_COL] = pd.to_datetime(frame[KNOWLEDGE_DATE_COL])
    frame[VALUE_COL] = frame[VALUE_COL].astype("float64")
    frame[IS_RESTATEMENT_COL] = frame[IS_RESTATEMENT_COL].astype("bool")
    frame[REPORTING_BASIS_COL] = frame[REPORTING_BASIS_COL].astype("string")
    frame[VINTAGE_ID_COL] = frame[VINTAGE_ID_COL].astype("string")
    frame[IS_BLANK_OUT_COL] = frame[IS_BLANK_OUT_COL].astype("bool")
    frame[SOURCE_VENDOR_COL] = frame[SOURCE_VENDOR_COL].astype("string")
    frame[ACCESS_PATH_COL] = frame[ACCESS_PATH_COL].astype("string")
    frame[RETRIEVED_AT_COL] = pd.to_datetime(frame[RETRIEVED_AT_COL])
    # ``report_type`` is intentionally left as object/string-capable because
    # CH3 rows carry the anchor's code while others may be None.
    return frame.reset_index(drop=True)


def _records_to_uncertain_frame(records: Sequence[Mapping]) -> pd.DataFrame:
    frame = pd.DataFrame.from_records(list(records), columns=_UNCERTAIN_COLUMNS)
    frame[STOCK_COL] = frame[STOCK_COL].astype("string")
    frame[FIELD_COL] = frame[FIELD_COL].astype("string")
    frame[REPORT_PERIOD_END_COL] = pd.to_datetime(frame[REPORT_PERIOD_END_COL])
    frame[REASON_COL] = frame[REASON_COL].astype("string")
    frame[SOURCE_ENDPOINT_COL] = frame[SOURCE_ENDPOINT_COL].astype("string")
    frame[RETRIEVED_AT_COL] = pd.to_datetime(frame[RETRIEVED_AT_COL])
    return frame.reset_index(drop=True)


def _empty_uncertain() -> pd.DataFrame:
    return _records_to_uncertain_frame([])


# ---------------------------------------------------------------------------
# Fetch orchestration
# ---------------------------------------------------------------------------
def _as_naive_ts(value: date | str | pd.Timestamp) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    if ts.tzinfo is not None:
        ts = ts.tz_convert("UTC").tz_localize(None)
    return ts.normalize()


def _period_ends(start: date | str, end: date | str) -> list[pd.Timestamp]:
    """Quarter-end ``report_period_end`` values in ``[start, end]``.

    Tushare statement periods are fiscal quarter ends; the adapter never
    infers a period from an arbitrary date.
    """
    start_ts = _as_naive_ts(start)
    end_ts = _as_naive_ts(end)
    if start_ts > end_ts:
        raise ValueError(f"start {start_ts} is after end {end_ts}")
    periods: list[pd.Timestamp] = []
    for year in range(start_ts.year, end_ts.year + 1):
        for month, day in ((3, 31), (6, 30), (9, 30), (12, 31)):
            candidate = pd.Timestamp(year=year, month=month, day=day)
            if start_ts <= candidate <= end_ts:
                periods.append(candidate)
    return periods


def _required_endpoints(fields: Sequence[str]) -> set[str]:
    endpoints: set[str] = set()
    for field in fields:
        endpoint = resolve_field_endpoint(field)
        if endpoint == FINA_INDICATOR_ENDPOINT:
            endpoints.update({INCOME_ENDPOINT, BALANCESHEET_ENDPOINT, FINA_INDICATOR_ENDPOINT})
        else:
            endpoints.add(endpoint)
    return endpoints


def _collect(
    client: TushareClient,
    ts_codes: Sequence[str],
    start: date | str,
    end: date | str,
    fields: Sequence[str],
    *,
    retrieved_at: pd.Timestamp,
) -> tuple[list[dict], list[dict]]:
    """Fetch every required payload and map it to fact/uncertainty records."""
    requested_fields = list(dict.fromkeys(fields))
    endpoints = _required_endpoints(requested_fields)
    periods = _period_ends(start, end)

    fact_records: list[dict] = []
    uncertain_records: list[dict] = []

    for ts_code in ts_codes:
        for period in periods:
            payloads: dict[str, Mapping] = {}
            for endpoint in sorted(endpoints):
                payloads[endpoint] = client.fetch(
                    endpoint,
                    ts_code=ts_code,
                    period=period.strftime("%Y%m%d"),
                )

            for endpoint in (INCOME_ENDPOINT, BALANCESHEET_ENDPOINT, CASHFLOW_ENDPOINT):
                if endpoint not in payloads:
                    continue
                endpoint_fields = [
                    f for f in requested_fields if resolve_field_endpoint(f) == endpoint
                ]
                if not endpoint_fields:
                    continue
                facts, uncertain = _map_statement_records(
                    payloads[endpoint],
                    endpoint,
                    endpoint_fields,
                    retrieved_at=retrieved_at,
                )
                fact_records.extend(facts)
                uncertain_records.extend(uncertain)

            if CH3_FIELD in requested_fields:
                facts, uncertain = _assemble_ch3_records(
                    payloads[INCOME_ENDPOINT],
                    payloads[BALANCESHEET_ENDPOINT],
                    payloads[FINA_INDICATOR_ENDPOINT],
                    stock_id=ts_code,
                    period_end=period,
                    retrieved_at=retrieved_at,
                )
                fact_records.extend(facts)
                uncertain_records.extend(uncertain)

    return fact_records, uncertain_records


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def get_fundamentals(
    client: TushareClient,
    ts_codes: Sequence[str],
    start: date | str,
    end: date | str,
    fields: Sequence[str],
    *,
    retrieved_at: pd.Timestamp | None = None,
) -> pd.DataFrame:
    """Raw, fully-timestamped Tushare fundamentals facts.

    Returns a ``FUNDAMENTALS_FACT_SCHEMA``-conforming frame plus the
    adapter-owned extra columns (``reporting_basis``, ``report_type``,
    ``vintage_id``, ``is_blank_out``, ``source_vendor``, ``access_path``,
    ``retrieved_at``). Restatements are new rows distinguished by
    ``knowledge_date``; this function never resolves an as-of query and never
    subtracts periods -- that is ``pit.fundamentals.latest_known_value``'s
    job downstream.
    """
    stamp = _resolve_stamp(retrieved_at)
    fact_records, _ = _collect(client, ts_codes, start, end, fields, retrieved_at=stamp)
    frame = _finalize_facts(fact_records)
    validate_panel(frame, FUNDAMENTALS_FACT_SCHEMA, name="tushare_fundamentals")
    return frame


def get_uncertain_observations(
    client: TushareClient,
    ts_codes: Sequence[str],
    start: date | str,
    end: date | str,
    fields: Sequence[str],
    *,
    retrieved_at: pd.Timestamp | None = None,
) -> pd.DataFrame:
    """Per-observation uncertainty side table.

    One row per ``(stock_id, report_period_end, field)`` observation that
    could not be emitted: knowledge-date cases C/D/E and CH3 join
    suppressions. This is the machine-visible form of
    ``Ambiguous/UncertifiedKnowledgeDate`` and
    ``NotCertifiedVintageCoverage``; it is deliberately *not* collapsed into
    a bare count. It is adapter-specific API surface, not a
    ``PITDataSource`` change.
    """
    stamp = _resolve_stamp(retrieved_at)
    _, uncertain_records = _collect(client, ts_codes, start, end, fields, retrieved_at=stamp)
    frame = _records_to_uncertain_frame(uncertain_records)
    if not frame.empty:
        duplicate = frame.duplicated(
            subset=[STOCK_COL, REPORT_PERIOD_END_COL, FIELD_COL, REASON_COL]
        )
        if duplicate.any():
            frame = frame.loc[~duplicate].reset_index(drop=True)
    return frame


def _resolve_stamp(retrieved_at: pd.Timestamp | None) -> pd.Timestamp:
    if retrieved_at is None:
        return pd.Timestamp.now(tz="UTC").tz_localize(None)
    stamp = pd.Timestamp(retrieved_at)
    if stamp.tzinfo is not None:
        stamp = stamp.tz_convert("UTC").tz_localize(None)
    return stamp


class TushareFundamentals:
    """Thin class wrapper over the module functions.

    ``TushareFundamentals(client, ts_codes).get_fundamentals(start, end, fields)``
    matches the task spec's method signature while keeping the transport and
    universe explicit. The per-``ts_code`` universe is required because the
    proxy rejects a period-only bulk query (see module docstring).
    """

    def __init__(
        self,
        client: TushareClient,
        ts_codes: Sequence[str],
        *,
        retrieved_at: pd.Timestamp | None = None,
    ) -> None:
        self._client = client
        self._ts_codes = list(ts_codes)
        self._retrieved_at = retrieved_at

    def get_fundamentals(
        self, start: date | str, end: date | str, fields: Sequence[str]
    ) -> pd.DataFrame:
        return get_fundamentals(
            self._client,
            self._ts_codes,
            start,
            end,
            fields,
            retrieved_at=self._retrieved_at,
        )

    def get_uncertain_observations(
        self, start: date | str, end: date | str, fields: Sequence[str]
    ) -> pd.DataFrame:
        return get_uncertain_observations(
            self._client,
            self._ts_codes,
            start,
            end,
            fields,
            retrieved_at=self._retrieved_at,
        )
