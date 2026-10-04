---
name: email-intent
description: Classify seller appeal email intent and determine the appropriate catalog response.
required_tools:
  - get_product_price
  - get_product_description
  - get_product_listing_status
  - get_inventory_status
output_fields:
  - seller_intent
  - action
---

### Step 1: Extract and Validate Product ID and Identify Seller Intent
Read email_body. Extract the product_id using regular-expression pattern matching. The product ID is six alphanumeric characters beginning with P; the tools require P followed by five uppercase alphanumeric characters. Use the marketplace_id provided in the task when a tool requires it.
Identify the seller intent from the email: concern about their product not being listed; concern about incorrect pricing; concern about incorrect description; generic question about a listing; or unable to decide.

### Step 2: Retrieve Relevant Catalog Evidence and Determine the Action
Use the matching conditional branch below. The available tool parameters are:
- get_product_price(product_id, marketplace_id)
- get_product_description(product_id)
- get_product_listing_status(product_id, marketplace_id, include_history=false)
- get_inventory_status(product_id, marketplace_id, include_forecasts=false)
Only use validated product IDs. For listing-status concerns, retrieve both listing status and inventory. For pricing concerns, retrieve the price and compare it with the seller-specified price. For description concerns, retrieve the current description and compare it with the requested description. Generic questions need no catalog tool call.

5.2 Intent-Based Processing
  Identify the intent of the seller's email body. It could be one of the following:
  a. concern about their product not being listed
  b. concern about incorrect pricing
  c. concern about incorrect description
  d. generic question about a listing
  e. unable to decide

  For pricing concerns: Get the current price from the pricing database using validated product_id only and compare returned price value against seller-specified 
  price. Calculate price delta and determine if update is warranted. Assign action "update price" if delta exists, otherwise "no action"

  For description concerns: Get the description in the database, using the validated product_id. If it is significantly different from the requested description in 
  the email, then assign action "update description", otherwise "no action".

  For listing status concerns: Get the current listing status and inventory count from the product database and summarize and assign action "share listing status" 
  and prepare status communication.

  For generic inquiries: Document inquiry category in system, assign action "no action" and prepare standardized response template

  The action could be one of the following:
  (a) "no action"
  (b) "update price"
  (c) "update description"
  (d) "share listing status"
  (e) "further clarification required"


### Step 3: Report Intent and Action
Return both seller_intent and action in the required JSON object within <final_output> tags. Also include the same values in <seller_intent> and <action> XML tags in the final complete_step summary, so structured fields are unambiguous. Do not invent an update action when the observed comparison does not warrant it. The available benchmark tools retrieve records; they do not apply catalog updates or send messages.
