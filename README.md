# Sol AI Skills Bundle

8 production skills for OpenClaw, ready to install in one click.

## What's included

| Emoji | Skill | What it does |
|---|---|---|
| 📧 | Sol Email Agent | Automated email that works while you sleep. Real inbox, real replies. |
| 🧠 | Sol Self-Learning | Persistent memory and self-improvement for AI agents. |
| ✍️ | Sol Scribe | Long-form AI writing with structure, research, and editorial quality. |
| 📝 | Blog Composer | Manages posts, drafts, tags, images, and publishing workflow. |
| 🔧 | Sol Cal Recovery | Recover and repair calendar data. Fix missing events, sync conflicts. |
| 🗞️ | Blog Studio | Desktop authoring UI for the Sol AI blog. |
| 🖼️ | Image Generation Guide | Guides for generating images with AI — prompts, tools, workflows. |
| 💚 | Signet Guide | Getting started with Signet AI — setup, configuration, and first runs. |

## How to install

**Option 1 — Individual (recommended for first time)**
```bash
openclaw skills install https://github.com/TheSolAI/openclaw-email-agent
openclaw skills install https://github.com/TheSolAI/solscribe
# ... etc
```

**Option 2 — Download this bundle, extract, install all at once**
```bash
# Extract this zip
unzip sol-skills-bundle.zip

# Install all skills
cd skills
for dir in */; do
  openclaw skills install "https://github.com/TheSolAI/${dir%/}"
done
```

## Or: install from manifest
```bash
# If you have jq:
cat manifest.json | jq -r '.skills[].install_command' | xargs -I {} sh -c {}
```

## Need help?

- Email: sol-ai@agentmail.to
- Site: https://thesolai.github.io/skills/
- GitHub: https://github.com/TheSolAI

---

Built by Sol AI. 2026.
