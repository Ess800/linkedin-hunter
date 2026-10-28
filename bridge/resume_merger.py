"""
Deterministic Resume Merger & Section Extractor.
Zero API quota dependencies — parses sections (Summary, Competencies, Experience, Education, Certifications)
and combines multiple resumes into a single unified master dossier.
"""

import re
from typing import Dict, List, Tuple


def extract_sections(text: str) -> Dict[str, str]:
    """Parse text/markdown into standard resume sections."""
    sections: Dict[str, str] = {
        "summary": "",
        "competencies": "",
        "experience": "",
        "education": "",
        "certifications": "",
        "languages": "",
        "other": ""
    }

    text = text.replace("\r\n", "\n")

    # Regular expressions for section headers
    header_patterns = [
        (r"(?:^|\n)(?:#+\s*|\b)(?:executive\s+summary|summary|profile|professional\s+summary)\b[:\s]*(.*?)(?=\n(?:#+\s*|\b)(?:core\s+competencies|skills|technical\s+skills|experience|work\s+experience|professional\s+experience|education)|$)", "summary"),
        (r"(?:^|\n)(?:#+\s*|\b)(?:core\s+competencies|skills|technical\s+proficiencies|key\s+skills)\b[:\s]*(.*?)(?=\n(?:#+\s*|\b)(?:experience|work\s+experience|professional\s+experience|education|certifications)|$)", "competencies"),
        (r"(?:^|\n)(?:#+\s*|\b)(?:professional\s+experience|work\s+experience|experience|employment\s+history)\b[:\s]*(.*?)(?=\n(?:#+\s*|\b)(?:education|certifications|languages|target\s+role)|$)", "experience"),
        (r"(?:^|\n)(?:#+\s*|\b)(?:education|academic\s+background)\b[:\s]*(.*?)(?=\n(?:#+\s*|\b)(?:certifications|languages|target\s+role)|$)", "education"),
        (r"(?:^|\n)(?:#+\s*|\b)(?:certifications?|credentials?|licenses?)\b[:\s]*(.*?)(?=\n(?:#+\s*|\b)(?:languages|target\s+role)|$)", "certifications"),
        (r"(?:^|\n)(?:#+\s*|\b)(?:languages?)\b[:\s]*(.*?)(?=\n(?:#+\s*|\b)(?:target\s+role|references)|$)", "languages"),
    ]

    for pat, sec_name in header_patterns:
        m = re.search(pat, text, re.DOTALL | re.IGNORECASE)
        if m:
            sections[sec_name] = m.group(1).strip()

    return sections


def merge_profiles(existing_md: str, new_text: str) -> Tuple[str, Dict[str, int]]:
    """
    Intelligently merges a new resume into the existing master profile.
    """
    new_sec = extract_sections(new_text)
    stats = {"new_skills": 0, "new_bullets": 0, "new_entries": 0}

    existing_sec = extract_sections(existing_md)

    # 1. Merge Competencies
    merged_competencies = existing_sec.get("competencies", "")
    if new_sec.get("competencies"):
        new_comp_lines = [l.strip() for l in new_sec["competencies"].split("\n") if l.strip()]
        for line in new_comp_lines:
            clean_line = re.sub(r"^[-*•\s]+", "", line).strip()
            if clean_line and clean_line.lower() not in merged_competencies.lower():
                merged_competencies += f"\n- {clean_line}"
                stats["new_skills"] += 1

    # 2. Merge Experience
    merged_experience = existing_sec.get("experience", "")
    if new_sec.get("experience"):
        # Split into blocks by double newline or company lines
        new_exp_blocks = [b.strip() for b in new_sec["experience"].split("\n\n") if b.strip()]
        for block in new_exp_blocks:
            first_line = block.split("\n")[0]
            company_token = first_line.split("—")[0].split("-")[0].replace("*", "").replace("#", "").strip()
            if company_token and len(company_token) > 3 and company_token.lower() in merged_experience.lower():
                # Merge new bullet points
                lines = block.split("\n")[1:]
                for l in lines:
                    cl = l.strip()
                    if (cl.startswith("-") or cl.startswith("•") or cl.startswith("*")) and cl.lower() not in merged_experience.lower():
                        merged_experience += f"\n- {re.sub(r'^[-*•\s]+', '', cl)}"
                        stats["new_bullets"] += 1
            else:
                # Append brand new company/experience block
                merged_experience += f"\n\n### {block}"
                stats["new_entries"] += 1

    # 3. Merge Certifications
    merged_certs = existing_sec.get("certifications", "")
    if new_sec.get("certifications"):
        for l in new_sec["certifications"].split("\n"):
            cl = re.sub(r"^[-*•\s]+", "", l).strip()
            if cl and cl.lower() not in merged_certs.lower():
                merged_certs += f"\n- {cl}"

    contact_header = """# Alex Example (Alex) — Master Profile Dossier
> **This file is the single source of truth for all ATS tailoring.**
> The pipeline MUST NEVER invent experience, fabricate metrics, or add unearned certifications.
> Tailoring = reframing authentic achievements to match JD keywords.

---

## Contact Information
- **Full Name:** Alex Example
- **Preferred Name:** Alex
- **Email:** alex@example.com
- **Phone:** +1-555-0100
- **Location:** Abu Dhabi, United Arab Emirates
- **Mobility:** UAE Resident Visa, Driving License & Vehicle
- **Portfolio:** https://example.com
- **LinkedIn:** https://www.linkedin.com/in/example/
- **GitHub:** https://github.com/example
"""

    summary = existing_sec.get("summary") or "Customer Operations Lead & Support Automation Builder with 10+ years of high-impact experience spanning priority enterprise operations, medical insurance administration, high-availability cloud infrastructure, and multi-agent AI automation."

    target_roles = """## Target Role Filters
- **Focus Areas:** Customer Operations, Health Insurance Ops, Systems Automation, Process Optimization, AI/ML Operations, Solutions Architecture
- **Target Locations:** Abu Dhabi, Dubai, UAE, GCC, Remote
- **Seniority:** Mid-Senior, Associate Director, Lead
- **Workplace:** Remote, Hybrid, On-site (UAE preferred)
"""
    m_roles = re.search(r"(## Target Role Filters\n.*)", existing_md, re.DOTALL)
    if m_roles:
        target_roles = m_roles.group(1).strip()

    final_md = f"""{contact_header}
---

## Executive Summary
{summary}

---

## Core Competencies
{merged_competencies.strip()}

---

## Professional Experience
{merged_experience.strip()}

---

## Education
{existing_sec.get('education', '- **Bachelor of Law (LL.B.), General Law** — Cairo University, 2010').strip()}

## Certifications
{merged_certs.strip()}

## Languages
| Language | Proficiency |
|----------|-------------|
| English  | Fluent / Full Professional |
| Arabic   | Native |
| Italian  | Working Professional |

---

{target_roles}
"""
    return final_md, stats
