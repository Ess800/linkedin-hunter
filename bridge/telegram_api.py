#!/usr/bin/env python3
"""
Shared Telegram transport for the LinkedIn Hunter bot and bridge worker.

Both processes talk to the same bot token, so the poller (telegram_bot.py) is
the only one that reads updates; everything else just sends. Keeping the
transport here means review cards and tailored results are formatted by the
same code no matter which process triggered them.
"""

import json
import os
import time
import urllib.error
import urllib.request
from typing import Any, Dict, Optional

from env_loader import load_project_env

load_project_env()

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = int(os.environ.get("TELEGRAM_CHAT_ID", "0") or 0)
TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}" if BOT_TOKEN else ""

if not BOT_TOKEN:
    print("[TG_API] TELEGRAM_BOT_TOKEN is not set — no messages can be sent.", flush=True)


def log(msg: str):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] [TG_API] {msg}", flush=True)


def tg_request(endpoint: str, data: Optional[Dict[str, Any]] = None, timeout: int = 40) -> Dict[str, Any]:
    url = f"{TELEGRAM_API}/{endpoint}"
    if data is not None:
        payload = json.dumps(data).encode("utf-8")
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"}, method="POST")
    else:
        req = urllib.request.Request(url)

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="ignore")
        log(f"API Error ({endpoint}): HTTP {e.code} - {err_body}")
        return {"ok": False, "error": f"HTTP {e.code}: {err_body}"}
    except Exception as e:
        log(f"API Error ({endpoint}): {e}")
        return {"ok": False, "error": str(e)}


def send_message(chat_id: int, text: str, reply_markup: Optional[Dict[str, Any]] = None, parse_mode: str = "HTML") -> Optional[int]:
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup

    res = tg_request("sendMessage", payload)
    if res.get("ok"):
        return res["result"]["message_id"]
    return None


def edit_message(chat_id: int, message_id: int, text: str, reply_markup: Optional[Dict[str, Any]] = None) -> bool:
    payload = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    return tg_request("editMessageText", payload).get("ok", False)


def clear_keyboard(chat_id: int, message_id: int, text: str) -> bool:
    """Rewrite a card and drop its buttons (used once a job is handled)."""
    return edit_message(chat_id, message_id, text, reply_markup={"inline_keyboard": []})


def send_document(chat_id: int, file_path: str, caption: str, reply_markup: Optional[Dict[str, Any]] = None) -> bool:
    if not os.path.exists(file_path):
        log(f"File not found: {file_path}")
        return False

    boundary = "----WebKitFormBoundaryTelegramUpload"
    url = f"{TELEGRAM_API}/sendDocument"

    with open(file_path, "rb") as f:
        file_bytes = f.read()

    filename = os.path.basename(file_path)

    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="chat_id"\r\n\r\n{chat_id}\r\n'
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="caption"\r\n\r\n{caption}\r\n'
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="parse_mode"\r\n\r\nHTML\r\n'
    )
    if reply_markup:
        body += (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="reply_markup"\r\n\r\n{json.dumps(reply_markup)}\r\n'
        )

    body_bytes = (
        body.encode("utf-8")
        + f'--{boundary}\r\nContent-Disposition: form-data; name="document"; filename="{filename}"\r\nContent-Type: application/pdf\r\n\r\n'.encode("utf-8")
        + file_bytes
        + f"\r\n--{boundary}--\r\n".encode("utf-8")
    )

    req = urllib.request.Request(
        url,
        data=body_bytes,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST"
    )

    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("ok", False)
    except urllib.error.HTTPError as e:
        log(f"Upload error: HTTP {e.code} - {e.read().decode('utf-8', errors='ignore')}")
        return False
    except Exception as e:
        log(f"Upload error: {e}")
        return False


def get_file_download_url(file_id: str) -> Optional[str]:
    res = tg_request("getFile", {"file_id": file_id})
    if res.get("ok"):
        file_path = res["result"].get("file_path")
        return f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}"
    return None


def esc_html(value: Any) -> str:
    return (str(value or "")
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;"))


def review_card(job: Dict[str, Any], description_chars: int = 900) -> tuple:
    """Build the preview + Approve/Ignore keyboard for a newly discovered job."""
    from job_filters import explain_role

    job_id = job.get("id") or job.get("job_id")
    title = esc_html(job.get("job_title"))
    company = esc_html(job.get("company_name") or job.get("company"))
    location = esc_html(job.get("location") or "Not specified")
    workplace = esc_html(job.get("workplace_type") or "")
    source = esc_html(job.get("source") or "linkedin_public")
    url = job.get("job_url") or job.get("url") or ""

    details = f"📍 {location}"
    if workplace:
        details += f" · {workplace}"
    details += f"\n🗂 Source: {source}"

    brief = explain_role(job)
    blocks = [
        f"🆕 <b>{title}</b>\n"
        f"🏢 <b>{company}</b>\n"
        f"{details}"
    ]

    if brief["summary"]:
        blocks.append(f"🧭 <b>Role snapshot</b>\n<i>{esc_html(brief['summary'])}</i>")

    if brief["responsibilities"]:
        bullets = "\n".join(f"• {esc_html(s)}" for s in brief["responsibilities"])
        blocks.append(f"✅ <b>What you'd do</b>\n{bullets}")

    if brief["requirements"]:
        bullets = "\n".join(f"• {esc_html(s)}" for s in brief["requirements"])
        blocks.append(f"🎯 <b>Key requirements</b>\n{bullets}")

    if not (brief["summary"] or brief["responsibilities"] or brief["requirements"]):
        description = " ".join((job.get("raw_description") or job.get("description") or "").split())
        if len(description) > description_chars:
            description = description[:description_chars].rstrip() + "…"
        blocks.append(f"<b>Job description</b>\n{esc_html(description or 'No description captured from the public listing.')}")

    blocks.append("<i>Approve to tailor your CV + recruiter note for this role. Ignore to discard it.</i>")
    text = "\n\n".join(blocks)

    # Telegram rejects messages over 4096 characters.
    if len(text) > 3900:
        text = text[:3870].rstrip() + "…\n\n<i>Open the posting for the full text.</i>"

    keyboard = {"inline_keyboard": [[
        {"text": "✅ Generate Application", "callback_data": f"apv:{job_id}"},
        {"text": "❌ Ignore", "callback_data": f"ign:{job_id}"},
    ]]}
    if url:
        keyboard["inline_keyboard"].append([{"text": "🔗 Open original posting", "url": url}])

    return text, keyboard


def send_review_card(job: Dict[str, Any], chat_id: Optional[int] = None) -> bool:
    text, keyboard = review_card(job)
    return send_message(chat_id or CHAT_ID, text, reply_markup=keyboard) is not None
