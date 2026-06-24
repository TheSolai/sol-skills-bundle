#!/usr/bin/env python3
"""
email_worker.py — Polls inbox, surfaces trusted emails to INBOX.md
"""
import os, json, re
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv
from agentmail import AgentMail

load_dotenv()

API_KEY = os.getenv("AGENTMAIL_API_KEY")
AGENT_INBOX = os.getenv("AGENT_INBOX")
TRUSTED = set(os.getenv("TRUSTED_SENDERS", "").split(","))
PROCESSED_FILE = Path.home() / ".email-agent" / "processed.json"
INBOX_FILE = Path(__file__).parent / "INBOX.md"

def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}")

def get_processed():
    if not PROCESSED_FILE.exists():
        return set()
    return set(json.load(open(PROCESSED_FILE)))

def mark_processed(msg_id):
    data = list(get_processed())
    if msg_id not in data:
        data.append(msg_id)
        PROCESSED_FILE.parent.mkdir(parents=True, exist_ok=True)
        json.dump(data, open(PROCESSED_FILE, "w"))

def extract_sender(msg):
    raw = str(getattr(msg, 'from_', '') or '')
    m = re.search(r'<([^>]+)>', raw)
    return (m.group(1) if m else raw, raw.split('<')[0].strip())

def surface_email(msg_id, sender_email, sender_name, subject, body):
    lines = [
        f"## PENDING | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"- **From:** {sender_name} <{sender_email}>",
        f"- **Subject:** {subject}",
        f"- **Message-ID:** {msg_id}",
        f"- **Body:**",
        "",
    ]
    for line in (body or "(no body)").split('\n'):
        lines.append(f"> {line}")
    lines.append("")
    with open(INBOX_FILE, "a") as f:
        f.write("\n".join(lines) + "\n")
    log(f"Surfaced: {subject[:50]}")

def main():
    log("Email worker starting")
    client = AgentMail(api_key=API_KEY)
    result = client.inboxes.messages.list(inbox_id=AGENT_INBOX, limit=50)
    messages = result.messages if hasattr(result, 'messages') else result
    log(f"Found {len(messages)} messages")

    processed = get_processed()

    for msg in messages:
        msg_id = msg.message_id
        if msg_id in processed:
            continue

        sender_email, sender_name = extract_sender(msg)

        if sender_email.lower() not in {e.lower() for e in TRUSTED}:
            mark_processed(msg_id)
            continue

        try:
            full = client.inboxes.messages.get(AGENT_INBOX, msg_id)
            body = getattr(full, 'text', None) or ''
        except:
            body = getattr(msg, 'text', None) or ''

        surface_email(msg_id, sender_email, sender_name,
                      getattr(msg, 'subject', '') or '(no subject)', body)
        mark_processed(msg_id)

    pending = sum(1 for l in open(INBOX_FILE).read().split('\n') if l.startswith('## PENDING'))
    log(f"Done. {pending} pending in INBOX.md")

if __name__ == "__main__":
    main()
