#!/usr/bin/env python3
"""
Biotech research scraper: SEC EDGAR + ClinicalTrials.gov
=========================================================

For each ticker it collects:
  * SEC EDGAR (XBRL "company facts" API): cash + investments, quarterly cash burn,
    implied runway, R&D spend, net loss, revenue, shares outstanding
  * SEC EDGAR (submissions API): recent filings - offerings (S-3 / 424B / S-1),
    8-Ks (with item numbers), Form 4 insider filings, 13D/13G stakes
  * ClinicalTrials.gov (API v2): every trial where the company is lead sponsor
    or collaborator - phase, status, enrollment, endpoints, completion dates

Output (one dated folder per run, e.g. biotech_data/2026-10-01/):
  * biotech_research.xlsx with sheets:
      Summary     - one row per ticker (cash, burn, runway, pipeline counts)
      Readouts    - trials with primary completion in the look-back/look-ahead window
      Trials      - every trial found
      Filings     - tracked SEC filings in the window, with links
      Changes     - differences vs the previous run (status, dates, enrollment, new trials)
  * the same tables as CSV files

Setup
-----
    pip install requests pandas openpyxl

The SEC requires a User-Agent that identifies you (name + email). Pass it with
--user-agent or set the SEC_USER_AGENT environment variable.

Usage
-----
    python biotech_scraper.py VRTX CRSP BEAM --user-agent "Jane Doe jane@example.com"
    python biotech_scraper.py --file tickers.txt

tickers.txt: one ticker per line. ClinicalTrials.gov searches by sponsor name,
and the name is cleaned from the SEC company name automatically. If a company
registers trials under a different name, override it with a pipe:
    VRTX
    ALNY | Alnylam Pharmaceuticals
Lines starting with # are ignored.

Run it on a schedule (e.g. weekly); the Changes sheet then shows slipped
completion dates, status changes and new trials since the last run.

Notes and limitations
---------------------
  * Cash = cash & equivalents + short- and long-term marketable securities on the
    latest balance sheet date. Concept choice varies by company, so spot-check.
  * Burn is operating cash flow from the latest filing, normalised to a quarter
    (10-Q cash-flow statements are year-to-date, so this is an average).
  * Foreign filers reporting under IFRS (20-F) will mostly have blank financials.
  * ClinicalTrials.gov sponsor matching is by name; check the sponsor_role column -
    "name match only" rows may belong to a different organisation.
"""

import argparse
import os
import re
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import requests

SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
SEC_DOC_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/{doc}"
CTGOV_URL = "https://clinicaltrials.gov/api/v2/studies"
CTGOV_STUDY_URL = "https://clinicaltrials.gov/study/{nct}"

# --- XBRL concepts (first = preferred when several exist for the same date) ---
CASH_CONCEPTS = [
    "CashAndCashEquivalentsAtCarryingValue",
    "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
    "Cash",
]
CASH_AND_ST_COMBINED = ["CashCashEquivalentsAndShortTermInvestments"]
ST_INVESTMENT_CONCEPTS = [
    "ShortTermInvestments",
    "AvailableForSaleSecuritiesDebtSecuritiesCurrent",
    "MarketableSecuritiesCurrent",
    "HeldToMaturitySecuritiesCurrent",
]
LT_INVESTMENT_CONCEPTS = [
    "LongTermInvestments",
    "AvailableForSaleSecuritiesDebtSecuritiesNoncurrent",
    "MarketableSecuritiesNoncurrent",
    "HeldToMaturitySecuritiesNoncurrent",
]
OPCF_CONCEPTS = ["NetCashProvidedByUsedInOperatingActivities"]
RND_CONCEPTS = [
    "ResearchAndDevelopmentExpense",
    "ResearchAndDevelopmentExpenseExcludingAcquiredInProcessCost",
]
NET_INCOME_CONCEPTS = ["NetIncomeLoss", "ProfitLoss"]
REVENUE_CONCEPTS = [
    "Revenues",
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "RevenueFromCollaborativeArrangementExcludingRevenueFromContractWithCustomer",
]
FINANCIAL_FORMS = ("10-K", "10-Q", "20-F", "40-F")

# --- SEC filings worth tracking ---
OFFERING_FORMS = {
    "S-1", "S-1/A", "S-3", "S-3/A", "S-3ASR",
    "424B1", "424B2", "424B3", "424B4", "424B5", "424B7",
}
STAKE_FORMS = {
    "SC 13D", "SC 13D/A", "SC 13G", "SC 13G/A",
    "SCHEDULE 13D", "SCHEDULE 13D/A", "SCHEDULE 13G", "SCHEDULE 13G/A",
}
TRACKED_FORMS = OFFERING_FORMS | STAKE_FORMS | {
    "8-K", "10-Q", "10-K", "20-F", "6-K", "4", "DEF 14A",
}

# --- ClinicalTrials.gov ---
ACTIVE_STATUSES = {
    "RECRUITING", "NOT_YET_RECRUITING", "ACTIVE_NOT_RECRUITING",
    "ENROLLING_BY_INVITATION",
}
PHASE_LABELS = {
    "EARLY_PHASE1": ("Early Phase 1", 0.5), "PHASE1": ("Phase 1", 1),
    "PHASE2": ("Phase 2", 2), "PHASE3": ("Phase 3", 3),
    "PHASE4": ("Phase 4", 4), "NA": ("N/A", 0),
}
NAME_SUFFIX_RE = re.compile(
    r"\b(inc|incorporated|corp|corporation|ltd|limited|plc|llc|l\.?p|"
    r"n\.?v|s\.?a|ag|se|holdings?|group)\b\.?",
    re.IGNORECASE,
)


# ----------------------------------------------------------------------------
# HTTP
# ----------------------------------------------------------------------------
class Fetcher:
    """requests wrapper with a polite rate limit and retries on 429/5xx."""

    def __init__(self, user_agent, min_interval=0.15, retries=4):
        self.session = requests.Session()
        self.session.headers.update(
            {"User-Agent": user_agent, "Accept": "application/json"}
        )
        self.min_interval = min_interval  # SEC allows max 10 requests/second
        self.retries = retries
        self._last = 0.0

    def get_json(self, url, params=None):
        err = None
        for attempt in range(self.retries):
            wait = self.min_interval - (time.time() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.time()
            try:
                r = self.session.get(url, params=params, timeout=30)
            except requests.RequestException as e:
                err = e
            else:
                if r.status_code == 200:
                    return r.json()
                if r.status_code == 404:
                    return None
                err = RuntimeError(f"HTTP {r.status_code} for {r.url}")
                if r.status_code not in (429, 500, 502, 503, 504):
                    raise err
            time.sleep(2 ** attempt)
        raise err


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------
def parse_date(s):
    """Parse 'YYYY-MM-DD' or 'YYYY-MM' (ClinicalTrials.gov uses both)."""
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    return None


def clean_company_name(name):
    """'Vertex Pharmaceuticals Inc /MA' -> 'Vertex Pharmaceuticals'."""
    name = re.sub(r"\s*/[A-Za-z]{2,3}/?\s*$", "", name)
    name = name.replace(",", " ")
    name = NAME_SUFFIX_RE.sub(" ", name)
    return re.sub(r"\s+", " ", name).strip(" .-")


def to_millions(v):
    return None if v is None else round(v / 1e6, 1)


# ----------------------------------------------------------------------------
# SEC EDGAR
# ----------------------------------------------------------------------------
def load_ticker_map(f):
    data = f.get_json(SEC_TICKERS_URL) or {}
    return {
        row["ticker"].upper(): (int(row["cik_str"]), row["title"])
        for row in data.values()
    }


def _facts(facts, concept, unit="USD", taxonomy="us-gaap"):
    try:
        rows = facts["facts"][taxonomy][concept]["units"].get(unit, [])
    except (KeyError, TypeError):
        return []
    return [r for r in rows if str(r.get("form", "")).startswith(FINANCIAL_FORMS)]


def latest_instant(facts, concepts, unit="USD", taxonomy="us-gaap"):
    """Most recent balance-sheet value across concepts. Returns (row, concept)."""
    best = None
    for priority, c in enumerate(concepts):
        for r in _facts(facts, c, unit, taxonomy):
            if "start" in r:
                continue
            key = (r["end"], -priority, r.get("filed", ""))
            if best is None or key > best[0]:
                best = (key, r, c)
    return (best[1], best[2]) if best else (None, None)


def value_at(facts, concepts, end):
    """Largest value among concepts reported for a given balance-sheet date
    (concepts often overlap, so taking the max avoids double counting)."""
    vals = []
    for c in concepts:
        rows = [r for r in _facts(facts, c) if "start" not in r and r["end"] == end]
        if rows:
            vals.append(max(rows, key=lambda r: r.get("filed", ""))["val"])
    return max(vals) if vals else None


def latest_flow_per_quarter(facts, concepts):
    """Latest income/cash-flow value normalised to a 91-day quarter.
    Returns (per_quarter_value, period_start, period_end)."""
    rows = []
    for c in concepts:
        rows += [r for r in _facts(facts, c) if "start" in r]
    if not rows:
        return None, None, None
    latest_end = max(r["end"] for r in rows)
    cands = [r for r in rows if r["end"] == latest_end]

    def days(r):
        return (parse_date(r["end"]) - parse_date(r["start"])).days

    cands = [r for r in cands if days(r) >= 80] or cands
    cands.sort(key=lambda r: r.get("filed", ""), reverse=True)
    r = min(cands, key=days)  # shortest period = most current signal
    d = max(days(r), 1)
    return r["val"] / d * 91.3, r["start"], r["end"]


def shares_outstanding(facts):
    rows = _facts(facts, "EntityCommonStockSharesOutstanding", "shares", "dei")
    if not rows:
        rows = [r for r in _facts(facts, "CommonStockSharesOutstanding", "shares")
                if "start" not in r]
    if not rows:
        return None, None
    latest = max(rows, key=lambda r: (r.get("filed", ""), r["end"]))
    # multiple share classes appear as several rows in the same filing
    same = [r for r in rows if r.get("accn") == latest.get("accn")
            and r["end"] == latest["end"]]
    return sum(r["val"] for r in same), latest["end"]


def sec_financials(f, cik):
    facts = f.get_json(SEC_FACTS_URL.format(cik=cik))
    out = {}
    if not facts:
        return out

    cash_row, cash_concept = latest_instant(facts, CASH_CONCEPTS)
    if cash_row:
        as_of = cash_row["end"]
        cash = cash_row["val"]
        st = value_at(facts, ST_INVESTMENT_CONCEPTS, as_of) or 0
        lt = value_at(facts, LT_INVESTMENT_CONCEPTS, as_of) or 0
        combined = value_at(facts, CASH_AND_ST_COMBINED, as_of)
        total = cash + st + lt
        if combined and combined + lt > total:
            total = combined + lt
        out.update(
            balance_sheet_date=as_of,
            cash_musd=to_millions(cash),
            st_investments_musd=to_millions(st),
            lt_investments_musd=to_millions(lt),
            total_cash_investments_musd=to_millions(total),
        )

        opcf, start, end = latest_flow_per_quarter(facts, OPCF_CONCEPTS)
        if opcf is not None:
            out["op_cash_flow_per_qtr_musd"] = to_millions(opcf)
            out["burn_period"] = f"{start} to {end}"
            if opcf < 0:
                runway_months = total / (-opcf) * 3
                out["runway_months"] = round(runway_months, 1)
                out["est_cash_out_date"] = (
                    parse_date(as_of) + timedelta(days=runway_months * 30.44)
                ).isoformat()
            else:
                out["runway_months"] = None
                out["est_cash_out_date"] = "cash-flow positive"

    for key, concepts in (
        ("rnd_per_qtr_musd", RND_CONCEPTS),
        ("net_income_per_qtr_musd", NET_INCOME_CONCEPTS),
        ("revenue_per_qtr_musd", REVENUE_CONCEPTS),
    ):
        v, _, _ = latest_flow_per_quarter(facts, concepts)
        out[key] = to_millions(v)

    shares, shares_date = shares_outstanding(facts)
    if shares:
        out["shares_outstanding_m"] = round(shares / 1e6, 2)
        out["shares_as_of"] = shares_date
    return out


def sec_filings(f, cik, ticker, since):
    sub = f.get_json(SEC_SUBMISSIONS_URL.format(cik=cik))
    if not sub:
        return []
    recent = sub.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    rows = []
    for i, form in enumerate(forms):
        filed = parse_date(recent["filingDate"][i])
        if form not in TRACKED_FORMS or not filed or filed < since:
            continue
        acc = recent["accessionNumber"][i]
        doc = recent.get("primaryDocument", [""] * len(forms))[i]
        items = recent.get("items", [""] * len(forms))[i]
        if form in OFFERING_FORMS:
            category = "offering"
        elif form in STAKE_FORMS:
            category = "13D/13G stake"
        elif form == "4":
            category = "insider (Form 4)"
        else:
            category = form
        rows.append({
            "ticker": ticker,
            "filed": filed.isoformat(),
            "form": form,
            "category": category,
            "8k_items": items if form == "8-K" else "",
            "description": recent.get("primaryDocDescription", [""] * len(forms))[i],
            "url": SEC_DOC_URL.format(cik=cik, acc=acc.replace("-", ""), doc=doc),
        })
    return rows


# ----------------------------------------------------------------------------
# ClinicalTrials.gov
# ----------------------------------------------------------------------------
def fetch_studies(f, sponsor, max_pages=10):
    studies, token = [], None
    for _ in range(max_pages):
        params = {"query.spons": sponsor, "pageSize": 100, "format": "json"}
        if token:
            params["pageToken"] = token
        data = f.get_json(CTGOV_URL, params)
        if not data:
            break
        studies += data.get("studies", [])
        token = data.get("nextPageToken")
        if not token:
            break
    return studies


def parse_study(study, ticker, sponsor_query):
    p = study.get("protocolSection", {})
    ident = p.get("identificationModule", {})
    status = p.get("statusModule", {})
    design = p.get("designModule", {})
    spons = p.get("sponsorCollaboratorsModule", {})
    arms = p.get("armsInterventionsModule", {})
    outcomes = p.get("outcomesModule", {})

    phases = design.get("phases") or []
    phase_label = "/".join(PHASE_LABELS.get(ph, (ph, 0))[0] for ph in phases)
    phase_rank = max((PHASE_LABELS.get(ph, (ph, 0))[1] for ph in phases), default=0)

    lead = spons.get("leadSponsor", {}).get("name", "")
    collaborators = [c.get("name", "") for c in spons.get("collaborators", [])]
    q = sponsor_query.lower()
    if q in lead.lower():
        role = "lead"
    elif any(q in c.lower() for c in collaborators):
        role = "collaborator"
    else:
        role = "name match only"

    interventions = [
        i.get("name", "") for i in arms.get("interventions", [])
        if i.get("type") in ("DRUG", "BIOLOGICAL", "GENETIC", "COMBINATION_PRODUCT")
    ] or [i.get("name", "") for i in arms.get("interventions", [])]

    endpoints = [
        f"{o.get('measure', '')} [{o.get('timeFrame', '')}]"
        for o in outcomes.get("primaryOutcomes", [])[:3]
    ]
    pcd = status.get("primaryCompletionDateStruct", {})
    enroll = design.get("enrollmentInfo", {})
    nct = ident.get("nctId", "")

    return {
        "ticker": ticker,
        "nct_id": nct,
        "title": ident.get("briefTitle", ""),
        "phase": phase_label or "N/A",
        "phase_rank": phase_rank,
        "status": status.get("overallStatus", ""),
        "study_type": design.get("studyType", ""),
        "conditions": "; ".join(p.get("conditionsModule", {}).get("conditions", [])),
        "interventions": "; ".join(dict.fromkeys(interventions)),
        "enrollment": enroll.get("count"),
        "enrollment_type": enroll.get("type", ""),
        "start_date": status.get("startDateStruct", {}).get("date", ""),
        "primary_completion_date": pcd.get("date", ""),
        "primary_completion_type": pcd.get("type", ""),
        "completion_date": status.get("completionDateStruct", {}).get("date", ""),
        "has_results": bool(study.get("hasResults")),
        "last_update": status.get("lastUpdatePostDateStruct", {}).get("date", ""),
        "lead_sponsor": lead,
        "collaborators": "; ".join(collaborators),
        "sponsor_role": role,
        "primary_endpoints": " | ".join(endpoints),
        "url": CTGOV_STUDY_URL.format(nct=nct),
    }


# ----------------------------------------------------------------------------
# Analysis
# ----------------------------------------------------------------------------
def upcoming_readouts(trials, today, lookback_days, lookahead_days):
    lo, hi = today - timedelta(days=lookback_days), today + timedelta(days=lookahead_days)
    rows = []
    for t in trials:
        if t["study_type"] != "INTERVENTIONAL":
            continue
        pending = t["status"] in ACTIVE_STATUSES or (
            t["status"] == "COMPLETED" and not t["has_results"]
        )
        d = parse_date(t["primary_completion_date"])
        if pending and d and lo <= d <= hi:
            r = dict(t)
            r["days_until_primary_completion"] = (d - today).days
            rows.append(r)
    rows.sort(key=lambda r: r["days_until_primary_completion"])
    return rows


def summarise_pipeline(trials):
    active = [t for t in trials if t["status"] in ACTIVE_STATUSES]
    by_phase = {}
    for t in active:
        by_phase[t["phase"]] = by_phase.get(t["phase"], 0) + 1
    top = max(active, key=lambda t: t["phase_rank"], default=None)
    return {
        "trials_total": len(trials),
        "trials_active": len(active),
        "trials_lead_sponsor": sum(t["sponsor_role"] == "lead" for t in trials),
        "most_advanced_active_phase": top["phase"] if top else "",
        "active_by_phase": ", ".join(f"{k}: {v}" for k, v in sorted(by_phase.items())),
    }


def diff_trials(prev, curr):
    """Compare two trial tables keyed on (ticker, nct_id)."""
    watch = ["status", "phase", "primary_completion_date", "enrollment", "has_results"]
    key = ["ticker", "nct_id"]
    prev = prev.drop_duplicates(key).set_index(key)
    curr = curr.drop_duplicates(key).set_index(key)
    rows = []
    for k in curr.index.difference(prev.index):
        rows.append({"ticker": k[0], "nct_id": k[1], "change": "new trial",
                     "field": "", "old": "", "new": curr.loc[k, "title"]})
    for k in prev.index.difference(curr.index):
        rows.append({"ticker": k[0], "nct_id": k[1], "change": "no longer found",
                     "field": "", "old": prev.loc[k, "title"], "new": ""})
    for k in curr.index.intersection(prev.index):
        for col in watch:
            if col not in prev.columns:
                continue
            old, new = prev.loc[k, col], curr.loc[k, col]
            if pd.isna(old) and pd.isna(new):
                continue
            if str(old) != str(new):
                change = "changed"
                if col == "primary_completion_date":
                    od, nd = parse_date(str(old)), parse_date(str(new))
                    if od and nd:
                        change = "date slipped" if nd > od else "date pulled in"
                rows.append({"ticker": k[0], "nct_id": k[1], "change": change,
                             "field": col, "old": old, "new": new})
    return pd.DataFrame(rows, columns=["ticker", "nct_id", "change", "field", "old", "new"])


# ----------------------------------------------------------------------------
# Output
# ----------------------------------------------------------------------------
def write_excel(path, sheets):
    with pd.ExcelWriter(path, engine="openpyxl") as xw:
        for name, df in sheets.items():
            df.to_excel(xw, sheet_name=name, index=False)
            ws = xw.sheets[name]
            ws.freeze_panes = "B2"
            if len(df.columns):
                ws.auto_filter.ref = ws.dimensions
            for i, col in enumerate(df.columns, start=1):
                sample = [str(col)] + [str(v) for v in df[col].head(200).tolist()]
                width = min(max(len(s) for s in sample) + 2, 60)
                ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = width


def previous_snapshot(out_root, today_dir):
    dirs = sorted(d for d in out_root.iterdir()
                  if d.is_dir() and d != today_dir and (d / "trials.csv").exists())
    return dirs[-1] if dirs else None


# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------
def read_tickers(args):
    entries = []
    for t in args.tickers:
        entries.append((t.strip().upper(), None))
    if args.file:
        for line in Path(args.file).read_text().splitlines():
            line = line.split("#", 1)[0].strip()
            if not line:
                continue
            if "|" in line:
                t, name = (s.strip() for s in line.split("|", 1))
                entries.append((t.upper(), name))
            else:
                entries.append((line.upper(), None))
    seen, out = set(), []
    for t, n in entries:
        if t not in seen:
            seen.add(t)
            out.append((t.replace(".", "-"), n))
    return out


def run(args):
    ua = args.user_agent or os.environ.get("SEC_USER_AGENT")
    if not ua or "@" not in ua:
        sys.exit("The SEC requires a User-Agent with your name and email, e.g.\n"
                 '  --user-agent "Jane Doe jane@example.com"  (or set SEC_USER_AGENT)')
    tickers = read_tickers(args)
    if not tickers:
        sys.exit("No tickers given. Pass them as arguments or with --file.")

    today = date.today()
    out_root = Path(args.out)
    out_dir = out_root / today.isoformat()
    out_dir.mkdir(parents=True, exist_ok=True)

    f = Fetcher(ua)
    print("Loading SEC ticker list...")
    ticker_map = load_ticker_map(f)
    since = today - timedelta(days=args.filing_days)

    summary, all_trials, all_filings = [], [], []
    for n, (ticker, sponsor_override) in enumerate(tickers, 1):
        print(f"[{n}/{len(tickers)}] {ticker}")
        row = {"ticker": ticker}
        try:
            if ticker not in ticker_map:
                row["error"] = "ticker not found in SEC list"
                summary.append(row)
                continue
            cik, sec_name = ticker_map[ticker]
            sponsor = sponsor_override or clean_company_name(sec_name)
            row.update(company=sec_name, cik=cik, ctgov_sponsor_query=sponsor)

            row.update(sec_financials(f, cik))

            filings = sec_filings(f, cik, ticker, since)
            all_filings += filings
            offerings = [x["filed"] for x in filings if x["category"] == "offering"]
            row["last_offering_filing"] = max(offerings) if offerings else ""
            row[f"form4_count_{args.filing_days}d"] = sum(x["form"] == "4" for x in filings)
            row[f"8k_count_{args.filing_days}d"] = sum(x["form"] == "8-K" for x in filings)

            studies = fetch_studies(f, sponsor)
            trials = list({t["nct_id"]: t for t in
                           (parse_study(s, ticker, sponsor) for s in studies)}.values())
            all_trials += trials
            row.update(summarise_pipeline(trials))

            ups = upcoming_readouts(trials, today, 0, args.lookahead_days)
            if ups:
                row["next_primary_completion"] = ups[0]["primary_completion_date"]
                row["next_readout_trial"] = f'{ups[0]["nct_id"]} ({ups[0]["phase"]})'
        except Exception as e:  # keep going if one company fails
            row["error"] = str(e)
            print(f"   error: {e}")
        summary.append(row)

    summary_df = pd.DataFrame(summary)
    trials_df = pd.DataFrame(all_trials)
    filings_df = pd.DataFrame(all_filings)
    readouts_df = pd.DataFrame(
        upcoming_readouts(all_trials, today, args.lookback_days, args.lookahead_days)
    )
    if not filings_df.empty:
        filings_df = filings_df.sort_values(["filed", "ticker"], ascending=[False, True])

    # Put the most-used columns first in the Readouts sheet
    if not readouts_df.empty:
        first = ["ticker", "nct_id", "phase", "status", "primary_completion_date",
                 "primary_completion_type", "days_until_primary_completion",
                 "has_results", "interventions", "conditions", "enrollment", "title"]
        readouts_df = readouts_df[first + [c for c in readouts_df.columns if c not in first]]

    sheets = {"Summary": summary_df, "Readouts": readouts_df,
              "Trials": trials_df, "Filings": filings_df}

    prev_dir = previous_snapshot(out_root, out_dir)
    if prev_dir is not None and not trials_df.empty:
        prev_df = pd.read_csv(prev_dir / "trials.csv")
        changes = diff_trials(prev_df, trials_df)
        changes.insert(0, "compared_to", prev_dir.name)
        sheets["Changes"] = changes
        print(f"Compared with snapshot from {prev_dir.name}: {len(changes)} changes")

    for name, df in sheets.items():
        df.to_csv(out_dir / f"{name.lower()}.csv", index=False)
    xlsx = out_dir / "biotech_research.xlsx"
    write_excel(xlsx, sheets)
    print(f"\nDone. {len(all_trials)} trials, {len(readouts_df)} upcoming readouts, "
          f"{len(all_filings)} filings.\nSaved to {xlsx}")


def main():
    ap = argparse.ArgumentParser(description="Biotech research scraper (SEC EDGAR + ClinicalTrials.gov)")
    ap.add_argument("tickers", nargs="*", help="Tickers, e.g. VRTX CRSP")
    ap.add_argument("--file", help="Text file with one ticker per line (optional '| sponsor name')")
    ap.add_argument("--user-agent", help='SEC User-Agent, e.g. "Jane Doe jane@example.com"')
    ap.add_argument("--out", default="biotech_data", help="Output folder (default: biotech_data)")
    ap.add_argument("--filing-days", type=int, default=180, help="SEC filing window in days (default 180)")
    ap.add_argument("--lookahead-days", type=int, default=365, help="Readout window ahead (default 365)")
    ap.add_argument("--lookback-days", type=int, default=180,
                    help="Also show trials whose primary completion passed this many days ago "
                         "without posted results (default 180)")
    run(ap.parse_args())


if __name__ == "__main__":
    main()
