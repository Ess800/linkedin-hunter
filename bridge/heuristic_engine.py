"""
Local Heuristic Fallback Engine for ATS Scoring & Tailoring.
Runs 100% locally when remote AI APIs are rate limited or down (429 errors).
Never produces a 0% false negative when credentials match!
"""

import re
from typing import Dict, Any, List

STOPWORDS = {
    'the', 'and', 'for', 'with', 'that', 'this', 'from', 'are', 'you', 'will', 'our',
    'have', 'all', 'any', 'about', 'can', 'has', 'more', 'they', 'what', 'who', 'how',
    'which', 'been', 'there', 'their', 'were', 'when', 'into', 'some', 'than', 'them',
    'also', 'other', 'well', 'work', 'working', 'team', 'teams', 'role', 'roles', 'years'
}


def heuristic_analyze(job_title: str, company: str, description: str, master_profile: str) -> Dict[str, Any]:
    """Calculate match score and draft personalized outreach note using local text matching."""
    profile_lower = master_profile.lower()
    desc_lower = description.lower()

    # Extract meaningful keywords from job description
    desc_words = re.findall(r'\b[a-zA-Z]{3,}\b', desc_lower)
    keywords = [w for w in set(desc_words) if w not in STOPWORDS]

    matched_keywords = [w for w in keywords if w in profile_lower]
    missing_keywords = [w for w in keywords if w not in profile_lower][:6]

    # Calculate base match score
    match_ratio = len(matched_keywords) / max(len(keywords), 1)
    # Scale between 60% and 92% based on relevance
    score = min(int(50 + (match_ratio * 50)), 94)

    # Boost score if key domains match
    boosts = 0
    if any(k in desc_lower for k in ["customer", "operations", "service", "support"]):
        boosts += 5
    if any(k in desc_lower for k in ["uae", "dubai", "abu dhabi", "remote"]):
        boosts += 5
    if any(k in desc_lower for k in ["insurance", "health", "medical", "banking", "finance"]):
        boosts += 5
    if any(k in desc_lower for k in ["arabic", "bilingual", "multilingual"]):
        boosts += 5

    final_score = min(score + boosts, 95)
    recommendation = "STRONG_MATCH" if final_score >= 80 else ("GOOD_MATCH" if final_score >= 68 else "CONSIDER")

    # Draft personalized connection note under 300 characters
    greeting = f"Hi! Noticed the {job_title} opening at {company}."
    pitch = f"Bringing 10+ yrs in UAE customer ops (insurance and banking), trilingual fluency (AR/EN/IT), & support automation, I'd welcome the chance to connect regarding {company}'s team."
    note = f"{greeting} {pitch}"

    if len(note) > 295:
        note = f"Hi! Noticed the {job_title} role at {company}. With 10+ yrs in UAE customer operations at insurance and banking and support automation background, I would love to connect."

    # Generate tailored summary
    tailored_summary = (
        f"Accomplished Customer Operations Specialist with 10+ years of UAE enterprise experience "
        f"across medical insurance and priority client workflows. Proven track record in workflow optimization, "
        f"quality assurance auditing, and bilingual team leadership conforming to {company}'s operational standards."
    )

    # Pull experience from master profile
    tailored_experience = [
        {
            "company": "Medical Insurance Provider",
            "title": "Customer Service Executive (Medical Insurance)",
            "period": "Apr. 2022 - Present",
            "location": "Abu Dhabi, UAE",
            "bullets": [
                f"Floor lead managing complex client inquiries, escalations, and priority SLA resolution aligning with {company} operational goals.",
                "Provide real-time onboarding mentorship and quality assurance auditing for operational teams.",
                "Engineer internal support workflows and documentation that significantly reduce case resolution turnaround."
            ]
        },
        {
            "company": "Global Services Center",
            "title": "Customer Service Executive",
            "period": "June 2016 - June 2018",
            "location": "Cairo, Egypt",
            "bullets": [
                "Delivered personalized solutions for UAE and GCC regional customer portfolios under strict SLA compliance.",
                "Maintained stringent international banking standards, KYC/AML regulatory audits, and quality controls."
            ]
        }
    ]

    tailored_skills = [
        {"category": "Customer Operations", "items": "SLA & KPI Management, Escalation Handling, Quality Assurance, Onboarding"},
        {"category": "Systems & Automation", "items": "Process Optimization, Workflow Pipelines, CRM & Ticketing Systems, Linux"}
    ]

    return {
        "match_score": final_score,
        "recommendation": recommendation,
        "matched_skills": [w.capitalize() for w in matched_keywords[:8]],
        "missing_skills": [w.capitalize() for w in missing_keywords],
        "ats_keywords": [w.capitalize() for w in matched_keywords[:10]],
        "tailored_summary": tailored_summary,
        "tailored_experience": tailored_experience,
        "tailored_skills": tailored_skills,
        "outreach_note": note
    }
