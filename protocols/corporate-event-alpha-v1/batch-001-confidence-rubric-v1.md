# Batch 001 evidence-based confidence rubric

Version: batch-001-confidence-rubric-v1. Routing policy remains ai-confidence-review-policy-v1, cutoff 90. This rubric was written after the 25 source findings were saved, before assigning the batch scores. It refines score anchors without changing the frozen event definition or threshold.

Scope: the first 25 frozen source-control records, all ACTIVIST_13D. These are component-level judgments; four supporting components represent two disclosure bundles, not four independent events. No evidence for the other four event families is supplied by this batch.

## Decision rules established from source review

1. Exact 13D amendments and 13G forms are outside the initial-13D event route. Preserve their substantive context. Do not convert board or transaction language in an amendment into a new initial event.
2. Read issuer, reporter, parent/subsidiary and client roles separately. Repeated group holdings are not additive. A reporter's board title is not a proposal to change the issuer's board.
3. Specific control-acquisition and board-replacement obligations support the frozen broad intent definition, including negotiated transactions. No hostile-campaign requirement is added.
4. General investment or securities-resale representations do not override explicit control or board provisions. Read the exceptions and incorporated transaction terms.
5. Lists of actions the filer may take, with reserved rights and no definite plan, remain INTENT_UNRESOLVED. A detailed list alone does not establish an affirmative action.
6. Joint-filing agreements, transaction schedules and reporter rosters receive component-scoped NO_EVENT findings if they contain no independent intent. This does not negate another component's event.
7. Full-source negatives require the entire supplied visible text to have been reviewed. Targeted contract review is explicitly partial.
8. XML leaf review establishes documentary facts only; it does not certify an XSD version or public dissemination timestamp.

## Score anchors used for this batch

The score is ordinal review support, not a percentage probability. Overall = minimum(classification, evidence). Numbers are provisional judgment anchors, not calibrated accuracy.

| Evidence pattern | Classification | Evidence | Result |
| --- | ---: | ---: | --- |
| Exact excluded form, full component reviewed | 98 | 98 | Clear if essential eligibility/role checks resolved |
| Full joint-filing agreement, schedule or roster with scoped absence finding | 97 | 98 | Clear if source role and scope resolved |
| Explicit control/board action in a fully reviewed component | 95 | 95 | Clear with conditions retained |
| Explicit control terms in a partially reviewed long agreement | 95 | 85 | Mandatory review for partial coverage |
| Conditional may-list with no established definite action | 75 | 95 | Mandatory review for unresolved intent |

Hard overrides: unresolved material facts, conflicting decisive evidence, missing evidence, unresolved source roles, unsupported features and partial coverage require review at any score. An irrelevant contextual discrepancy does not overturn a conclusive form exclusion; preserve it in notes. Public dissemination timing stays unresolved behind its separate gate.

## Audit and refinement

All judgments are explicitly AI assessments in a separate append-only journal. One earlier provisional assessment is superseded with its history retained. Human decisions remain separately identified. No human accuracy, precision, recall or probability calibration is claimed. The 90 cutoff is operational and was not fitted to these 25 outcomes. Validate a later rubric against genuinely independent human labels and audit high-score cases before claiming measured reliability. Preserve the policy version and prior records when changing the threshold.

The assessed batch has 24 fully reviewed components and one targeted long-contract review. Remaining development records stay pending. These findings do not open final QA, full extraction or outcome research.