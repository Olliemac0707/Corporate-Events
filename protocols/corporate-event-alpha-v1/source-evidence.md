# Official source evidence and conservative interpretation

Reviewed 2026-11-04. Only documentation was consulted; no event corpus or market outcomes.

- SEC Accessing EDGAR Data: https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data
  Supports historical form/master indexes, complete submissions, filing documents and Feed/Oldloads archives. Indices record filing dates, which are not automatically dissemination days. Rebuilt indexes may incorporate later corrections. Discovery must not depend on present ticker survivors.
- SEC Webmaster FAQ: https://www.sec.gov/about/webmaster-frequently-asked-questions
  Requires a declared research contact/User-Agent and fair access. It explicitly warns that acceptance-to-website lag varies without a guarantee. The protocol's 900 seconds is operational preparation time, not a maximum dissemination delay.
- SEC July 2003 EDGAR manual adoption: https://www.sec.gov/rules-regulations/2003/07/adoption-updated-edgar-filer-manual
  Gives ownership-form processing context applicable before this study; does not certify exact public time of individual filings.
- SEC EDGAR Release 23.4: https://www.sec.gov/submit-filings/edgar-news-announcements/edgar-release-23-4
  Documents 13D/G hours and structured-form transitions; historical aliases remain separate from amendments and passive forms.
- SEC modernization rule: https://www.sec.gov/files/rules/final/2023/33-11253.pdf
  Supports the February 2024 regime change and December 2024 structured reporting compliance. Neither acquisition nor filing deadline is a public timestamp.
- WRDS Terms of Use: https://wrds-www.wharton.upenn.edu/users/tou/
  Academic subscribers' use is limited to academic/non-commercial research; subscriber and vendor restrictions also apply. Raw vendor data stays private. Permission for local intended use and derived publication/redistribution is not established merely by technical access.

## Registered source routes

https://www.sec.gov/Archives/edgar/full-index/{year}/QTR{quarter}/master.idx
https://www.sec.gov/Archives/edgar/full-index/{year}/QTR{quarter}/form.idx
https://www.sec.gov/Archives/edgar/daily-index/{year}/QTR{quarter}/master.{yyyymmdd}.idx
https://data.sec.gov/submissions/CIK{ten_digit_cik}.json
Historical submissions files explicitly referenced by that metadata, same host/path.
https://www.sec.gov/Archives/{registered_index_filename}
https://www.sec.gov/Archives/edgar/data/{cik}/{accession_without_dashes}/{document_name}
https://www.sec.gov/Archives/edgar/data/{cik}/{accession_with_dashes}-index.html
Registered complete text, header, index.json and constituent documents/exhibits.
SEC Feed/Oldloads day archive members when needed for independent dissemination-day evidence.

Form aliases, SEC URL templates and source priorities identify candidate sources, not automatic event qualification. Any unknown form/schema is retained for review rather than guessed. Exhibits such as 99.1 are priorities, not exclusive filters. Relevant registration/proxy forms can corroborate spin-off provenance but do not automatically set first disclosure.

No current query-time observation, filing date, printed release date or acceptance timestamp alone is sufficient for historical dissemination. A day-bound needs archive membership or another registered official item that actually establishes public dissemination on that day. If that meaning/version cannot be verified, use TIMESTAMP_UNRESOLVED. Later official authority corrections require transparent revisions; no retroactive confidence assertion.

The Phase-1A QA architecture is carried forward: five per nonempty family/era/form/source/control cell, deterministic SHA-256 rank, predetermined edge cases and independent source controls. Phase 1C did not draw an event QA sample.

The FAQ currently links to https://www.sec.gov/info/edgar/specifications/pds_dissemination_spec.pdf, a March 2025 draft. It distinguishes acceptance from dissemination and archival corrections; it is not assumed to establish all historical source timezone semantics. SGML local-time conversion therefore requires explicitly verified source-zone semantics; JSON offsets are preserved separately and unknown cases remain unresolved.
