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

The SEC acquisition/cache layer and the 2006–2024 metadata-only historical-index census have passed validation. The current Phase-2 milestone is bounded parser development and blinded review tooling. A real SEC_RESEARCH_CONTACT must be configured privately before networking. The user explicitly approved using the local Git author contact for this research identity; never infer a contact without approval or commit it. The separately authorized development pilot permits up to 108 complete submissions and 512 MiB; final QA and full extraction remain gated.

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


## Phase 2C: human development review

The frozen development sample contains 718 records in 297 cells, with SHA256
5da0f6c6061ebea6ac08e57e598e670636d634dd7b1dc158767fba135be073ae.
The parser/classifier remains frozen during the complete first human pass.
The separate review_admin.py command only manages batches, forms, submitted
human decisions, completion, concerns and descriptive review progress. It has
no SEC or market networking capability and cannot read raw filing objects.

Use python -I -S -B tools/corporate_events/review_admin.py with:

- prepare --reviewer-id HUMAN_SUPPLIED_ID
- validate --input LOCAL_FILENAME.json
- record --input LOCAL_FILENAME.json
- status

All configuration and human review artifacts stay in the ignored private
parser-qa-v1/human-review-v1 directory. The existing immutable packets and
718-record selection are not modified. Batch membership is fixed by a stable
control-first partition of the frozen selection's existing order, followed
by consecutive groups of 25 (final 18). All 627 source controls must have
valid human dispositions before any of the 91 candidate views unlock.
The mixed boundary batch initially exposes only its two control records.
Do not use the earlier mixed development packet index during this first pass.

Open the private human-review-v1/review-index.html, review the linked source-only
packets yourself, fill the blank form and export completed reviews. Save the
JSON in human-review-v1/inbox. The administrative record command validates
all submitted records before appending any substantive decision; it invokes
the unchanged parser_qa.py record-review --run development-parse-v2 mechanism.
Unknown fields, incorrect identities, unregistered evidence and invalid
controlled labels are rejected without substantive repair. A human finding
in a source control is permitted; controls are not assumed negative.

Review terminal status and human-reported concern categories are administrative
sidecars, not additions to the frozen event ontology or decision vocabulary.
Completion receipts contain provenance/status only, with substantive decisions
in the existing append-only registered history. Changed decisions explicitly
supersede the latest record. Repeated imports are idempotent; an interrupted
receipt can be recovered without duplicating a registered decision.

No human decision or concern is inferred by the assistant. No parser/classifier
fixes, sample redraws, replacements or new live filings are permitted during
this task. First-pass acceptance metrics remain uncalculated until all 718
records have valid human terminal dispositions. Missing concerns do not prove
correctness, and this design does not establish population recall. Development
QA never authorizes final QA, full extraction, Phase 3 or outcome analysis.

## AI-assisted confidence review amendment

The user approved AI-assisted development review with confidence-based manual
routing. The fixed sample remains 718 records with its existing byte identity.
The initial human-only Phase 2C workflow is retained as provenance.

The new policy is ai-confidence-review-policy-v1.json. The provisional threshold
is 90/100, using the minimum of classification confidence and evidence confidence.
Scores below the threshold and all critical evidence/coverage issues enter the
manual queue. Scores are uncalibrated operational measures. Empirical probability
calibration and accuracy require human labels.

Use python -I -S -B tools/corporate_events/confidence_review.py with prepare,
record --input NAME.json, or status. AI assessment inputs are placed in the ignored
private parser-qa-v1/ai-confidence-review-v1/inbox directory. Append-only AI
assessments retain source/evidence hashes, rationales, essential checks, scores,
routing reasons, code/policy identity and supersession. Human decisions continue
through the registered human review mechanism.

All source-control AI assessments precede candidate assessment recording.
Unassessed records remain pending. Manual records are grouped by source for
reading, with each original sample record retained. A suggested audit of up to
10 high-score records is selected deterministically after the AI pass.

Public-time authority stays a separate gate. The confidence workflow authorizes
development triage. Full extraction, final QA, Phase 3 and parser changes require
their later authorizations.
