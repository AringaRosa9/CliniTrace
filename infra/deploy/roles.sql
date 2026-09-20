-- Execute as the migration owner after each migration. These roles never LOGIN.
DO $$ BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='bljgh_api') THEN
    CREATE ROLE bljgh_api NOLOGIN NOSUPERUSER NOBYPASSRLS;
  END IF;
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='bljgh_worker') THEN
    CREATE ROLE bljgh_worker NOLOGIN NOSUPERUSER NOBYPASSRLS;
  END IF;
END $$;
GRANT USAGE ON SCHEMA public TO bljgh_api, bljgh_worker;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO bljgh_api, bljgh_worker;
REVOKE SELECT ON outbox_events FROM bljgh_api;
GRANT INSERT ON patients, patient_identities, encounters, encounter_details, documents, document_versions, jobs, idempotency_keys, audit_events, outbox_events TO bljgh_api;
GRANT UPDATE ON documents, jobs TO bljgh_api;
GRANT INSERT ON job_attempts, parse_artifacts, outbox_events TO bljgh_worker;
GRANT UPDATE ON documents, jobs, job_attempts, outbox_events TO bljgh_worker;
GRANT INSERT ON extraction_runs TO bljgh_api;
GRANT SELECT, INSERT, UPDATE ON active_runs, review_sets TO bljgh_api, bljgh_worker;
GRANT INSERT ON extraction_measurements, extraction_results, clinical_facts, evidence, fact_evidence, fact_relations TO bljgh_worker;
GRANT INSERT ON fact_revisions, fact_checks, issue_dispositions, review_snapshots, dataset_definitions, export_jobs, clinical_facts, fact_relations TO bljgh_api;
GRANT INSERT ON export_files TO bljgh_worker;
GRANT INSERT ON template_drafts, template_versions, terminology_versions, terminology_activations, mapping_decisions, quality_samples, quality_annotations, gold_dataset_versions, evaluation_runs, correction_candidates, correction_reviews, correction_releases TO bljgh_api;
GRANT UPDATE ON template_drafts TO bljgh_api;
