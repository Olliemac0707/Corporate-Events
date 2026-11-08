# Corporate Event Alpha Research

Standalone repository for corporate-event-alpha-v1. It contains the frozen SEC event-data protocol, standalone capability boundary and synthetic tests. It contains no inherited trading-project history, filing corpus, credentials, licensed observations or private audits.

## Protocol identity

The original 11-file Phase-1D payload is imported byte-for-byte. Its freeze-manifest SHA-256 remains:

    1461b12b2c2860564700868763bd4d9a8b9f94ab561ec8059dd9c1eadab464ff

A fresh Git history means the original local freeze commit 853902a1e2cb3ccba2d274e9cb2e271cb59b315d is provenance only, not an ancestor here. Likewise, the imported manifest's base_commit refers to the original project, not this repository.

The imported TOML branch/base-commit fields are source provenance; the standalone canonical branch is main. Repository identity is recorded separately from unchanged event methodology.

The canonical standalone freeze is the annotated tag:

    corporate-event-alpha-v1-phase1-event-data-protocol-v1-standalone

phase2-protocol-identity.json records the standalone freeze commit, tag, manifest hash and migration provenance. Before Phase-2 work, verify that the recorded freeze commit is in HEAD, that the annotated tag resolves to it, and that every frozen payload hash matches. Do not use the old project's commit as the standalone ancestry requirement.

This repository separation changes Git identity only. It does not change the five event families, source rules, dates, splits, clocks, 900-second buffer, review/dedup rules or later market/return gates. The original Phase-1D README's no-push statement describes that earlier task; the user subsequently authorized this standalone code publication on 2026-11-04.

## Local setup

Use Python 3.12. Create an ignored virtual environment, then install the verified development tools:

    python -m venv .venv
    .venv/Scripts/python.exe -m pip install pytest==8.4.2 ruff==0.17.3 mypy==1.20.2

Validate the frozen payload and the supported SEC-only boundary:

    .venv/Scripts/python.exe -I -S -B tools/corporate_events/phase2.py validate
    .venv/Scripts/python.exe -m pytest -q tests/test_corporate_event_phase2.py
    .venv/Scripts/python.exe -m ruff check tools/corporate_events/phase2.py tests/test_corporate_event_phase2.py
    .venv/Scripts/python.exe -m mypy --strict tools/corporate_events/phase2.py tests/test_corporate_event_phase2.py

## Current phase

The current Phase-2 milestone is the SEC acquisition/cache layer and the 2006–2024 metadata-only historical-index census. A real SEC_RESEARCH_CONTACT must be configured privately before networking. The user explicitly approved using the local Git author contact for this research identity; never infer a contact without approval or commit it. Filing-body extraction, event classification and parser/QA implementation have not begun.

The unchanged frozen phase2.py entrypoint validates the protocol or initializes an empty schema and has no network capability. The separate sec_census.py entrypoint implements the restricted SEC metadata adapter described below. Its current local status and census evidence are kept in the ignored milestone reports.

Phase 3 requires a later explicit task. Phase 4+ returns/inference are unauthorized. Terminal-economics, security mapping, SPAC lifecycle, horizon maturity, exchange calendar and vendor-rights requirements remain intact.

See protocols/corporate-event-alpha-v1/README.md for the full protocol map. Private sources, databases, runtime configuration and reports stay ignored. No event outcomes were observed during separation.

## Phase-2 acquisition milestone

The separate metadata-only extension is registered in protocols/corporate-event-alpha-v1/acquisition-extension.json. The original Phase-1D payload and manifest remain unchanged.

    .venv/Scripts/python.exe -I -S -B tools/corporate_events/sec_census.py validate
    .venv/Scripts/python.exe -I -S -B tools/corporate_events/sec_census.py census --snapshot UNIQUE_SNAPSHOT_ID

The client accepts only SEC historical master indexes for all 76 quarters in 2006–2024. It does not accept filing-body, financial-observation, ticker-list or market-provider URLs. master.zip is the official compressed index container; its sole master.idx member and both byte hashes are validated.

The real SEC_RESEARCH_CONTACT is read privately from the environment or ignored local .env. No contact value or complete User-Agent is written to request/report ledgers. Requests have a fixed identity, verified HTTPS, no proxy rotation and no cookie/authentication handling.

A persistent single-owner lock governs this workflow, across all SEC hosts. No other worker or machine may use this research identity unless it shares a common scheduler. This implementation is a local workflow coordinator, not a distributed request broker. Request starts are at least 0.5 seconds apart, using wall and monotonic clocks; failed-attempt budgets persist across snapshots until a successful retrieval. Five attempts maximum, bounded exponential backoff/jitter, Retry-After, 30-second timeout, status/type/length checks and 50 MiB bounds apply. HTTP 403 stops and blocks the batch for review. Two consecutive 429 responses stop the batch. Long server delays are preserved and deferred rather than shortened.

Private layout under reports/corporate-event-alpha-v1/phase2-private/:

- objects/sha256/PREFIX/HASH: immutable raw accepted index objects.
- object-versions.jsonl: append-only URL-to-object versions; cache reuse verifies the hash.
- scheduler-state.json and sec-acquisition.lock: operational pacing/attempt/owner state.
- snapshots/ID/: request and failure ledgers, source-object manifest, census SQLite, index plan, preflight evidence, summary and CSV count reports.
- snapshots/ID/authority-manifest.json: final immutable artifact hashes, source/code identity and firewall counters.

Snapshots cannot be overwritten. Reuse accepted cache objects by default; explicit refresh creates a new retrieval version without replacing older objects. A stale owner lock or blocked state requires review, not automatic bypass. Failed/partial snapshots are labeled incomplete; unobserved years/forms are not reported as zero populations.

The census counts filing-index records, not qualifying events. It deduplicates canonical five-field index records and reports distinct filing paths separately. Family discovery routes are nonadditive: one issuer filing can feed three registered families. Amendments/passive forms remain context routes, not automatic activist/purchase events. Filing date is not public dissemination time. No event classification, security mapping, entry-session materialization or financial result is constructed.

Parser/QA implementation becomes ready to begin only after the complete metadata census and reconciliation pass. It is not run by the acquisition command, and no full event-database freeze or Phase-3 authorization follows from this milestone.

The first live metadata pilot was retained as an interrupted operational snapshot while row-wise SQL was replaced with batched inserts/joins for throughput. The five-field deduplication identity, forms and registered date range are unchanged; accepted raw objects are reused. Code hashes and Git commits distinguish the implementations, and the same synthetic controls are rerun.

Local workflow firewall denials are recorded with their exact fixed reason code and stop immediately. They are not transient SEC failures and must not be retried automatically. Historical request/failure ledgers and exhausted attempt budgets remain preserved for review.

A finalized metadata checkpoint can be continued into a new exclusive snapshot with `--resume-from PRIOR_SNAPSHOT_ID`. The prior authority, artifact hashes, raw/index hashes, producer compatibility, exact schema, totals and contiguous registered index prefix must verify before networking. The database is cloned in chunks; only the missing quarters are appended. The old snapshot remains byte-identical, the new authority binds continuation.json, and request budgets are retained. Checkpoint continuation cannot be combined with --refresh. Hashing large metadata artifacts streams bytes rather than loading the entire database into memory.

SEC master-index rows with an empty company-name field are retained with that exact empty value when the CIK, form, filing date and accession path validate. No name is inferred and no record is dropped. The five-field hash includes the empty name. This addresses a source-data condition observed in the cached 2016Q1 metadata index; the original ZIP bytes remain unchanged.

Redirects must preserve the canonical registered host and full index path, including quarter and compression kind. A redirect to another registered quarter is rejected before following it. Cached metadata remains replayable after an expired network deadline; the deadline continues to prohibit new network starts.


## Bounded parser-development milestone

The separate parser_qa.py entrypoint implements the next bounded development milestone. The metadata-only sec_census.py entrypoint and the Phase-1D payload remain unchanged. The user authorized up to 108 complete submissions and a cumulative 512-MiB received-body budget, with the same private research identity and global scheduler. Technical-catalogue access is separately registered; body cache/version ledgers live under the private parser-qa-v1 namespace.

Commands use python -I -S -B tools/corporate_events/parser_qa.py with validate, plan, fetch --run UNIQUE_ID, parse --run UNIQUE_ID, status, or record-review --run ID --review-input NAME.json. The plan is a deterministic minimum metadata-filename hash per nonempty exact frozen form-alias/filing-era cell, chosen before candidate filtering and bound to the full validated metadata authority. It is a development source frame, not the final frozen QA sample. There are no replacements for failed or uninformative documents.

Original SGML bytes, component hashes/ranges, documentary XML paths, source-role distinctions, proposals and unsupported/unresolved cases are retained. Form-4 transaction consideration stays in raw evidence rather than a structured price field. Ordinary dividends remain baseline-unresolved unless strictly prior comparable public declarations are independently supported. Form10 is not assumed to be the first spin-off announcement. Acceptance and filing dates do not certify public dissemination. No permanent market mapping, session, price join or financial result is produced.

The frozen five-per-cell document-byte-hash QA rank is implemented without alteration. The first development selection is saved and reused across parser iterations. Blinded packets include short previews and original component bytes; source/control selection is independent of candidate filtering. Human decisions require actual reviewer IDs and append-only provenance. No human agreement, precision or recall is invented. Structural XML parsing is explicitly not XSD validation. Unknown schema, historical public time, dividend baseline and action-link cases remain unresolved. Full extraction remains closed pending a separately frozen final QA source frame/sample, official historical schema authority, evidence/chronology checks and actual independent reviews/adjudication under the predeclared operational criteria.

Parser policy and implementation hashes are in protocols/corporate-event-alpha-v1/parser-qa-policy.json and parser-qa-extension.json. No full-corpus command or Phase-2 event-database tag is created by this milestone. Do not push.
