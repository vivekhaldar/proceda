---
name: customer-onboarding
description: Add a new customer to the Northwind database with duplicate checking and territory assignment
required_tools:
  - northwind__query
  - northwind__execute
---

### Step 1: Collect customer information

Ask the user for the new customer's details:
- Company name (required)
- Contact name and title
- Address, city, region, postal code, country
- Phone and fax

Also generate a CustomerID: take the first 5 characters of the company name, uppercased, letters only. If that ID already exists, append a number.

### Step 2: Check for duplicates

Query the Customers table to check if:
- A company with the same name already exists
- A company with the same phone number already exists
- The generated CustomerID is already taken

Report any matches found. If duplicates exist, ask the user whether to proceed or abort.

### Step 3: Assign sales representative

Query Employees to find a sales representative. Use a query like:
SELECT e.EmployeeID, e.FirstName, e.LastName, e.Title, COUNT(DISTINCT o.CustomerID) as customer_count
FROM Employees e JOIN Orders o ON e.EmployeeID = o.EmployeeID
JOIN Customers c ON o.CustomerID = c.CustomerID
GROUP BY e.EmployeeID ORDER BY customer_count DESC LIMIT 5

Pick the employee who handles the most customers. Present the recommended sales rep with their name and title.

### Step 4: Insert customer record

[APPROVAL REQUIRED]

Insert the new customer into the Customers table with all collected fields.

After insertion, query the record back to confirm it was created correctly. Display the complete customer record.
