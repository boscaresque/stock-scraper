# stock-scraper
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
