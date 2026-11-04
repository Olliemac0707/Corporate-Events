-- SEC-only normalized event store. No prices, performance or vendor identifiers.
PRAGMA foreign_keys=ON;
CREATE TABLE protocol_binding (
 version TEXT PRIMARY KEY, manifest_sha256 TEXT NOT NULL CHECK(length(manifest_sha256)=64));
CREATE TABLE issuer_identities (
 issuer_id TEXT PRIMARY KEY, cik_raw TEXT, cik_normalized TEXT,
 identity_status TEXT NOT NULL CHECK(identity_status IN ('RESOLVED_SEC','UNRESOLVED')),
 evidence_locator TEXT NOT NULL,
 CHECK(cik_normalized IS NULL OR
 (length(cik_normalized)=10 AND cik_normalized NOT GLOB '*[^0-9]*'
 AND cik_normalized<>'0000000000')),
 CHECK(identity_status<>'RESOLVED_SEC' OR cik_normalized IS NOT NULL));
CREATE TABLE disclosures (
 disclosure_id TEXT PRIMARY KEY, issuer_id TEXT REFERENCES issuer_identities,
 accession TEXT NOT NULL, form_raw TEXT NOT NULL, form_canonical TEXT NOT NULL,
 document_name TEXT NOT NULL, document_sha256 TEXT NOT NULL CHECK(length(document_sha256)=64),
 sec_schema_version TEXT, source_kind TEXT NOT NULL CHECK(source_kind IN
 ('BODY','EXHIBIT','PERIODIC','OWNERSHIP_XML','BENEFICIAL_OWNERSHIP','REGISTRATION','PROXY')),
 filing_date TEXT, observed_at_utc TEXT NOT NULL,
 UNIQUE(accession,document_name,document_sha256));
CREATE TABLE source_evidence (
 evidence_id TEXT PRIMARY KEY, disclosure_id TEXT NOT NULL REFERENCES disclosures,
 object_sha256 TEXT NOT NULL CHECK(length(object_sha256)=64),
 object_locator TEXT NOT NULL, evidence_locator TEXT NOT NULL,
 source_role TEXT NOT NULL, public_cutoff_utc TEXT,
 -- Quotes stay in immutable raw SEC bytes; locator is not an arbitrary analysis payload.
 evidence_note TEXT NOT NULL);
CREATE TABLE timing_authority (
 timing_id TEXT PRIMARY KEY, disclosure_id TEXT NOT NULL REFERENCES disclosures,
 evidence_id TEXT REFERENCES source_evidence,
 economic_event_date TEXT, transaction_date TEXT, board_approval_date TEXT, filing_date TEXT,
 acceptance_raw TEXT, acceptance_et TEXT, acceptance_utc TEXT,
 verified_dissemination_utc TEXT, public_lower_utc TEXT, public_upper_utc TEXT,
 public_time_basis TEXT NOT NULL CHECK(public_time_basis IN
 ('VERIFIED_DISSEMINATION','EVIDENCED_DISSEMINATION_DAY','TIMESTAMP_UNRESOLVED')),
 timestamp_precision TEXT NOT NULL CHECK(timestamp_precision IN
 ('SOURCE_TIMESTAMP','DAY_BOUND','UNRESOLVED')),
 registered_public_datetime TEXT, public_disclosure_date TEXT,
 classification_available_at TEXT, classification_reconstructed_from_packet INTEGER,
 observed_at_utc TEXT NOT NULL,
 CHECK(public_lower_utc IS NULL OR public_upper_utc IS NULL OR public_lower_utc<=public_upper_utc),
 CHECK(public_time_basis='TIMESTAMP_UNRESOLVED' OR
 (evidence_id IS NOT NULL AND public_lower_utc IS NOT NULL AND public_upper_utc IS NOT NULL
 AND registered_public_datetime=public_upper_utc)),
 CHECK(public_time_basis<>'TIMESTAMP_UNRESOLVED' OR registered_public_datetime IS NULL));
CREATE TABLE events (
 event_id TEXT PRIMARY KEY, family TEXT NOT NULL CHECK(family IN ('SHARE_REPURCHASE','INSIDER_OPEN_MARKET_PURCHASE','ACTIVIST_13D','DIVIDEND_POSITIVE_CHANGE','SPINOFF_ANNOUNCEMENT')),
 issuer_id TEXT NOT NULL REFERENCES issuer_identities,
 first_disclosure_id TEXT NOT NULL REFERENCES disclosures,
 economic_action_anchor TEXT NOT NULL, timing_id TEXT REFERENCES timing_authority,
 public_disclosure_date TEXT,
 split_label TEXT NOT NULL CHECK(split_label IN
 ('DEVELOPMENT','VALIDATION','SEALED_HOLDOUT','OUTSIDE_INITIAL_STUDY','PRE_STUDY_CONTEXT','UNRESOLVED_DATE')),
 disposition TEXT NOT NULL CHECK(disposition IN ('CANDIDATE','QUALIFYING','REJECTED','UNRESOLVED')),
 created_at_utc TEXT NOT NULL,
 CHECK(public_disclosure_date IS NOT NULL OR split_label='UNRESOLVED_DATE'),
 CHECK(public_disclosure_date IS NULL OR
 (public_disclosure_date BETWEEN '2006-01-01' AND '2015-12-31' AND split_label='DEVELOPMENT') OR
 (public_disclosure_date BETWEEN '2016-01-01' AND '2020-12-31' AND split_label='VALIDATION') OR
 (public_disclosure_date BETWEEN '2021-01-01' AND '2024-12-31' AND split_label='SEALED_HOLDOUT') OR
 (public_disclosure_date>='2025-01-01' AND split_label='OUTSIDE_INITIAL_STUDY') OR
 (public_disclosure_date<'2006-01-01' AND split_label='PRE_STUDY_CONTEXT')),
 CHECK(disposition<>'QUALIFYING' OR split_label NOT IN ('OUTSIDE_INITIAL_STUDY','PRE_STUDY_CONTEXT','UNRESOLVED_DATE')));
CREATE TABLE event_revisions (
 revision_id TEXT PRIMARY KEY, event_id TEXT NOT NULL REFERENCES events,
 supersedes_revision_id TEXT REFERENCES event_revisions,
 disclosure_id TEXT NOT NULL REFERENCES disclosures, evidence_id TEXT NOT NULL REFERENCES source_evidence,
 subtype TEXT NOT NULL, disposition TEXT NOT NULL CHECK(disposition IN ('CANDIDATE','QUALIFYING','REJECTED','UNRESOLVED')),
 classification_available_at TEXT, recorded_at_utc TEXT NOT NULL, reason TEXT NOT NULL);
CREATE TABLE event_clusters (
 cluster_id TEXT PRIMARY KEY, issuer_id TEXT NOT NULL REFERENCES issuer_identities,
 action_anchor TEXT NOT NULL, created_at_utc TEXT NOT NULL);
CREATE TABLE cluster_link_decisions (
 decision_id TEXT PRIMARY KEY, cluster_id TEXT NOT NULL REFERENCES event_clusters,
 event_id TEXT NOT NULL REFERENCES events, disclosure_id TEXT NOT NULL REFERENCES disclosures,
 decision TEXT NOT NULL CHECK(decision IN ('LINK','DO_NOT_LINK','AMBIGUOUS')),
 evidence_id TEXT NOT NULL REFERENCES source_evidence,
 reviewer_id TEXT NOT NULL, decided_at_utc TEXT NOT NULL,
 supersedes_decision_id TEXT REFERENCES cluster_link_decisions, reason TEXT NOT NULL);
CREATE TABLE classification_reviews (
 review_id TEXT PRIMARY KEY, event_id TEXT NOT NULL REFERENCES events,
 reviewer_id TEXT NOT NULL, reviewed_at_utc TEXT NOT NULL,
 packet_sha256 TEXT NOT NULL CHECK(length(packet_sha256)=64), packet_public_cutoff_utc TEXT NOT NULL,
 decision TEXT NOT NULL CHECK(decision IN ('PENDING','ACCEPTED','REJECTED','AMBIGUOUS','ADJUDICATED')),
 reason TEXT NOT NULL, evidence_id TEXT NOT NULL REFERENCES source_evidence,
 classification_available_at TEXT, adjudicator_id TEXT, adjudication_reason TEXT,
 supersedes_review_id TEXT REFERENCES classification_reviews);
CREATE TABLE event_security_mapping_placeholders (
 event_id TEXT PRIMARY KEY REFERENCES events, sec_class_label TEXT,
 target_authority TEXT CHECK(target_authority IS NULL),
 target_identifier TEXT CHECK(target_identifier IS NULL),
 status TEXT NOT NULL DEFAULT 'SECURITY_MAPPING_UNRESOLVED'
 CHECK(status='SECURITY_MAPPING_UNRESOLVED'));
CREATE TABLE mapping_coverage_status (
 status_id TEXT PRIMARY KEY, event_id TEXT NOT NULL REFERENCES events,
 code TEXT NOT NULL CHECK(code IN ('PRICE_SERIES_UNAVAILABLE','SECURITY_MAPPING_UNRESOLVED','DELISTED_PRICE_UNAVAILABLE','NO_VALID_FIRST_TRADABLE_BAR','CORPORATE_ACTION_AMBIGUITY','TIMESTAMP_UNRESOLVED','BASELINE_UNRESOLVED','INTENT_UNRESOLVED','SECURITY_TYPE_UNRESOLVED','LEFT_CENSORED_HISTORY','INSUFFICIENT_PRE_EVENT_HISTORY','RIGHT_CENSORED_HORIZON','SPLIT_WINDOW_CROSSES_BOUNDARY')),
 assessment_phase TEXT NOT NULL DEFAULT 'PHASE2_SEC_ONLY' CHECK(assessment_phase='PHASE2_SEC_ONLY'),
 reason TEXT NOT NULL, evidence_id TEXT REFERENCES source_evidence, recorded_at_utc TEXT NOT NULL);
CREATE TABLE reporting_owners (
 owner_id TEXT PRIMARY KEY, cik_raw TEXT, cik_normalized TEXT,
 name_from_sec TEXT NOT NULL);
CREATE TABLE event_reporting_owners (
 event_id TEXT NOT NULL REFERENCES events, owner_id TEXT NOT NULL REFERENCES reporting_owners,
 joint_action_anchor TEXT NOT NULL, relation_from_sec TEXT, officer_title_from_sec TEXT,
 PRIMARY KEY(event_id,owner_id));
CREATE TABLE repurchase_facts (
 event_id TEXT PRIMARY KEY REFERENCES events,
 state TEXT NOT NULL CHECK(state IN ('NEW_AUTHORIZATION','AUTHORIZATION_INCREASE','EXTENSION_ONLY','ROUTINE_ACTUAL_REPURCHASES','EXPIRATION','COMPLETION','ASR_AGREEMENT','TENDER_OFFER','AMBIGUOUS')),
 program_identifier TEXT, authorization_total_decimal TEXT, incremental_decimal TEXT,
 currency TEXT, units TEXT, prior_authorization_decimal TEXT, remaining_decimal TEXT,
 approval_date TEXT, expiration_date TEXT, scope_from_sec TEXT, capacity_increase_evidence_id TEXT REFERENCES source_evidence);
CREATE TABLE insider_purchase_facts (
 event_id TEXT PRIMARY KEY REFERENCES events, transaction_row_locator TEXT NOT NULL,
 table_identifier TEXT NOT NULL, transaction_code TEXT, acquired_disposed TEXT,
 shares_decimal TEXT, ownership_form TEXT, transaction_date TEXT, plan_adoption_date TEXT,
 venue_state TEXT NOT NULL CHECK(venue_state IN ('OPEN_MARKET_SUPPORTED','PRIVATE_PURCHASE','VENUE_UNRESOLVED')),
 discretion_state TEXT NOT NULL CHECK(discretion_state IN ('NON_PLAN_DISCRETION_SUPPORTED','PLAN_TRANSACTION','DISCRETION_UNRESOLVED')),
 instrument_state TEXT NOT NULL CHECK(instrument_state IN ('NONDERIVATIVE_COMMON_SUPPORTED','DERIVATIVE_OR_OTHER','SECURITY_CLASS_UNRESOLVED')),
 footnote_evidence_id TEXT NOT NULL REFERENCES source_evidence);
CREATE TABLE activist_facts (
 event_id TEXT PRIMARY KEY REFERENCES events, intent_state TEXT NOT NULL
 CHECK(intent_state IN ('ACTIVIST_INTENT_SUPPORTED','NONACTIVIST_INITIAL_13D','INTENT_UNRESOLVED')),
 initial_filing_only INTEGER NOT NULL CHECK(initial_filing_only IN (0,1)),
 item4_evidence_id TEXT NOT NULL REFERENCES source_evidence,
 reported_fraction_decimal TEXT, denominator_decimal TEXT, group_identity TEXT);
CREATE TABLE dividend_facts (
 event_id TEXT PRIMARY KEY REFERENCES events, state TEXT NOT NULL
 CHECK(state IN ('DIVIDEND_INITIATION','DIVIDEND_INCREASE','SPECIAL_DIVIDEND','UNCHANGED','DECREASE','SUSPENSION','RESUMPTION','UNKNOWN_BASELINE')),
 declared_cash_per_share_decimal TEXT, currency TEXT, sec_class_label TEXT, frequency TEXT,
 declaration_date TEXT, record_date TEXT, ex_date TEXT, payment_date TEXT,
 baseline_evidence_id TEXT REFERENCES source_evidence,
 baseline_public_upper_utc TEXT, comparable_rate_bridge_evidence_id TEXT REFERENCES source_evidence);
CREATE TABLE spinoff_facts (
 event_id TEXT PRIMARY KEY REFERENCES events, state TEXT NOT NULL
 CHECK(state IN ('SPINOFF_INTENTION','STRATEGIC_ALTERNATIVES_REVIEW','ASSET_SALE','CARVE_OUT_IPO','SPLIT_OFF','COMPLETION_ONLY_RECAP','CANCELLED_PROPOSAL','AMBIGUOUS')),
 parent_cik_raw TEXT, spinco_identity_from_sec TEXT, business_segment TEXT, structure_from_sec TEXT,
 intent_evidence_id TEXT NOT NULL REFERENCES source_evidence,
 conditions_from_sec TEXT, earlier_disclosure_search_status TEXT NOT NULL);
CREATE TRIGGER protocol_binding_no_update BEFORE UPDATE ON protocol_binding BEGIN SELECT RAISE(ABORT,'append-only SEC ledger'); END;
CREATE TRIGGER protocol_binding_no_delete BEFORE DELETE ON protocol_binding BEGIN SELECT RAISE(ABORT,'append-only SEC ledger'); END;
CREATE TRIGGER issuer_identities_no_update BEFORE UPDATE ON issuer_identities BEGIN SELECT RAISE(ABORT,'append-only SEC ledger'); END;
CREATE TRIGGER issuer_identities_no_delete BEFORE DELETE ON issuer_identities BEGIN SELECT RAISE(ABORT,'append-only SEC ledger'); END;
CREATE TRIGGER disclosures_no_update BEFORE UPDATE ON disclosures BEGIN SELECT RAISE(ABORT,'append-only SEC ledger'); END;
CREATE TRIGGER disclosures_no_delete BEFORE DELETE ON disclosures BEGIN SELECT RAISE(ABORT,'append-only SEC ledger'); END;
CREATE TRIGGER source_evidence_no_update BEFORE UPDATE ON source_evidence BEGIN SELECT RAISE(ABORT,'append-only SEC ledger'); END;
CREATE TRIGGER source_evidence_no_delete BEFORE DELETE ON source_evidence BEGIN SELECT RAISE(ABORT,'append-only SEC ledger'); END;
CREATE TRIGGER timing_authority_no_update BEFORE UPDATE ON timing_authority BEGIN SELECT RAISE(ABORT,'append-only SEC ledger'); END;
CREATE TRIGGER timing_authority_no_delete BEFORE DELETE ON timing_authority BEGIN SELECT RAISE(ABORT,'append-only SEC ledger'); END;
CREATE TRIGGER events_no_update BEFORE UPDATE ON events BEGIN SELECT RAISE(ABORT,'append-only SEC ledger'); END;
CREATE TRIGGER events_no_delete BEFORE DELETE ON events BEGIN SELECT RAISE(ABORT,'append-only SEC ledger'); END;
CREATE TRIGGER event_revisions_no_update BEFORE UPDATE ON event_revisions BEGIN SELECT RAISE(ABORT,'append-only SEC ledger'); END;
CREATE TRIGGER event_revisions_no_delete BEFORE DELETE ON event_revisions BEGIN SELECT RAISE(ABORT,'append-only SEC ledger'); END;
CREATE TRIGGER event_clusters_no_update BEFORE UPDATE ON event_clusters BEGIN SELECT RAISE(ABORT,'append-only SEC ledger'); END;
CREATE TRIGGER event_clusters_no_delete BEFORE DELETE ON event_clusters BEGIN SELECT RAISE(ABORT,'append-only SEC ledger'); END;
CREATE TRIGGER cluster_link_decisions_no_update BEFORE UPDATE ON cluster_link_decisions BEGIN SELECT RAISE(ABORT,'append-only SEC ledger'); END;
CREATE TRIGGER cluster_link_decisions_no_delete BEFORE DELETE ON cluster_link_decisions BEGIN SELECT RAISE(ABORT,'append-only SEC ledger'); END;
CREATE TRIGGER classification_reviews_no_update BEFORE UPDATE ON classification_reviews BEGIN SELECT RAISE(ABORT,'append-only SEC ledger'); END;
CREATE TRIGGER classification_reviews_no_delete BEFORE DELETE ON classification_reviews BEGIN SELECT RAISE(ABORT,'append-only SEC ledger'); END;
CREATE TRIGGER event_security_mapping_placeholders_no_update BEFORE UPDATE ON event_security_mapping_placeholders BEGIN SELECT RAISE(ABORT,'append-only SEC ledger'); END;
CREATE TRIGGER event_security_mapping_placeholders_no_delete BEFORE DELETE ON event_security_mapping_placeholders BEGIN SELECT RAISE(ABORT,'append-only SEC ledger'); END;
CREATE TRIGGER mapping_coverage_status_no_update BEFORE UPDATE ON mapping_coverage_status BEGIN SELECT RAISE(ABORT,'append-only SEC ledger'); END;
CREATE TRIGGER mapping_coverage_status_no_delete BEFORE DELETE ON mapping_coverage_status BEGIN SELECT RAISE(ABORT,'append-only SEC ledger'); END;
CREATE TRIGGER reporting_owners_no_update BEFORE UPDATE ON reporting_owners BEGIN SELECT RAISE(ABORT,'append-only SEC ledger'); END;
CREATE TRIGGER reporting_owners_no_delete BEFORE DELETE ON reporting_owners BEGIN SELECT RAISE(ABORT,'append-only SEC ledger'); END;
CREATE TRIGGER event_reporting_owners_no_update BEFORE UPDATE ON event_reporting_owners BEGIN SELECT RAISE(ABORT,'append-only SEC ledger'); END;
CREATE TRIGGER event_reporting_owners_no_delete BEFORE DELETE ON event_reporting_owners BEGIN SELECT RAISE(ABORT,'append-only SEC ledger'); END;
CREATE TRIGGER repurchase_facts_no_update BEFORE UPDATE ON repurchase_facts BEGIN SELECT RAISE(ABORT,'append-only SEC ledger'); END;
CREATE TRIGGER repurchase_facts_no_delete BEFORE DELETE ON repurchase_facts BEGIN SELECT RAISE(ABORT,'append-only SEC ledger'); END;
CREATE TRIGGER insider_purchase_facts_no_update BEFORE UPDATE ON insider_purchase_facts BEGIN SELECT RAISE(ABORT,'append-only SEC ledger'); END;
CREATE TRIGGER insider_purchase_facts_no_delete BEFORE DELETE ON insider_purchase_facts BEGIN SELECT RAISE(ABORT,'append-only SEC ledger'); END;
CREATE TRIGGER activist_facts_no_update BEFORE UPDATE ON activist_facts BEGIN SELECT RAISE(ABORT,'append-only SEC ledger'); END;
CREATE TRIGGER activist_facts_no_delete BEFORE DELETE ON activist_facts BEGIN SELECT RAISE(ABORT,'append-only SEC ledger'); END;
CREATE TRIGGER dividend_facts_no_update BEFORE UPDATE ON dividend_facts BEGIN SELECT RAISE(ABORT,'append-only SEC ledger'); END;
CREATE TRIGGER dividend_facts_no_delete BEFORE DELETE ON dividend_facts BEGIN SELECT RAISE(ABORT,'append-only SEC ledger'); END;
CREATE TRIGGER spinoff_facts_no_update BEFORE UPDATE ON spinoff_facts BEGIN SELECT RAISE(ABORT,'append-only SEC ledger'); END;
CREATE TRIGGER spinoff_facts_no_delete BEFORE DELETE ON spinoff_facts BEGIN SELECT RAISE(ABORT,'append-only SEC ledger'); END;
CREATE TRIGGER repurchase_facts_family BEFORE INSERT ON repurchase_facts WHEN (SELECT family FROM events WHERE event_id=NEW.event_id)<>'SHARE_REPURCHASE' BEGIN SELECT RAISE(ABORT,'wrong event family'); END;
CREATE TRIGGER insider_purchase_facts_family BEFORE INSERT ON insider_purchase_facts WHEN (SELECT family FROM events WHERE event_id=NEW.event_id)<>'INSIDER_OPEN_MARKET_PURCHASE' BEGIN SELECT RAISE(ABORT,'wrong event family'); END;
CREATE TRIGGER activist_facts_family BEFORE INSERT ON activist_facts WHEN (SELECT family FROM events WHERE event_id=NEW.event_id)<>'ACTIVIST_13D' BEGIN SELECT RAISE(ABORT,'wrong event family'); END;
CREATE TRIGGER dividend_facts_family BEFORE INSERT ON dividend_facts WHEN (SELECT family FROM events WHERE event_id=NEW.event_id)<>'DIVIDEND_POSITIVE_CHANGE' BEGIN SELECT RAISE(ABORT,'wrong event family'); END;
CREATE TRIGGER spinoff_facts_family BEFORE INSERT ON spinoff_facts WHEN (SELECT family FROM events WHERE event_id=NEW.event_id)<>'SPINOFF_ANNOUNCEMENT' BEGIN SELECT RAISE(ABORT,'wrong event family'); END;

CREATE TABLE qa_source_controls (
 control_id TEXT PRIMARY KEY,
 disclosure_id TEXT NOT NULL REFERENCES disclosures,
 family TEXT NOT NULL CHECK(family IN ('SHARE_REPURCHASE','INSIDER_OPEN_MARKET_PURCHASE',
 'ACTIVIST_13D','DIVIDEND_POSITIVE_CHANGE','SPINOFF_ANNOUNCEMENT')),
 era TEXT NOT NULL CHECK(era IN ('2006-2012','2013-2022','2023-2024')),
 candidate_control_status TEXT NOT NULL CHECK(candidate_control_status IN
 ('PARSER_CANDIDATE','INDEPENDENT_SOURCE_CONTROL')),
 selected_before_candidate_filter INTEGER NOT NULL CHECK(selected_before_candidate_filter IN(0,1)),
 rank_sha256 TEXT NOT NULL CHECK(length(rank_sha256)=64),
 source_id_tiebreak TEXT NOT NULL, selected_at_utc TEXT NOT NULL, selection_reason TEXT NOT NULL,
 CHECK(candidate_control_status<>'INDEPENDENT_SOURCE_CONTROL' OR selected_before_candidate_filter=1));
CREATE TRIGGER qa_source_controls_no_update BEFORE UPDATE ON qa_source_controls
 BEGIN SELECT RAISE(ABORT,'append-only SEC ledger'); END;
CREATE TRIGGER qa_source_controls_no_delete BEFORE DELETE ON qa_source_controls
 BEGIN SELECT RAISE(ABORT,'append-only SEC ledger'); END;
