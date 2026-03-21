---
name: monthly-sales-report
description: Generate a monthly sales analysis with top products, customers, and anomaly detection
required_tools:
  - northwind__query
  - northwind__get_schema
---

### Step 1: Aggregate monthly sales

First check the schema and the date range of orders in the database to understand what data is available.

Then aggregate sales for the most recent complete month of data. Calculate:
- Total revenue (sum of UnitPrice * Quantity * (1 - Discount) from OrderDetails)
- Number of orders
- Number of unique customers who placed orders
- Average order value

Compare these metrics to the previous month if data is available.

### Step 2: Identify top performers

Find the top 5 products by revenue for the month, including:
- Product name, category, total revenue, units sold

Find the top 5 customers by order volume:
- Company name, number of orders, total spend

Also identify the top-performing product category by total revenue.

### Step 3: Flag anomalies

Look for unusual patterns in the data:
- Orders with total value more than 3x the average order value
- Products with a sudden spike in demand compared to the prior month
- Customers who placed orders in the previous month but not this month (churn risk)
- Any region or country with a significant change in order volume

Present each anomaly with supporting data.

### Step 4: Generate executive summary

Compile all findings into a structured report:
- Key metrics with month-over-month trends
- Top performers (products and customers)
- Anomalies and recommended actions
- One-paragraph narrative summary suitable for an executive audience
