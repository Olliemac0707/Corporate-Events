# Corporate Event Alpha: parser development v2

The user authorized a separate parser-improvement phase after the initial 718-record AUTOMATED disposition pass. This development branch preserves the Phase-1D protocol, original parser extension, frozen sample, original packets, confidence history and all three human excerpt decisions.

This version adds two modules and their tests. It runs offline on the same frozen 718 records in 297 cells. It never redraws a sample, replaces difficult sources, acquires filings, queries market data, or calculates outcomes. The registered v1 parser remains available unchanged.

## Changes

- Render narrative text without script, style, inline-XBRL headers and structural contexts. Recognize SEC SGML XBRL wrappers around HTML separately from standalone XBRL instances. Unsupported and structured components remain explicitly unresolved.
- Keep sentence prefixes, negation, declaration dates and adjacent payment/record statements in evidence spans. Every narrative proposal/context span is hashed against its own versioned derived text and original component.
- Identify Item 4 headings rather than inline cross-references. Supporting joint-filing agreements and trade schedules do not create independent Item 4 events. Explicitly retain the need for other-section/exhibit corroboration; do not guess ownership or sum overlapping holdings.
- Separate common-share dividend declarations from preferred shares, employee award equivalents, investment income and generic distribution rights. A dollar symbol alone does not establish currency. An amount alone does not establish an increase or initiation.
- Distinguish repurchase approvals, execution recaps, renewals, extensions, ASR agreements and tender offers. No-expiration language does not establish expiry; execution payments do not become authorization capacity. Prior capacity is never inferred by subtraction.
- Keep SEC filer identity separately from unresolved spin transaction-parent identity. Later registration documents do not become first public announcements.
- Consolidate exact repeats and specifically dated, matching documentary capacity/rate facts within a component. Cross-document links remain review suggestions within the same accession; they are not final action deduplication.

## Reproduction and integrity

Run the current sealed version from the repository root with the existing Python environment:

```powershell
python -X utf8 -I -S -B tools/corporate_events/parser_development_v2.py --run-id offline-development-v2-006
```

Use an unused run ID. Existing runs are immutable. The runner verifies its extension hashes, original protocol and completed baseline review, then installs the SEC-only filesystem/import firewall plus an explicit no-network hook. Its inputs are the original local cache and frozen packets. Each new run records code copies, source outputs, derived text, exact record comparisons, documentary link suggestions and a hash manifest. It verifies baseline hashes before and after processing. Failed runs are retained with a failure record.

Run 001 is an initial diagnostic v2.0 run. Its source snapshot is preserved separately under the private code-snapshots directory. The v2.1 and v2.2 diagnostics and current v2.4 run include code snapshots in their own manifests. The final revision distinguishes past completion from future conditions, preserves dates across company abbreviations, and separates standing programs from new approvals. Run004 was stopped for an unbounded prefix-scan performance issue; its partial artifacts and code snapshot are retained. Run005 uses a bounded local scan.

## Meaning of the result

Development regression checks are checks of specified behavior on already reviewed sources. They are not independent gold labels, precision, recall, accuracy or confidence calibration. An empty proposal list is not a whole-source negative. Fewer proposals do not demonstrate better precision or preserved recall.

All v2 proposals remain pending review; original confidence scores and clearances are not transferred. Partial source coverage, unsupported formats, historical ownership schema/plan/venue authority, comparable prior public dividend declarations, earliest spin disclosure, transaction identity and public timing remain evidence requirements.

Final QA, full extraction, Phase 3, market data and outcomes remain closed. The next scientific gate requires independently reviewed factual cases and adjudication, rather than using the development regression checks as a performance claim.
