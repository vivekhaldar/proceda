---
name: returns-processing
description: Process a product return by verifying the order, checking eligibility, and updating inventory
required_tools:
  - northwind__query
  - northwind__execute
---

### Step 1: Verify original order

Look up the order by OrderID (provided by the user). Confirm it exists and display:
- Customer company name and contact
- Order date and shipped date
- All line items with product names, quantities, and prices

If the order has no ShippedDate (never shipped), report that returns cannot be processed for unshipped orders.

### Step 2: Check return eligibility

Calculate the number of days since the order was shipped. The return window is 30 days from ShippedDate.

Determine eligibility:
- If within 30 days: eligible for full refund
- If 31-60 days: eligible for store credit only (note this but proceed)
- If beyond 60 days: not eligible for return

Present the eligibility determination with the specific dates and day count.

### Step 3: Calculate refund

[PRE-APPROVAL REQUIRED]

For the items being returned (ask the user which items and quantities if not specified, or assume all items), calculate:
- Refund per item: UnitPrice * Quantity * (1 - Discount)
- Total refund amount
- Whether this is a full or partial return

Present the refund breakdown for approval before proceeding.

### Step 4: Process return

[PRE-APPROVAL REQUIRED]

Update the database to reflect the return:
- Reduce the Quantity in OrderDetails for returned items (or delete the row if full quantity is returned)
- Increase UnitsInStock in the Products table for each returned product

Report each SQL statement before executing it.

### Step 5: Confirm processing

Query the updated OrderDetails and Products records to verify all changes were applied.

Summarize the completed return:
- Original order ID and customer
- Returned items and quantities
- Refund amount
- Updated inventory levels for affected products
