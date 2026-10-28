#!/usr/bin/env python3
"""
Telegram Bot Daemon for LinkedIn Hunter Pipeline with Rich Job Briefs & Reliable Document Dispatch.
"""

import os
import sys
import json
import time
import re
import random
import threading
import urllib.request
import urllib.parse
import urllib.error
import subprocess
from pathlib import Path
from typing import Dict, Any, Optional, Tuple, List

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from bridge.resume_merger import merge_profiles
from telegram_api import (
    BOT_TOKEN,
    CHAT_ID,
    TELEGRAM_API,
    clear_keyboard,
    esc_html,
    get_file_download_url,
    send_document,
    send_message,
    tg_request,
)

# Configuration
ALLOWED_CHAT_ID = CHAT_ID
BRIDGE_URL = os.environ.get("BRIDGE_URL", "http://127.0.0.1:8766")
BRIDGE_AUTH = f"Bearer {os.environ.get('BRIDGE_AUTH_TOKEN', '')}"
PROJECT_ROOT = Path(__file__).resolve().parent.parent
PROFILE_FILE = PROJECT_ROOT / "profile" / "master_profile.md"

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
BROWSER_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# /hunt configuration
HUNT_LIMIT = 5                      # jobs delivered per hunt
HUNT_MAX_KEYWORDS = 3               # focus areas tried per hunt
HUNT_MAX_PAGES = 3                  # LinkedIn listing pages (25 results each) per keyword
HUNT_PAGE_DELAY = (1.5, 2.5)        # jittered pause between guest API calls, seconds
DEFAULT_HUNT_KEYWORDS = ["Customer Operations"]
DEFAULT_HUNT_LOCATION = "United Arab Emirates"
HUNT_LOCK = threading.Lock()

WIZARD_BYPASS_COMMANDS = {
    "/start", "/help", "❓ /help",
    "/update", "📝 /update",
    "/stats", "📊 /stats",
    "/profile", "👤 /profile",
    "/hunt", "🔍 /hunt",
}

WIZARD_SESSIONS: Dict[int, Dict[str, Any]] = {}

def log(msg: str):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] [TG_BOT] {msg}", flush=True)

def sleep_jitter(delay_range: Tuple[float, float] = HUNT_PAGE_DELAY) -> None:
    time.sleep(random.uniform(*delay_range))

def parse_job_id(url_or_id: str) -> Optional[str]:
    m = re.search(r"/jobs/view/([0-9]+)", url_or_id) or re.search(r"currentJobId=([0-9]+)", url_or_id)
    if m:
        return m.group(1)
    if url_or_id.isdigit():
        return url_or_id
    return None

def fetch_linkedin_job(url_or_id: str, retries: int = 2) -> Optional[Dict[str, str]]:
    """Fetch one public job posting from the LinkedIn guest endpoint.

    Retries on 429/999 and transient network errors — back-to-back guest calls
    are rate limited often enough that a single attempt silently loses jobs.
    """
    job_id = parse_job_id(url_or_id)
    if not job_id:
        return None

    detail_url = f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}"

    html = None
    for attempt in range(retries + 1):
        req = urllib.request.Request(detail_url, headers=BROWSER_HEADERS)
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                html = r.read().decode("utf-8", errors="ignore")
            break
        except urllib.error.HTTPError as e:
            if e.code in (429, 999) and attempt < retries:
                log(f"LinkedIn throttled job {job_id} (HTTP {e.code}), retrying...")
                sleep_jitter((2.0, 3.5))
                continue
            log(f"Error fetching LinkedIn job {job_id}: HTTP {e.code}")
            return None
        except Exception as e:
            if attempt < retries:
                log(f"Transient error fetching job {job_id} ({e}), retrying...")
                sleep_jitter((1.5, 2.5))
                continue
            log(f"Error fetching LinkedIn job {job_id}: {e}")
            return None

    if not html:
        return None

    try:
        t_match = re.search(r'<h2 class="top-card-layout__title[^"]*">(.*?)</h2>', html, re.DOTALL)
        title = re.sub(r'<[^>]+>', '', t_match.group(1)).strip() if t_match else ""

        c_match = re.search(r'<a class="topcard__org-name-link[^"]*"[^>]*>(.*?)</a>', html, re.DOTALL)
        company = re.sub(r'<[^>]+>', '', c_match.group(1)).strip() if c_match else "Company"

        l_match = re.search(r'<span class="topcard__flavor topcard__flavor--bullet">([^<]+)</span>', html)
        location = l_match.group(1).strip() if l_match else "UAE / Remote"

        d_match = re.search(r'<div class="show-more-less-html__markup[^"]*"[^>]*>(.*?)</div>', html, re.DOTALL)
        desc = re.sub(r'<[^>]+>', ' ', d_match.group(1)).strip() if d_match else ""
        desc = " ".join(desc.split())

        # No title and no body means a closed/expired posting — skip it rather
        # than emitting a junk "Target Role" card.
        if not title and not desc:
            return None

        return {
            "external_id": f"li-{job_id}",
            "job_title": title or "Target Role",
            "company": company,
            "location": location,
            "workplace_type": "Public Post",
            "url": f"https://www.linkedin.com/jobs/view/{job_id}",
            "description": desc or f"Position for {title} at {company} in {location}."
        }
    except Exception as e:
        log(f"Error parsing LinkedIn job {job_id}: {e}")
        return None

def trigger_bridge_tailor(job_data: Dict[str, Any]) -> Dict[str, Any]:
    url = f"{BRIDGE_URL}/tailor"
    payload = json.dumps(job_data).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json", "Authorization": BRIDGE_AUTH},
        method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        log(f"Bridge call error: {e}")
        return {"status": "ERROR", "error": str(e)}

def notify_bridge_reload_profile():
    try:
        req = urllib.request.Request(f"{BRIDGE_URL}/reload-profile", data=b"{}", headers={"Authorization": BRIDGE_AUTH}, method="POST")
        urllib.request.urlopen(req, timeout=5)
    except Exception as e:
        log(f"Bridge reload error: {e}")

def get_bridge_stats() -> Dict[str, Any]:
    url = f"{BRIDGE_URL}/stats"
    req = urllib.request.Request(url, headers={"Authorization": BRIDGE_AUTH})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        return {"error": str(e)}

def bridge_job_seen(external_id: str) -> bool:
    """True if the bridge already has this job in PostgreSQL. Fails open on error."""
    url = f"{BRIDGE_URL}/seen?external_id={urllib.parse.quote(external_id)}"
    req = urllib.request.Request(url, headers={"Authorization": BRIDGE_AUTH})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return bool(json.loads(resp.read().decode("utf-8")).get("seen"))
    except Exception as e:
        log(f"Dedup check failed for {external_id}: {e}")
        return False

def hunt_keywords() -> List[str]:
    """Target roles from the profile's Focus Areas, falling back to a default."""
    try:
        content = PROFILE_FILE.read_text(encoding="utf-8")
        m = re.search(r"- \*\*Focus Areas:\*\* (.+)", content)
        if m:
            roles = [r.strip() for r in re.split(r"[,/]", m.group(1)) if 2 < len(r.strip()) < 60]
            if roles:
                return roles[:HUNT_MAX_KEYWORDS]
    except Exception as e:
        log(f"Could not read focus areas: {e}")
    return list(DEFAULT_HUNT_KEYWORDS)

def fetch_search_page(keyword: str, location: str, start: int) -> List[str]:
    """Return job IDs from one public LinkedIn listing page, newest first."""
    params = urllib.parse.urlencode({
        "keywords": keyword,
        "location": location,
        "start": start,
        "sortBy": "DD",     # newest postings first
        "f_TPR": "r604800", # posted in the last 7 days
    })
    search_url = f"https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search?{params}"
    req = urllib.request.Request(search_url, headers=BROWSER_HEADERS)

    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            html = r.read().decode("utf-8", errors="ignore")
    except Exception as e:
        log(f"Search page error ({keyword}, start={start}): {e}")
        return []

    ids = re.findall(r'/jobs/view/([0-9]+)', html) or re.findall(r'jobPosting:([0-9]+)', html)
    return list(dict.fromkeys(ids))

def perform_public_search(keyword: Optional[str] = None, location: str = DEFAULT_HUNT_LOCATION,
                          limit: int = HUNT_LIMIT, max_pages: int = HUNT_MAX_PAGES) -> List[Dict[str, str]]:
    """Scan public LinkedIn listing pages and return up to `limit` fresh jobs.

    Pages through results (newest first), tries each of the profile's focus
    areas, and skips anything already stored in PostgreSQL so repeat hunts
    surface genuinely new roles instead of the same two cards.
    """
    keywords = [keyword] if keyword else hunt_keywords()
    jobs: List[Dict[str, str]] = []
    seen_ids: set = set()

    for kw in keywords:
        for page in range(max_pages):
            if len(jobs) >= limit:
                break
            ids = [jid for jid in fetch_search_page(kw, location, page * 25) if jid not in seen_ids]
            if not ids:
                break
            for jid in ids:
                if len(jobs) >= limit:
                    break
                seen_ids.add(jid)
                if bridge_job_seen(f"li-{jid}"):
                    continue
                job = fetch_linkedin_job(jid)
                if job:
                    jobs.append(job)
                    log(f"Hunt found: {job['job_title']} @ {job['company']} ({kw})")
                sleep_jitter()
        if len(jobs) >= limit:
            break

    log(f"Hunt returned {len(jobs)}/{limit} fresh jobs across {len(keywords)} keyword(s)")
    return jobs

def build_main_keyboard() -> Dict[str, Any]:
    return {
        "keyboard": [
            [{"text": "🔍 /hunt"}, {"text": "📊 /stats"}],
            [{"text": "📝 /update"}, {"text": "👤 /profile"}],
            [{"text": "❓ /help"}]
        ],
        "resize_keyboard": True
    }

def start_update_wizard(chat_id: int):
    text = (
        "📝 <b>Resume & Profile Update Wizard</b>\n\n"
        "How would you like to update your resume and preferences?\n\n"
        "<b>1️⃣ 📑 Smart Merge (Recommended):</b>\n"
        "Upload an additional PDF or text. Merges new skills & experience into your existing profile without losing anything.\n\n"
        "<b>2️⃣ 🔄 Full Replace:</b>\n"
        "Upload a single PDF or text to overwrite the profile completely.\n\n"
        "<b>3️⃣ ✏️ Field Editor:</b>\n"
        "Tweak Target Roles, Headline, or Summary."
    )
    inline_keyboard = {
        "inline_keyboard": [
            [{"text": "📑 Smart Merge (Combine Resumes)", "callback_data": "wiz_merge"}],
            [{"text": "🔄 Replace with New PDF / File", "callback_data": "wiz_replace"}],
            [{"text": "🎯 Edit Target Roles & Filters", "callback_data": "wiz_roles"}],
            [{"text": "✏️ Edit Headline & Title", "callback_data": "wiz_headline"}],
            [{"text": "📝 Edit Summary", "callback_data": "wiz_summary"}],
            [{"text": "❌ Cancel", "callback_data": "wiz_cancel"}]
        ]
    }
    send_message(chat_id, text, reply_markup=inline_keyboard)

def handle_wizard_callback(callback_query: Dict[str, Any]):
    cq_id = callback_query.get("id")
    chat_id = callback_query.get("message", {}).get("chat", {}).get("id")
    data = callback_query.get("data", "")
    tg_request("answerCallbackQuery", {"callback_query_id": cq_id})

    if data == "wiz_cancel":
        WIZARD_SESSIONS.pop(chat_id, None)
        send_message(chat_id, "❌ Wizard cancelled. Your current profile remains unchanged.", reply_markup=build_main_keyboard())
    elif data == "wiz_merge":
        WIZARD_SESSIONS[chat_id] = {"step": "AWAIT_FILE_OR_TEXT", "mode": "MERGE"}
        send_message(chat_id, "📑 <b>Smart Merge Mode Activated!</b>\n\nPlease <b>attach your resume PDF</b> (or paste text) now.")
    elif data == "wiz_replace":
        WIZARD_SESSIONS[chat_id] = {"step": "AWAIT_FILE_OR_TEXT", "mode": "REPLACE"}
        send_message(chat_id, "🔄 <b>Full Replace Mode Activated!</b>\n\nPlease <b>attach your new resume PDF</b> (or paste text) now.")
    elif data == "wiz_headline":
        WIZARD_SESSIONS[chat_id] = {"step": "AWAIT_HEADLINE"}
        send_message(chat_id, "✏️ <b>Enter your new Professional Headline / Target Title:</b>")
    elif data == "wiz_roles":
        WIZARD_SESSIONS[chat_id] = {"step": "AWAIT_ROLES"}
        send_message(chat_id, "🎯 <b>Enter your Target Roles (comma separated):</b>")
    elif data == "wiz_summary":
        WIZARD_SESSIONS[chat_id] = {"step": "AWAIT_SUMMARY"}
        send_message(chat_id, "📝 <b>Paste your new 2-4 sentence Executive Summary:</b>")

def process_uploaded_document_or_text(chat_id: int, file_path_or_text: str, is_file: bool, filename: str = "") -> None:
    session = WIZARD_SESSIONS.get(chat_id, {})
    mode = session.get("mode", "MERGE")

    raw_text = ""
    if is_file:
        send_message(chat_id, f"📥 <b>Processing document:</b> <code>{filename}</code>...")
        if filename.lower().endswith(".pdf"):
            res = subprocess.run(["pdftotext", file_path_or_text, "-"], capture_output=True, text=True)
            raw_text = res.stdout.strip()
        else:
            with open(file_path_or_text, "r", encoding="utf-8", errors="ignore") as f:
                raw_text = f.read().strip()
    else:
        raw_text = file_path_or_text.strip()

    if not raw_text or len(raw_text) < 80:
        send_message(chat_id, "⚠️ <b>Insufficient text extracted.</b> If this is a scanned PDF, please paste the plain text directly into chat.")
        return

    existing_md = PROFILE_FILE.read_text(encoding="utf-8") if PROFILE_FILE.exists() else ""

    if mode == "MERGE" and existing_md:
        send_message(chat_id, "🧠 <b>Merging new resume into master profile...</b>")
        merged_md, stats = merge_profiles(existing_md, raw_text)
        PROFILE_FILE.write_text(merged_md, encoding="utf-8")
        notify_bridge_reload_profile()
        WIZARD_SESSIONS.pop(chat_id, None)

        send_message(
            chat_id,
            "✅ <b>Smart Merge Complete!</b>\n\n"
            f"• <b>New Skills Added:</b> {stats['new_skills']}\n"
            f"• <b>New Experience Bullets Added:</b> {stats['new_bullets']}\n"
            f"• <b>New Roles / Companies Added:</b> {stats['new_entries']}\n"
            f"• <b>Master Profile Size:</b> {len(merged_md)} characters\n\n"
            "All future job matches will tailor using your combined master profile! 🎉",
            reply_markup=build_main_keyboard()
        )
    else:
        merged_md, _ = merge_profiles("", raw_text)
        PROFILE_FILE.write_text(merged_md, encoding="utf-8")
        notify_bridge_reload_profile()
        WIZARD_SESSIONS.pop(chat_id, None)

        send_message(
            chat_id,
            "✅ <b>Master Profile Successfully Replaced!</b>\n\n"
            f"• Profile Size: {len(merged_md)} characters\n\n"
            "All future job matches will tailor against this new profile.",
            reply_markup=build_main_keyboard()
        )

def handle_wizard_input(chat_id: int, text: str) -> bool:
    session = WIZARD_SESSIONS.get(chat_id)
    if not session:
        return False

    if text == "/cancel":
        WIZARD_SESSIONS.pop(chat_id, None)
        send_message(chat_id, "❌ Wizard cancelled.", reply_markup=build_main_keyboard())
        return True

    step = session.get("step")
    if step == "AWAIT_FILE_OR_TEXT":
        process_uploaded_document_or_text(chat_id, text, is_file=False)
        return True
    elif step == "AWAIT_HEADLINE":
        content = PROFILE_FILE.read_text(encoding="utf-8") if PROFILE_FILE.exists() else ""
        content = re.sub(r"- \*\*Preferred Name:\*\* (.*)", f"- **Preferred Name:** Alex\n- **Headline:** {text}", content)
        PROFILE_FILE.write_text(content, encoding="utf-8")
        notify_bridge_reload_profile()
        WIZARD_SESSIONS.pop(chat_id, None)
        send_message(chat_id, f"✅ <b>Headline updated to:</b>\n<i>{text}</i>", reply_markup=build_main_keyboard())
        return True
    elif step == "AWAIT_ROLES":
        content = PROFILE_FILE.read_text(encoding="utf-8") if PROFILE_FILE.exists() else ""
        roles_block = f"\n## Target Role Filters\n- **Focus Areas:** {text}\n"
        if "## Target Role Filters" in content:
            content = re.sub(r"## Target Role Filters\n.*?(?=\n##|\Z)", roles_block, content, flags=re.DOTALL)
        else:
            content += "\n" + roles_block
        PROFILE_FILE.write_text(content, encoding="utf-8")
        notify_bridge_reload_profile()
        WIZARD_SESSIONS.pop(chat_id, None)
        send_message(chat_id, f"✅ <b>Target Roles updated to:</b>\n<i>{text}</i>", reply_markup=build_main_keyboard())
        return True
    elif step == "AWAIT_SUMMARY":
        content = PROFILE_FILE.read_text(encoding="utf-8") if PROFILE_FILE.exists() else ""
        summary_block = f"## Executive Summary\n{text}\n\n---"
        content = re.sub(r"## Executive Summary\n.*?\n---", summary_block, content, flags=re.DOTALL)
        PROFILE_FILE.write_text(content, encoding="utf-8")
        notify_bridge_reload_profile()
        WIZARD_SESSIONS.pop(chat_id, None)
        send_message(chat_id, "✅ <b>Executive Summary updated!</b>", reply_markup=build_main_keyboard())
        return True

    return False

def bridge_post(path: str, payload: Dict[str, Any], timeout: int = 180) -> Dict[str, Any]:
    req = urllib.request.Request(
        f"{BRIDGE_URL}{path}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": BRIDGE_AUTH},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="ignore")
        try:
            return json.loads(body)
        except Exception:
            return {"status": "ERROR", "error": f"HTTP {e.code}: {body[:200]}"}
    except Exception as e:
        log(f"Bridge call error ({path}): {e}")
        return {"status": "ERROR", "error": str(e)}

def bridge_ingest(jobs: List[Dict[str, Any]]) -> Dict[str, Any]:
    return bridge_post("/ingest", {"jobs": jobs}, timeout=120)

def bridge_approve(job_id: str) -> Dict[str, Any]:
    return bridge_post("/approve", {"job_id": job_id}, timeout=240)

def bridge_regenerate(job_id: str) -> Dict[str, Any]:
    return bridge_post("/regenerate", {"job_id": job_id}, timeout=240)

def bridge_ignore(job_id: str) -> Dict[str, Any]:
    return bridge_post("/ignore", {"job_id": job_id}, timeout=30)

def send_tailored_result(chat_id: int, job_data: Dict[str, Any], result: Dict[str, Any]):
    """Render one tailored application: PDF + brief + recruiter note."""
    title = job_data.get("job_title") or result.get("job_title") or "Unknown Role"
    company = job_data.get("company") or job_data.get("company_name") or result.get("company") or "Company"
    url = job_data.get("url") or job_data.get("job_url") or "https://linkedin.com"

    score = result.get("match_score", 0)
    rec = result.get("recommendation", "CONSIDER")
    note = result.get("outreach_note", "")
    pdf_path = result.get("pdf_path")
    company_brief = result.get("company_brief", "")
    job_summary = result.get("job_summary", "")

    score_emoji = "🎯" if score >= 85 else ("⚡" if score >= 70 else "⚠️")
    escaped_title = esc_html(title)
    escaped_company = esc_html(company)

    caption = (
        f"{score_emoji} <b>Match Score: {score}% | {rec}</b>\n"
        f"🏢 <b>{escaped_company}</b> — {escaped_title}\n\n"
        f"🏢 <b>Company Overview:</b>\n<i>{esc_html(company_brief)}</i>\n\n"
        f"📋 <b>Role Mission &amp; Responsibilities:</b>\n<i>{esc_html(job_summary)}</i>\n\n"
        f"💬 <b>Recruiter InMail / Connection Note:</b>\n"
        f"<code>{esc_html(note)}</code>\n\n"
        f"<i>Tap the box above to copy note instantly.</i>"
    )

    # Telegram caps document captions at 1024 characters: send the short card
    # with the PDF and push the full brief as a follow-up message.
    caption_ok = len(caption) <= 1000
    regenerate_target = result.get("job_id") or (result.get("job") or {}).get("id")
    inline_keyboard = {
        "inline_keyboard": [
            [{"text": "🚀 Open LinkedIn Job Post", "url": url}],
            [{"text": "🌐 Candidate Portfolio", "url": "https://example.com"}]
        ]
    }
    if regenerate_target:
        inline_keyboard["inline_keyboard"].append(
            [{"text": "🔄 Regenerate with latest resume", "callback_data": f"rgn:{regenerate_target}"}]
        )

    sent = False
    if pdf_path and os.path.exists(pdf_path):
        doc_caption = caption if caption_ok else (
            f"{score_emoji} <b>Match Score: {score}% | {rec}</b>\n"
            f"🏢 <b>{escaped_company}</b> — {escaped_title}\n\n<i>Full brief below.</i>"
        )
        sent = send_document(chat_id, pdf_path, caption=doc_caption, reply_markup=inline_keyboard)

    if not sent:
        send_message(chat_id, caption, reply_markup=inline_keyboard)
    elif not caption_ok:
        send_message(chat_id, caption)

def process_job_request(chat_id: int, job_data: Dict[str, Any]):
    """Explicit request (pasted link / manual text): tailor immediately."""
    title = job_data.get("job_title", "Unknown Role")
    company = job_data.get("company", "Company")

    send_message(
        chat_id,
        f"⚡ <b>Analyzing &amp; Tailoring Resume:</b>\n"
        f"🏢 <b>{esc_html(company)}</b> — <i>{esc_html(title)}</i>\n\n"
        "• Matching authentic experience\n"
        "• Compiling Typst ATS single-page PDF\n"
        "• Drafting recruiter connection note..."
    )

    result = trigger_bridge_tailor(job_data)
    if result.get("status") == "ERROR" or (result.get("error") and not result.get("pdf_path")):
        send_message(
            chat_id,
            f"❌ <b>Could not tailor this role.</b>\n"
            f"🏢 {esc_html(company)} — {esc_html(title)}\n\n"
            f"<code>{esc_html(result.get('error', 'unknown bridge error'))}</code>"
        )
        return

    send_tailored_result(chat_id, job_data, result)

def handle_job_callback(cq: Dict[str, Any]):
    """Approve / Ignore / Regenerate buttons."""
    data = cq.get("data", "")
    action, _, job_ref = data.partition(":")
    message = cq.get("message", {}) or {}
    chat_id = message.get("chat", {}).get("id") or ALLOWED_CHAT_ID
    message_id = message.get("message_id")
    original_text = message.get("text") or ""

    ack = {"ign": "Discarding…", "rgn": "Regenerating…"}.get(action, "Generating…")
    tg_request("answerCallbackQuery", {"callback_query_id": cq.get("id"), "text": ack})

    if action == "ign":
        res = bridge_ignore(job_ref)
        if res.get("status") == "DISCARDED":
            if message_id:
                clear_keyboard(chat_id, message_id, original_text + "\n\n🗑 <b>Dismissed</b> — not tailored.")
            else:
                send_message(chat_id, "🗑 Dismissed.")
        else:
            send_message(chat_id, f"❌ Could not dismiss: {esc_html(res.get('error', 'unknown error'))}")
        return

    regenerate = action == "rgn"

    def run_approval():
        if message_id:
            label = "Regenerating with your latest resume…" if regenerate else "Generating tailored CV…"
            clear_keyboard(chat_id, message_id, original_text + f"\n\n⏳ <b>{label}</b>")
        if regenerate:
            send_message(chat_id, "🔄 <b>Regenerating</b> with your latest uploaded resume…")
        else:
            send_message(chat_id, "⚡ <b>Approved.</b> Tailoring with DeepSeek, compiling the PDF…")

        res = bridge_regenerate(job_ref) if regenerate else bridge_approve(job_ref)
        status = res.get("status")

        if status == "TAILORED":
            send_tailored_result(chat_id, res.get("job") or {}, res)
            if message_id:
                done = "Application regenerated" if regenerate else "Application generated"
                clear_keyboard(chat_id, message_id, original_text + f"\n\n✅ <b>{done}</b>")
        elif status == "ALREADY_APPROVED":
            job = res.get("job") or {}
            caption = (
                f"✅ <b>Already generated</b>\n"
                f"🏢 <b>{esc_html(job.get('company_name') or 'Company')}</b> — {esc_html(job.get('job_title') or 'Role')}\n"
                f"🎯 Match Score: {res.get('match_score', 0)}%\n\n"
                f"💬 <code>{esc_html(res.get('outreach_note', ''))}</code>"
            )
            if res.get("pdf_path") and os.path.exists(res["pdf_path"]):
                send_document(chat_id, res["pdf_path"], caption=caption,
                              reply_markup={"inline_keyboard": [[{
                                  "text": "🔄 Regenerate with latest resume",
                                  "callback_data": f"rgn:{job_ref}"}]]})
            else:
                send_message(chat_id, caption)
        elif status == "DISCARDED":
            send_message(chat_id, "This job was dismissed earlier — tap /hunt to see fresh roles.")
        else:
            send_message(chat_id, f"❌ <b>Tailoring failed.</b>\n<code>{esc_html(res.get('error', 'unknown error'))}</code>")
            if message_id:
                clear_keyboard(chat_id, message_id, original_text + "\n\n⚠️ <b>Generation failed</b> — try again.")

    threading.Thread(target=run_approval, daemon=True).start()

def run_hunt(chat_id: int):
    """Discover + store jobs, then ask for approval. No AI spend until approved."""
    if not HUNT_LOCK.acquire(blocking=False):
        send_message(chat_id, "⏳ A hunt is already running. Review the cards above meanwhile.")
        return
    try:
        jobs = perform_public_search(limit=HUNT_LIMIT)
        if not jobs:
            send_message(
                chat_id,
                "⚠️ No fresh public jobs found right now.\n"
                "<i>Everything recent is already in your pipeline (deduped against PostgreSQL).</i>",
            )
            return

        send_message(chat_id, f"🔎 <b>Found {len(jobs)} candidate role(s).</b>\nChecking against your pipeline…")
        res = bridge_ingest(jobs)
        inserted = res.get("inserted", 0)
        duplicates = res.get("duplicates", 0)
        excluded = res.get("excluded", 0)

        if res.get("status") == "ERROR":
            send_message(chat_id, f"❌ <b>Ingest failed:</b> <code>{esc_html(res.get('error', ''))}</code>")
        elif inserted == 0:
            detail = f"All {duplicates} match(es) were already in your pipeline"
            if excluded:
                detail += f" and {excluded} were filtered out"
            send_message(chat_id, f"ℹ️ {detail} — nothing new to review.")
        else:
            extra = []
            if duplicates:
                extra.append(f"{duplicates} already known")
            if excluded:
                extra.append(f"{excluded} filtered out (nationality/language)")
            suffix = (" · " + " · ".join(extra)) if extra else ""
            send_message(
                chat_id,
                f"✅ <b>{inserted} new role(s) to review</b> above{suffix}.\n"
                "Tap <b>Generate Application</b> to tailor your CV, or <b>Ignore</b> to discard.",
            )
    except Exception as e:
        log(f"Hunt failed: {e}")
        send_message(chat_id, f"❌ <b>Hunt failed:</b> <code>{esc_html(str(e))}</code>")
    finally:
        HUNT_LOCK.release()

def handle_message(msg: Dict[str, Any]):
    chat = msg.get("chat", {})
    chat_id = chat.get("id")
    from_user = msg.get("from", {})
    user_id = from_user.get("id")
    text = (msg.get("text") or "").strip()
    doc = msg.get("document")

    if chat_id != ALLOWED_CHAT_ID and user_id != ALLOWED_CHAT_ID:
        send_message(chat_id, "⛔ <b>Access Denied</b>: This bot is locked to its owner.")
        return

    if doc:
        file_id = doc.get("file_id")
        file_name = doc.get("file_name", "uploaded_resume.pdf")
        download_url = get_file_download_url(file_id)
        if not download_url:
            send_message(chat_id, "❌ Could not retrieve file from Telegram servers.")
            return

        tmp_path = f"/tmp/{file_name}"
        try:
            req = urllib.request.Request(download_url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req) as resp, open(tmp_path, "wb") as out:
                out.write(resp.read())
            process_uploaded_document_or_text(chat_id, tmp_path, is_file=True, filename=file_name)
        except Exception as e:
            send_message(chat_id, f"❌ Download error: {e}")
        return

    if chat_id in WIZARD_SESSIONS:
        # A stale wizard session must not swallow a menu command.
        if text in WIZARD_BYPASS_COMMANDS:
            WIZARD_SESSIONS.pop(chat_id, None)
            send_message(chat_id, "ℹ️ Closed the resume wizard and ran your command instead.")
        elif handle_wizard_input(chat_id, text):
            return

    if text in ("/start", "/help", "❓ /help"):
        help_text = (
            "🤖 <b>Welcome, Alex!</b>\n\n"
            "Your production <b>LinkedIn Job Hunter &amp; ATS Tailoring Bot</b> is active.\n\n"
            "<b>Ways to use:</b>\n"
            "1️⃣ <b>Share or Paste a LinkedIn Job Link:</b>\n"
            "   Just send me <code>https://www.linkedin.com/jobs/view/xxxx/</code>\n"
            "2️⃣ <b>Update or Merge Resumes:</b>\n"
            "   Send a <b>PDF</b> directly or tap <b>/update</b> to merge multiple resumes into one master dossier.\n\n"
            "<b>Quick Menu Options:</b>\n"
            "• 🔍 <b>/hunt</b> — Scan public UAE feeds and send Approve/Ignore cards\n"
            "• ✅ <b>Approve</b> — tailor CV + recruiter note for that role (no AI spent until you approve)\n"
            "• ❌ <b>Ignore</b> — discard a card and keep it out of future hunts\n"
            "• 🔄 <b>Regenerate</b> — re-tailor an application with your latest uploaded resume\n"
            "• 📝 <b>/update</b> — Launch Resume Wizard (Smart Merge or Replace)\n"
            "• 📊 <b>/stats</b> — View total applications &amp; database metrics\n"
            "• 👤 <b>/profile</b> — View active candidate dossier summary"
        )
        send_message(chat_id, help_text, reply_markup=build_main_keyboard())
        return

    elif text in ("/update", "📝 /update"):
        start_update_wizard(chat_id)
        return

    elif text in ("/stats", "📊 /stats"):
        stats = get_bridge_stats()
        text_out = (
            "📊 <b>Pipeline Statistics</b>\n\n"
            f"• <b>Total Discovered Jobs:</b> {stats.get('total_jobs', 0)}\n"
            f"• <b>Tailored Applications:</b> {stats.get('tailored', 0)}\n"
            f"• <b>Average Match Score:</b> {stats.get('avg_score', 0)}%\n\n"
            f"💸 <b>AI spend (DeepSeek)</b>\n"
            f"• Calls: {stats.get('ai_calls', 0)} · {stats.get('ai_tokens', 0):,} tokens\n"
            f"• Today: ${stats.get('ai_cost_today_usd', 0):.4f}\n"
            f"• All time: ${stats.get('ai_cost_total_usd', 0):.4f}\n\n"
            "<i>Storage: PostgreSQL (docker: octopus_postgres)</i>"
        )
        send_message(chat_id, text_out, reply_markup=build_main_keyboard())
        return

    elif text in ("/profile", "👤 /profile"):
        summary = "No profile found."
        if PROFILE_FILE.exists():
            content = PROFILE_FILE.read_text()
            m = re.search(r"## Executive Summary\n(.*?)\n---", content, re.DOTALL)
            if m:
                summary = m.group(1).strip()
        text_out = (
            "👤 <b>Candidate Dossier: Alex Example (Alex)</b>\n\n"
            f"<b>Location:</b> Abu Dhabi, UAE\n"
            f"<b>Headline:</b> Lead Systems Architect &amp; Customer Operations Specialist\n"
            f"<b>Languages:</b> English (Fluent), Arabic (Native), Italian (Professional)\n\n"
            f"<b>Executive Summary:</b>\n<i>{summary}</i>\n\n"
            "<i>Tap /update to merge an additional resume or tweak preferences.</i>"
        )
        send_message(chat_id, text_out, reply_markup=build_main_keyboard())
        return

    elif text in ("/hunt", "🔍 /hunt"):
        send_message(
            chat_id,
            f"🔎 <b>Hunting up to {HUNT_LIMIT} fresh roles across UAE public feeds...</b>\n"
            "Scanning your target focus areas and skipping anything already in the pipeline.",
        )
        threading.Thread(target=run_hunt, args=(chat_id,), daemon=True).start()
        return

    # LinkedIn URL
    if "linkedin.com/jobs" in text or text.isdigit():
        send_message(chat_id, "🔍 <b>LinkedIn URL detected!</b>\nFetching public job details from LinkedIn guest endpoint...")
        job_data = fetch_linkedin_job(text)
        if not job_data:
            send_message(chat_id, "❌ <b>Could not fetch job details.</b>\nThe job post may be closed or private. Try pasting the text directly.")
            return
        process_job_request(chat_id, job_data)
        return

    # Raw Job Text
    if len(text) > 80:
        lines = text.split("\n", 1)
        first_line = lines[0].strip()
        desc = lines[1].strip() if len(lines) > 1 else text

        title = first_line
        company = "Direct Submission"
        if "@" in first_line:
            parts = first_line.split("@", 1)
            title = parts[0].strip()
            company = parts[1].strip()

        job_data = {
            "external_id": f"manual-{int(time.time())}",
            "job_title": title,
            "company": company,
            "location": "Custom Location",
            "workplace_type": "Direct Submission",
            "url": "https://linkedin.com",
            "description": desc
        }
        process_job_request(chat_id, job_data)
        return

    send_message(chat_id, "ℹ️ Send me a LinkedIn Job Link, attach a resume PDF, or tap <b>/update</b>.", reply_markup=build_main_keyboard())

def main():
    log("=" * 60)
    log("Telegram Hunter Bot Starting (Full Briefs & Document Dispatch)...")
    log(f"Listening for User Chat ID: {ALLOWED_CHAT_ID}")
    log("=" * 60)

    tg_request("deleteWebhook", {"drop_pending_updates": False})
    offset = 0
    while True:
        try:
            updates = tg_request("getUpdates", {"offset": offset, "timeout": 25, "limit": 10}, timeout=35)
            if updates.get("ok"):
                for item in updates.get("result", []):
                    offset = max(offset, item["update_id"] + 1)
                    if "message" in item:
                        handle_message(item["message"])
                    elif "callback_query" in item:
                        cq = item["callback_query"]
                        data = cq.get("data", "")
                        if data.startswith(("apv:", "ign:", "rgn:")):
                            handle_job_callback(cq)
                        else:
                            handle_wizard_callback(cq)
            time.sleep(0.5)
        except KeyboardInterrupt:
            break
        except Exception as e:
            log(f"Polling loop exception: {e}")
            time.sleep(3)

if __name__ == "__main__":
    main()
