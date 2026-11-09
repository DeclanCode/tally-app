import html
import json
import os
import smtplib
import time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from ai_service import fetch_jobs_for_profile, rank_radar_jobs_for_profile
from app import load_users, DEFAULT_GEMINI_KEY

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SEEN_FILE = os.path.join(BASE_DIR, "data", "seen_jobs_by_user.json")


def load_seen():
    if not os.path.exists(SEEN_FILE):
        return {}
    with open(SEEN_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_seen(data):
    os.makedirs(os.path.dirname(SEEN_FILE), exist_ok=True)
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def send_user_digest_email(user, top_3, relatable, app_base_url=""):
    sender = os.getenv("SMTP_USER", "declanjcooke@gmail.com")
    password = os.getenv("SMTP_PASSWORD", "")
    recipient = user.get("email") or sender
    smtp_server = os.getenv("SMTP_SERVER", "smtp.gmail.com")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))

    if not sender or not password or not recipient:
        print(f"[!] Missing SMTP credentials or recipient for user {user.get('id')}. Skipping email.")
        return False

    total_roles = len(top_3) + len(relatable)
    date_str = time.strftime("%A, %d %B %Y")
    cand_name = (user.get("cv_data") or {}).get("full_name") or user.get("label") or "there"
    subject = f"Tally Daily Brief: Top {len(top_3)} Picks + {len(relatable)} Matching Roles ({date_str})"

    top_cards_html = []
    text_lines = [
        f"Tally Daily Job Brief for {cand_name} - {date_str}\n",
        f"Top {len(top_3)} Recommended Role(s):\n"
    ]

    for idx, item in enumerate(top_3, start=1):
        title = html.escape(item.get("title", "Untitled Role"))
        company = html.escape(item.get("company") or "Employer")
        url = item.get("url") or "#"
        location = html.escape(item.get("location") or "Remote")
        source = html.escape(item.get("source") or "Job Board")
        score = item.get("score", 85)
        reason = html.escape(item.get("reason", ""))

        tailor_btn = ""
        if app_base_url:
            t_link = f"{app_base_url.rstrip('/')}/?u={user['id']}"
            tailor_btn = f"""
            <a href="{t_link}" style="background-color: #f1f5f9; color: #0f172a; text-decoration: none; padding: 8px 14px; border-radius: 6px; font-size: 12.5px; font-weight: 600; display: inline-block; margin-left: 8px; border: 1px solid #cbd5e1;">
                Tailor CV in Tally
            </a>
            """

        card = f"""
        <div style="background: #ffffff; border: 1.5px solid #0f172a; border-radius: 10px; padding: 16px; margin-bottom: 14px;">
            <div style="display: flex; justify-content: space-between; align-items: center;">
                <span style="background-color: #0f172a; color: #ffffff; font-weight: 600; font-size: 11px; padding: 3px 9px; border-radius: 6px;">
                    #{idx} PICK &bull; {score}%
                </span>
                <span style="font-size: 12px; color: #64748b;">{source}</span>
            </div>
            <div style="font-size: 16px; font-weight: 700; color: #0f172a; margin-top: 8px;">
                {title}
            </div>
            <div style="font-size: 13.5px; color: #475569; margin-top: 2px;">
                <strong>{company}</strong> &bull; {location}
            </div>
            <div style="background: #f8fafc; border-left: 3px solid #0f172a; padding: 9px 12px; margin-top: 10px; font-size: 12.5px; color: #334155;">
                {reason}
            </div>
            <div style="margin-top: 12px;">
                <a href="{url}" style="background-color: #0f172a; color: #ffffff; text-decoration: none; padding: 8px 14px; border-radius: 6px; font-size: 12.5px; font-weight: 600; display: inline-block;">
                    Open Listing &rarr;
                </a>
                {tailor_btn}
            </div>
        </div>
        """
        top_cards_html.append(card)
        text_lines.append(f"#{idx} {item.get('title')} at {item.get('company')} [{source}] ({score}%) - {url}")

    extra_html = ""
    if relatable:
        extra_cards = []
        text_lines.append(f"\nOther Matching Roles ({len(relatable)}):\n")
        for item in relatable:
            title = html.escape(item.get("title", "Untitled Role"))
            company = html.escape(item.get("company") or "Employer")
            url = item.get("url") or "#"
            location = html.escape(item.get("location") or "Remote")
            source = html.escape(item.get("source") or "Job Board")
            score = item.get("score", 60)
            reason = html.escape(item.get("reason", ""))
            extra_cards.append(f"""
            <div style="background: #ffffff; border: 1px solid #e2e8f0; border-radius: 8px; padding: 12px 14px; margin-bottom: 10px;">
                <div style="font-size: 14px; font-weight: 600; color: #0f172a;">
                    {title} <span style="font-size: 12px; color: #64748b; font-weight: normal;">({score}% &bull; {source})</span>
                </div>
                <div style="font-size: 12.5px; color: #64748b;"><strong>{company}</strong> &bull; {location}</div>
                <div style="font-size: 12px; color: #334155; margin-top: 5px;">{reason}</div>
                <div style="margin-top: 7px;">
                    <a href="{url}" style="color: #0f172a; font-size: 12px; font-weight: 600; text-decoration: underline;">Open Listing &rarr;</a>
                </div>
            </div>
            """)
            text_lines.append(f"• {item.get('title')} at {item.get('company')} ({score}%) - {url}")

        extra_html = f"""
        <h3 style="font-size: 14px; color: #0f172a; margin-top: 22px; border-bottom: 1px solid #e2e8f0; padding-bottom: 6px;">
            Other Relatable Roles ({len(relatable)})
        </h3>
        {''.join(extra_cards)}
        """

    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head><meta charset="utf-8"></head>
    <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Inter, Helvetica, Arial, sans-serif; background-color: #f8fafc; padding: 20px; color: #0f172a;">
        <div style="max-width: 640px; margin: 0 auto; background: #ffffff; border-radius: 12px; overflow: hidden; border: 1px solid #e2e8f0;">
            <div style="background: #0f172a; color: #ffffff; padding: 22px 24px;">
                <h1 style="margin: 0; font-size: 18px; font-weight: 700;">Tally &bull; Daily Job Brief</h1>
                <p style="margin: 4px 0 0 0; font-size: 12.5px; color: #94a3b8;">
                    Prepared for {html.escape(cand_name)} &bull; {date_str}
                </p>
            </div>
            <div style="padding: 20px;">
                {''.join(top_cards_html)}
                {extra_html}
            </div>
        </div>
    </body>
    </html>
    """

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = recipient
    msg.attach(MIMEText("\n".join(text_lines), "plain", "utf-8"))
    msg.attach(MIMEText(html_content, "html", "utf-8"))

    try:
        server = smtplib.SMTP(smtp_server, smtp_port, timeout=20)
        server.ehlo()
        server.starttls()
        server.ehlo()
        server.login(sender, password)
        server.sendmail(sender, recipient, msg.as_string())
        server.quit()
        print(f"[✓] Sent Tally digest ({total_roles} roles) to {recipient} for user {user.get('id')}")
        return True
    except Exception as e:
        print(f"[!] Failed to send email to {recipient}: {e}")
        return False


def run_daily_brief_for_all_users():
    users = load_users()
    seen_map = load_seen()
    app_url = os.getenv("TALLY_APP_URL", "")

    for user in users:
        if not user.get("digest_enabled", False):
            continue
        uid = user["id"]
        user_seen = set(seen_map.get(uid, []))

        print(f"[*] Running Tally daily scan for {uid} ({user.get('email')})...")
        all_candidates = fetch_jobs_for_profile(user)
        new_candidates = [c for c in all_candidates if c["id"] not in user_seen]

        if not new_candidates:
            print(f"  [-] No new unseen roles for {uid} today.")
            continue

        ranked = rank_radar_jobs_for_profile(user, new_candidates, DEFAULT_GEMINI_KEY)
        if not ranked:
            print(f"  [-] No relatable matches for {uid} today.")
            continue

        top_3 = ranked[:3]
        relatable = ranked[3:]
        if send_user_digest_email(user, top_3, relatable, app_base_url=app_url):
            for c in new_candidates:
                user_seen.add(c["id"])
            seen_map[uid] = list(user_seen)[-1000:]
            save_seen(seen_map)


if __name__ == "__main__":
    run_daily_brief_for_all_users()
