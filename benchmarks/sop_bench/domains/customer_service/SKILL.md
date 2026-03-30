---
name: customer-service
description: A structured offline process for diagnosing and resolving customer-reported service issues using internal data and automated tools.
required_tools:
  - validateAccount
  - getAuthenticationDetails
  - createSessionAndOpenTicket
  - checkAccountStatus
  - checkAccountSuspensionStatus
  - checkPaymentStatus
  - checkServiceAreaOutage
  - performTechnicalDiagnostics
  - executeTroubleshooting
  - createEscalation
output_fields:
  - final_resolution_status
---

### Step 1: Validate Account ID and Authenticate
Validate the format of the provided Account ID using the `validateAccount` tool with the `account_id` parameter. If the format is invalid, terminate the process. If valid, use the `getAuthenticationDetails` tool, passing the `account_id` and setting `is_account_id_valid` to true. Review the authentication history; if there are failed attempts without a successful recovery, close the case. If authentication requirements are met, use the `createSessionAndOpenTicket` tool with `account_id`, `is_account_id_valid` (true), and `is_authenticated` (true) to generate a session token and ticket ID.

### Step 2: Evaluate Account Status and Eligibility
Query the account status using the `checkAccountStatus` tool with the `account_id` and `session_token`. If the account is "Terminated", log the reason and end the case. If the account is "Suspended", use the `checkAccountSuspensionStatus` tool. If the suspension is due to non-payment, use the `checkPaymentStatus` tool to coordinate with Accounts Payable. If the suspension is for any other reason, conclude the case. If the account is "Active" or the suspension is lifted, proceed.

### Step 3: Analyze Service Area for Outages
Access the outage monitoring system using the `checkServiceAreaOutage` tool. Provide the `account_id`, `session_token`, and the customer's `service_area_code`. If an outage is detected within a 10-mile radius, log the outage ID, impact scope, and estimated resolution time, then conclude the diagnostics. If no outage is found, proceed to technical diagnostics.

### Step 4: Perform Technical Diagnostics
Execute the `performTechnicalDiagnostics` tool using the `account_id`, `session_token`, `service_type` (e.g., internet, voice, video), and the customer's `subscribed_bandwidth`. Evaluate the returned metrics: flag latency > 100ms, jitter > 30ms, or bandwidth below the subscribed plan. Identify and rank potential root causes based on these results and record all findings in the service ticket.

### Step 5: Execute Troubleshooting
Run the `executeTroubleshooting` tool using the `account_id`, `session_token`, and the list of `root_causes` identified in the previous step. This tool performs actions like modem resets or signal refreshes. After completion, re-run the diagnostics to check for metric improvements. If the metrics improve, classify the issue as fixed. If no significant improvement is observed, proceed to escalation.

### Step 6: Escalate Unresolved Issues
If troubleshooting fails, use the `createEscalation` tool. Provide the `session_token`, `ticket_id`, and set `metrics_improved_post_troubleshooting` to false and `escalation_required` to true. Assign the ticket to the appropriate group: Tier 2 Technical Support for complex issues, Field Operations for on-site problems, or Network Engineering for infrastructure issues.

### Step 7: Final Documentation and Resolution Summary
Compile a comprehensive Resolution Summary Document (RSD) as a JSON object. The summary must include account details, authentication results, status, diagnostic data, actions taken, and timestamps. Update the ticket with the final status: `RESOLVED`, `PENDING_ACTION`, `ESCALATED`, or `FAILED`.

Include the final status in your summary using the following format:
<final_resolution_status>VALUE</final_resolution_status>

Output the final RSD within `<final_output>` tags.