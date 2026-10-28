#!/usr/bin/env python3
"""
LinkedIn Hunter — Host Automation Bridge Worker
Listens on 0.0.0.0:8766.
DeepSeek AI analysis with deterministic heuristic fallback; no local model required.
Never returns 0% when credentials and requirements match.
"""

import http.server
import json
import os
import re
import subprocess
import sys
import time
import hashlib
import urllib.request
import urllib.error
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple, List

# Ensure bridge directory is in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from heuristic_engine import heuristic_analyze
from job_filters import exclusion_reason
from telegram_api import CHAT_ID, send_review_card
PROJECT_ROOT = Path(__file__).resolve().parent.parent
PROFILE_PATH = PROJECT_ROOT / "profile" / "master_profile.md"
TEMPLATE_PATH = PROJECT_ROOT / "templates" / "resume.typ"
OUTPUT_DIR = PROJECT_ROOT / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# DeepSeek API (OpenAI-compatible). Cheapest usable model by default.
DEEPSEEK_API_URL = os.environ.get("DEEPSEEK_API_URL", "https://api.deepseek.com/chat/completions")
DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")

def _read_dotenv(path: Path) -> Dict[str, str]:
    values: Dict[str, str] = {}
    try:
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            values[key.strip().removeprefix("export ").strip()] = val.strip().strip("'\"")
    except Exception:
        pass
    return values

def load_deepseek_keys() -> List[str]:
    """Collect DeepSeek keys: project .env first, then environment, then ~/.bashrc."""
    raw: List[str] = []

    for candidate in (PROJECT_ROOT / ".env", Path.home() / ".bashrc"):
        if candidate.exists():
            val = _read_dotenv(candidate).get("DEEPSEEK_API_KEY", "")
            if val:
                raw.append(val)

    env_val = os.environ.get("DEEPSEEK_API_KEY", "")
    if env_val:
        raw.extend(re.split(r"[,\s]+", env_val))

    keys: List[str] = []
    for key in raw:
        key = key.strip()
        if key and key not in keys:
            keys.append(key)
    return keys

DEEPSEEK_KEYS = load_deepseek_keys()
ACTIVE_KEY_INDEX = 0

AUTH_TOKEN = os.environ.get("BRIDGE_AUTH_TOKEN", "")
if not AUTH_TOKEN:
    print("[BRIDGE] BRIDGE_AUTH_TOKEN is not set — authenticated endpoints will reject everything.", flush=True)
PG_CONTAINER = "octopus_postgres"
PG_DB = "linkedin_hunter"
TYPST_BIN = os.getenv("TYPST_BIN", str(Path.home() / ".local" / "bin" / "typst"))

def log(msg: str):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)

def load_master_profile() -> str:
    if PROFILE_PATH.exists():
        return PROFILE_PATH.read_text(encoding="utf-8")
    return ""

MASTER_PROFILE = load_master_profile()

# Database
def pg_exec(sql: str, params: Optional[Dict] = None) -> str:
    if params:
        for key, val in params.items():
            escaped = str(val).replace("'", "''") if val else ""
            sql = sql.replace(f":{key}", f"'{escaped}'")

    result = subprocess.run(
        ["docker", "exec", "-i", PG_CONTAINER, "psql", "-U", "postgres",
         "-d", PG_DB, "-t", "-A", "-c", sql],
        capture_output=True, text=True, timeout=15
    )
    if result.returncode != 0 and "ERROR" in result.stderr:
        print(f"[DB ERROR] {result.stderr.strip()}", file=sys.stderr)
    output = result.stdout.strip()
    if "\n" in output:
        output = output.split("\n")[0].strip()
    return output

def db_check_duplicate(external_id: str) -> bool:
    res = pg_exec(f"SELECT COUNT(*) FROM job_listings WHERE external_id = '{external_id}'")
    try:
        return int(res) > 0
    except Exception:
        return False

UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")

def _job_where(ref: str) -> Optional[str]:
    ref = str(ref or "").strip()
    if not ref:
        return None
    if UUID_RE.match(ref):
        return f"id = '{ref}'::uuid"
    return f"external_id = '{ref.replace(chr(39), chr(39) * 2)}'"

def db_get_job(ref: str) -> Optional[Dict[str, Any]]:
    where = _job_where(ref)
    if not where:
        return None
    sql = (
        "SELECT json_build_object("
        "'id', id::text, 'external_id', external_id, 'source', source, "
        "'company_name', company_name, 'job_title', job_title, 'location', location, "
        "'workplace_type', workplace_type, 'job_url', job_url, "
        "'raw_description', raw_description, 'status', status)::text "
        f"FROM job_listings WHERE {where} LIMIT 1"
    )
    res = pg_exec(sql)
    try:
        return json.loads(res) if res else None
    except Exception:
        return None

def db_ingest_job(job: Dict[str, Any]) -> Tuple[Optional[str], bool]:
    """Insert a discovered job as PENDING_REVIEW. Returns (uuid, was_inserted)."""
    ext_id = str(job.get("external_id") or "").strip()
    if not ext_id:
        ext_id = "gen-" + hashlib.sha256(
            f"{job.get('company', '')}-{job.get('job_title', '')}-{job.get('url', '')}".encode()
        ).hexdigest()[:16]

    def esc(val):
        return str(val or "").replace("'", "''")

    sql = f"""
    INSERT INTO job_listings (external_id, source, company_name, job_title, location,
                              workplace_type, job_url, raw_description, status)
    VALUES ('{esc(ext_id)}', '{esc(job.get("source", "linkedin_public"))}',
            '{esc(job.get("company") or job.get("company_name") or "Unknown Company")}',
            '{esc(job.get("job_title") or "Unknown Role")}',
            '{esc(job.get("location", ""))}', '{esc(job.get("workplace_type", ""))}',
            '{esc(job.get("url") or job.get("job_url") or "")}',
            '{esc(job.get("description") or job.get("raw_description") or "")}',
            'PENDING_REVIEW')
    ON CONFLICT (external_id) DO NOTHING
    RETURNING id::text
    """
    new_id = pg_exec(sql)
    # ON CONFLICT DO NOTHING makes psql emit the command tag ("INSERT 0 0")
    # instead of a row, so only a real uuid counts as an insert.
    if new_id and UUID_RE.match(new_id):
        return new_id, True
    existing = db_get_job(ext_id)
    return (existing or {}).get("id"), False

def db_set_status(job_uuid: str, status: str) -> bool:
    if not UUID_RE.match(str(job_uuid or "")):
        return False
    pg_exec(f"UPDATE job_listings SET status = '{status.replace(chr(39), '')}' WHERE id = '{job_uuid}'::uuid")
    return True

def db_latest_tailored(job_uuid: str) -> Optional[Dict[str, Any]]:
    if not UUID_RE.match(str(job_uuid or "")):
        return None
    sql = (
        "SELECT json_build_object("
        "'pdf_path', pdf_path, 'match_score', match_score, 'recommendation', recommendation, "
        "'outreach_note', outreach_note, 'tailored_summary', tailored_summary)::text "
        f"FROM tailored_applications WHERE job_id = '{job_uuid}'::uuid ORDER BY created_at DESC LIMIT 1"
    )
    res = pg_exec(sql)
    try:
        return json.loads(res) if res else None
    except Exception:
        return None

def db_insert_job(job: Dict[str, Any]) -> Optional[str]:
    ext_id = job.get("external_id", "")
    if not ext_id:
        ext_id = hashlib.sha256(job.get("url", str(time.time())).encode()).hexdigest()[:16]

    def esc(val):
        return str(val or "").replace("'", "''")

    sql = f"""
    INSERT INTO job_listings (external_id, source, company_name, job_title, location,
                              workplace_type, job_url, raw_description, seniority, employment_type, status)
    VALUES ('{esc(ext_id)}', '{esc(job.get("source", "linkedin_public"))}',
            '{esc(job.get("company", ""))}', '{esc(job.get("job_title", job.get("title", "")))}',
            '{esc(job.get("location", ""))}', '{esc(job.get("workplace_type", ""))}',
            '{esc(job.get("url", job.get("job_url", "")))}', '{esc(job.get("description", job.get("raw_description", "")))}',
            '{esc(job.get("seniority", ""))}', '{esc(job.get("employment_type", ""))}', 'NEW')
    ON CONFLICT (external_id) DO UPDATE SET
        company_name = excluded.company_name,
        job_title = excluded.job_title,
        raw_description = excluded.raw_description
    RETURNING id::text
    """
    res = pg_exec(sql)
    return res or None

def db_insert_tailored(data: Dict[str, Any]) -> Optional[str]:
    def esc(val):
        return str(val or "").replace("'", "''")

    matched = "{" + ",".join(f'"{s}"' for s in data.get("matched_skills", [])) + "}"
    missing = "{" + ",".join(f'"{s}"' for s in data.get("missing_skills", [])) + "}"

    sql = f"""
    INSERT INTO tailored_applications (job_id, match_score, matched_skills, missing_skills,
                                       tailored_summary, pdf_path, outreach_note, recommendation, status)
    VALUES ('{esc(data["job_id"])}'::uuid, {int(data.get("match_score", 0))},
            '{matched}', '{missing}',
            '{esc(data.get("tailored_summary", ""))}',
            '{esc(data.get("pdf_path", ""))}',
            '{esc(data.get("outreach_note", ""))}',
            '{esc(data.get("recommendation", "CONSIDER"))}',
            'TAILORED')
    RETURNING id::text
    """
    return pg_exec(sql) or None

# AI layer — DeepSeek
AI_COOLDOWN_UNTIL = 0.0
AI_COOLDOWN_SECONDS = 90

# Token accounting (DeepSeek list prices, USD per 1M tokens — override via env).
PRICE_IN_PER_M = float(os.environ.get("DEEPSEEK_PRICE_IN", "0.28"))
PRICE_OUT_PER_M = float(os.environ.get("DEEPSEEK_PRICE_OUT", "0.42"))


def db_init_usage() -> None:
    pg_exec(
        "CREATE TABLE IF NOT EXISTS ai_usage ("
        "id BIGSERIAL PRIMARY KEY, created_at TIMESTAMPTZ DEFAULT NOW(), "
        "job_id UUID, model TEXT, prompt_tokens INT, completion_tokens INT, "
        "cost_usd NUMERIC(12,6), source TEXT DEFAULT 'bridge')"
    )


def db_record_usage(usage: Dict[str, Any], model: str, job_id: Optional[str] = None) -> None:
    try:
        prompt_tokens = int(usage.get("prompt_tokens") or 0)
        completion_tokens = int(usage.get("completion_tokens") or 0)
        cost = prompt_tokens / 1e6 * PRICE_IN_PER_M + completion_tokens / 1e6 * PRICE_OUT_PER_M
        job_sql = f"'{job_id}'::uuid" if job_id and UUID_RE.match(str(job_id)) else "NULL"
        pg_exec(
            "INSERT INTO ai_usage (job_id, model, prompt_tokens, completion_tokens, cost_usd) "
            f"VALUES ({job_sql}, '{model}', {prompt_tokens}, {completion_tokens}, {cost:.6f})"
        )
    except Exception as e:
        log(f"[AI] usage record failed: {e}")


def call_deepseek(prompt: str, system: str = "", job_ref: Optional[str] = None) -> str:
    """Single chat completion against DeepSeek, rotating keys on rate limits."""
    global ACTIVE_KEY_INDEX, AI_COOLDOWN_UNTIL

    if not DEEPSEEK_KEYS:
        log("[AI] No DEEPSEEK_API_KEY found — using heuristic engine.")
        return ""

    remaining = AI_COOLDOWN_UNTIL - time.time()
    if remaining > 0:
        log(f"[AI] DeepSeek in cooldown for another {int(remaining)}s — using heuristic engine.")
        return ""

    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    num_keys = len(DEEPSEEK_KEYS)
    for attempt in range(num_keys * 2):
        if attempt == num_keys:
            # One full pass failed; pause before the second sweep.
            time.sleep(3)

        key = DEEPSEEK_KEYS[(ACTIVE_KEY_INDEX + attempt) % num_keys]
        payload = json.dumps({
            "model": DEEPSEEK_MODEL,
            "messages": messages,
            "temperature": 0.15,
            "max_tokens": 4096,
            "response_format": {"type": "json_object"},
            "stream": False,
        }).encode("utf-8")

        req = urllib.request.Request(
            DEEPSEEK_API_URL,
            data=payload,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=90) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            ACTIVE_KEY_INDEX = (ACTIVE_KEY_INDEX + attempt) % num_keys
            content = data["choices"][0]["message"]["content"]
            usage = data.get("usage") or {}
            log(f"[AI:DEEPSEEK] {DEEPSEEK_MODEL} ok — tokens in/out {usage.get('prompt_tokens', '?')}/{usage.get('completion_tokens', '?')}")
            db_record_usage(usage, DEEPSEEK_MODEL, job_ref)
            return content
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="ignore")[:200]
            if e.code in (429, 402, 503):
                log(f"[AI] Key {key[:6]}… HTTP {e.code}, rotating key ({body})")
                ACTIVE_KEY_INDEX = (ACTIVE_KEY_INDEX + attempt + 1) % num_keys
                continue
            log(f"[AI] Key {key[:6]}… HTTP {e.code}: {body}")
        except Exception as e:
            log(f"[AI] DeepSeek error: {e}")

    AI_COOLDOWN_UNTIL = time.time() + AI_COOLDOWN_SECONDS
    log(f"⚠️ DeepSeek unavailable. Cooling down {AI_COOLDOWN_SECONDS}s, heuristic engine next.")
    return ""

def analyze_and_tailor(job_title: str, company: str, description: str, job_url: str,
                       job_ref: Optional[str] = None) -> Dict[str, Any]:
    prompt = f"""You are an ATS resume strategist. Match candidate against job description. Return JSON ONLY:
{{
  "match_score": <int 0-100>,
  "recommendation": "<STRONG_MATCH|GOOD_MATCH|CONSIDER>",
  "company_brief": "<1-2 sentences summarizing what this company does and their core market/product>",
  "job_summary": "<2-3 sentences summarizing the key responsibilities and mission of this specific position>",
  "matched_skills": ["<skill1>", "<skill2>"],
  "missing_skills": ["<gap1>", "<gap2>"],
  "tailored_summary": "<2-3 sentence executive summary tailored to this JD without fabricating experience>",
  "tailored_experience": [
    {{"company": "Medical Insurance Provider", "title": "Customer Service Executive", "period": "Apr. 2022 - Present", "location": "Abu Dhabi, UAE", "bullets": ["<bullet 1>", "<bullet 2>"]}}
  ],
  "tailored_skills": [{{"category": "Core", "items": "<skills>"}}],
  "outreach_note": "<personalized recruiter message strictly under 280 characters>"
}}

CANDIDATE:
{MASTER_PROFILE}

JOB:
Title: {job_title} | Company: {company}
{description[:5000]}
"""

    system_prompt = (
        "You are an ATS resume strategist. You always reply with a single valid JSON object "
        "and never fabricate experience, employers, dates or metrics that are not in the candidate profile."
    )

    raw = call_deepseek(prompt, system=system_prompt, job_ref=job_ref)
    result = {}
    if raw:
        try:
            result = json.loads(raw)
        except Exception:
            m = re.search(r'(\{[\s\S]*\})', raw)
            if m:
                try:
                    result = json.loads(m.group(1))
                except Exception:
                    pass

    # No usable AI result (or a 0 score) — degrade to the deterministic engine
    # so a rate limit never blocks an approval the user explicitly asked for.
    if not result or result.get("match_score", 0) == 0:
        log("⚡ Using heuristic fallback engine...")
        result = heuristic_analyze(job_title, company, description, MASTER_PROFILE)

    # Normalize fields from the AI
    if isinstance(result.get("matched_skills"), dict):
        result["matched_skills"] = list(result["matched_skills"].keys())
    if isinstance(result.get("missing_skills"), dict):
        result["missing_skills"] = list(result["missing_skills"].keys())
    if not isinstance(result.get("matched_skills"), list):
        result["matched_skills"] = []
    if not isinstance(result.get("missing_skills"), list):
        result["missing_skills"] = []

    # Ensure outreach_note exists
    if not result.get("outreach_note"):
        result["outreach_note"] = (
            f"Hi! Noticed the {job_title} opening at {company}. Bringing 10+ yrs in UAE customer ops (insurance and banking), "
            f"trilingual fluency (AR/EN/IT), & support automation, I'd welcome the chance to connect regarding {company}'s team."
        )

    # Ensure company_brief and job_summary exist
    if not result.get("company_brief"):
        result["company_brief"] = f"{company} operates in the region delivering specialized industry solutions and customer operations."
    if not result.get("job_summary"):
        result["job_summary"] = f"This position as {job_title} at {company} focuses on operational leadership, SLA fulfillment, and customer satisfaction."

    return result

# Typst
def _typ_esc(value: Any) -> str:
    """Escape for a Typst string literal, not markup.

    Values are injected as "...", so only backslash and double quote are
    special. Escaping ampersand, hash, dollar, percent and at-sign here used to
    print them with a visible leading backslash in the PDF.
    """
    if not value:
        return ""
    return (str(value)
            .replace("\\", "\\\\")
            .replace('"', '\\"'))


def _clean(value: Any) -> str:
    """Strip model markdown artifacts and collapse whitespace."""
    text = str(value or "")
    text = text.replace("**", "").replace("__", "")
    text = re.sub(r"^\s*[-•*]\s+", "", text)
    return " ".join(text.split())


_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


def _period_end_key(period: str) -> Tuple[int, int]:
    """Sort key for "Apr. 2022 - Present" style ranges; current roles rank first."""
    text = (period or "").lower()
    if "present" in text or "current" in text or "now" in text:
        return (9999, 12)

    years = [int(y) for y in re.findall(r"\b(19\d{2}|20\d{2})\b", text)]
    end_year = years[-1] if years else 0

    end_month = 1
    tail = re.split(r"[-–—]", text)[-1]
    for word in re.findall(r"[a-z]{3,}", tail):
        if word[:3] in _MONTHS:
            end_month = _MONTHS[word[:3]]
    return (end_year, end_month)


def sorted_experience(exps: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return sorted(exps, key=lambda e: _period_end_key(e.get("period", "")), reverse=True)


def generate_typst_code(analysis: Dict[str, Any], job_title: str, company: str) -> str:
    template = TEMPLATE_PATH.read_text(encoding="utf-8") if TEMPLATE_PATH.exists() else ""
    summary = analysis.get("tailored_summary", "")

    exp_entries = []
    exps = analysis.get("tailored_experience")
    if not exps or not isinstance(exps, list) or not any(e.get("bullets") for e in exps):
        exps = [
            {
                "company": "Medical Insurance Provider",
                "title": "Customer Service Executive",
                "period": "Apr. 2022 - Present",
                "location": "Abu Dhabi, UAE",
                "bullets": [
                    "Resolved 2,500+ complex medical pre-authorization claims monthly under strict SLAs.",
                    "Automated claim status notifications reducing inbound inquiry volume by 35%.",
                    "Cross-functional coordination with UAE medical provider network for expedited approvals."
                ]
            },
            {
                "company": "Global Bank Middle East",
                "title": "Customer Operations Specialist",
                "period": "Jan. 2018 - Mar. 2022",
                "location": "Dubai, UAE",
                "bullets": [
                    "Managed high-volume multi-channel customer banking operations across English, Arabic, and Italian.",
                    "Reduced customer inquiry turnaround time by 40% via CRM ticketing workflow overhaul."
                ]
            }
        ]

    for exp in sorted_experience(exps):
        b_list = [_clean(b) for b in (exp.get("bullets") or []) if _clean(b)]
        if not b_list:
            b_list = ["Managed core operations, workflow execution, and client SLA compliance."]
        bullets_code = ", ".join(f'"{_typ_esc(b)}"' for b in b_list)
        bullets_tuple = f"({bullets_code},)" if bullets_code else "()"
        exp_entries.append(
            f'  (company: "{_typ_esc(_clean(exp.get("company", "")))}", '
            f'title: "{_typ_esc(_clean(exp.get("title", "")))}", '
            f'period: "{_typ_esc(_clean(exp.get("period", "")))}", '
            f'location: "{_typ_esc(_clean(exp.get("location", "")))}", '
            f'bullets: {bullets_tuple})'
        )
    exp_code = ",\n".join(exp_entries)

    skills_entries = []
    skills = analysis.get("tailored_skills")
    if not skills or not isinstance(skills, list):
        skills = [
            {"category": "Core Competencies", "items": ", ".join(analysis.get("matched_skills", [])) or "Customer Operations, Workflow Automation, CRM Systems, SLA Management"},
            {"category": "Tools & Systems", "items": "CRM (Salesforce, Zendesk), Linux, Python, PostgreSQL, n8n, Supabase"},
            {"category": "Languages", "items": "Arabic (Native), English (Fluent), Italian (Working)"}
        ]
    for sk in skills:
        items_val = sk.get("items", "")
        if isinstance(items_val, list):
            items_val = ", ".join(str(x) for x in items_val)
        skills_entries.append(
            f'  (category: "{_typ_esc(_clean(sk.get("category", "")))}", '
            f'items: "{_typ_esc(_clean(items_val))}")'
        )
    skills_code = ",\n".join(skills_entries)

    typ_code = template
    replacements = {
        '"CANDIDATE_NAME"': '"Alex Example"',
        '"CANDIDATE_HEADLINE"': f'"{_typ_esc(job_title)} | Customer Operations & Systems"',
        '"CANDIDATE_EMAIL"': '"alex@example.com"',
        '"CANDIDATE_PHONE"': '"+1-555-0100"',
        '"CANDIDATE_LOCATION"': '"Abu Dhabi, UAE"',
        '"CANDIDATE_WEB"': '"example.com"',
        '"CANDIDATE_LINKEDIN"': '"linkedin.com/in/example"',
        '"CANDIDATE_GITHUB"': '"github.com/example"',
        '"EXECUTIVE_SUMMARY"': f'"{_typ_esc(_clean(summary))}"',
    }
    for old, new in replacements.items():
        typ_code = typ_code.replace(old, new)

    typ_code = typ_code.replace('#let experience = ()', f'#let experience = (\n{exp_code},\n)')
    typ_code = typ_code.replace('#let skills = ()', f'#let skills = (\n{skills_code},\n)')
    typ_code = typ_code.replace(
        '#let education = ()',
        '#let education = (\n  (institution: "Cairo University", degree: "Bachelor of Law (LL.B.), General Law", year: "2010"),\n)'
    )
    typ_code = typ_code.replace(
        '#let certifications = ()',
        '#let certifications = (\n  "MODLI Advanced Business English (2014)",\n  "Legal Translation Diploma – TOLES (2015)",\n  "Google Digital Sales & Digital Garage Certifications (2019)",\n)'
    )
    typ_code = typ_code.replace(
        '#let languages = ()',
        '#let languages = (\n  (language: "English", proficiency: "Fluent / Full Professional"),\n  (language: "Arabic", proficiency: "Native"),\n  (language: "Italian", proficiency: "Working Professional"),\n)'
    )
    return typ_code

def compile_typst(typ_code: str, job_id: str) -> Tuple[Optional[str], Optional[str]]:
    typ_path = OUTPUT_DIR / f"{job_id}.typ"
    pdf_path = OUTPUT_DIR / f"{job_id}.pdf"
    typ_path.write_text(typ_code, encoding="utf-8")

    result = subprocess.run([TYPST_BIN, "compile", str(typ_path), str(pdf_path)], capture_output=True, text=True, timeout=15)
    if result.returncode == 0 and pdf_path.exists():
        return str(pdf_path), None
    return None, result.stderr.strip() or "Compilation failed"

# Main pipeline execution
def run_tailor_pipeline(job_data: Dict[str, Any]) -> Dict[str, Any]:
    job_title = job_data.get("job_title", job_data.get("title", "Target Role"))
    company = job_data.get("company", job_data.get("company_name", "Company"))
    description = job_data.get("description", job_data.get("raw_description", ""))
    job_url = job_data.get("url", job_data.get("job_url", ""))
    external_id = job_data.get("external_id", "")
    if not external_id:
        external_id = hashlib.sha256(f"{company}-{job_title}-{job_url}".encode()).hexdigest()[:16]

    log(f"📋 Processing: {job_title} @ {company}")

    # Always tailor against the newest uploaded resume, even if the bridge has
    # been running since before the user replaced their profile.
    global MASTER_PROFILE
    MASTER_PROFILE = load_master_profile()

    if job_data.get("skip_duplicate") and db_check_duplicate(external_id):
        log(f"↩️ Skipping duplicate: {job_title} @ {company} ({external_id})")
        return {
            "status": "DUPLICATE",
            "external_id": external_id,
            "job_title": job_title,
            "company": company,
        }

    job_uuid = db_insert_job({**job_data, "external_id": external_id})
    if not job_uuid:
        res = pg_exec(f"SELECT id::text FROM job_listings WHERE external_id = '{external_id}'")
        job_uuid = res or external_id

    # Analysis
    analysis = analyze_and_tailor(job_title, company, description, job_url, job_ref=job_uuid)
    match_score = analysis.get("match_score", 85)
    recommendation = analysis.get("recommendation", "STRONG_MATCH")

    # Generate Typst PDF
    typ_code = generate_typst_code(analysis, job_title, company)
    pdf_path, typ_err = compile_typst(typ_code, job_uuid)
    if pdf_path:
        log(f"✅ PDF compiled: {pdf_path}")
    else:
        log(f"⚠️ Typst error: {typ_err}")

    # Save to PostgreSQL
    db_insert_tailored({
        "job_id": job_uuid,
        "match_score": match_score,
        "matched_skills": analysis.get("matched_skills", []),
        "missing_skills": analysis.get("missing_skills", []),
        "tailored_summary": analysis.get("tailored_summary", ""),
        "pdf_path": pdf_path or "",
        "outreach_note": analysis.get("outreach_note", ""),
        "recommendation": recommendation
    })

    return {
        "status": "TAILORED",
        "job_id": job_uuid,
        "external_id": external_id,
        "job_title": job_title,
        "company": company,
        "company_brief": analysis.get("company_brief", ""),
        "job_summary": analysis.get("job_summary", ""),
        "match_score": match_score,
        "recommendation": recommendation,
        "pdf_path": pdf_path,
        "outreach_note": analysis.get("outreach_note", ""),
        "matched_skills": analysis.get("matched_skills", []),
        "missing_skills": analysis.get("missing_skills", []),
        "typst_error": typ_err
    }

class BridgeHandler(http.server.BaseHTTPRequestHandler):
    def _send_json(self, status: int, data: Any):
        body = json.dumps(data, indent=2, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self) -> bool:
        return self.headers.get("Authorization", "") == f"Bearer {AUTH_TOKEN}"

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        route = parsed.path

        if route == "/ping":
            self._send_json(200, {
                "status": "PONG",
                "service": "job_worker",
                "ai_provider": "deepseek",
                "deepseek_keys": len(DEEPSEEK_KEYS),
                "deepseek_model": DEEPSEEK_MODEL,
                "ai_cooldown": max(0, int(AI_COOLDOWN_UNTIL - time.time())),
                "typst": Path(TYPST_BIN).exists(),
                "profile_loaded": bool(MASTER_PROFILE)
            })
        elif route in ("/stats", "/seen"):
            if not self._authorized():
                self._send_json(401, {"error": "Unauthorized"})
                return

            if route == "/stats":
                total = pg_exec("SELECT COUNT(*) FROM job_listings")
                tailored = pg_exec("SELECT COUNT(*) FROM tailored_applications")
                avg = pg_exec("SELECT COALESCE(ROUND(AVG(match_score)), 0) FROM tailored_applications")
                calls = pg_exec("SELECT COUNT(*) FROM ai_usage")
                tokens = pg_exec("SELECT COALESCE(SUM(prompt_tokens + completion_tokens), 0) FROM ai_usage")
                cost_today = pg_exec("SELECT COALESCE(SUM(cost_usd), 0) FROM ai_usage WHERE created_at >= CURRENT_DATE")
                cost_total = pg_exec("SELECT COALESCE(SUM(cost_usd), 0) FROM ai_usage")
                self._send_json(200, {
                    "total_jobs": int(total or 0),
                    "tailored": int(tailored or 0),
                    "avg_score": int(avg or 0),
                    "ai_calls": int(calls or 0),
                    "ai_tokens": int(tokens or 0),
                    "ai_cost_today_usd": round(float(cost_today or 0), 4),
                    "ai_cost_total_usd": round(float(cost_total or 0), 4),
                })
            else:
                external_id = (urllib.parse.parse_qs(parsed.query).get("external_id") or [""])[0]
                if not external_id:
                    self._send_json(400, {"error": "external_id query parameter required"})
                    return
                self._send_json(200, {
                    "external_id": external_id,
                    "seen": db_check_duplicate(external_id)
                })
        else:
            self._send_json(404, {"error": "Not found"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b""

        route = urllib.parse.urlparse(self.path).path
        if route in ("/tailor", "/reload-profile", "/ingest", "/approve", "/ignore", "/regenerate") and not self._authorized():
            log(f"Rejected unauthorized POST {route} from {self.client_address[0]}")
            self._send_json(401, {"error": "Unauthorized"})
            return

        if route == "/tailor":
            try:
                job_data = json.loads(body) if body else {}
                res = run_tailor_pipeline(job_data)
                self._send_json(200, res)
            except Exception as e:
                log(f"Error: {e}")
                self._send_json(500, {"error": str(e)})

        elif route == "/ingest":
            # Discovery only: store new jobs and send one Approve/Ignore card
            # each. No AI call, no PDF — that costs nothing until approved.
            try:
                payload = json.loads(body) if body else {}
                incoming = payload.get("jobs") if isinstance(payload, dict) else payload
                if isinstance(incoming, dict):
                    incoming = [incoming]
                if not isinstance(incoming, list):
                    self._send_json(400, {"error": "Expected a job object or a list of jobs"})
                    return

                inserted, duplicates, excluded = [], [], []
                failed = 0
                for raw_job in incoming:
                    if not isinstance(raw_job, dict):
                        failed += 1
                        continue

                    reason = exclusion_reason(raw_job)
                    if reason:
                        excluded.append({
                            "job_title": raw_job.get("job_title") or raw_job.get("title"),
                            "company": raw_job.get("company") or raw_job.get("company_name"),
                            "reason": reason,
                        })
                        log(f"⛔ Excluded ({reason}): {raw_job.get('job_title')} @ {raw_job.get('company')}")
                        continue

                    job_uuid, was_inserted = db_ingest_job(raw_job)
                    if not job_uuid:
                        failed += 1
                        continue
                    if not was_inserted:
                        duplicates.append(job_uuid)
                        continue

                    job = db_get_job(job_uuid) or {}
                    inserted.append(job)
                    try:
                        if send_review_card(job, CHAT_ID):
                            continue
                        log(f"Review card failed to send for {job_uuid}")
                    except Exception as e:
                        log(f"Review card error for {job_uuid}: {e}")

                log(f"📥 Ingest: {len(inserted)} new, {len(duplicates)} duplicate, "
                    f"{len(excluded)} excluded, {failed} invalid")
                self._send_json(200, {
                    "status": "OK",
                    "inserted": len(inserted),
                    "duplicates": len(duplicates),
                    "excluded": len(excluded),
                    "failed": failed,
                    "excluded_jobs": excluded,
                    "jobs": [{"id": j.get("id"), "external_id": j.get("external_id"),
                              "job_title": j.get("job_title"), "company_name": j.get("company_name")}
                             for j in inserted],
                })
            except Exception as e:
                log(f"Ingest error: {e}")
                self._send_json(500, {"error": str(e)})

        elif route == "/approve":
            try:
                payload = json.loads(body) if body else {}
                ref = payload.get("job_id") or payload.get("id") or payload.get("external_id")
                job = db_get_job(ref)
                if not job:
                    self._send_json(404, {"status": "ERROR", "error": f"Unknown job: {ref}"})
                    return
                if job.get("status") == "DISCARDED":
                    self._send_json(409, {"status": "DISCARDED", "error": "This job was dismissed."})
                    return
                if job.get("status") == "APPROVED":
                    existing = db_latest_tailored(job["id"]) or {}
                    self._send_json(200, {"status": "ALREADY_APPROVED", "job": job, **existing})
                    return

                res = run_tailor_pipeline({
                    "external_id": job["external_id"],
                    "job_title": job["job_title"],
                    "company": job["company_name"],
                    "location": job.get("location", ""),
                    "workplace_type": job.get("workplace_type", ""),
                    "url": job.get("job_url", ""),
                    "description": job.get("raw_description", ""),
                    "source": job.get("source", "linkedin_public"),
                })
                res["job"] = job
                if res.get("status") == "TAILORED":
                    db_set_status(job["id"], "APPROVED")
                self._send_json(200, res)
            except Exception as e:
                log(f"Approve error: {e}")
                self._send_json(500, {"status": "ERROR", "error": str(e)})

        elif route == "/regenerate":
            # Re-run tailoring against the newest uploaded profile, even if the
            # job was already approved (or was ignored and the user changed
            # their mind).
            try:
                payload = json.loads(body) if body else {}
                ref = payload.get("job_id") or payload.get("id") or payload.get("external_id")
                job = db_get_job(ref)
                if not job:
                    self._send_json(404, {"status": "ERROR", "error": f"Unknown job: {ref}"})
                    return

                log(f"🔄 Regenerating: {job['job_title']} @ {job['company_name']}")
                res = run_tailor_pipeline({
                    "external_id": job["external_id"],
                    "job_title": job["job_title"],
                    "company": job["company_name"],
                    "location": job.get("location", ""),
                    "workplace_type": job.get("workplace_type", ""),
                    "url": job.get("job_url", ""),
                    "description": job.get("raw_description", ""),
                    "source": job.get("source", "linkedin_public"),
                })
                res["job"] = job
                if res.get("status") == "TAILORED":
                    db_set_status(job["id"], "APPROVED")
                self._send_json(200, res)
            except Exception as e:
                log(f"Regenerate error: {e}")
                self._send_json(500, {"status": "ERROR", "error": str(e)})

        elif route == "/ignore":
            try:
                payload = json.loads(body) if body else {}
                ref = payload.get("job_id") or payload.get("id") or payload.get("external_id")
                job = db_get_job(ref)
                if not job:
                    self._send_json(404, {"status": "ERROR", "error": f"Unknown job: {ref}"})
                    return
                db_set_status(job["id"], "DISCARDED")
                log(f"🗑 Discarded: {job['job_title']} @ {job['company_name']}")
                self._send_json(200, {"status": "DISCARDED", "job_id": job["id"]})
            except Exception as e:
                self._send_json(500, {"status": "ERROR", "error": str(e)})

        elif route == "/reload-profile":
            global MASTER_PROFILE
            MASTER_PROFILE = load_master_profile()
            self._send_json(200, {"status": "reloaded", "length": len(MASTER_PROFILE)})
        else:
            self._send_json(404, {"error": "Not found"})

    def log_message(self, format, *args):
        log(f"HTTP {args[0]}")

class BridgeServer(http.server.ThreadingHTTPServer):
    """Threaded so a slow /approve does not block /ingest or another approval."""
    daemon_threads = True


def main():
    db_init_usage()
    server = BridgeServer(("0.0.0.0", 8766), BridgeHandler)
    log(f"Bridge Worker listening on 0.0.0.0:8766 | DeepSeek keys: {len(DEEPSEEK_KEYS)} | model: {DEEPSEEK_MODEL}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.server_close()

if __name__ == "__main__":
    main()
