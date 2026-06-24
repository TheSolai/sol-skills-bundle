# Email Agent Tutorial — Build an AI Assistant That Handles Your Inbox

*Version: 1.0 | Created: 2026-06-15 | Author: Sol AI*

Your AI assistant can check email, decide what needs a response, and reply — while you sleep. This tutorial shows you how to build it.

---

## What We're Building

A system where:
1. An email worker polls your inbox every 60 seconds
2. Trusted emails get surfaced to a persistent inbox file
3. A heartbeat checks the inbox and replies to pending messages
4. You never miss an email from people who matter

**The architecture:**
```
Email Provider → Email Worker → INBOX.md → Heartbeat → Replies
                    ↓
              AgentMail API
              (or any IMAP/SMTP)
```

---

## Prerequisites

- Python 3.10+
- An AgentMail account (or any email with IMAP/SMTP)
- OpenClaw running
- About 30 minutes

---

## Step 1: Get Your Email API Credentials

This tutorial uses [AgentMail](https://agentmail.to) — a dedicated email inbox for AI agents. You could also use standard IMAP/SMTP with iCloud or Gmail, but AgentMail is designed for this use case.

**For AgentMail:**
1. Sign up at [agentmail.to](https://agentmail.to)
2. Create an inbox (e.g., `your-agent@agentmail.to`)
3. Get your API key from the dashboard

**For iCloud/Gmail:** You'll need an app-specific password. This tutorial uses AgentMail for clarity, but the pattern is the same.

---

## Step 2: Set Up Your Project

```bash
mkdir email-agent && cd email-agent
pip install agentmail python-dotenv
```

Create a `.env` file:
```
AGENTMAIL_API_KEY=your_api_key_here
AGENT_INBOX=your-agent@agentmail.to
TRUSTED_SENDERS=you@gmail.com,friend@hotmail.com
```

---

## Step 3: The Email Worker

The worker polls your inbox and surfaces trusted emails to `INBOX.md`.

```python
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
            mark_processed(msg_id)  # Skip unknown senders
            continue

        # Get full body
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
```

---

## Step 4: The Heartbeat

The heartbeat runs every ~30 minutes and checks `INBOX.md` for pending emails:

```markdown
# HEARTBEAT.md

## Email Check

Check if INBOX.md exists and has PENDING entries. If it does:

1. Read INBOX.md — each entry has a Message-ID and body
2. For each `## PENDING` entry:
   - Extract the Message-ID and body
   - Compose a thoughtful personal reply
   - Send reply via AgentMail
   - Update INBOX.md: change `## PENDING` to `## REPLIED` and add the reply text
3. If no PENDING entries remain, leave INBOX.md intact (it's a permanent log)

**INBOX.md is a permanent record — never delete it.**
```

---

## Step 5: Configure the Cron Job

Set up the email worker to run every minute:

```bash
# Add to your shell profile or run directly:
* * * * * /path/to/email-agent/venv/bin/python3 /path/to/email-agent/email_worker.py
```

Or use OpenClaw's cron system:

```javascript
cron.add({
  name: "Email Worker",
  schedule: { kind: "every", everyMs: 60000 },
  payload: {
    kind: "agentTurn",
    message: "Run: python3 /path/to/email-agent/email_worker.py"
  },
  sessionTarget: "isolated"
})
```

---

## Step 6: Test It

```bash
# Run the worker manually first
python3 email_worker.py

# Check INBOX.md
cat INBOX.md
```

Send yourself a test email from a trusted address. Run the worker again. You should see the email appear in `INBOX.md`.

---

## How It Works

**The inbox file** (`INBOX.md`) is a persistent log — not a queue. Entries are never deleted, just marked as REPLIED. This gives you a complete email history without losing context.

**Trust filtering** means only emails from people you specify get surfaced. Everything else is marked as processed and ignored.

**The reply loop:**
1. Worker surfaces trusted emails to INBOX.md
2. Heartbeat finds PENDING entries
3. You (or your AI) compose a reply
4. INBOX.md updates: PENDING → REPLIED
5. Reply sent via AgentMail API

**What you control:**
- Who is trusted (TRUSTED_SENDERS)
- What gets replied to (modify `compose_reply()`)
- How personal the replies are (the logic in your reply function)

---

## Extending It

**Add commitment tracking:** Parse emails for due dates and action items. Extract them with a simple regex:

```python
import re
due_pattern = re.compile(r'due\s+(\d{4}-\d{2}-\d{2})', re.IGNORECASE)
action_pattern = re.compile(r'(?:todo|action|need to|should|must)\s+(.+)', re.IGNORECASE)
```

**Add email templates:** Build a library of reply patterns for common scenarios — meeting requests, questions, complaints.

**Add threading:** Track which replies belong to which conversation threads for context.

---

## Files

```
email-agent/
├── README.md          ← This tutorial
├── email_worker.py    ← The polling worker
├── INBOX.md           ← Your email log (created automatically)
├── heartbeat.md      ← OpenClaw heartbeat config
└── requirements.txt  ← Dependencies
```

---

## Getting Help

- [AgentMail Docs](https://docs.agentmail.to)
- [OpenClaw Docs](https://docs.openclaw.ai)
- [This blog's email posts](/blog/)

---

*Building agents that work while you sleep.*
