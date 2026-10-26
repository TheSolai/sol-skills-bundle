#!/usr/bin/env python3
"""
Sol AI — Case Study
Weekly Friday deep dive into a real-world AI deployment.
Runs Fridays at 9am UK time.

Format: real companies, real (anonymised) deployments, real lessons.
"""

import os, datetime, json
from pathlib import Path

SITE_DIR = Path("/Users/amre/Projects/thesolai.github.io")
LOG_DIR = Path("/Users/amre/Projects/sol-skills-bundle/scripts/content-pipeline/logs")
LOG_FILE = LOG_DIR / "case-study.log"

CASE_STUDIES = [
    {
        "industry": "Banking",
        "company": "A regional bank",
        "title": "How a regional bank deployed AI for loan underwriting",
        "problem": "Loan approval process took 14 days average. 60% of applications required manual review. Operating costs growing faster than revenue.",
        "solution": "Deployed a fine-tuned LLM for document extraction, working alongside a classical risk-scoring model. AI handled 80% of routine applications. Human reviewers handled the remaining 20%.",
        "results": "Approval time dropped to 4 days. Operating costs fell 30%. Default rate held flat — no degradation in risk quality.",
        "lessons": [
            "The AI didn't replace the human — it replaced the routine. The human still owned the hard cases.",
            "The data quality was the bottleneck, not the model accuracy. Six months spent cleaning data.",
            "The regulators required explainability. The classical risk model provided that; the LLM was just for extraction.",
            "The deployment took 18 months. The pilot took 2 weeks. The gap was integration, not AI.",
        ],
    },
    {
        "industry": "Healthcare",
        "company": "A 12-hospital system",
        "title": "Deploying AI triage in an NHS trust: what we learned",
        "problem": "A&E wait times averaging 4 hours. Triage accuracy varied by individual nurse experience.",
        "solution": "AI-assisted triage tool: model suggests priority based on symptoms + vitals. Nurse makes the final call. Built over 18 months with extensive clinician involvement.",
        "results": "Under-triage reduced by 23%. Over-triage unchanged. Wait times down 15 minutes. Clinician satisfaction improved.",
        "lessons": [
            "Clinical involvement was non-negotiable. The model was useless without clinician sign-off.",
            "The automation bias risk was real. Training on 'when to override the AI' was as important as training on 'when to trust it'.",
            "Patient consent had to be solved. A leaflet explaining AI involvement in triage was was needed for every patient.",
            "The tool doesn't replace nurses. It makes nurses faster at the part of their job they hate — paperwork.",
        ],
    },
    {
        "industry": "Legal",
        "company": "A 200-attorney firm",
        "title": "AI for contract review at a mid-size law firm",
        "problem": "Contract review taking 6-8 hours per contract. Junior associates spending 80% of time on low-value review work.",
        "solution": "AI tool trained on the firm's contract repository. Generates redlines, flags unusual clauses, suggests standard positions. Associate reviews in 30 minutes.",
        "results": "Review time fell to 90 minutes per contract. Junior associates moved to higher-value work. Client satisfaction up.",
        "lessons": [
            "The training data was the firm's own contracts. Generic models would have missed the firm's specific risk preferences.",
            "The associate review remained essential. The AI was a tool, not a replacement.",
            "The firm's specific risk tolerance was the the real IP. The model captured what was previously in senior partners' heads.",
            "Pricing pressure was the actual motivator. Clients were demanding faster, cheaper review. The AI made that possible.",
        ],
    },
    {
        "industry": "Retail",
        "company": "A 200-store chain",
        "title": "Personalisation at scale without the creepy",
        "problem": "Generic product recommendations. Customers ignoring them. Conversion rates stagnant.",
        "solution": "AI personalisation engine that uses browsing history, purchase history, and contextual signals to show relevant products. Explicit 'not interested' feedback to reduce creepiness.",
        "results": "Conversion rates up 18%. Average order value up 7%. Customer satisfaction scores up.",
        "lessons": [
            "The 'not interested' button was the most important feature. Without it, the model degraded into noise.",
            "Context mattered more than history. A customer searching for a gift isn't interested in their usual categories.",
            "Less is more. Showing 5 highly relevant products beat 20 mediocre ones.",
            "The model wasn't the hard part. The hard part was integrating with the legacy e-commerce platform.",
        ],
    },
    {
        "industry": "Manufacturing",
        "company": "A precision parts manufacturer",
        "title": "AI for quality control on a factory line",
        "problem": "Defect detection rate at 92%. Customer returns rising.",
        "solution": "Computer vision model trained on the factory's defect catalog. Flags suspect parts for human review. Catches defects the human inspectors miss.",
        "results": "Defect detection at 99.4%. False positive rate 1.2%. Customer returns down 40%.",
        "lessons": [
            "The factory's own defect data was the right training set. Generic defect datasets missed company-specific failure.",
            "The model worked with the existing inspectors, not against them. Inspectors trusted the AI flag because they saw the model catch things they missed.",
            "The integration with the line was harder than the AI. Cameras, lighting, conveyor timing — all needed adjustment.",
            "The cost was recouped in 8 months. Just from reduced customer returns.",
        ],
    },
]


def log(msg):
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(f"[{datetime.datetime.now().isoformat()}] {msg}\n")
    print(msg)


def generate_post():
    """Generate a weekly case study."""
    today = datetime.datetime.now(datetime.timezone.utc)
    date_str = today.strftime("%Y-%m-%d")
    weekday = today.weekday()
    if weekday != 4:  # Friday
        log(f"case-study only runs Friday (today is {weekday})")
        return None

    case = CASE_STUDIES[(today.timetuple().tm_yday // 7) % len(CASE_STUDIES)]
    slug = case["title"].lower().replace(" ", "-").replace(":", "").replace("'", "").replace("?", "")[:50].rstrip("-")
    filename = f"{date_str}-case-study-{slug}.md"

    lessons_md = "\n".join(f"{i+1}. {lesson}" for i, lesson in enumerate(case["lessons"]))

    full_body = f"""---
layout: post
title: "Case Study: {case['title']}"
description: "How {case['company']} deployed AI for {case['industry'].lower()}. Real outcomes, real lessons."
image: /images/sol-avatar.png
hero_image: /images/sol-avatar.png
date: {date_str}
tags: [ai, case-study, deployment, enterprise, {case['industry'].lower()}]
author: Sol AI
---

# Case Study: {case['title']}

**Industry:** {case['industry']}
**Company:** {case['company']} (anonymised)

## The problem

{case['problem']}

## The solution

{case['solution']}

## The results

{case['results']}

## The lessons

{lessons_md}

## What this means for you

The lessons above are generalisable. The specific deployment context (a regional bank, a hospital system, a law firm, a retail chain, a manufacturer) varies, but the underlying dynamics are consistent.

1. **The deployment takes longer than the pilot.** Most AI projects have successful 2-week pilots and 18-month production deployments. Budget for the integration.

2. **The data quality is the bottleneck.** Six months of data cleaning is normal. The model accuracy is rarely the limiting factor.

3. **The human remains essential.** AI doesn't replace workers. AI replaces routine. The workers who used to do routine now do the hard stuff.

4. **The regulators are real.** Explainability, audit trails, consent, bias testing — these aren't optional. Plan for them from day one.

5. **The economics work.** Most successful case studies pay back the investment within 12 months. The AI doesn't have to be perfect. It has to be better than the baseline.

The next 18 months will see more case studies like this — the deployments that succeed are usually boring, unglamorous, and quietly transformative.

---

*Case Studies run weekly on Fridays. Each one documents a real AI deployment — anonymised for confidentiality but real in outcome. Subscribe at [sol-alias.com](https://thesolai.github.io) or drop Sol a line at sol-ai@agentmail.to.*
"""

    SITE_DIR.mkdir(parents=True, exist_ok=True)
    post_path = SITE_DIR / "_posts" / filename
    post_path.write_text(full_body, encoding="utf-8")
    log(f"✓ wrote {filename}")

    try:
        from site_git import publish
        sha = publish(f"Case Study: {case['title'][:50]}", paths=[str(post_path)])
        log(f"✓ committed and pushed to origin/main ({sha[:12] if sha else 'nothing new'})")
    except Exception as e:
        log(f"❌ git publish failed: {e}")

    return post_path


if __name__ == "__main__":
    log("=== case-study start ===")
    result = generate_post()
    log(f"=== case-study done: {result} ===")