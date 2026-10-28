-- ============================================================
-- LinkedIn Hunter Pipeline — Supabase Schema Migration
-- File: sql/001_job_hunter_schema.sql
-- ============================================================

-- Enable UUID generation
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ── Job Listings ──
CREATE TABLE IF NOT EXISTS job_listings (
  id             UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  external_id    TEXT UNIQUE NOT NULL,
  source         TEXT NOT NULL DEFAULT 'linkedin_public',
  company_name   TEXT NOT NULL,
  job_title      TEXT NOT NULL,
  location       TEXT,
  workplace_type TEXT,
  job_url        TEXT NOT NULL,
  raw_description TEXT,
  seniority      TEXT,
  employment_type TEXT,
  status         TEXT NOT NULL DEFAULT 'NEW',
  discovered_at  TIMESTAMPTZ DEFAULT NOW(),
  created_at     TIMESTAMPTZ DEFAULT NOW()
);

-- ── Tailored Applications ──
CREATE TABLE IF NOT EXISTS tailored_applications (
  id                UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  job_id            UUID NOT NULL REFERENCES job_listings(id) ON DELETE CASCADE,
  match_score       INT NOT NULL DEFAULT 0,
  matched_skills    TEXT[] DEFAULT '{}',
  missing_skills    TEXT[] DEFAULT '{}',
  tailored_summary  TEXT,
  tailored_typst_code TEXT,
  pdf_path          TEXT,
  outreach_note     TEXT,
  cover_letter      TEXT,
  recommendation    TEXT DEFAULT 'CONSIDER',
  status            TEXT NOT NULL DEFAULT 'PENDING_REVIEW',
  created_at        TIMESTAMPTZ DEFAULT NOW()
);

-- ── Indexes ──
CREATE INDEX IF NOT EXISTS idx_job_listings_external_id ON job_listings(external_id);
CREATE INDEX IF NOT EXISTS idx_job_listings_status ON job_listings(status);
CREATE INDEX IF NOT EXISTS idx_job_listings_created ON job_listings(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_tailored_applications_job ON tailored_applications(job_id);
CREATE INDEX IF NOT EXISTS idx_tailored_applications_score ON tailored_applications(match_score DESC);
CREATE INDEX IF NOT EXISTS idx_tailored_applications_status ON tailored_applications(status);

-- ── Enable Row Level Security (public API access blocked by default) ──
ALTER TABLE job_listings ENABLE ROW LEVEL SECURITY;
ALTER TABLE tailored_applications ENABLE ROW LEVEL SECURITY;

-- Service role bypass (our bridge worker uses service role key)
CREATE POLICY "service_role_all_job_listings"
  ON job_listings FOR ALL
  USING (auth.role() = 'service_role')
  WITH CHECK (auth.role() = 'service_role');

CREATE POLICY "service_role_all_tailored_applications"
  ON tailored_applications FOR ALL
  USING (auth.role() = 'service_role')
  WITH CHECK (auth.role() = 'service_role');
