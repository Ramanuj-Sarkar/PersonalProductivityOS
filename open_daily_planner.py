"""
Personal Productivity OS — Daily Planner
Fetches Gmail + Google Calendar directly via Google APIs,
then passes the data to a local Ollama model for planning.

Setup:
  pip install ollama google-api-python-client google-auth google-auth-oauthlib

  Install Ollama:  https://ollama.com/download
  Pull a model:    ollama pull llama3.2      # fast, ~2GB
                   ollama pull llama3.1:8b   # smarter, ~5GB (recommended)
                   ollama pull mistral       # good alternative, ~4GB
  Start server:    ollama serve              # runs on http://localhost:11434

First-time Google OAuth setup:
  1. Go to https://console.cloud.google.com
  2. Create a project → Enable "Gmail API" and "Google Calendar API"
  3. APIs & Services → Credentials → Create OAuth 2.0 Client ID (Desktop App)
  4. Download and save as client_secret.json in this directory
  5. APIs & Services → OAuth consent screen → Test users → add your Gmail address
  6. Run the script — a browser window opens once to authorise, then caches token.json

Usage:
  python open_daily_planner.py
"""

import ollama
import json
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path

# ── Google OAuth ──────────────────────────────────────────────────────────────

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/calendar",
]
TOKEN_FILE  = Path("token.json")
SECRET_FILE = Path("client_secret.json")


def get_google_credentials():
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from google_auth_oauthlib.flow import InstalledAppFlow

    creds = None
    if TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not SECRET_FILE.exists():
                raise FileNotFoundError(
                    "client_secret.json not found.\n"
                    "Download it from Google Cloud Console → APIs & Services → Credentials."
                )
            flow = InstalledAppFlow.from_client_secrets_file(str(SECRET_FILE), SCOPES)
            creds = flow.run_local_server(port=0)
        TOKEN_FILE.write_text(creds.to_json())
    return creds

# ── Google API fetchers ───────────────────────────────────────────────────────

def fetch_emails(creds, max_results: int = 10) -> list[dict]:
    """Fetch recent unread emails via Gmail API."""
    from googleapiclient.discovery import build
    import base64, re

    service = build("gmail", "v1", credentials=creds)
    result  = service.users().messages().list(
        userId="me", labelIds=["UNREAD"], maxResults=max_results
    ).execute()

    messages = result.get("messages", [])
    emails   = []
    for msg in messages:
        full = service.users().messages().get(
            userId="me", id=msg["id"], format="metadata",
            metadataHeaders=["From", "Subject", "Date"]
        ).execute()
        headers = {h["name"]: h["value"] for h in full["payload"]["headers"]}
        snippet = full.get("snippet", "")
        emails.append({
            "sender":  headers.get("From", ""),
            "subject": headers.get("Subject", ""),
            "date":    headers.get("Date", ""),
            "snippet": snippet[:200],
        })
    return emails


def fetch_calendar_events(creds) -> dict:
    """Fetch today's calendar events and compute free blocks."""
    from googleapiclient.discovery import build

    service = build("calendar", "v3", credentials=creds)

    now       = datetime.now(timezone.utc)
    day_start = now.replace(hour=0,  minute=0,  second=0,  microsecond=0)
    day_end   = now.replace(hour=23, minute=59, second=59, microsecond=0)

    result = service.events().list(
        calendarId="primary",
        timeMin=day_start.isoformat(),
        timeMax=day_end.isoformat(),
        singleEvents=True,
        orderBy="startTime",
    ).execute()

    raw_events = result.get("items", [])
    events = []
    busy_windows = []

    for e in raw_events:
        start_str = e["start"].get("dateTime", e["start"].get("date", ""))
        end_str   = e["end"].get("dateTime",   e["end"].get("date",   ""))
        attendees = len(e.get("attendees", []))
        events.append({
            "title":           e.get("summary", "Untitled"),
            "start":           start_str,
            "end":             end_str,
            "attendees_count": attendees,
        })
        try:
            s = datetime.fromisoformat(start_str)
            en = datetime.fromisoformat(end_str)
            busy_windows.append((s, en))
        except ValueError:
            pass

    # Compute free blocks during working hours (8am–6pm)
    work_start = now.replace(hour=8,  minute=0,  second=0, microsecond=0)
    work_end   = now.replace(hour=18, minute=0,  second=0, microsecond=0)
    free_blocks = []
    cursor = work_start

    for s, e in sorted(busy_windows):
        s = s.astimezone(timezone.utc)
        e = e.astimezone(timezone.utc)
        if s > cursor:
            gap_mins = int((s - cursor).total_seconds() / 60)
            if gap_mins >= 30:
                free_blocks.append({
                    "start":            cursor.strftime("%H:%M"),
                    "end":              s.strftime("%H:%M"),
                    "duration_minutes": gap_mins,
                })
        cursor = max(cursor, e)

    if cursor < work_end:
        gap_mins = int((work_end - cursor).total_seconds() / 60)
        if gap_mins >= 30:
            free_blocks.append({
                "start":            cursor.strftime("%H:%M"),
                "end":              work_end.strftime("%H:%M"),
                "duration_minutes": gap_mins,
            })

    return {"events": events, "free_blocks": free_blocks}


def book_focus_block(creds, title: str, start_time: str, duration_minutes: int):
    """Create a focus block event on Google Calendar."""
    from googleapiclient.discovery import build

    service = build("calendar", "v3", credentials=creds)
    today   = datetime.now().date()

    h, m    = map(int, start_time.split(":"))
    start   = datetime(today.year, today.month, today.day, h, m, tzinfo=timezone.utc)
    end     = start + timedelta(minutes=duration_minutes)

    event = {
        "summary":     f"Focus: {title}",
        "start":       {"dateTime": start.isoformat()},
        "end":         {"dateTime": end.isoformat()},
        "description": "Created by Personal Productivity OS",
    }
    service.events().insert(calendarId="primary", body=event).execute()
    return f"  ✓ Booked '{event['summary']}' at {start_time} for {duration_minutes} min"

# ── Ollama agents ─────────────────────────────────────────────────────────────

# Change this to any model you have pulled locally, e.g. "llama3.1:8b" or "mistral"
MODEL = "llama3.2"


def chat(system: str, user: str) -> str:
    """Send a chat request to the local Ollama server and return the response text."""
    response = ollama.chat(
        model=MODEL,
        messages=[
            {"role": "system",  "content": system},
            {"role": "user",    "content": user},
        ],
    )
    return response.message.content


def run_planner_agent(today: str, emails: list, cal_data: dict) -> str:
    """Synthesise a structured daily brief from email + calendar data."""
    print("  → Generating your daily brief...")
    context = json.dumps({"emails": emails, "calendar": cal_data}, indent=2)
    return chat(
        system=(
            "You are a personal productivity assistant. "
            "Given the user's emails and calendar for today, produce a concise daily brief. "
            "Structure it with these sections:\n"
            "1. Good morning greeting with today's date\n"
            "2. Today at a glance (meeting count, free time, urgent emails)\n"
            "3. Top 3 priorities for the day\n"
            "4. Meeting prep (one bullet per meeting with key context)\n"
            "5. Emails that likely need a reply today\n"
            "6. Suggested focus blocks (mapped to the free calendar slots)\n"
            "7. One motivating closing sentence\n\n"
            "Keep it tight — the user reads this in under 2 minutes."
        ),
        user=f"Today is {today}. Here is my inbox and calendar data:\n\n{context}",
    )


def run_priority_extractor(brief: str) -> list[dict]:
    """Extract top priorities as structured JSON for calendar booking."""
    raw = chat(
        system=(
            "Extract the top 3 priorities from the daily brief. "
            "Return ONLY a JSON array of objects with keys: "
            "'title' (short task name, max 5 words) and 'duration_minutes' (suggested focus time as integer). "
            "No preamble, no markdown fences, no explanation."
        ),
        user=brief,
    )
    raw = raw.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return []

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("\n Authenticating with Google...")
    creds = get_google_credentials()
    print("  ✓ Google auth OK")

    today = datetime.now().strftime("%A, %B %-d, %Y")

    print(f"\n Personal Productivity OS")
    print(f" Daily Planner — {today}  [model: {MODEL}]")
    print(" " + "─" * 40)

    # Step 1: Fetch data directly from Google APIs
    print("  → Scanning inbox...")
    emails = fetch_emails(creds)

    print("  → Checking calendar...")
    cal_data = fetch_calendar_events(creds)

    # Step 2: Local model synthesises the brief
    brief = run_planner_agent(today, emails, cal_data)

    print("\n" + "═" * 50)
    print(brief)
    print("═" * 50)

    # Step 3: Optionally book focus blocks
    free_blocks = cal_data.get("free_blocks", [])
    if free_blocks:
        answer = input("\nBook focus blocks on your calendar? [y/N] ").strip().lower()
        if answer == "y":
            priorities = run_priority_extractor(brief)
            booked = []
            for i, p in enumerate(priorities[:len(free_blocks)]):
                block = free_blocks[i]
                duration = min(p.get("duration_minutes", 60), block["duration_minutes"])
                msg = book_focus_block(creds, p["title"], block["start"], duration)
                booked.append(msg)
                print(msg)
            if not booked:
                print("  Could not extract priorities to book.")
    else:
        print("\n No free blocks available to schedule today.")

    print("\n Have a great day!\n")


if __name__ == "__main__":
    main()
