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

Phase 2 is authorized for outcome-blind SEC event data only. No SEC filings have been ingested. A real user-supplied SEC_RESEARCH_CONTACT in private local configuration is still required before SEC networking. Never infer it from a Git author email or commit it.

The implemented commands validate the protocol or initialize an empty schema. They have no network capability. A future SEC acquisition adapter must enforce the registered SEC-only access, contact, rate/cache/retry controls and firewall.

Phase 3 requires a later explicit task. Phase 4+ returns/inference are unauthorized. Terminal-economics, security mapping, SPAC lifecycle, horizon maturity, exchange calendar and vendor-rights requirements remain intact.

See protocols/corporate-event-alpha-v1/README.md for the full protocol map. Private sources, databases, runtime configuration and reports stay ignored. No event outcomes were observed during separation.
