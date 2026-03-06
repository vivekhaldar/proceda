---
id: expense_approval
name: Expense Approval
description: Validate and approve a submitted expense report
required_tools: ledger.lookup_employee, ledger.submit_expense, slack.notify_manager
---

# Expense Approval SOP

Use this procedure when an employee submits an expense reimbursement request.

## Step 1: Validate the request [requires_approval]
- Confirm the report has employee id, amount, date, and receipt URL.
- If anything is missing, request clarification with exact missing fields.
- If the amount exceeds policy thresholds, flag it in your step summary.

## Step 2: Cross-check policy
- Check category limits and monthly cap.
- If policy conflict exists, include the specific policy clause.

## Step 3: Submit reimbursement [requires_post_approval]
- Submit only after policy checks pass.
- Include a concise audit note with employee id and policy outcome.

## Step 4: Notify manager [optional]
- Send completion summary and ticket link.
