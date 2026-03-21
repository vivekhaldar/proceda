---
name: order-fulfillment
description: Process a pending order by verifying the customer, checking inventory, and updating shipment status
required_tools:
  - northwind__query
  - northwind__execute
---

### Step 1: Look up order and customer

Find a recent pending order (one where ShippedDate IS NULL) in the Orders table. If a specific OrderID was provided as a variable, use that instead.

Join with the Customers table to display:
- Order ID, order date, required date
- Customer company name, contact name, country
- Freight cost and ship-to address

### Step 2: Check product inventory

Query OrderDetails for the order found in Step 1. For each line item, join with Products to check that UnitsInStock is sufficient to cover the Quantity ordered.

Present a table showing each product name, quantity ordered, units currently in stock, and whether stock is sufficient. Flag any items that cannot be fulfilled.

### Step 3: Update order and inventory

[PRE-APPROVAL REQUIRED]

If all items are in stock:
- Set the order's ShippedDate to today's date
- For each line item, reduce the product's UnitsInStock by the quantity ordered

If some items are out of stock, explain which items cannot ship and only update the ones that can. Set ShippedDate only if at least one item ships.

Report exactly which UPDATE statements will be executed before running them.

### Step 4: Generate shipping summary

Query the Shippers table using the order's ShipVia field to identify the carrier.

Compile a final summary:
- Order ID and customer
- Items shipped with quantities and unit prices
- Total order value (sum of UnitPrice * Quantity * (1 - Discount) from OrderDetails)
- Shipping carrier and freight cost
- Any items that could not be fulfilled
