# SOP-Bench Upstream Response Note

Generated: 2026-06-05 America/Los_Angeles

This note summarizes the Amazon Science SOP-Bench maintainer responses to the
issues filed from the Proceda evaluation, the recent upstream commits they made,
and the implications for Proceda's benchmark claims.

## Short Version

Proceda's SOP-Bench work remains a strong demonstration of explicit SOP
execution, benchmark auditing, and traceable failure analysis. The recent
upstream response changes the interpretation of some adjusted results, though.

The maintainers did not say that Proceda's analysis was wrong. For the two
closed labeling issues, they effectively said that the released SOP text was
underspecified and that the benchmark labels encode intended human judgment or
severity policy. They resolved those issues by clarifying the SOP text to match
the CSV labels, not by changing the CSV labels.

That means the public "4 raw SOTA / 8 SOP-consistent SOTA" framing should be
tightened. A safer claim is:

> Proceda achieves 4 raw SOTA results on SOP-Bench, 100% ECR on the domains it
> ran, and uncovered several benchmark specification/data issues. Some adjusted
> "SOP-consistent" wins were valid against the initial released SOP text, but
> are now contested or superseded by upstream SOP clarifications.

## Current Issue Status

Status as verified from GitHub during the analysis:

| Issue | Domain | Current status | Maintainer response |
| --- | --- | --- | --- |
| [#2](https://github.com/amazon-science/SOP-Bench/issues/2) | Content Flagging | Open | No substantive maintainer response found. |
| [#3](https://github.com/amazon-science/SOP-Bench/issues/3) | Warehouse Inspection | Open | No substantive maintainer response found. |
| [#4](https://github.com/amazon-science/SOP-Bench/issues/4) | Referral Abuse v1 | Closed | Clarified SOP to use severity-first priority. Data unchanged. |
| [#5](https://github.com/amazon-science/SOP-Bench/issues/5) | Traffic Spoofing | Closed | Clarified SOP to allow investigator discretion for Medium-risk enforcement. Data unchanged. |
| [#6](https://github.com/amazon-science/SOP-Bench/issues/6) | Video Annotation | Open | No substantive maintainer response found. |
| [#7](https://github.com/amazon-science/SOP-Bench/issues/7) | Email Intent | Open | Duplicate/related issue was fixed; this issue itself remains open. |
| [#8](https://github.com/amazon-science/SOP-Bench/issues/8) | Know Your Business | Open | No substantive maintainer response found. |
| [#9](https://github.com/amazon-science/SOP-Bench/issues/9) | Video Classification | Open | Another user noted missing data/tools; no substantive maintainer response found. |
| [#10](https://github.com/amazon-science/SOP-Bench/issues/10) | Email Intent duplicate | Closed | Maintainer said the merge-conflict issue is fixed. |

## Recent Relevant Commits

### Referral Abuse v1

Commit:
[`1fdeba5949c8ef07f0c9a7e3a3b263f0999bd292`](https://github.com/amazon-science/SOP-Bench/commit/1fdeba5949c8ef07f0c9a7e3a3b263f0999bd292)

Message:

```text
fix: clarify referral_abuse_detection_v1 scoring as severity-first priority
```

What changed:

- The old SOP said to choose the violation type based on the highest score.
- The new SOP says to use severity-first priority.
- If any closure-category violation meets its threshold, select the highest
  scoring closure category.
- Otherwise, select the highest-scoring non-closure category.
- The test CSV did not change.

Meaning:

Proceda's original failure analysis was fair against the released SOP text. The
local Proceda skill followed the written "highest score" rule and therefore got
9 tasks wrong where the CSV preferred closure-category outcomes. Upstream has
now changed the written SOP to describe the CSV's closure-priority behavior.

So the original 95.5% raw TSR remains the official result for that run. The
100% "SOP-consistent" adjusted score should now be described as "consistent
with the initial released SOP text," not as a current upstream benchmark score.

### Traffic Spoofing

Commit:
[`2fdce4c57e6b02b725d5437ec079c142cffd8e07`](https://github.com/amazon-science/SOP-Bench/commit/2fdce4c57e6b02b725d5437ec079c142cffd8e07)

Message:

```text
Add investigator discretion for medium-risk enforcement actions

Closes #5
```

What changed:

- The old SOP mapped Medium risk to Temporary Suspension.
- The new SOP says Temporary Suspension is the default for Medium risk.
- It now explicitly allows investigators to issue Warning based on contextual
  factors such as traffic-pattern severity, source-verification findings, and
  engagement-anomaly indicators.
- The test CSV did not change.

Meaning:

The maintainer agreed that a strictly deterministic SOP-following agent that
uses only the risk-level mapping will hit a ceiling. They interpret that ceiling
as intentional "real-world ambiguity" rather than a labeling error.

Proceda's local skill still encodes the deterministic old rule, so its 39
Medium-risk "Temporary Suspension" answers were faithful to the old SOP text.
Under the revised SOP, those tasks are discretionary judgment cases, not clean
CSV bugs.

### Email Intent

Commit:
[`5460e3f96a88bf9bbc68d27528cc24bb5cb1ea7a`](https://github.com/amazon-science/SOP-Bench/commit/5460e3f96a88bf9bbc68d27528cc24bb5cb1ea7a)

Message:

```text
fix(data): Remove corrupted email_intent benchmark files
```

What changed:

- Merge-conflict markers were removed from the email_intent SOP, tools, and CSV
  files.
- Missing order_fulfillment metadata was added.
- The duplicate issue #10 was closed as fixed.

Meaning:

The original Proceda report was correct for the initial released benchmark, but
the public claim that Email Intent is currently unrunnable due to merge
conflicts is now stale against latest upstream. The original issue #7 remains
open, but the underlying merge-conflict problem appears to have been addressed
through #10 and the commit above.

## Implications For Proceda

Proceda performed the role it was designed for: convert a written SOP into a
structured executable artifact, execute it step by step, and produce traceable
evidence when the outcome disagrees with the benchmark labels.

The maintainer responses highlight an important distinction:

- Proceda is strongest at explicit, auditable SOP execution.
- SOP-Bench also tests latent human policy, discretion, and unstated priority
  rules in some domains.

For real enterprise use, Proceda's behavior is defensible. If a policy says
Medium risk -> Temporary Suspension, an agent should not silently invent a
Warning exception. It should follow the policy or escalate ambiguity to a human.

For benchmark optimization, however, Proceda may need one of the following:

1. Regenerate skills from the latest upstream SOP text and rerun affected
   domains.
2. Add an explicit "discretion/judgment" mode for SOP steps that require
   contextual policy interpretation.
3. Treat underspecified decision points as approval/clarification gates instead
   of deterministic model-only choices.

The product lesson is good: Proceda makes hidden policy visible. The benchmark
lesson is sharper: a system can be excellent at written SOP conformance and
still lose points when the benchmark rewards unstated operational norms.

## Implications For The Evaluation

The evaluation should separate three metrics:

| Metric | What it means | Use in claims |
| --- | --- | --- |
| Raw TSR | Exact match against upstream CSV labels. | Safe for official benchmark comparison. |
| Initial-text SOP conformance | Match against the SOP text as originally released. | Useful benchmark audit evidence, but not official SOTA. |
| Current-upstream TSR | Rerun after upstream SOP/data fixes. | Needed before making updated current benchmark claims. |

Recommended claim hygiene:

- Keep the 4 raw SOTA wins.
- Keep 100% ECR as a strong operational result.
- Reframe "8 of 10 SOP-consistent SOTA" as an audit finding from the initial
  release, not a current official leaderboard claim.
- Mark Email Intent as "fixed upstream after report" rather than still
  currently unrunnable.
- Treat Referral Abuse v1 and Traffic Spoofing adjusted scores as superseded by
  upstream SOP clarifications unless rerun against the new SOPs.
- Be cautious about Know Your Business: upstream may apply the same
  "real-world ambiguity" argument there.

## Suggested Public-Report Revision

Replace strong language like:

> Proceda beats the best published baseline on 8 of 10 domains when measured on
> tasks where the benchmark's ground truth is consistent with its own SOP rules.

With:

> Proceda achieves 4 raw SOTA results on SOP-Bench. In a separate audit against
> the initially released SOP text, Proceda exposed several cases where labels
> depended on unstated policy or discretion; upstream has since clarified some
> SOPs to make those policies explicit.

This preserves the real achievement and makes the report much harder to attack.

## Local Follow-Ups

- Update `docs/sop-bench-results.md` and `docs/sop-bench-results.html` before
  further public promotion.
- Update `README.md`, which currently repeats the 8-of-10 SOP-consistent claim.
- Regenerate `referral_abuse_detection_v1` and `traffic_spoofing_detection`
  skills from the latest upstream SOP text if current-upstream reruns are
  desired.
- Re-check `email_intent` against latest upstream; it may now be runnable.
- Consider preserving the initial-release analysis as a versioned benchmark
  audit tied to SOP-Bench initial commit
  `156e9ecd60f42c43e4f3a12824e466afff21e9d8`.
