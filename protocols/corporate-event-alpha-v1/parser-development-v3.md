# Corporate Event Alpha: structural parser development v3

The user resumed offline factual corrections after the v2 focused development review. This separate version preserves the frozen Phase-1D identity, the original 718 records in 297 cells, cached sources, prior parser versions, AI review journals and the three human excerpt decisions.

The scope is local development only. No acquisition, network calls, market data, outcome calculations, sample replacement, final QA or full extraction are authorized by this extension.

## Structural and factual changes

- Carry paragraph, heading, table and definition roles through extraction. Exclude hidden, executable, taxonomy definition and performance/compensation content from event evidence. Keep numeric table-column associations unresolved.
- Recognize SEC documents containing HTML with optional paragraph end tags. Bound structural parsing and retain unsupported sources explicitly.
- Require finite action wording. Authorization nouns, suspended programs and possible future actions do not establish new approval.
- Separate dates explicitly reported as announcements from Board approval dates and unresolved public disclosure timing.
- Preserve multiple dividend record/payment dates. Keep scheduled payable dates distinct from dates reported as paid. Do not certify pairings or select a single distribution.
- Treat source genre at document level, retain parent filing chronology and avoid guessing transaction-parent identity from SEC filer identity.
- Deduplicate only exact same-location evidence/state proposals. Identical wording at different locations remains separate; cross-source documentary links require review.

Version v3.0 and v3.1 were diagnostic replays. Version v3.2 passed software checks but its specified live-source check exposed omitted actual payment dates after an earlier payable-to-shareholders clause. These runs, their code snapshots and their manifests remain preserved. Version v3.3 corrects that date-role behavior. The current extension manifest records the five registered file hashes; each replay also saves the exact code/extension used.

## Reproduction

Use the existing research Python environment from the standalone repository root:

```powershell
python -X utf8 -I -S -B tools/corporate_events/parser_development_v3.py --run-id offline-development-v3-005
```

Always use an unused run ID. The runner verifies the frozen baseline, previous version hashes, sample membership, source/container bytes and registered extension before processing. It installs the existing SEC-only filesystem/import firewall and a no-network audit hook. It verifies evidence spans and original baseline hashes, then writes a manifest. Existing run directories cannot be overwritten.

The validated run ID and software results are recorded separately in the ignored private v3 checkpoint, together with source-derived regression checks and a five-item human development packet. The packet embeds source excerpts and hides AI answers. Previous v2 pause status remains a historical snapshot; v3 has its own current status.

## Meaning and limits

All proposals remain pending and withheld from research. Development checks on previously reviewed sources do not establish independent precision, recall, accuracy or calibrated confidence. A lower proposal count is not a performance claim, and an empty proposal list is not a whole-source negative.

Numeric table mapping, unsupported formats, ownership schema authority, comparable prior dividend baselines, first disclosure, final action identity and public timing still require evidence. The small human packet is a developmental cross-check on previously exposed cases, not independent final QA. Final QA, full extraction, Phase 3 and outcome work remain closed.
