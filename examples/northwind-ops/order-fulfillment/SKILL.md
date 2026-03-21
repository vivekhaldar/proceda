---
name: order-fulfillment
description: Process a pending order by verifying the customer, checking inventory, and updating shipment status
required_tools:
  - northwind__query
  - northwind__execute
  - northwind__get_schema
---

### Step 1: Look up order and customer

First use get_schema to learn the database table structure. Note: the order line items table is called "Order Details" (with a space — use square brackets: [Order Details]).

Then find a recent pending order (one where ShippedDate IS NULL) in the Orders table. Pick just one order — use LIMIT 1.

Join with the Customers table to display:
- Order ID, order date, required date
- Customer company name, contact name, country
- Freight cost and ship-to address

### Step 2: Check product inventory

Query [Order Details] for the order found in Step 1. For each line item, join with Products to check that UnitsInStock is sufficient to cover the Quantity ordered.

Present a table showing each product name, quantity ordered, units currently in stock, and whether stock is sufficient. Flag any items that cannot be fulfilled.

### Step 3: Update order and inventory

[PRE-APPROVAL REQUIRED]

Execute at most 3 statements total:
1. UPDATE Orders SET ShippedDate = date('now') WHERE OrderID = <id>
2. For EACH product in the order, run ONE update: UPDATE Products SET UnitsInStock = UnitsInStock - <qty> WHERE ProductID = <id>

Keep the number of execute calls to a minimum. Only update products that are in stock.

### Step 4: Generate shipping summary

Query the Shippers table using the order's ShipVia field to identify the carrier.

Compile a final summary:
- Order ID and customer
- Items shipped with quantities and unit prices
- Total order value (sum of UnitPrice * Quantity * (1 - Discount) from [Order Details])
- Shipping carrier and freight cost
- Any items that could not be fulfilled
