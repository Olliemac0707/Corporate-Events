# Corporate Event Alpha Research

**Research preview: SEC event-data tooling. Empirical alpha results are not available yet.**

Builds a traceable SEC dataset for share repurchases, insider purchases, activist ownership, dividends and spin-offs. Source evidence, uncertainty and review history are retained before market analysis.

## Offline demo

Python 3.12; no credentials, private corpus or network access:

    python tools/corporate_events/demo.py

The three synthetic examples show a repurchase authorization, a dividend with an unknown prior baseline, and an intended separation. Outputs are provisional facts, not certified events or investment results.

## Install and check

Hash-pinned files require preserved line endings. Clone with conversion disabled:

    git -c core.autocrlf=false clone https://github.com/Olliemac0707/Corporate-Events.git
    cd Corporate-Events
    python -m venv .venv
    .venv/Scripts/python.exe -m pip install -r requirements-dev.txt
    .venv/Scripts/python.exe -m pytest -q
    .venv/Scripts/python.exe -m ruff check tools tests
    .venv/Scripts/python.exe -m mypy tools/corporate_events tests
    .venv/Scripts/python.exe -I -S -B tools/corporate_events/phase2.py validate

GitHub Actions runs the checks on Windows/Python 3.12. Tests use synthetic inputs and temporary directories and do not require the private corpus. Core tools use the Python standard library.

## Research status

Latest local development checkpoint, 2026-11-26:

| Milestone | Status |
| --- | --- |
| Historical SEC index census | Completed: 76 quarters, 2006-2024 |
| Original bounded development corpus | 76 complete submissions; supplemental authority evidence separate |
| Frozen development QA frame | 718 records, 297 cells; membership preserved |
| Original pilot parser | event-parsers-development-v4.1 |
| Original pilot provisional mentions | 69; not 69 unique certified events |
| Accepted research events | 0 |
| Latest recorded software checks | 1,142 tests passed; Ruff and mypy passed |
| Targeted interpretation check | 12 earlier teaching questions and eight later repurchase excerpt responses; not independent full-source gold |
| Accuracy, precision, recall and probability calibration | Not established |
| Returns, regressions, CAR/BHAR, portfolios and backtests | Not run |
| Full extraction / final QA / market-data phases | Closed pending registered requirements and authorization |

These counts describe private development artifacts. A fresh clone reproduces the software checks and synthetic demo, not corpus counts. A software test pass does not establish predictive value.

## Bounded repurchase discovery

The metadata-only adapter completed all 120 monthly partitions for 2006-2015 and replayed 1,889 immutable raw response pages. The query returned 182,703 document hits; these are not certified events.

The private first-wave manifest fixes 100 unique complete-submission accessions: eight candidate hits and two independently selected source controls per year. Every selected source is reconciled against the historical SEC master index. Controls are not assumed negative. **All 100 selected submissions are now acquired and container-validated.** The original 718-record QA frame and all research gates remain unchanged.

See the [aggregate discovery checkpoint](protocols/corporate-event-alpha-v1/repurchase-discovery-checkpoint-v1.json) for annual counts, budgets and the source-manifest fingerprint. Raw responses, the accession manifest, request logs and the contact value remain local.

The active local entrypoint is sec_repurchase_discovery_v4.py, followed by the offline sec_discovery_audit_v2.py replay. These require the registered private index cache and original manifests. Earlier adapter versions are retained to reproduce recorded diagnostics; use the active entrypoint without restarting or resetting budgets. The adapter uses an observed backend route from the official SEC search site; this is not a claim of a stable, documented public API contract.

## Bounded source acquisition

All 100 fixed sources passed header/container checks and offline raw-cache replay: 80 candidate-hit submissions and 20 source controls, comprising 95 forms 8-K and five 8-K/A. Acquisition received 122,364,106 bytes (116.7 MiB) under the 512 MiB cap, across 101 requests. One interrupted response contributed 64 KiB to the byte ledger before a successful retry. No 403 or 429 responses occurred. The legacy SEC privacy-envelope format is retained and validated without discarding the original stopped attempt or resetting budgets.

The [aggregate acquisition checkpoint](protocols/corporate-event-alpha-v1/repurchase-acquisition-checkpoint-v1.json) records annual form counts, manifest fingerprints, software checks and firewall counters. Raw filings, contact configuration and request journals remain local. The active local entrypoint is repurchase_acquisition_v2.py with acquire and audit commands; audit is offline. Both require the original registered private artifacts. Completed acquisition reuses the hash-verified cache.

These sources now feed the bounded repurchase ledger below. Independent factual QA and all full-extraction and market-data gates remain separate requirements.

## Bounded repurchase event ledger

The first study asks whether new share-buyback announcements are followed by stock performance beyond general market moves. The current job is to establish which announcements actually occurred before joining prices.

The fixed 100-submission sample now has an offline source-linked ledger: 745 passages, including 22 provisional authorization/increase candidates across 11 filings, 217 context passages, 200 provisional non-target contexts and 306 unresolved passages. These are passage counts, not distinct verified event counts. All 745 evidence records passed raw-source/derived-text offset and hash replay. The ledger retains approval dates, stated announcement dates and filing dates separately; public dissemination remains unresolved.

All 599 source components are accounted for: 272 were processed as visible text, 326 non-text components remain in their original containers, and one had empty visible text. Binary/unparsed content remains a coverage limitation. A zero-keyword result does not establish that a source is a negative control. The two large contract exhibits and wrapped source lines were handled in versioned renderer corrections, preserving earlier snapshots and classification rules.

The [aggregate ledger checkpoint](protocols/corporate-event-alpha-v1/bounded-repurchase-ledger-checkpoint-v1.json) contains counts, evidence fingerprints, remaining requirements and software checks. The private local ledger.html provides source quotes and links to complete extracted component text. It includes predictions and is not a blinded review packet. The active local entrypoint is bounded_repurchase_ledger_v3.py with build and validate commands; it requires the original private cache and preserved earlier snapshots.

No event is yet certified for research use. Economic-action identity, duplicate resolution, issuer/security eligibility, programme scope, public timing and independent factual QA remain open. The original 718-record frame is unchanged. This ledger stage used zero network requests and left all 17 outcome-firewall counters at zero. No returns, regressions or backtests have been run.

## Repurchase action context

The 22 shortlisted passages are now linked to 12 development action groups using the filing/exhibit relationships and programme references. Two distinct older actions found in the related context are retained separately, giving 14 source-context action records across the same 11 filings. Equal amounts do not merge different programmes, and an original authorization stays separate from a later increase.

Five current releases have cited common-share and open-market scope. One current increase has unresolved open-market permission. Seven older action references retain unresolved original-disclosure evidence, and one accelerated-repurchase execution/derivative-contract case is outside the initial authorization scope. These are development source-context findings, not independently certified research events or an exhaustive result for all 745 source passages.

The [aggregate context checkpoint](protocols/corporate-event-alpha-v1/repurchase-context-checkpoint-v1.json) binds the output and software checks. The private actions.html view includes all 14 records and 40 cited evidence spans. Approval, announcement, effective and filing dates remain distinct, including partial dates. Amounts are retained as stated; no implied aggregate capacity or currency code is invented. Source claims and original container bytes passed replay checks.

Independent factual QA, first-public dissemination timing and historical security eligibility remain open. Research-qualifying events remain zero. The original 718-record frame is unchanged, no new filings or market data were requested, and all 17 outcome-firewall counters remain zero. The active local entrypoint is repurchase_context_resolution.py; it requires the registered private proposals and original cache. Earlier ledgers are preserved.

## Risk-based review triage

An offline interpretation-risk score now prioritises review across all 100 bounded filings, 745 ledger passages and 14 action records. Warning reasons include parser/context disagreement, confusing contract execution with authorization, equal capacities belonging to distinct actions, grouped passages and competing date roles. Scores are 0–100 risk points, not measured error probabilities. The registered development threshold is 35; repeated passages do not inflate a filing's score.

Seven filings cross the threshold. The frozen first review wave contains ten filing packets: six priority cases, two randomly selected lower-risk discovery hits and two randomly selected fixed controls. One priority filing remains in the backlog. Grouping related passages by filing reduces repeated reading. Lower-risk and control audits check for errors the rules may miss; this targeted batch does not estimate population accuracy.

The [aggregate triage checkpoint](protocols/corporate-event-alpha-v1/repurchase-review-triage-checkpoint-v1.json) records counts, source replay and software checks. The [review-routing amendment](protocols/corporate-event-alpha-v1/repurchase-review-prioritisation-amendment-v1.json) changes the initial diagnostic workload without waiving acceptance requirements. The [frozen scoring policy](protocols/corporate-event-alpha-v1/repurchase-review-triage-policy-v1.json) records weights, threshold, sampling seed and limitations. The active local entrypoint is repurchase_review_triage.py with build and validate commands; private registered inputs are required.

Private source-only-review.html packets contain source excerpts and working local links to complete context, with scores and proposed labels withheld. The separate operator-queue.html shows selection reasons. All ten selected original containers, their 29 components and 18 excerpt offsets passed replay. Independent decisions remain zero. Missing source evidence stays a separate follow-up task, and low scores do not automatically accept cases. Calibration and an acceptance design still require independent labels and evaluation separate from development. This stage made zero acquisition requests and all 17 outcome-firewall counters remain zero.

The design draws on [selective classification](https://www.jmlr.org/papers/v24/21-0048.html), [sample-efficient evaluation](https://proceedings.mlr.press/v139/kossen21a.html) and [probability calibration](https://proceedings.mlr.press/v70/guo17a.html). These motivate the review design; the handcrafted score has no demonstrated statistical guarantee.

## Human feedback and remaining work

The original registered progress snapshot compares the first three submitted excerpt answers with the frozen source-context interpretations: a later increase versus the initial authorization, incremental versus total capacity, and two distinct programmes. These are three checked questions, not three fully reviewed filings. They compare against the source-context layer; one original-parser subtype error remains recorded separately.

The progress tracker retains every original score and recomputes remaining review-priority points after removing only the warning codes covered by a confirmed question. In that original snapshot, three of the seven priority filings fell below the routing threshold and four retained open priority flags, including three in the unchanged ten-filing first wave and one backlog filing. Other warning codes, source-evidence tasks and scientific requirements remain open. Disagreement or uncertainty retains review work and requires adjudication. Zero remaining points do not certify a filing.

The [cumulative six-response checkpoint](protocols/corporate-event-alpha-v1/repurchase-review-feedback-checkpoint-v2.json) records the completed short follow-up batch without changing the original snapshot. Five responses provide scoped confirmation of source-context facts; one separately resolves a continuation passage with no prior action-context prediction. The date response explicitly distinguishes the July 29 release dateline from the July 30 filing signature and preserves uncertainty about actual public announcement timing. It does not certify first-public dissemination. At that checkpoint, two of the original seven priority filings retained open flags. No six-question accuracy estimate is claimed.

The [eight-response checkpoint](protocols/corporate-event-alpha-v1/repurchase-review-feedback-checkpoint-v3.json) records the two subsequent combined date/grouping answers. The displayed Mothers Work passages support one programme across linked documents and distinguish the June 30 financial-period end from the July 29 stated announcement date. The Smith & Wesson passages distinguish the December 27 additional authorization from its June 30 end date, link the repeated descriptions and retain the earlier December 6 authorization separately. All seven originally flagged filings now fall below the interpretation-review threshold for these scoped checks. Original scores and the ten-filing review frame remain unchanged. This completes the priority excerpt queue, not full-source QA or scientific acceptance. Eight responses do not establish population accuracy or calibrated probabilities; the date qualification remains preserved.

The [aggregate feedback checkpoint](protocols/corporate-event-alpha-v1/repurchase-review-progress-checkpoint-v1.json) records denominators, output identities and checks. The [registered progress policy](protocols/corporate-event-alpha-v1/repurchase-review-progress-policy-v1.json) makes clear that this is post-review accounting, without retuning original weights or treating owner feedback as independent gold. The active local entrypoint is repurchase_review_progress.py with build and validate commands. Its private progress.html shows original versus remaining points, and candidate-readiness.json retains all 14 action records and their open requirements.

The private repurchase-study-status-v3.html report presents the five current-release candidates with supporting source scope, their stated capacities and dates, plus links to the evidence and the completed short-question frame. Earlier reports and append-only receipts are preserved. Review prompts retained the original source membership and showed sources without predictions or expected answers. Source/control audits, applicable independent full-source QA, historical security eligibility and first-public timing remain open; Smith & Wesson open-market permission also remains unresolved. Independent gold labels and research-qualifying events remain zero; calibrated correctness probabilities are unestablished. This stage used no acquisition requests or market data; all 17 outcome-firewall counters remain zero.

## Source/control and clock audit

All 100 fixed submissions and 599 components passed a fresh original-byte, component-offset and renderer replay. All 20 source controls are retained, including their 81 components: 41 visible-text, 39 retained non-text and one empty-text component. An alternative-wording crosscheck found 12 cues across five controls. Development inspection of those excerpts found third-party acquisition, underwriting, compensation, reorganization and reported-execution contexts; none established an additional issuer open-market authorization. These findings do not certify whole-source negatives or estimate recall.

All 100 headers contain a calendar-valid raw acceptance string. Source-specific timezone semantics remain unverified, so the audit supplies no normalized acceptance times or public-time guarantees. The registered cached archive-prefix replay verified 69 member headers, with zero matches to the 2006–2015 pilot. That partial archive is dated 2022-07-26; non-membership in a prefix is not evidence of absence or dissemination timing. Public times certified remain zero.

The prepared source-only review bundle contains 31 unique filings: the 11 existing action-context source filings plus all 20 fixed controls. Original containers and complete extracted component links passed 143 local-link checks. Proposed classifications, scores, owner answers and per-source sampling roles are withheld. Non-text coverage limitations remain explicit. No reviewer is assigned and no independent decisions are recorded; this bundle does not replace original full-programme QA.

The [aggregate source-audit checkpoint](protocols/corporate-event-alpha-v1/repurchase-source-audit-checkpoint-v1.json) records counts, evidence identities, limitations and checks. The active local entrypoint is repurchase_source_audit.py with build and validate commands. The private repurchase-study-status-v4.html report links the audit, source-only review bundle and model-readiness worklist. All prior snapshots and review receipts remain intact. No new filing acquisition, market-provider access, returns or regressions occurred; all 17 outcome-firewall counters remain zero.

The [outcome-blind model draft](protocols/corporate-event-alpha-v1/repurchase-model-design-draft-v1.json) retains the original 21-session event study as primary and proposes a later secondary regression comparing increases with new authorizations. It is a draft, not registered analysis or permission to fit a model. Dependence, inference and multiplicity choices must be resolved before outcomes; event-date clustering can distort naive event-study tests. [Kolari and Pynnonen (2010)](https://doi.org/10.1093/rfs/hhq072).

The immediate dependencies are an independent full-source reviewer and accession-specific timing evidence. A prospective timing worklist identifies the five current-release candidates across four candidate archive-date URLs, with no requests sent and no claim that those routes or date semantics are validated. Applicable event QA must precede separately authorized Phase-3 historical identity/economics/calendar/rights work. The final model and analysis authorization must then precede outcome access. Model-eligible rows currently remain zero; no new owner teaching batch is queued.

## Bounded timing diagnostics

The subsequently authorized timing stage implemented and registered an exact-plan client, passed offline fault tests and an in-firewall synthetic transport check, and completed the four approved archive-prefix requests. All four returned valid HTTP 206 responses with matching Content-Range/Content-Length. Exactly 4,194,304 bytes (4 MiB) were received. No automatic retries, redirects, unbounded/full-GET fallbacks or prior-budget resets occurred. Earlier stage snapshots remain historical records.

All five target accessions remain unlocated in the downloaded prefixes. Across the four archives, 95 distinct member headers were inspected; separate target scans of the same prefix do not add distinct physical headers. A prefix miss does not prove archive absence. Header membership alone would still require the registered dissemination-day/time semantic authority. Verified public times and model-eligible rows therefore remain zero.

The [diagnostic checkpoint](protocols/corporate-event-alpha-v1/sec-timing-diagnostics-checkpoint-v1.json) records HTTP statuses, prefix hashes, byte counts, archive-size metadata and preserved budgets. The complete archives are reported by the server to total about 821 MiB; those complete archives were not downloaded. The approved four requests and 4 MiB are fully spent. Further acquisition requires a separately registered exact-source/budget plan and explicit authorization; no continuation or reset is automatic.

The active entrypoint is sec_timing_diagnostics.py. Its validate command is offline, acquire enforces durable per-URL/request/byte limits under the original global scheduler, and audit replays cached prefixes without networking. A used or interrupted namespace cannot silently start a new budget. Runtime authorization and contact remain private. The client version was committed before live use; offline audit verified all four cached responses and all ten pinned prior budget files. Original source, QA and outcome gates are unchanged.

The private repurchase-study-status-v5.html report adds the per-archive results. Independent full-source review is still unassigned, and accession-specific public-time evidence remains unresolved. No market observations, prices, returns or regressions were accessed or calculated; all 17 outcome-firewall counters remain zero.

## Route to first empirical results

The authorized directory probe received one complete HTTP 200 JSON response (8,508 bytes), then stopped because SEC used the relative label Feed/2007/QTR3/ where the frozen client expected a full directory path. The original client, raw cache and stopped ledger are preserved. The other three quarter requests were not made, and the stopped batch does not automatically resume. The earlier archive-prefix budget stays sealed.

A separately registered offline decoder accepts only the exact full or Feed-relative directory identity tied to the source URL. It validated 63 listed names, all daily compressed archives, with no additional HTTP requests. The selected 20070802 archive is listed, but target accession membership remains unproven. No compact audit or membership file is listed in this one response; this is not an absence claim for other quarters or SEC resources. The [validation checkpoint](protocols/corporate-event-alpha-v1/sec-feed-directory-validation-checkpoint-v1.json) binds the live and replay manifests, stop record, counters and software checks.

The [cached official-specification review](protocols/corporate-event-alpha-v1/sec-timing-semantics-review-v1.json) identifies daily dissemination audit files as a possible compact evidence source. The actual target audit files have not been found. The [updated route assessment](protocols/corporate-event-alpha-v1/sec-timing-route-assessment-v2.json) also reviews already cached SEC access guidance. Directory names, modification dates and daily-index filing-date fields cannot substitute for the accession-specific dissemination evidence required by the frozen protocol. No public clocks or model rows are certified.

The retained live entrypoint is sec_feed_directory.py; its registered stop behavior is unchanged. The new sec_feed_listing_replay.py entrypoint is offline-only and rejects all networking. Its synthetic tests cover source/quarter mismatches, duplicate JSON keys, unsafe paths and conflicting item links. The private repurchase-study-status-v7.html report records this milestone. Further acquisition needs an exact bounded evidence plan; no additional scope is granted by the replay or route assessment.

The next registered client, sec_two_archive_ranges.py, is limited to the first two candidate archives. It can reuse the preserved prefixes and request at most eight additional ranges, each at most 50 MiB, with a combined maximum of 365,284,503 bytes. The client requires the prior archive version, preserves the existing five-attempt-per-URL limit and stops on any changed response or failure. Its separate offline inspection checks compressed-stream integrity and exact target membership; the archive day remains unresolved without documentary semantic authority. The other three candidate filings are outside this client scope.

The authorized run completed all eight ranges, receiving 365,284,503 bytes. Offline inspection reconstructed both archives, verified their gzip checksums and found both target accessions. Both archived submissions have different raw container bytes from the previously cached complete submissions. After line-ending and delimiter normalization, the first filing's two components match; two of the second filing's three components match, while one remains flagged for review. A header-name inventory found no daily-audit filename inside these two archives, with no conclusion about other archives. See the [aggregate evidence checkpoint](protocols/corporate-event-alpha-v1/sec-two-archive-evidence-checkpoint-v1.json).

Archive membership does not by itself certify the day of dissemination or establish the earliest public announcement. Both public clocks remain unresolved, and no research events or model rows are certified. The offline sec_two_archive_content_check.py entrypoint compares source-linked component hashes without networking or altering the earlier records. The private repurchase-study-status-v8.html report links the completed validation milestone. The remaining three candidates require another evidence route or prospective review of the existing request limits.

A subsequent [offline passage replay](protocols/corporate-event-alpha-v1/sec-mothers-span-replay-checkpoint-v1.json) checked the nine previously recorded action-support and capacity passages for the 2008 filing. All nine appear in the archive rendering. The first component's rendered text still differs, while both exhibits render identically. This targeted comparison preserves the mismatch and does not establish full-source factual accuracy or public timing.

Code publication is separate from scientific certification.

1. Resolve event evidence: action identity, dividend comparators, issuer/reporting roles, separation structures and information timing. Preserve unresolved cases.
2. Register a defensible final factual-validation design and evaluate examples separate from teaching. The teaching pilot does not meet the existing final QA requirements.
3. Build and freeze the qualifying event ledger, including rejection and coverage evidence.
4. Under a separately authorized market-data phase, establish historical security mapping, prices, corporate actions, terminal outcomes and calendar alignment.
5. Run preregistered statistical analysis with uncertainty and development/validation/holdout separation. Report null results as well as positive ones.

The first workstream is now share-repurchase announcements in the 2006-2015 development period. See [the staged execution plan](EXECUTION_PLAN.md) for source limits, deliverables, QA dependencies and the route to empirical results. This prioritization does not waive the original scientific gates or authorize market-data queries.

## Public and local artifacts

- Public: code, protocols, synthetic tests, development policies and an offline demo.
- Local only: .env, research contact, filing caches, databases, review journals, private reports and licensed vendor observations.
- Demo/tests need no SEC contact. Live SEC acquisition requires a real privately configured SEC_RESEARCH_CONTACT and the registered access controls.
- Private corpus replay/audit commands require the original local artifacts and pinned manifests. Missing artifacts are not a successful research QA result.
- Original protocol payloads, tags and predecessor parsers are preserved. Development amendments remain separate.


## Protocol identity

The original frozen payload and annotated tag remain intact. Methodology, review provenance and predecessor policies are preserved under protocols/corporate-event-alpha-v1. Automated classifications and actual reviewer decisions remain distinct.

Original manifest SHA-256: 1461b12b2c2860564700868763bd4d9a8b9f94ab561ec8059dd9c1eadab464ff.

Current publication files use neutral assessment terminology. The [publication migration record](protocols/corporate-event-alpha-v1/publication-terminology-migration-v1.json) identifies the changed bytes and corresponding registries. Automated assessments remain separate from human decisions. The original frozen protocol payload and tag are unchanged. Earlier development runs and their original private journals can be replayed from commit 3b0255d819be07d550f3c6e26d57b86b893ee0c1; that commit retains the original source bytes and historical wording. The current confidence-review entrypoint uses a separate development-confidence-review-policy-v2.json and automated-confidence-review-v2 namespace; existing assessment journals are not rewritten or relabelled. The bounded ledger, source-context records and ten-packet triage batch retain their recorded hashes.
