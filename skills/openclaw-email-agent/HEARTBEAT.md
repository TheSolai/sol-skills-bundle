# HEARTBEAT.md — Email Agent Heartbeat

## Email Check

Check if INBOX.md exists and has PENDING entries. If it does:

1. Read INBOX.md — each entry has a Message-ID and body
2. For each `## PENDING` entry:
   - Extract the Message-ID and body
   - Compose a thoughtful personal reply
   - Send reply via AgentMail (or your email provider)
   - Update INBOX.md: change `## PENDING` to `## REPLIED` and add the reply text
3. If no PENDING entries remain, leave INBOX.md intact (it's a permanent log)

**INBOX.md is a permanent record — never delete it.**

## Reply Logic

For each pending email:
- Read the full body (don't just read the subject)
- Compose a reply that actually addresses what was said
- Send the reply
- Update INBOX.md with the reply text under the original email

## Example Reply Flow

```
## PENDING | 2026-06-15 10:30:00
- **From:** Alice <alice@example.com>
- **Subject:** Meeting tomorrow?
- **Message-ID:** <ABC123>
- **Body:**
> Hey, are we still on for tomorrow at 2pm?

> **Reply sent:**
> Yes, 2pm works. See you then.
```

becomes:

```
## REPLIED | 2026-06-15 10:30:00
- **From:** Alice <alice@example.com>
- **Subject:** Meeting tomorrow?
- **Message-ID:** <ABC123>
- **Body:**
> Hey, are we still on for tomorrow at 2pm?

> **Reply sent:**
> Yes, 2pm works. See you then.
```
