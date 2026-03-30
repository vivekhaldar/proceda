---
name: dangerous-goods
description: Systematically identifies and classifies dangerous goods hazard classes using multi-source data and quantitative severity assessment.
required_tools:
  - calculate_sds_label_score
  - calculate_handling_score
  - calculate_transportation_score
  - calculate_disposal_score
output_fields:
  - hazard_class
---

### Step 1: Validate Product Data
Verify the completeness of product identification documentation using the Document Validation Protocol (DVP-2023). Cross-reference the product ID against the master dangerous goods database. If the product ID fails to meet format requirements (e.g., missing "P_" prefix, invalid character set, or non-resolvable reference), stop the process immediately, set the hazard score to 0, and set the hazard class to "Unable to Decide".

### Step 2: Analyze Safety Data Sheet
Extract hazard statements from Section 2 of the Safety Data Sheet (SDS). Use the `calculate_sds_label_score` tool with the `product_id` and `sds_label_text` to obtain a safety data sheet score. Validate that the resulting score is between 1 and 5.

### Step 3: Evaluate Handling and Storage
Parse the handling and storage guidelines. Use the `calculate_handling_score` tool with the `product_id`, `handling_and_storage_guidelines`, and the `assessmentFormId` (CAF-01) to compute a handling score. Validate that the score is between 1 and 5.

### Step 4: Assess Transportation Requirements
Analyze transportation requirements using the Transportation Compliance Module. Use the `calculate_transportation_score` tool with the `product_id` and `transportation_requirements` to obtain a transportation severity score. Validate that the score is between 1 and 5.

### Step 5: Evaluate Disposal Guidelines
Process disposal guidelines through the waste classification API. Use the `calculate_disposal_score` tool with the `product_id` and `disposal_guidelines` to calculate a disposal severity score. Validate that the score is between 1 and 5.

### Step 6: Compute Cumulative Hazard Score
Calculate the cumulative `hazard_score` using the formula: `hazard_score = safety score + handling score + transportation score + disposal score`. 
- If any single component is missing or 0, impute it by taking the maximum value of the other available scores.
- If more than two component scores are missing, set the hazard class to "Unable to Decide" and the hazard score to 0.
- Validate that the final total score is within the acceptable range (4-20) unless marked as 0.

### Step 7: Determine and Register Hazard Class
[APPROVAL REQUIRED]
Assign a hazard class based on the cumulative score, where higher scores indicate higher severity:
- Hazard Class A (Lowest severity)
- Hazard Class B
- Hazard Class C
- Hazard Class D (Highest severity)
- Unable to Decide (If criteria in Step 1 or Step 6 are met)

Document the final classification in the Hazard Classification Registry and ensure all API response logs and audit trails are preserved. The final output must be formatted in XML.

Include the final values in the summary:
<hazard_score>value</hazard_score>
<hazard_class>value</hazard_class>