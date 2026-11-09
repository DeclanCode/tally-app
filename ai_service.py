import json
import os
import re
import time
import requests

FALLBACK_MODELS = [
    "gemini-3.6-flash",
    "gemini-3.1-flash-lite",
    "gemini-3.7-flash",
    "gemini-3.8-flash",
    "gemini-3.5-flash",
    "gemini-3-flash-preview",
    "gemini-omni-1.1-flash",
    "gemini-3.1-pro-preview",
    "gemma-4-31b-it"
]


def _extract_json(text):
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return json.loads(text)


def call_gemini_json(prompt, api_key):
    last_err = None
    for attempt in range(2):
        if attempt > 0:
            time.sleep(5)
        for model in FALLBACK_MODELS:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
            gen_cfg = {"temperature": 0.2}
            if not model.startswith("gemma"):
                gen_cfg["responseMimeType"] = "application/json"
            payload = {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": gen_cfg
            }
            try:
                resp = requests.post(url, json=payload, timeout=75)
                if resp.status_code == 200:
                    data = resp.json()
                    text = data["candidates"][0]["content"]["parts"][0]["text"]
                    return _extract_json(text)
                else:
                    last_err = f"{model} returned HTTP {resp.status_code}: {resp.text[:200]}"
            except Exception as e:
                last_err = f"{model} exception: {e}"
    raise RuntimeError(f"All Gemini models failed. Last error: {last_err}")


def fetch_job_page_text(url):
    """
    Scrapes the text of a job posting URL (supports Ashby, PeopleForce, Greenhouse, Lever, standard career pages).
    """
    if not url or not url.startswith("http"):
        return ""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    }
    try:
        r = requests.get(url, headers=headers, timeout=15)
        html_text = r.text
        # Special handling for Ashby embedded JSON
        m = re.search(r"window\.__appData\s*=\s*(\{.*?\});", html_text)
        if m:
            try:
                data = json.loads(m.group(1))
                posting = data.get("posting") or {}
                title = posting.get("title") or ""
                desc = posting.get("descriptionPlainText") or posting.get("descriptionHtml") or ""
                desc_clean = re.sub(r"\s+", " ", re.sub(r"<[^<]+?>", " ", desc)).strip()
                return f"Job Title: {title}\n{desc_clean}"[:8000]
            except Exception:
                pass
        cleaned = re.sub(r"<style.*?>.*?</style>", " ", html_text, flags=re.S)
        cleaned = re.sub(r"<script.*?>.*?</script>", " ", cleaned, flags=re.S)
        cleaned = re.sub(r"<[^<]+?>", " ", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        return cleaned[:8000]
    except Exception as e:
        return f"[Could not automatically scrape URL ({e}). Please paste job description text if needed.]"


def build_profile_from_notes_or_cv(raw_text, current_profile, api_key):
    """
    Takes rough user notes (or extracted text from an old CV PDF) and structures it into
    our clean 1-page Master CV schema + recommended search keywords.
    """
    prompt = f"""You are an elite executive career coach and CV architect.
Convert the candidate's raw career notes or old CV text below into a crisp, high-impact 1-page Master CV JSON profile and job search configuration.

Current Profile Context (if any):
{json.dumps(current_profile, indent=2)}

Candidate's Raw Input / Notes / CV Text:
{raw_text}

Rules:
1. NEVER use em-dashes (`—`) anywhere in the output. Use standard commas or parentheses.
2. Start every single bullet point with a short 2-4 word boldable lead-in phrase followed by a colon (e.g. `Concept-to-CAD Facade Engineering: Translated 2D artist impressions...`).
3. Keep `headline` to a clean uppercase role title banner ONLY (no pipe-separated tool lists under the name).
4. Return JSON in this exact structure:
{{
  "label": "<Name & Short Specialty Label>",
  "target_roles": ["<Role 1>", "<Role 2>", "<Role 3>", "<Role 4>"],
  "search_keywords": ["<kw1>", "<kw2>", "<kw3>", "<kw4>", "<kw5>"],
  "location_preference": "<e.g. New Zealand (Remote / Hybrid / Global)>",
  "cv_data": {{
    "full_name": "<Candidate Full Name>",
    "headline": "<CLEAN UPPERCASE ROLE TITLE>",
    "email": "<email>",
    "phone": "<phone>",
    "location": "<City, Country>",
    "linkedin": "<linkedin url or empty>",
    "portfolio": "<portfolio url or empty>",
    "summary": "<3-4 punchy sentences>",
    "experience": [
      {{
        "company": "<Company Name>",
        "location": "<City>",
        "roles": [
          {{
            "title": "<Role Title>",
            "dates": "<Date Range>",
            "location": "<City>",
            "bullets": [
              "<Lead-in Phrase: High impact achievement bullet...>",
              "<Lead-in Phrase: High impact achievement bullet...>"
            ]
          }}
        ]
      }}
    ],
    "technology": "<Comma-separated tools, software, machines, languages>",
    "skills": "<Comma-separated core competencies>",
    "certifications": "<Comma-separated certifications or highlights>",
    "interests": "<Comma-separated interests>",
    "education": [
      {{
        "institution": "<Institution>",
        "degree": "<Degree / Qualification>",
        "location": "<City>"
      }}
    ]
  }}
}}
"""
    return call_gemini_json(prompt, api_key)


def tailor_cv_and_cl_for_job(user_profile, job_title, company_name, job_description, custom_notes, api_key):
    """
    Tailors ANY user's Master CV (`user_profile["cv_data"]`) to a target job description + custom user instructions.
    Returns:
      - `cv_data`: tailored CV dict ready for `generate_cv_pdf`
      - `cover_letter_paragraphs`: list of 4-5 concise paragraphs (~230 words total, zero em-dashes)
      - `short_pitch`: conversational ~130-word "Why I'm a great fit" blurb for startup application textboxes
      - `match_analysis`: summary of key alignments & salary/application tips
    """
    base_cv = user_profile.get("cv_data") or {}
    prompt = f"""You are an expert career strategist tailoring {base_cv.get('full_name', 'the candidate')}'s Master CV and Cover Letter for a specific job application.

Target Job Title: {job_title or 'Infer from job description'}
Target Company: {company_name or 'Infer from job description'}
User's Special Instructions / Highlights to Emphasize:
{custom_notes or 'None provided — highlight the most relevant experience, tools, and industry overlap.'}

Target Job Description:
{job_description[:6500]}

Candidate's Master CV Data:
{json.dumps(base_cv, indent=2)}

Strict Rules:
1. NEVER use em-dashes (`—`) anywhere in the CV, Cover Letter, or Short Pitch! People view em-dashes as an AI telltale. Use standard commas, parentheses, or full stops instead.
2. Stay truthful to the candidate's real companies, dates, and background, while strategically reordering and framing the `headline`, `summary`, role `bullets`, `technology` stack order, and `skills` to mirror the exact priorities and terminology of the target job.
3. Keep `headline` to a clean uppercase target role title ONLY (do NOT append a tool list to the headline).
4. Start EVERY experience bullet with a short 2-4 word boldable lead-in theme followed by a colon (e.g. `Gaming & Lotteries BI Reporting: Designed and delivered...`).
5. Keep the CV concise so it fits cleanly on 1 page (keep total bullets across all roles roughly the same as the Master CV).
6. Write a concise, natural 4-paragraph Cover Letter (~220-250 words total, `cover_letter_paragraphs`):
   - Paragraph 1: `"Dear <Company> Hiring Team,"`
   - Paragraph 2: Excitement for the role and company mission + core years of experience and domain alignment.
   - Paragraph 3: Specific, concrete achievements from their most recent roles that directly prove they can solve this company's problems.
   - Paragraph 4: Remote/team collaboration style, communication, and why they'd be a great fit. (ONLY if the company is clearly a recruitment/staffing agency, include a line offering to stay on their books for other similar roles).
   - Paragraph 5: `"Thank you very much for considering my application. I look forward to discussing this opportunity further."`
7. Also write a `short_pitch` (~120-145 words): a warm, conversational, numbered 3-point blurb ("Hey <Company> team! Three quick reasons why this role jumped out at me: 1... 2... 3...") for applications that have a "Tell us why you're a good fit" textbox instead of a formal cover letter upload.

Return JSON in this exact schema:
{{
  "inferred_job_title": "<Clean Job Title>",
  "inferred_company": "<Clean Company Name>",
  "match_score": <int 0-100>,
  "match_summary": "<2-3 sentences on why this role fits and what was tailored>",
  "cv_data": {{
    "full_name": "{base_cv.get('full_name', '')}",
    "headline": "<CLEAN TARGET ROLE TITLE ONLY>",
    "email": "{base_cv.get('email', '')}",
    "phone": "{base_cv.get('phone', '')}",
    "location": "{base_cv.get('location', '')}",
    "linkedin": "{base_cv.get('linkedin', '')}",
    "portfolio": "{base_cv.get('portfolio', '')}",
    "summary": "<Tailored 3-4 sentence summary>",
    "experience": <Tailored experience array matching the exact schema of Master CV>,
    "technology": "<Reordered comma-separated technology stack putting target tools first>",
    "skills": "<Tailored comma-separated core competencies>",
    "certifications": "{base_cv.get('certifications', '')}",
    "interests": "{base_cv.get('interests', '')}",
    "education": {json.dumps(base_cv.get('education', []))}
  }},
  "cover_letter_paragraphs": [
    "Dear <Company> Hiring Team,",
    "<Body Para 1>",
    "<Body Para 2>",
    "<Body Para 3>",
    "Thank you very much for considering my application. I look forward to discussing this opportunity further."
  ],
  "short_pitch": "<Conversational 3-point pitch for application form textboxes>"
}}
"""
    return call_gemini_json(prompt, api_key)


def fetch_jobs_for_profile(user_profile):
    """
    Fetches candidate jobs across Himalayas, Remotive, and Freehire tailored to the user's `search_keywords` and categories.
    """
    headers = {"User-Agent": "RoleCraft/1.0 (AI Job Radar)"}
    keywords = user_profile.get("search_keywords") or ["data analyst", "power bi"]
    remotive_cats = user_profile.get("remotive_categories") or ["data"]
    freehire_cats = user_profile.get("freehire_categories") or ["data_analytics"]

    candidates = []
    seen_ids = set()

    # 1. Himalayas Search API (Supports any profession/keyword!)
    for kw in keywords[:6]:
        for params in [{"q": kw, "worldwide": "true"}, {"q": kw, "country": "NZ"}]:
            try:
                resp = requests.get("https://himalayas.app/jobs/api/search", params=params, headers=headers, timeout=12)
                if resp.status_code != 200:
                    continue
                for hj in (resp.json().get("jobs") or [])[:15]:
                    guid = hj.get("guid") or hj.get("applicationLink")
                    if not guid or guid in seen_ids:
                        continue
                    locs = hj.get("locationRestrictions") or []
                    locs_lower = [str(l).lower() for l in locs]
                    if locs_lower and "new zealand" not in locs_lower and "nz" not in locs_lower and "worldwide" not in locs_lower:
                        continue

                    seen_ids.add(guid)
                    desc_html = hj.get("description") or hj.get("excerpt") or ""
                    desc_clean = re.sub(r"<[^<]+?>", " ", desc_html)
                    desc_clean = re.sub(r"\s+", " ", desc_clean).strip()
                    loc_label = "Worldwide (Himalayas)" if not locs else (
                        "NZ / Global (Himalayas)" if len(locs) > 3 else f"{', '.join(locs)} (Himalayas)"
                    )
                    candidates.append({
                        "id": f"him_{re.sub(r'[^A-Za-z0-9]+', '_', guid)[-45:]}",
                        "title": hj.get("title") or "Untitled Role",
                        "company": hj.get("companyName") or "Employer",
                        "location": loc_label,
                        "url": hj.get("applicationLink") or guid,
                        "source": "Himalayas",
                        "employment_type": hj.get("employmentType") or "Full Time",
                        "description": desc_clean[:3000]
                    })
            except Exception:
                pass

    # 2. Remotive API
    for r_cat in remotive_cats:
        try:
            resp = requests.get("https://remotive.com/api/remote-jobs", params={"category": r_cat}, headers=headers, timeout=12)
            if resp.status_code == 200:
                for rj in (resp.json().get("jobs") or [])[:30]:
                    jid = f"rem_{rj.get('id')}"
                    if jid in seen_ids:
                        continue
                    loc = (rj.get("candidate_required_location") or "Worldwide").strip()
                    if not any(k in loc.lower() for k in ["worldwide", "anywhere", "global", "new zealand", "apac", "oceania"]):
                        continue
                    seen_ids.add(jid)
                    desc_clean = re.sub(r"<[^<]+?>", " ", rj.get("description") or "")
                    desc_clean = re.sub(r"\s+", " ", desc_clean).strip()
                    candidates.append({
                        "id": jid,
                        "title": rj.get("title") or "Untitled Role",
                        "company": rj.get("company_name") or "Employer",
                        "location": f"{loc} (Remotive)",
                        "url": rj.get("url"),
                        "source": "Remotive",
                        "employment_type": (rj.get("job_type") or "Full Time").replace("_", " ").title(),
                        "description": desc_clean[:3000]
                    })
        except Exception:
            pass

    # 3. Freehire API
    try:
        params = [("limit", "25"), ("offset", "0"), ("work_mode", "remote"), ("regions", "global"), ("regions", "apac")]
        for c in freehire_cats:
            params.append(("category", c))
        resp = requests.get("https://freehire.me/api/v1/jobs/search", params=params, headers=headers, timeout=12)
        if resp.status_code == 200:
            for fj in (resp.json().get("data") or []):
                jid = fj.get("external_id") or fj.get("public_slug")
                if not jid or jid in seen_ids:
                    continue
                countries = [c.lower() for c in (fj.get("countries") or [])]
                if countries and "nz" not in countries and "new zealand" not in countries:
                    continue
                seen_ids.add(jid)
                candidates.append({
                    "id": str(jid),
                    "title": fj.get("title") or "Untitled Role",
                    "company": fj.get("company") or "Employer",
                    "location": fj.get("location") or "Remote (Freehire)",
                    "url": fj.get("url") or f"https://freehire.me/job/{fj.get('public_slug')}",
                    "source": "Freehire",
                    "employment_type": "Remote",
                    "description": (fj.get("description") or "")[:3000]
                })
    except Exception:
        pass

    return candidates


def rank_radar_jobs_for_profile(user_profile, candidates, api_key):
    """
    Scores & ranks fetched candidate jobs for the specific user's profile (works for Data/BI OR 3D CAD/CNC OR any profession).
    """
    if not candidates:
        return []

    batch = candidates[:30]
    payload = [
        {
            "index": i,
            "title": c["title"],
            "company": c["company"],
            "location": c["location"],
            "source": c["source"],
            "description_excerpt": c["description"][:1500]
        }
        for i, c in enumerate(batch)
    ]

    cv_summary = {
        "headline": user_profile["cv_data"].get("headline"),
        "summary": user_profile["cv_data"].get("summary"),
        "technology": user_profile["cv_data"].get("technology"),
        "skills": user_profile["cv_data"].get("skills"),
        "target_roles": user_profile.get("target_roles")
    }

    prompt = f"""You are an AI Career Radar scoring job listings for {user_profile['cv_data'].get('full_name')}.

Candidate Profile:
{json.dumps(cv_summary, indent=2)}

Location Preference: {user_profile.get('location_preference', 'New Zealand / Remote')}

Task:
Evaluate each job below and return:
- `nz_eligible` (bool): True if open to New Zealand, APAC, Global/Worldwide, or flexible remote contractors. False only if strictly locked to US-only W2 or UK-only.
- `is_relatable` (bool): True if the role relates to the candidate's target roles, tools, or transferable skills (score >= 40).
- `fit_score` (int 0-100): How well it matches.
- `reason` (1-2 concise sentences): Why it fits or how the candidate should angle their application.

Return JSON:
{{
  "evaluations": [
    {{
      "index": <int>,
      "nz_eligible": <bool>,
      "is_relatable": <bool>,
      "fit_score": <int>,
      "reason": "<string>"
    }}
  ]
}}

Candidate Jobs:
{json.dumps(payload, indent=2)}
"""
    try:
        res = call_gemini_json(prompt, api_key)
        evals = {e["index"]: e for e in res.get("evaluations", []) if isinstance(e, dict) and "index" in e}
        results = []
        for idx, item in enumerate(batch):
            ev = evals.get(idx)
            if not ev:
                continue
            if ev.get("nz_eligible", True) and ev.get("is_relatable", False) and ev.get("fit_score", 0) >= 40:
                enriched = dict(item)
                enriched["score"] = ev.get("fit_score", 50)
                enriched["reason"] = ev.get("reason", "")
                results.append(enriched)
        results.sort(key=lambda x: x["score"], reverse=True)
        return results
    except Exception as e:
        # Rule-based fallback if Gemini is temporarily busy
        kws = [k.lower() for k in (user_profile.get("search_keywords") or [])]
        results = []
        for item in batch:
            text = f"{item['title']} {item['description']}".lower()
            hits = [k for k in kws if k in text]
            score = min(50 + len(hits) * 10, 92)
            enriched = dict(item)
            enriched["score"] = score
            enriched["reason"] = f"Matches profile keywords ({', '.join(hits[:4]) or 'Remote fit'})."
            results.append(enriched)
        results.sort(key=lambda x: x["score"], reverse=True)
        return results
