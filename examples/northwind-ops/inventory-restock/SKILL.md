---
name: inventory-restock
description: Identify low-stock products and create purchase orders from suppliers
required_tools:
  - northwind__query
  - northwind__execute
  - northwind__get_schema
---

### Step 1: Identify low-stock products

Query the Products table for items where UnitsInStock is at or below the ReorderLevel and Discontinued is 0 (still active).

For each product, display:
- Product name and category (join with Categories)
- Current UnitsInStock vs. ReorderLevel
- UnitsOnOrder (already in the pipeline)
- UnitPrice

Sort by urgency: products furthest below their reorder level first.

### Step 2: Find suppliers

For each low-stock product from Step 1, look up the supplier from the Suppliers table using SupplierID.

Display supplier company name, contact name, country, and phone for each product. Note if multiple low-stock products share the same supplier (consolidation opportunity).

### Step 3: Calculate restock quantities

For each product, recommend an order quantity that would bring stock up to twice the ReorderLevel, minus any UnitsOnOrder already in the pipeline.

Formula: order_qty = (ReorderLevel * 2) - UnitsInStock - UnitsOnOrder

Present a summary table with product name, supplier, recommended quantity, estimated cost (quantity * UnitPrice), and total across all products.

### Step 4: Create purchase orders

[PRE-APPROVAL REQUIRED]

For each product being restocked, update the Products table:
- Add the recommended quantity to UnitsOnOrder

Report the total estimated purchase value and the number of products being restocked.

### Step 5: Verify updates

[OPTIONAL]

Query the restocked products to confirm UnitsOnOrder reflects the new values. Display a before/after comparison.
