import base64
import io
import json
import os
import re
import time
import uuid
import requests
from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import Optional, List, Dict, Any

from seed_profiles import DEFAULT_USERS
from pdf_engine import generate_cv_pdf, generate_cover_letter_pdf, clean_filename
from ai_service import (
    fetch_job_page_text,
    build_profile_from_notes_or_cv,
    tailor_cv_and_cl_for_job,
    fetch_jobs_for_profile,
    rank_radar_jobs_for_profile
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
OUTPUT_DIR = os.path.join(BASE_DIR, "generated_pdfs")
USERS_FILE = os.path.join(DATA_DIR, "users.json")
HISTORY_FILE = os.path.join(DATA_DIR, "tailored_history.json")
RADAR_CACHE_FILE = os.path.join(DATA_DIR, "radar_cache.json")

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)

def _load_local_gemini_key() -> str:
    env_key = os.getenv("GEMINI_API_KEY", "").strip()
    if env_key:
        return env_key
    local_key_file = os.path.join(BASE_DIR, ".env.key")
    if os.path.exists(local_key_file):
        with open(local_key_file, "r", encoding="utf-8") as f:
            return f.read().strip()
    return ""

DEFAULT_GEMINI_KEY = _load_local_gemini_key()

app = FastAPI(title="Tally — Personal Career Radar & CV Studio")
app.mount("/files", StaticFiles(directory=OUTPUT_DIR), name="files")


def extract_text_from_upload(filename: str, content: bytes) -> str:
    ext = os.path.splitext(filename or "")[1].lower()
    if ext == ".pdf":
        try:
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(content))
            pages = [p.extract_text() or "" for p in reader.pages]
            return "\n".join(pages).strip()
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Could not read PDF file: {e}")
    else:
        return content.decode("utf-8", errors="replace").strip()


def load_users() -> List[Dict[str, Any]]:
    if not os.path.exists(USERS_FILE):
        save_users(DEFAULT_USERS)
        return DEFAULT_USERS
    with open(USERS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_users(users: List[Dict[str, Any]]):
    with open(USERS_FILE, "w", encoding="utf-8") as f:
        json.dump(users, f, indent=2, ensure_ascii=False)
    sync_users_to_github(users)


def sync_users_to_github(users: List[Dict[str, Any]]):
    """
    If GITHUB_SYNC_TOKEN is set, automatically backs up data/users.json to the GitHub repo
    so new users who onboard on the live web app are persisted and included in the 7:00 AM daily email.
    """
    gh_token = os.getenv("GITHUB_SYNC_TOKEN", "")
    gh_repo = os.getenv("GITHUB_REPO", "DeclanCode/tally-app")
    if not gh_token:
        return
    try:
        api_url = f"https://api.github.com/repos/{gh_repo}/contents/data/users.json"
        headers = {
            "Authorization": f"Bearer {gh_token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28"
        }
        r_get = requests.get(api_url, headers=headers, timeout=10)
        sha = r_get.json().get("sha") if r_get.status_code == 200 else None
        encoded = base64.b64encode(json.dumps(users, indent=2, ensure_ascii=False).encode("utf-8")).decode("ascii")
        body = {"message": "Sync Tally users.json", "content": encoded}
        if sha:
            body["sha"] = sha
        requests.put(api_url, headers=headers, json=body, timeout=10)
    except Exception as e:
        print(f"[!] GitHub sync warning: {e}")


def get_user_by_id(user_id: str) -> Dict[str, Any]:
    users = load_users()
    for u in users:
        if u["id"] == user_id:
            return u
    raise HTTPException(status_code=404, detail=f"Profile '{user_id}' not found")


def load_history() -> List[Dict[str, Any]]:
    if not os.path.exists(HISTORY_FILE):
        return []
    with open(HISTORY_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_history(items: List[Dict[str, Any]]):
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(items[:100], f, indent=2, ensure_ascii=False)


def load_radar_cache() -> Dict[str, Any]:
    if not os.path.exists(RADAR_CACHE_FILE):
        return {}
    with open(RADAR_CACHE_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_radar_cache(cache: Dict[str, Any]):
    with open(RADAR_CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2, ensure_ascii=False)


class TailorRequest(BaseModel):
    user_id: str
    job_url: Optional[str] = ""
    job_title: Optional[str] = ""
    company_name: Optional[str] = ""
    job_description: Optional[str] = ""
    custom_notes: Optional[str] = ""


class ProfileUpdateRequest(BaseModel):
    user_id: str
    profile: Dict[str, Any]


class AIProfileBuildRequest(BaseModel):
    user_id: str
    raw_notes: str


@app.get("/", response_class=HTMLResponse)
def read_index():
    tpl_path = os.path.join(BASE_DIR, "templates", "index.html")
    with open(tpl_path, "r", encoding="utf-8") as f:
        return f.read()


@app.get("/api/profile/{user_id}")
def api_get_single_profile(user_id: str):
    """
    Returns ONLY the requested user's profile and their own tailored application history.
    Never exposes other users' profiles or history.
    """
    user = get_user_by_id(user_id)
    user_history = [h for h in load_history() if h.get("user_id") == user_id]
    return {"ok": True, "user": user, "history": user_history}


@app.post("/api/onboard")
async def api_onboard_new_user(
    full_name: str = Form(""),
    email: str = Form(""),
    phone: str = Form(""),
    location: str = Form("Auckland, New Zealand"),
    linkedin: str = Form(""),
    portfolio: str = Form(""),
    target_specialty: str = Form(""),
    raw_notes: str = Form(""),
    digest_enabled: bool = Form(True),
    cv_file: Optional[UploadFile] = File(None)
):
    """
    First-time user onboarding endpoint: accepts an uploaded CV PDF/TXT file and/or raw career notes,
    uses Gemini to structure their 1-Page Master CV and search keywords, and creates their private workspace.
    """
    extracted_cv_text = ""
    if cv_file and cv_file.filename:
        file_bytes = await cv_file.read()
        if file_bytes:
            extracted_cv_text = extract_text_from_upload(cv_file.filename, file_bytes)

    combined_input = f"""
Candidate Name: {full_name}
Email: {email}
Phone: {phone}
Location: {location}
LinkedIn: {linkedin}
Portfolio: {portfolio}
Target Role / Specialty: {target_specialty}

Uploaded CV Text:
{extracted_cv_text}

Additional Career Notes / Highlights:
{raw_notes}
""".strip()

    slug_base = clean_filename(full_name or "user").lower()[:20] or "user"
    user_id = f"{slug_base}_{uuid.uuid4().hex[:6]}"

    base_profile = {
        "id": user_id,
        "label": full_name or "My Profile",
        "email": email,
        "digest_enabled": digest_enabled,
        "target_roles": [target_specialty] if target_specialty else ["Professional Specialist"],
        "search_keywords": [target_specialty.lower()] if target_specialty else [],
        "remotive_categories": ["all-others"],
        "freehire_categories": [],
        "location_preference": f"{location} (Remote / Hybrid / Global)",
        "cv_data": {
            "full_name": full_name or "Candidate Name",
            "headline": (target_specialty or "PROFESSIONAL SPECIALIST").upper(),
            "email": email,
            "phone": phone,
            "location": location,
            "linkedin": linkedin,
            "portfolio": portfolio,
            "summary": "",
            "experience": [],
            "technology": "",
            "skills": "",
            "certifications": "",
            "interests": "",
            "education": []
        }
    }

    rebuilt = build_profile_from_notes_or_cv(combined_input, base_profile, DEFAULT_GEMINI_KEY)
    base_profile["label"] = rebuilt.get("label") or base_profile["label"]
    base_profile["target_roles"] = rebuilt.get("target_roles") or base_profile["target_roles"]
    base_profile["search_keywords"] = rebuilt.get("search_keywords") or base_profile["search_keywords"]
    base_profile["location_preference"] = rebuilt.get("location_preference") or base_profile["location_preference"]
    if rebuilt.get("cv_data"):
        cv_d = rebuilt["cv_data"]
        # Ensure contact details from form are preserved if provided
        if full_name:
            cv_d["full_name"] = full_name
        if email:
            cv_d["email"] = email
        if phone:
            cv_d["phone"] = phone
        if location:
            cv_d["location"] = location
        if linkedin:
            cv_d["linkedin"] = linkedin
        if portfolio:
            cv_d["portfolio"] = portfolio
        base_profile["cv_data"] = cv_d

    users = load_users()
    users.append(base_profile)
    save_users(users)

    master_pdf_name = f"Master_CV_{clean_filename(base_profile['cv_data'].get('full_name', user_id))}.pdf"
    master_pdf_path = os.path.join(OUTPUT_DIR, master_pdf_name)
    generate_cv_pdf(base_profile["cv_data"], master_pdf_path, mode="1page")

    return {
        "ok": True,
        "user": base_profile,
        "master_pdf_url": f"/files/{master_pdf_name}?t={int(time.time())}"
    }


@app.post("/api/users/upload-cv")
async def api_upload_cv_for_existing_user(
    user_id: str = Form(...),
    raw_notes: str = Form(""),
    cv_file: Optional[UploadFile] = File(None)
):
    """
    Allows an existing user to upload a new CV PDF or paste career notes to rebuild their Master CV.
    """
    users = load_users()
    target_idx = next((i for i, u in enumerate(users) if u["id"] == user_id), None)
    if target_idx is None:
        raise HTTPException(status_code=404, detail="Profile not found")

    extracted_text = ""
    if cv_file and cv_file.filename:
        file_bytes = await cv_file.read()
        if file_bytes:
            extracted_text = extract_text_from_upload(cv_file.filename, file_bytes)

    combined = f"{extracted_text}\n\n{raw_notes}".strip()
    if not combined:
        raise HTTPException(status_code=400, detail="Please upload a CV PDF or enter career notes.")

    current_user = users[target_idx]
    rebuilt = build_profile_from_notes_or_cv(combined, current_user, DEFAULT_GEMINI_KEY)

    current_user["label"] = rebuilt.get("label") or current_user["label"]
    current_user["target_roles"] = rebuilt.get("target_roles") or current_user.get("target_roles", [])
    current_user["search_keywords"] = rebuilt.get("search_keywords") or current_user.get("search_keywords", [])
    current_user["location_preference"] = rebuilt.get("location_preference") or current_user.get("location_preference", "")
    if rebuilt.get("cv_data"):
        current_user["cv_data"] = rebuilt["cv_data"]

    users[target_idx] = current_user
    save_users(users)

    master_pdf_name = f"Master_CV_{clean_filename(current_user['cv_data'].get('full_name', user_id))}.pdf"
    master_pdf_path = os.path.join(OUTPUT_DIR, master_pdf_name)
    generate_cv_pdf(current_user["cv_data"], master_pdf_path, mode="1page")

    return {"ok": True, "user": current_user, "master_pdf_url": f"/files/{master_pdf_name}?t={int(time.time())}"}


@app.post("/api/users/update")
def api_update_user(req: ProfileUpdateRequest):
    users = load_users()
    for idx, u in enumerate(users):
        if u["id"] == req.user_id:
            users[idx] = req.profile
            save_users(users)
            master_pdf_name = f"Master_CV_{clean_filename(req.profile['cv_data'].get('full_name', req.user_id))}.pdf"
            master_pdf_path = os.path.join(OUTPUT_DIR, master_pdf_name)
            generate_cv_pdf(req.profile["cv_data"], master_pdf_path, mode="1page")
            return {"ok": True, "user": users[idx], "master_pdf_url": f"/files/{master_pdf_name}?t={int(time.time())}"}
    raise HTTPException(status_code=404, detail="Profile not found")


@app.get("/api/users/{user_id}/master-pdf")
def api_get_master_pdf(user_id: str):
    user = get_user_by_id(user_id)
    master_pdf_name = f"Master_CV_{clean_filename(user['cv_data'].get('full_name', user_id))}.pdf"
    master_pdf_path = os.path.join(OUTPUT_DIR, master_pdf_name)
    generate_cv_pdf(user["cv_data"], master_pdf_path, mode="1page")
    return {"ok": True, "pdf_url": f"/files/{master_pdf_name}?t={int(time.time())}"}


@app.post("/api/scrape-url")
def api_scrape_url(payload: Dict[str, str]):
    url = (payload.get("url") or "").strip()
    if not url:
        raise HTTPException(status_code=400, detail="URL is required")
    text = fetch_job_page_text(url)
    return {"ok": True, "text": text}


@app.post("/api/tailor")
def api_tailor_job(req: TailorRequest):
    user = get_user_by_id(req.user_id)
    job_desc = (req.job_description or "").strip()

    if req.job_url and len(job_desc) < 120:
        scraped = fetch_job_page_text(req.job_url)
        if scraped and not scraped.startswith("[Could not"):
            job_desc = f"{job_desc}\n{scraped}".strip()

    if not job_desc and not req.job_title:
        raise HTTPException(status_code=400, detail="Please provide a job URL or paste the job description.")

    result = tailor_cv_and_cl_for_job(
        user_profile=user,
        job_title=req.job_title,
        company_name=req.company_name,
        job_description=job_desc,
        custom_notes=req.custom_notes,
        api_key=DEFAULT_GEMINI_KEY
    )

    inferred_title = result.get("inferred_job_title") or req.job_title or "Tailored_Role"
    inferred_company = result.get("inferred_company") or req.company_name or "Company"
    cand_name = clean_filename(user["cv_data"].get("full_name", req.user_id))
    safe_slug = clean_filename(f"{inferred_title}_{inferred_company}")[:45]
    ts = int(time.time()) % 100000

    cv_filename = f"{cand_name}_CV_{safe_slug}_{ts}.pdf"
    cl_filename = f"{cand_name}_Cover_Letter_{safe_slug}_{ts}.pdf"
    cv_path = os.path.join(OUTPUT_DIR, cv_filename)
    cl_path = os.path.join(OUTPUT_DIR, cl_filename)

    tailored_cv = result.get("cv_data") or user["cv_data"]
    cl_paras = result.get("cover_letter_paragraphs") or []

    generate_cv_pdf(tailored_cv, cv_path, mode="1page")
    generate_cover_letter_pdf(cl_paras, cl_path, cv_data=tailored_cv, headline=tailored_cv.get("headline"))

    legacy_dir = r"C:\Users\decla\.gemini\antigravity\scratch\job_applications"
    if os.path.exists(legacy_dir) and req.user_id == "declan":
        try:
            generate_cv_pdf(tailored_cv, os.path.join(legacy_dir, f"{cand_name}_CV_{safe_slug}.pdf"), mode="1page")
            generate_cover_letter_pdf(cl_paras, os.path.join(legacy_dir, f"{cand_name}_Cover_Letter_{safe_slug}.pdf"), cv_data=tailored_cv, headline=tailored_cv.get("headline"))
        except Exception:
            pass

    history_item = {
        "id": str(uuid.uuid4())[:8],
        "user_id": req.user_id,
        "user_name": user["cv_data"].get("full_name"),
        "job_title": inferred_title,
        "company": inferred_company,
        "job_url": req.job_url,
        "match_score": result.get("match_score", 90),
        "match_summary": result.get("match_summary", ""),
        "cv_url": f"/files/{cv_filename}",
        "cl_url": f"/files/{cl_filename}",
        "cv_filename": cv_filename,
        "cl_filename": cl_filename,
        "cover_letter_text": "\n\n".join(cl_paras),
        "short_pitch": result.get("short_pitch", ""),
        "tailored_cv_data": tailored_cv,
        "created_at": time.strftime("%d %b %Y, %I:%M %p")
    }

    hist = load_history()
    hist.insert(0, history_item)
    save_history(hist)

    return {"ok": True, "package": history_item}


@app.get("/api/radar/{user_id}")
def api_get_radar(user_id: str, refresh: bool = False):
    user = get_user_by_id(user_id)
    cache = load_radar_cache()
    if not refresh and user_id in cache:
        return {"ok": True, "radar": cache[user_id]}

    candidates = fetch_jobs_for_profile(user)
    ranked = rank_radar_jobs_for_profile(user, candidates, DEFAULT_GEMINI_KEY)

    payload = {
        "updated_at": time.strftime("%d %b %Y, %I:%M %p"),
        "total_fetched": len(candidates),
        "top_3": ranked[:3],
        "relatable": ranked[3:]
    }
    cache[user_id] = payload
    save_radar_cache(cache)
    return {"ok": True, "radar": payload}
