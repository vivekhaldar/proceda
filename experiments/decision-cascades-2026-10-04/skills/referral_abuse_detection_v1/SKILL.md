---
name: referral-abuse-detection-v1
description: Detects referral abuse violations by investigating accounts, analyzing traffic patterns, and scoring violation indicators to determine enforcement action.
required_tools:
  - investigate_account
  - analyze_traffic_patterns
  - determine_enforcement_action
output_fields:
  - enforcement_action
---

### Step 1: Investigate Account
Call `investigate_account` with `account_id`. Record all returned fields: `address_validity`, `email_pattern_suspicious`, `website_verified`, `business_description`, `account_status`, `connected_accounts`, and `login_geographic_consistency`.

### Step 2: Analyze Traffic Patterns
Call `analyze_traffic_patterns` with `account_id`. Record all returned fields: `revenue_amount`, `click_through_rate`, `page_views`, `device_distribution`, `referral_source_quality`, `payment_method_shared`, and `order_patterns_suspicious`.

### Step 3: Get Enforcement Guidelines
Call `determine_enforcement_action` with `account_id` to retrieve enforcement action guidelines.

### Step 4: Calculate Violation Scores and Determine Enforcement Action
5.5 Determine Enforcement Action
5.5.1 Analyze tool outputs to calculate a violation score and determine the violation type:

   STEP 1: Calculate Violation Indicators (count how many are TRUE)
   
   IMPORTANT: Boolean fields (address_validity, email_pattern_suspicious, etc.) are actual boolean values (true/false), NOT strings.
   Compare them directly as booleans: if address_validity is false, if email_pattern_suspicious is true, etc.
   
   Abusive Account Creation Score (count TRUE indicators):
   - address_validity is false (+1)
   - email_pattern_suspicious is true (+1)
   - website_verified is false (+1)
   - connected_accounts >= 15 (+1)
   - login_geographic_consistency is false (+1)
   Score >= 3 → Abusive Account Creation
   
   Misleading Ad Copy Score (count TRUE indicators):
   - website_verified is false (+1)
   - referral_source_quality is "Low" or "Medium" (+1)
   - order_patterns_suspicious is true (+1)
   - click_through_rate > 0.4 (+1)
   Score >= 3 → Misleading Ad Copy
   
   Personal Orders Score (count TRUE indicators):
   - payment_method_shared is true (+1)
   - connected_accounts > 0 AND connected_accounts < 15 (+1)
   - order_patterns_suspicious is true (+1)
   - referral_source_quality is "High" (+1)
   Score >= 3 → Personal Orders (Related)
   
   No Violation Score (count TRUE indicators):
   - address_validity is true (+1)
   - email_pattern_suspicious is false (+1)
   - website_verified is true (+1)
   - login_geographic_consistency is true (+1)
   - payment_method_shared is false (+1)
   - order_patterns_suspicious is false (+1)
   Score >= 4 → No Violation

   STEP 2: Determine violation type based on severity-first priority
   - If any closure-category (Abusive Account Creation, Misleading Ad Copy) meets its threshold, select it (highest score among closure-categories)
   - Otherwise, select the highest-scoring non-closure category
   - If no score meets threshold → INCONCLUSIVE
   - If tied scores → Use priority order: Abusive Account Creation > Misleading Ad Copy > Personal Orders

5.5.2 Apply enforcement action based on determined violation type:
   - Abusive Account Creation → "Account Closure"
   - Misleading Ad Copy → "Account Closure"
   - Personal Orders (Related) → "No Action"
   - No Violation → "No Action"
   - INCONCLUSIVE → "Inconclusive" (insufficient evidence)


### Step 5: Report the Enforcement Action
Output the exact enforcement action determined in Step 4 in <enforcement_action>...</enforcement_action> tags in the final complete_step summary. The allowed actions are Account Closure, No Action, and Inconclusive. Do not execute account closure; this procedure reports the decision.
