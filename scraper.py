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


