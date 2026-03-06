---
id: customer_escalation
name: Customer Escalation Response
description: Triage and resolve a high-priority customer support escalation
required_tools: ticketing.get_case, kb.search, pagerduty.create_incident
---

# Customer Escalation SOP

Run this skill for P1 support escalations.

## Step 1: Gather case context
- Pull customer ticket details, severity, and impacted features.
- Summarize current status in one paragraph.

## Step 2: Identify likely root cause [requires_approval]
- Search runbooks and known incidents.
- Present top 2 hypotheses with confidence.

## Step 3: Trigger incident response [requires_post_approval]
- Open incident with clear title and impact statement.
- Attach relevant ticket and runbook links.

## Step 4: Request customer-facing update draft
- Draft a concise customer update message.
