# Corporate Event Alpha v1 — event-data protocol v1

Phase 1D deliberately replaces the earlier blocked transition rule before event results or price joins. The original gate required complete market/security/action/terminal authority before event ingestion. The revised gate permits SEC-only event records in Phase 2. It does not permit vendor mapping, market observations or returns.

The original ignored blocked specification remains untouched. Its SHA-256 is recorded in event-data-protocol.toml. Phase 1C remains B: CRSP core validated; material supplements required. No licensed metadata or raw audit result is included in this freeze.

## Files

- event-data-protocol.toml is the single normalized register for phases, sources/forms, ontology, clocks, review, deduplication, unresolved codes, rights and later gates. Keeping these in one file avoids conflicting copies.
- event-schema.sql defines normalized, append-only SEC facts with nullable mapping placeholders, no vendor identifiers and no market/performance fields.
- source-evidence.md records official documentation and the conservative timing interpretation.
- freeze-manifest.json binds protocol, source, schema and synthetic tests with SHA-256. Its own hash is recorded in the annotated Git tag, avoiding self-reference.

## Standalone command boundary

From the repository root, use:

    .venv/Scripts/python.exe -I -S -B tools/corporate_events/phase2.py validate
    .venv/Scripts/python.exe -I -S -B tools/corporate_events/phase2.py init-db

The command verifies the freeze manifest before operating. It bypasses the legacy trading CLI, site packages and environment Python paths. It has no network capability. It only reads this protocol/its own source and operates a dedicated new-schema SQLite database under ignored reports/corporate-event-alpha-v1/phase2-private/. It cannot open market/audit databases, attach SQLite databases, import other project or vendor/provider modules, spawn processes or make sockets. This is a capability boundary for the supported commands, not a claim to sandbox malicious replacement Python code.

No crawler, corpus download or live SEC candidate review occurs in this freeze. A later ingestion implementation must stay inside this boundary and add only the registered SEC fetch capability, with real contact, the global scheduler, immutable cache and synthetic firewall regression tests. Missing contact is a runtime gate, not a protocol ambiguity.

Phase 2 retains events with missing timing/class/mapping. It never treats unknown market coverage as measured unavailability. Event facts do not require PERMNO, prices or returns. Form 4 documentary consideration stays in raw SEC evidence; there is no structured market-price field.

## Later gates

Phase 2 success is a prerequisite only. Phase 3 still requires a later explicit task, and may construct market/security authority and outcome-blind identity/coverage QA only. Phase 4+ return/inference execution remains unauthorized. Terminal economics, SPAC lifecycle, complete horizon maturity, calendar, mapping, formulas and vendor-use/publication permissions remain hard later gates.

A disclosure with no evidenced public day/time is retained TIMESTAMP_UNRESOLVED. Acceptance plus 900 seconds is never substituted for dissemination. Classification dependencies have their own availability; later documents never enrich an earlier packet. The calendar is not frozen here, so no executable entry sessions are materialized.

SEC contact is runtime configuration and is not tracked. No credentials, raw corpus, vendor observations or prospective files belong in this commit. No push is authorized.

Information-clock ET/UTC conversion uses the hash-bound America_New_York.tzif from tzdata 2026.6. Provenance and its licence are included. This freezes timezone conversion only, not exchange sessions or early closes.

Scoped .gitattributes rules keep the hashed protocol/runtime/test text bytes at LF across checkouts; the timezone TZif is explicitly binary. These rules do not change other studies.

Dividend comparison keys use only SEC issuer/class evidence in Phase 2; uncertain class bridges remain BASELINE_UNRESOLVED. The append-only qa_source_controls relation retains independently selected controls separately from parser candidates, without requiring an associated event or vendor mapping.
