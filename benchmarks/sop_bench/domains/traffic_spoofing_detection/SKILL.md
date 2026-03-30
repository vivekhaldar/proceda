---
name: traffic-spoofing-detection
description: Detects and investigates traffic spoofing violations in affiliate marketing to determine enforcement actions.
required_tools:
  - InvestigateViolations
  - AnalyzeTrafficPatterns
  - ValidateReferralSources
  - CalculateRiskScore
  - GenerateEvidenceReport
  - ExecuteEnforcementAction
output_fields:
  - enforcement_action
---

### Step 1: Validate Partner Account
Execute the Traffic Attribution Validation Matrix (TAVM) by calling the `InvestigateViolations` tool. Use the `partner_id`, `registered_websites`, and `earnings_amount` to cross-reference authenticity parameters and validate the unique users to total orders ratio (minimum 0.4 ratio required for legitimacy).

### Step 2: Analyze Traffic Patterns
Analyze the traffic data for suspicious patterns using the `AnalyzeTrafficPatterns` tool. Input the `partner_id`, `engagement_score`, `conversion_rate`, and `bounce_rate`. Verify if the engagement score is below 1.0, if the conversion rate is below 0.05%, and if the bounce rate exceeds 90%. Document any click spikes exceeding 10x the baseline and check if device traffic distribution is skewed (>80% single device type).

### Step 3: Authenticate Referral Sources
Perform a source verification sequence using the `ValidateReferralSources` tool. Provide the `partner_id`, `unattributed_clicks`, and `top_referral_source`. Evaluate if unattributed clicks exceed 50% and if visit durations are suspiciously short (less than 30 seconds).

### Step 4: Classify Violation and Calculate Risk
Determine the specific violation type (e.g., traffic cloaking, spoofing traffic, redirect traffic, or blank source) based on the gathered data. Use the `CalculateRiskScore` tool with the `partner_id`, `violation_type`, `engagement_score`, and `conversion_rate` to determine the severity of the violation.

### Step 5: Document Evidence
[APPROVAL REQUIRED]
Generate a comprehensive evidence report for the Partner Violation Documentation System (PVDS) using the `GenerateEvidenceReport` tool. Include the `partner_id`, the determined `violation_type`, and a detailed list of `evidence_collected` from the previous analysis steps.

### Step 6: Determine and Execute Enforcement Action
Based on the risk level and violation type, determine the final enforcement action: "Account Closure" (High risk), "Temporary Suspension" (Medium risk), "Warning Issued" (Low risk with evidence), or "No Action" (Low risk without evidence). Execute the action using the `ExecuteEnforcementAction` tool with the `partner_id`, `risk_level`, and `violation_type`.

### Step 7: Finalize Investigation
Update the digital violation records and partner lifecycle tracking data. Provide the final enforcement action in the summary using the required XML format: <enforcement_action>value</enforcement_action>.