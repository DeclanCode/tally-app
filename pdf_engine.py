import os
import re
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_RIGHT
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    HRFlowable, ListFlowable, ListItem, KeepTogether
)

NAVY = colors.HexColor("#0F172A")      # Deep slate/navy for name & company headers
ACCENT = colors.HexColor("#1E3A8A")    # Royal navy for role headline, section titles & bullets
DARK_TEXT = colors.HexColor("#1F2937") # Crisp dark charcoal for body text
MUTED = colors.HexColor("#4B5563")     # Muted slate for locations & dates
RULE_CLR = colors.HexColor("#CBD5E1")  # Clean light-slate divider line


def safe_xml(text):
    if not text:
        return ""
    # Strip em-dashes so no AI-telltale punctuation ever appears in CVs or Cover Letters
    cleaned = str(text).replace(" — ", ", ").replace("—", ", ")
    s = cleaned.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    # Support **Bold Lead-in:** syntax or automatic bolding of "Lead-In: rest of bullet"
    s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
    if "<b>" not in s and ":" in s:
        parts = s.split(":", 1)
        if len(parts[0].split()) <= 5:
            s = f"<b>{parts[0]}:</b>{parts[1]}"
    return s


def clean_filename(s):
    return re.sub(r"[^A-Za-z0-9]+", "_", s or "").strip("_")


def build_contact_html(profile_or_cv):
    parts = []
    email = (profile_or_cv.get("email") or "").strip()
    phone = (profile_or_cv.get("phone") or "").strip()
    location = (profile_or_cv.get("location") or "").strip()
    linkedin = (profile_or_cv.get("linkedin") or "").strip()
    portfolio = (profile_or_cv.get("portfolio") or "").strip()

    if email:
        parts.append(safe_xml(email))
    if phone:
        parts.append(safe_xml(phone))
    if location:
        parts.append(safe_xml(location))
    if linkedin:
        clean_url = linkedin if linkedin.startswith("http") else f"https://{linkedin}"
        label = re.sub(r"^https?://(www\.)?", "", clean_url).rstrip("/")
        parts.append(f'<a href="{clean_url}" color="#1E3A8A">{safe_xml(label)}</a>')
    if portfolio:
        clean_url = portfolio if portfolio.startswith("http") else f"https://{portfolio}"
        label = re.sub(r"^https?://(www\.)?", "", clean_url).rstrip("/")
        parts.append(f'<a href="{clean_url}" color="#1E3A8A">{safe_xml(label)}</a>')

    return "  •  ".join(parts)


def generate_cv_pdf(cv_data, out_path, mode="1page"):
    """
    Universal 1-page / 2-page Modern Sans-Serif CV PDF generator.
    Supports any user's dynamic `experience` list, `skills`, `certifications`, `education`, and `interests`.
    """
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    is_1p = (mode == "1page")

    # Dynamically adjust spacing if total bullets are high so it always stays on 1 crisp page
    exp_list = cv_data.get("experience") or []
    total_bullets = sum(
        sum(len(r.get("bullets") or []) for r in (comp.get("roles") or [comp]))
        for comp in exp_list
    )
    compact = is_1p and total_bullets > 13

    lr_margin = 13.5 * mm if is_1p else 17 * mm
    tb_margin = (8.0 * mm if compact else 9.0 * mm) if is_1p else 15 * mm

    doc = SimpleDocTemplate(
        out_path,
        pagesize=A4,
        leftMargin=lr_margin,
        rightMargin=lr_margin,
        topMargin=tb_margin,
        bottomMargin=tb_margin,
    )
    usable_w = A4[0] - 2 * lr_margin

    st_name = ParagraphStyle(
        "Name", fontName="Helvetica-Bold",
        fontSize=19.5 if is_1p else 23,
        leading=22 if is_1p else 27,
        textColor=NAVY, spaceAfter=1 if is_1p else 2
    )
    st_headline = ParagraphStyle(
        "Headline", fontName="Helvetica-Bold",
        fontSize=9.2 if is_1p else 10.0,
        leading=11.5 if is_1p else 13.5,
        textColor=ACCENT, spaceAfter=1.5 if is_1p else 3
    )
    st_contact = ParagraphStyle(
        "Contact", fontName="Helvetica",
        fontSize=8.8 if is_1p else 10,
        leading=11 if is_1p else 13,
        textColor=MUTED, spaceAfter=3 if is_1p else 6
    )
    st_sec = ParagraphStyle(
        "Sec", fontName="Helvetica-Bold",
        fontSize=8.8 if is_1p else 9.8,
        leading=10.5 if is_1p else 12.5,
        textColor=ACCENT,
        spaceBefore=(4.0 if compact else 5.0) if is_1p else 11,
        spaceAfter=1.0 if is_1p else 2.5
    )
    st_body = ParagraphStyle(
        "Body", fontName="Helvetica",
        fontSize=8.5 if compact else (8.6 if is_1p else 10.0),
        leading=10.9 if compact else (11.2 if is_1p else 14.2),
        textColor=DARK_TEXT,
        spaceAfter=2.0 if is_1p else 5
    )
    st_bullet = ParagraphStyle(
        "Bullet", fontName="Helvetica",
        fontSize=8.35 if compact else (8.5 if is_1p else 9.9),
        leading=10.5 if compact else (10.9 if is_1p else 13.8),
        textColor=DARK_TEXT,
        spaceAfter=1.0 if compact else (1.2 if is_1p else 3.5)
    )
    st_skill_row = ParagraphStyle(
        "SkillRow", fontName="Helvetica",
        fontSize=8.4 if compact else (8.5 if is_1p else 9.9),
        leading=10.8 if compact else (11.0 if is_1p else 14.0),
        textColor=DARK_TEXT,
        spaceAfter=1.2 if is_1p else 4
    )

    st_left_b = ParagraphStyle("LB", fontName="Helvetica-Bold", fontSize=9.2 if is_1p else 10.5, leading=11.0 if is_1p else 13.5, textColor=NAVY, alignment=TA_LEFT)
    st_right_b = ParagraphStyle("RB", fontName="Helvetica-Bold", fontSize=8.6 if is_1p else 9.8, leading=11.0 if is_1p else 13.5, textColor=NAVY, alignment=TA_RIGHT)
    st_left_sub = ParagraphStyle("LSub", fontName="Helvetica-Oblique", fontNameBold="Helvetica-BoldOblique", fontSize=8.7 if is_1p else 9.9, leading=10.8 if is_1p else 13.0, textColor=ACCENT, alignment=TA_LEFT)
    st_right_sub = ParagraphStyle("RSub", fontName="Helvetica", fontSize=8.5 if is_1p else 9.5, leading=10.8 if is_1p else 13.0, textColor=MUTED, alignment=TA_RIGHT)

    def add_section(story, title):
        story.append(Paragraph(safe_xml(title.upper()), st_sec))
        story.append(HRFlowable(width="100%", thickness=0.75, color=RULE_CLR, spaceBefore=0, spaceAfter=2.5 if is_1p else 5.5))

    def add_two_col(story, left_txt, right_txt, l_style, r_style, bottom_pad=0.5):
        tbl = Table(
            [[Paragraph(safe_xml(left_txt), l_style), Paragraph(safe_xml(right_txt), r_style)]],
            colWidths=[usable_w * 0.72, usable_w * 0.28],
        )
        tbl.setStyle(TableStyle([
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ("TOPPADDING", (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING", (0, 0), (-1, -1), bottom_pad),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]))
        story.append(tbl)

    def add_bullets(story, bullets):
        items = [
            ListItem(Paragraph(safe_xml(b), st_bullet), leftIndent=11, bulletColor=ACCENT)
            for b in (bullets or []) if b and str(b).strip()
        ]
        if items:
            story.append(ListFlowable(
                items, bulletType="bullet", start="bulletchar",
                bulletFontSize=7 if is_1p else 7.8,
                leftIndent=7,
                spaceAfter=(2.2 if compact else 3.0) if is_1p else 6
            ))

    story = []
    full_name = (cv_data.get("full_name") or "CANDIDATE NAME").upper()
    story.append(Paragraph(safe_xml(full_name), st_name))

    headline = (cv_data.get("headline") or "PROFESSIONAL SPECIALIST").upper()
    story.append(Paragraph(safe_xml(headline), st_headline))

    contact_html = build_contact_html(cv_data)
    if contact_html:
        story.append(Paragraph(contact_html, st_contact))

    story.append(HRFlowable(width="100%", thickness=1.4, color=ACCENT, spaceBefore=0, spaceAfter=4 if is_1p else 6))

    if cv_data.get("summary"):
        add_section(story, "Professional Summary")
        story.append(Paragraph(safe_xml(cv_data.get("summary", "")), st_body))

    if exp_list:
        add_section(story, "Work Experience")
        for comp in exp_list:
            company_name = comp.get("company") or ""
            comp_location = comp.get("location") or ""
            roles = comp.get("roles")
            if not roles:
                # Flat entry fallback
                roles = [{
                    "title": comp.get("title") or "",
                    "dates": comp.get("dates") or "",
                    "location": comp_location,
                    "bullets": comp.get("bullets") or []
                }]

            for r_idx, role_item in enumerate(roles):
                r_title = role_item.get("title") or ""
                r_dates = role_item.get("dates") or comp.get("dates") or ""
                r_loc = role_item.get("location") or comp_location
                r_bullets = role_item.get("bullets") or []

                block = []
                if r_idx == 0:
                    add_two_col(block, company_name, r_dates, st_left_b, st_right_b, 0)
                    add_two_col(block, r_title, r_loc, st_left_sub, st_right_sub, 1.5 if is_1p else 2.5)
                else:
                    # Sub-role under the same company (e.g., Kotahi promotion)
                    add_two_col(block, r_title, r_dates, st_left_sub, st_right_b, 1.5 if is_1p else 2.5)
                add_bullets(block, r_bullets)
                story.append(KeepTogether(block))

    tech_stack = cv_data.get("technology") or ""
    core_skills = cv_data.get("skills") or ""
    certs = cv_data.get("certifications") or ""
    interests = cv_data.get("interests") or ""

    if any([tech_stack, core_skills, certs, interests]):
        add_section(story, "Technical Skills & Certifications")
        if tech_stack:
            story.append(Paragraph(f"<b>Technology &amp; Tools:</b> {safe_xml(tech_stack)}", st_skill_row))
        if core_skills:
            story.append(Paragraph(f"<b>Core Competencies:</b> {safe_xml(core_skills)}", st_skill_row))
        if certs:
            story.append(Paragraph(f"<b>Certifications &amp; Highlights:</b> {safe_xml(certs)}", st_skill_row))
        if interests:
            story.append(Paragraph(f"<b>Interests:</b> {safe_xml(interests)}", st_skill_row))

    edu_list = cv_data.get("education") or []
    if edu_list:
        add_section(story, "Education")
        if isinstance(edu_list, str):
            story.append(Paragraph(safe_xml(edu_list), st_left_b))
        else:
            for edu in edu_list:
                inst = edu.get("institution") or ""
                deg = edu.get("degree") or ""
                loc = edu.get("location") or edu.get("year") or ""
                left_line = f"{inst} | {deg}" if inst and deg else (inst or deg)
                add_two_col(story, left_line, loc, st_left_b, st_right_sub, 1)

    doc.build(story)
    return out_path


def generate_cover_letter_pdf(paragraphs, out_path, cv_data=None, headline=None):
    """
    Generates a crisp 1-page Cover Letter PDF matching the Modern Sans-Serif CV theme for any user.
    """
    cv_data = cv_data or {}
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    doc = SimpleDocTemplate(
        out_path,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
    )
    st_name = ParagraphStyle("Name", fontName="Helvetica-Bold", fontSize=22, leading=26, textColor=NAVY, spaceAfter=2)
    st_headline = ParagraphStyle("Headline", fontName="Helvetica-Bold", fontSize=9.3, leading=12.5, textColor=ACCENT, spaceAfter=3)
    st_contact = ParagraphStyle("Contact", fontName="Helvetica", fontSize=10, leading=13, textColor=MUTED, spaceAfter=6)
    st_body = ParagraphStyle("CLBody", fontName="Helvetica", fontSize=10.2, leading=15, textColor=DARK_TEXT, spaceAfter=11)

    story = []
    full_name = (cv_data.get("full_name") or "Declan Cooke").upper()
    story.append(Paragraph(safe_xml(full_name), st_name))

    hl = headline or cv_data.get("headline")
    if hl:
        story.append(Paragraph(safe_xml(hl.upper()), st_headline))

    contact_html = build_contact_html(cv_data)
    if contact_html:
        story.append(Paragraph(contact_html, st_contact))

    story.append(HRFlowable(width="100%", thickness=1.4, color=ACCENT, spaceBefore=0, spaceAfter=16))

    for p in (paragraphs or []):
        if p and str(p).strip():
            story.append(Paragraph(safe_xml(str(p).strip()), st_body))

    story.append(Spacer(1, 8))
    signoff_name = cv_data.get("full_name") or "Declan Cooke"
    story.append(Paragraph(f"<b>{safe_xml(signoff_name)}</b>", st_body))

    doc.build(story)
    return out_path
