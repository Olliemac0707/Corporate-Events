# Execution plan: first repurchase study

Status: execution sequence registered on 2026-11-24. This plan prioritizes one family; it does not waive a scientific gate or rewrite the original five-family protocol.

## Fixed scope

- First family: issuer open-market common-share repurchase authorizations and authorization increases.
- First development window: disclosures in 2006-2015. Earlier programmes can supply context and are not relabelled as new events when repeated in later filings.
- Preserve 2016-2020 validation and 2021-2024 outcome-holdout intentions. Some later-period source classifications have already been development-exposed; do not claim an untouched classification holdout.
- Keep all original source records, the frozen 718-record frame, prior parsers, review history and uncertainty.
- Primary later economic analysis: the original 21-session market-adjusted event study. Secondary horizons remain those in the original protocol. Any additional regression specification must be frozen before outcome access.
- No stock-return results have been observed. No other family is removed from the programme.
- Code publication and research certification are separate milestones.

## Stage 1 — freeze the worklist and discovery design

Work now: create a repurchase-only worklist from the existing cached pilot. Record document date, action date, issuer roles, state, evidence and every unresolved requirement.

Baseline: 31 repurchase mentions in the full pilot; 16 occur in development-period filing sources, spanning three components and only two complete filings. These are mentions, not distinct events. This corpus is too concentrated to support a population claim.

Deliverables: private seed-worklist.json, public scope specification and a prospective source-selection plan.

Exit: original hashes pass; every seed mention is retained; source dates are not treated as disclosure or action dates; discovery rules are fixed before new bodies or outcomes.

## Stage 2 — acquire a broader bounded source sample

1. Verify the official SEC full-text search discovery route and historical coverage. The SEC describes electronic filing search since 2001, including exhibits. A documented website feature is not assumed to be a supported automated API.
2. Implement/test an exact-URL metadata adapter before live use; allow at most 12 metadata diagnostic requests. Use the private contact and shared scheduler. Retain all responses and failures.
3. Reconcile every selected accession, form, filing date and historical CIK against the existing master-index census.
4. Freeze a first-wave manifest: at most eight candidate-hit complete submissions and two independently ranked source-control submissions per year for 2006-2015, giving at most 100 additional submissions.
5. Candidate-hit query: repurchase OR buyback OR "buy back", limited to 8-K/8-K/A and their exhibits. Controls are selected independently from the same historical 8-K/8-K/A index frame, before looking at bodies. This first wave is a targeted feasibility sample, not an exhaustive event census.
6. Rank accessions by SHA256 of the plan identity, year, role and historical filename. Do not select on prices, performance, current listing status or company fame. No duplicates across roles; record exhausted cells and failures without replacements chosen for better results.
7. Acquire only after the adapter and metadata manifest pass. New namespace, at most 512 MiB received bodies, existing response/attempt/TLS/pacing/block controls. Do not reset any earlier budget.
8. If the official discovery route cannot be validated, stop body acquisition and publish the precise blocker; do not silently substitute a different search engine or imply full coverage.

Deliverables: metadata manifest, raw content-addressed cache and source-discovery/coverage report.

Exit: source identity and cache hashes verified; target/control roles fixed; request controls pass; scope/shortfalls documented. No final QA or market-data gate opens.

## Stage 3 — build and validate the pilot event ledger

Work:
- Extract the complete source components, retaining table-contained narrative and footnotes.
- Resolve issuer versus reporting-party identity, approval versus announcement dates, total versus incremental capacity, open-market permission, programme links and repeated disclosures.
- Keep routine executions, tenders/ASRs, recaps and unsupported actions as separate context/rejection/unresolved records.
- Find original disclosure evidence. Acceptance time, filing date and a printed release date are not silently promoted to public dissemination.
- Cluster documents into economic actions with explicit documentary links; similarity alone is insufficient.
- Freeze rule/version identity and predicted facts before independent review.

Independent-review packet:
- All distinct potentially qualifying pilot clusters, capped at the first 30 by a preregistered hash rank, plus the 20 fixed source-control documents (or all available if fewer).
- A reviewer separate from parser development examines original source context before seeing predictions. Disagreements receive separate adjudication or remain unresolved.
- This is not another automatic questionnaire for the owner. A reviewer assignment is an explicit remaining resource dependency; it cannot be replaced by calling extrapolated labels independent gold.
- Preserve the 12 completed teaching questions separately. Do not reuse their cases as untouched checks.
- Measure resolved fact agreement, positive precision, missing-event findings and deduplication. Report denominators, unresolved cases and exact confidence intervals. No empty-denominator pass; no whole-population recall claim from an uninformative control sample.
- Operational targets inherited from the current policy: at least 95% resolved-fact agreement, at least 90% resolved positive precision, complete deduplication agreement and no critical provenance/blinding defects. Small samples and wide intervals remain explicit.
- A smaller pilot packet does not by itself pass the original full-programme QA gate. Any scope-specific pilot acceptance amendment must be registered before evaluation; thresholds are not lowered after seeing results.

Deliverables: candidate/context/rejection/qualifying pilot ledgers, independent validation report, source timing evidence and unresolved-case register.

Exit: documented applicable QA gate passes. If no defensible qualifying events or adequate validation evidence exist, report a feasibility failure instead of proceeding to returns. Full-scale extraction stays closed until its own requirements pass.

## Stage 4 — establish market/security authority

Conditional on event-ledger/QA acceptance and explicit market-data authorization:
- Use licensed historical CRSP/CCM access, not a current-ticker or Yahoo substitute.
- Freeze historical issuer/security mappings, common-share/exchange eligibility, trading calendar, price/distribution adjustments, corporate actions and terminal outcomes.
- Resolve the applicable local research and publication permissions for licensed data.
- Keep mapping failures, missing prices and ambiguous actions as coverage records; never delete failed events to improve a result.
- Freeze entry timing and economic-return formula before joining outcomes.

Deliverables: mapping/coverage report, calendar/economics tests and an analysis-ready pilot sample with explicit missingness.

Exit: Phase-3 requirements pass. No outcome analysis runs merely because database access works.

## Stage 5 — produce first empirical results

Conditional on a separately authorized, frozen analysis plan:
- Run the specified primary 21-session market-adjusted event study.
- Report event/sample counts, exclusions, effect estimates, uncertainty, dependence/overlap handling and null findings.
- Run only preregistered secondary analyses. A regression needs a frozen formula, variables, sample and inference method before outcomes are read.
- Label a small development pilot exploratory; do not claim validated alpha or holdout success.
- Publish code and permitted aggregate findings. Raw licensed observations and local credentials remain private.

Deliverables: a reproducible results report, approved aggregate tables/figures and the exact analysis-code revision on GitHub.

## Delivery discipline

- Every stage ends with its named artifact, completed checks and precise remaining blocker.
- Work proceeds automatically within authorized scope. No new teaching questions are requested.
- Do not broaden the study to all five families before delivering the first well-defined study.
- An unmet gate is reported explicitly; no implicit waiver, inferred human approval or invented review.
- Implementation time for local work is controllable. SEC availability, independent reviewer turnaround, evidence sufficiency and licensed-data rights prevent a guaranteed empirical-results date.
- Current action: Stage 1 worklist and prospective discovery design. No SEC filing-body or market-data queries run as part of this plan-writing change.

Sources:
- [SEC full-text search coverage and exhibits](https://www.sec.gov/edgar/search/efts-faq.html)
- [Exact binomial confidence intervals](https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats._result_classes.BinomTestResult.proportion_ci.html)

