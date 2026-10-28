// ──────────────────────────────────────────────────────
// ATS-Optimized Resume Template — linkedin-hunter pipeline
// Single column, no tables, no images: parser-friendly first,
// typographically clean second.
// ──────────────────────────────────────────────────────

// ── Data Injection (populated by bridge/job_worker.py) ──
#let candidate_name     = "CANDIDATE_NAME"
#let candidate_headline = "CANDIDATE_HEADLINE"
#let candidate_email    = "CANDIDATE_EMAIL"
#let candidate_phone    = "CANDIDATE_PHONE"
#let candidate_location = "CANDIDATE_LOCATION"
#let candidate_web      = "CANDIDATE_WEB"
#let candidate_linkedin = "CANDIDATE_LINKEDIN"
#let candidate_github   = "CANDIDATE_GITHUB"
#let executive_summary  = "EXECUTIVE_SUMMARY"

// Experience entries: list of dicts
// Each: (company: "", title: "", period: "", location: "", bullets: ())
#let experience = ()

// Education entries: list of dicts
// Each: (institution: "", degree: "", year: "")
#let education = ()

// Certifications: list of strings
#let certifications = ()

// Skills blocks: list of dicts
// Each: (category: "", items: "comma, separated, string")
#let skills = ()

// Languages: list of dicts
// Each: (language: "", proficiency: "")
#let languages = ()

// ── Palette ──
#let ink    = rgb("#111827")
#let accent = rgb("#1f3a5f")
#let muted  = rgb("#4b5563")
#let rule   = rgb("#9ca3af")

// ── Page Setup ──
#set page(
  paper: "us-letter",
  margin: (top: 0.42in, bottom: 0.42in, left: 0.55in, right: 0.55in),
  fill: white,
)

#set text(
  font: ("Libertinus Serif", "DejaVu Serif"),
  size: 10pt,
  lang: "en",
  fill: ink,
  hyphenate: false, // ATS-safe: no split words
)

// Ragged-right reads better than justified once hyphenation is off.
#set par(leading: 0.52em, justify: false, spacing: 0.6em)

// ── Headings ──
#show heading.where(level: 2): it => {
  v(7pt)
  set text(size: 10.2pt, weight: "bold", fill: accent, tracking: 0.6pt)
  upper(it.body)
  v(1.5pt)
  line(length: 100%, stroke: 0.7pt + rule)
  v(3pt)
}

// ── Helpers ──
#let sep = text(fill: muted)[ #sym.bar.v ]
#let bullet_line(body) = block(
  inset: (left: 9pt),
  above: 1.1pt,
  below: 0pt,
  width: 100%,
)[
  #set par(hanging-indent: 8pt, justify: false, leading: 0.5em)
  • #body
]

#let experience_entry(company, title, period, location, bullets) = block(
  breakable: false,
  above: 0pt,
  below: 3.5pt,
  width: 100%,
)[
  #grid(
    columns: (1fr, auto),
    align: (left + horizon, right + horizon),
    column-gutter: 8pt,
    [#text(weight: "bold", size: 10.3pt)[#company]
     #if location != "" { text(size: 9.2pt, fill: muted)[ — #location] }],
    text(size: 9.2pt, style: "italic", fill: muted)[#period],
  )
  #v(0.6pt)
  #text(style: "italic", size: 9.8pt)[#title]
  #v(1.6pt)
  #for b in bullets { bullet_line(b) }
]

#let education_entry(institution, degree, year) = block(
  breakable: false,
  above: 0pt,
  below: 2pt,
  width: 100%,
)[
  #grid(
    columns: (1fr, auto),
    align: (left + horizon, right + horizon),
    column-gutter: 8pt,
    text(weight: "bold", size: 9.9pt)[#degree],
    text(size: 9.2pt, style: "italic", fill: muted)[#year],
  )
  #text(size: 9.6pt)[#institution]
]

// ═══════════════════════════════════════
// RESUME BODY
// ═══════════════════════════════════════

// ── Header ──
#align(center)[
  #text(size: 20pt, weight: "bold", tracking: 0.3pt)[#candidate_name]
  #v(1pt)
  #text(size: 10.4pt, fill: accent)[#candidate_headline]
  #v(3pt)
  #text(size: 9pt, fill: muted)[
    #candidate_location #sep #candidate_email #sep #candidate_phone #sep
    #candidate_web #sep #candidate_linkedin
  ]
]

// ── Summary ──
#heading(level: 2)[Professional Summary]
#text(size: 9.8pt)[#executive_summary]

// ── Experience (before skills: senior candidates lead with impact) ──
#if experience.len() > 0 {
  heading(level: 2)[Professional Experience]
  for exp in experience {
    experience_entry(exp.company, exp.title, exp.period, exp.location, exp.bullets)
  }
}

// ── Core Competencies ──
#if skills.len() > 0 {
  heading(level: 2)[Core Competencies]
  for skill_block in skills {
    block(above: 0pt, below: 1.2pt, width: 100%)[
      #text(weight: "bold", size: 9.6pt, fill: accent)[#skill_block.category:] #text(size: 9.6pt)[#skill_block.items]
    ]
  }
}

// ── Education & Certifications ──
#if education.len() > 0 or certifications.len() > 0 {
  heading(level: 2)[Education & Certifications]
  for edu in education {
    education_entry(edu.institution, edu.degree, edu.year)
  }
  if certifications.len() > 0 {
    v(1.5pt)
    for cert in certifications {
      bullet_line(text(size: 9.5pt)[#cert])
    }
  }
}

// ── Languages ──
#if languages.len() > 0 {
  heading(level: 2)[Languages]
  let langs = languages.map(l => [#text(weight: "bold", size: 9.6pt)[#l.language:] #l.proficiency]).join(", ")
  text(size: 9.6pt)[#langs]
}
