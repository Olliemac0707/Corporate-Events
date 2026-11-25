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

Latest local development checkpoint, 2026-11-25:

| Milestone | Status |
| --- | --- |
| Historical SEC index census | Completed: 76 quarters, 2006-2024 |
| Original bounded development corpus | 76 complete submissions; supplemental authority evidence separate |
| Frozen development QA frame | 718 records, 297 cells; membership preserved |
| Current parser | event-parsers-development-v4.1 |
| Provisional event mentions | 69; not 69 unique certified events |
| Accepted research events | 0 |
| Latest recorded software checks | 834 tests passed; Ruff and mypy passed |
| Targeted interpretation check | 12 short questions completed; not independent full-source gold |
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

This completes bounded source acquisition. Event classification has not started on these sources. The next stage is the bounded repurchase event ledger; independent factual QA and all full-extraction and market-data gates remain separate requirements.

## Route to first empirical results

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
