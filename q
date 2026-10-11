[1mdiff --git a/scraper.py b/scraper.py[m
[1mindex d906802..f5b2a49 100644[m
[1m--- a/scraper.py[m
[1m+++ b/scraper.py[m
[36m@@ -15,3 +15,66 @@[m [mSEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"[m
 SEC_DOC_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/{doc}"[m
 CTGOV_URL = "https://clinicaltrials.gov/api/v2/studies"[m
 CTGOV_STUDY_URL = "https://clinicaltrials.gov/study/{nct}"[m
[32m+[m
[32m+[m[32m# --- XBRL concepts (first = preferred when several exist for the same date) ---[m
[32m+[m[32mCASH_CONCEPTS = [[m
[32m+[m[32m    "CashAndCashEquivalentsAtCarryingValue",[m
[32m+[m[32m    "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",[m
[32m+[m[32m    "Cash",[m
[32m+[m[32m][m
[32m+[m[32mCASH_AND_ST_COMBINED = ["CashCashEquivalentsAndShortTermInvestments"][m
[32m+[m[32mST_INVESTMENT_CONCEPTS = [[m
[32m+[m[32m    "ShortTermInvestments",[m
[32m+[m[32m    "AvailableForSaleSecuritiesDebtSecuritiesCurrent",[m
[32m+[m[32m    "MarketableSecuritiesCurrent",[m
[32m+[m[32m    "HeldToMaturitySecuritiesCurrent",[m
[32m+[m[32m][m
[32m+[m[32mLT_INVESTMENT_CONCEPTS = [[m
[32m+[m[32m    "LongTermInvestments",[m
[32m+[m[32m    "AvailableForSaleSecuritiesDebtSecuritiesNoncurrent",[m
[32m+[m[32m    "MarketableSecuritiesNoncurrent",[m
[32m+[m[32m    "HeldToMaturitySecuritiesNoncurrent",[m
[32m+[m[32m][m
[32m+[m[32mOPCF_CONCEPTS = ["NetCashProvidedByUsedInOperatingActivities"][m
[32m+[m[32mRND_CONCEPTS = [[m
[32m+[m[32m    "ResearchAndDevelopmentExpense",[m
[32m+[m[32m    "ResearchAndDevelopmentExpenseExcludingAcquiredInProcessCost",[m
[32m+[m[32m][m
[32m+[m[32mNET_INCOME_CONCEPTS = ["NetIncomeLoss", "ProfitLoss"][m
[32m+[m[32mREVENUE_CONCEPTS = [[m
[32m+[m[32m    "Revenues",[m
[32m+[m[32m    "RevenueFromContractWithCustomerExcludingAssessedTax",[m
[32m+[m[32m    "RevenueFromCollaborativeArrangementExcludingRevenueFromContractWithCustomer",[m
[32m+[m[32m][m
[32m+[m[32mFINANCIAL_FORMS = ("10-K", "10-Q", "20-F", "40-F")[m
[32m+[m
[32m+[m[32m# --- SEC filings worth tracking ---[m
[32m+[m[32mOFFERING_FORMS = {[m
[32m+[m[32m    "S-1", "S-1/A", "S-3", "S-3/A", "S-3ASR",[m
[32m+[m[32m    "424B1", "424B2", "424B3", "424B4", "424B5", "424B7",[m
[32m+[m[32m}[m
[32m+[m[32mSTAKE_FORMS = {[m
[32m+[m[32m    "SC 13D", "SC 13D/A", "SC 13G", "SC 13G/A",[m
[32m+[m[32m    "SCHEDULE 13D", "SCHEDULE 13D/A", "SCHEDULE 13G", "SCHEDULE 13G/A",[m
[32m+[m[32m}[m
[32m+[m[32mTRACKED_FORMS = OFFERING_FORMS | STAKE_FORMS | {[m
[32m+[m[32m    "8-K", "10-Q", "10-K", "20-F", "6-K", "4", "DEF 14A",[m
[32m+[m[32m}[m
[32m+[m
[32m+[m[32m# --- ClinicalTrials.gov ---[m
[32m+[m[32mACTIVE_STATUSES = {[m
[32m+[m[32m    "RECRUITING", "NOT_YET_RECRUITING", "ACTIVE_NOT_RECRUITING",[m
[32m+[m[32m    "ENROLLING_BY_INVITATION",[m
[32m+[m[32m}[m
[32m+[m[32mPHASE_LABELS = {[m
[32m+[m[32m    "EARLY_PHASE1": ("Early Phase 1", 0.5), "PHASE1": ("Phase 1", 1),[m
[32m+[m[32m    "PHASE2": ("Phase 2", 2), "PHASE3": ("Phase 3", 3),[m
[32m+[m[32m    "PHASE4": ("Phase 4", 4), "NA": ("N/A", 0),[m
[32m+[m[32m}[m
[32m+[m[32mNAME_SUFFIX_RE = re.compile([m
[32m+[m[32m    r"\b(inc|incorporated|corp|corporation|ltd|limited|plc|llc|l\.?p|"[m
[32m+[m[32m    r"n\.?v|s\.?a|ag|se|holdings?|group)\b\.?",[m
[32m+[m[32m    re.IGNORECASE,[m
[32m+[m[32m)[m
[41m+[m
[41m+[m
