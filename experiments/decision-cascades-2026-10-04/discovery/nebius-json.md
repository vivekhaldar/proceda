> ## Documentation Index
> Fetch the complete documentation index at: https://docs.tokenfactory.nebius.com/llms.txt
> Use this file to discover all available pages before exploring further.

# Structured output & JSON

> Learn how to get structured output from our text models

## Overview

By default, a text generation model responds to your requests with a text in natural language. You can also force the model to respond with JSON instead, which makes it easier to work with outputs programmatically in your application.

For example, you can tell a model the name of an actor or actress and get a response with details of a film they have starred in, in a format like this:

```json theme={null}
{
  "title": "The Shining",
  "year": 1980,
  "director": "Stanley Kubrick",
  "cast": ["Jack Nicholson", "Shelley Duvall", "Danny Lloyd"],
}
```

Use `response_format`parameter to get JSON output. Depending on the settings in your request, the response can be:

* **JSON schema following.** Follows the specific schema you provide - `{"type": "json_schema"}` in `response_format `parameter.
* **Arbitrary JSON object.** Forces arbitrary JSON output, the model will decide on the schema by itself `{"type": "json_object"}` in `response_format `parameter.

<Info>
  **Supported models**\
  Some models are better in providing JSON due to their training data or our inference engine limitations. Use `JSON mode` tag in on a model card to find a model with structured output supported.
</Info>

<Steps>
  <Step title="Choose response format type">
    Select between strict `"json_schema"` and arbitrary `"json_object"`
  </Step>

  <Step title="Optionally: provide JSON schema">
    Provide the schema in a [JSON Schema Specification](https://json-schema.org/specification) compliant format.
  </Step>

  <Step title="Add instructions to the system or user prompt">
    It's always better to provide the instruction to follow JSON output and the schema to your model's system or user prompt to increase output quality and consistency
  </Step>

  <Step title="Test several models">
    Different models has different structured output capabilities. Always test and compare a few to obtain the best result
  </Step>
</Steps>

## JSON Schema following

JSON that follows a schema provided in your request. Make a request to the Nebius Token Factory API with `{"type": "json_schema"}` in `response_format `and provide desirable schema in a [JSON Schema Specification](https://json-schema.org/specification) compliant format.

<Tip>
  Many practical tests show that it's always better to provide the schema both in text prompt for the model and`"json_schema"`**parameter**
</Tip>

<CodeGroup>
  ```python Python expandable theme={null}
  import os
  import json
  from openai import OpenAI
  from typing import List, Literal
  from pydantic import BaseModel

  # 1. Define a schema using Pydantic's `BaseModel`. You can also define 
  # a JSON Schema directly; see details after this code example

  class Film(BaseModel):
    title: str
    year: int
    director: str
    cast: List[str]
    genre: Literal[
        "drama", "thriller", "sci-fi",
        "comedy", "horror", "fantasy"
    ]

  # 2. Add the schema to your request

  client = OpenAI(
      base_url="https://api.tokenfactory.nebius.com/v1/",
      api_key=os.environ.get("NEBIUS_API_KEY"),
  )

  completion = client.chat.completions.create(
      model="Qwen/Qwen3-235B-A22B",
  	response_format={
          	"type": "json_schema"
  			"json_schema": Film.model_json_schema()
      	}
      messages=[
          {
              "role": "system",
              "content": (
                  "I will give you an actor or actress, and you will respond "
                  "with details of a real film they have starred in, according "
                  "to the provided structure."
              )
          },
          {
              "role": "user",
              "content": "Jack Nicholson"
          }
      ],

  )

  # 3. Work with the JSON output or a refusal

  output = completion.choices[0].message
  if output.refusal:
      # Handle refusal
      print(output.refusal)
  elif output.content:
      try:
          output_json = json.loads(output.content)
          print(output_json)
          print("Film: {} ({})".format(output_json['title'], 
                                       output_json['year']))
          # etc.
      except Exception as e:
          # Handle possible exceptions, e.g. invalid JSON
          print(e)
          pass
  ```

  ```bash cURL expandable theme={null}
  # 1. Define a JSON Schema

  export SCHEMA='
  {
    "type": "object",
    "properties": {
      "title": {"type": "string"},
      "year": {"type": "integer"},
      "director": {"type": "string"},
      "cast": {
        "type": "array",
        "items": {"type": "string"}
      },
      "genre": {
        "enum": [
          "drama", "thriller", "sci-fi",
          "comedy", "horror", "fantasy"
        ]
      }
    },
    "required": ["title", "year", "director", "cast", "genre"]
  }
  '

  # 2. Add the schema to your request

  export OUTPUT=$(curl 'https://api.tokenfactory.nebius.com/v1/chat/completions' \
    -X 'POST' \
    -H 'Content-Type: application/json' \
    -H 'Accept: */*' \
    -H "Authorization: Bearer $NEBIUS_API_KEY" \
    --data-binary '
      {
        "model": "mistralai/Mistral-Nemo-Instruct-2407",
        "messages": [
          {
            "role": "system",
            "content": "I will give you an actor or actress, and you will respond with details of a real film they have starred in, according to the provided structure."

          },
          {
            "role": "user",
            "content": "Jack Nicholson"
          }
        ],
        "response format": {
          	"type": "json_schema"
  			"json_schema": '"$SCHEMA"'
      	}  
      }
    '
  )

  # 3. Work with the JSON output or a refusal

  echo $OUTPUT | jq '
    .choices[0].message.refusal
    // (.choices[0].message.content | fromjson | .title)
  '
  ```

  ```javascript JavaScript expandable theme={null}
  import OpenAI from "openai";
  import { z } from "zod";
  import { zodResponseFormat } from "openai/helpers/zod";

  // 1. Define a schema using Zod. You can also define a JSON Schema directly;
  // see details after this code example

  const Film = z.object({
    title: z.string(),
    year: z.number(),
    director: z.string(),
    cast: z.array(z.string()),
    genre: z.enum(["drama", "thriller", "sci-fi", "comedy", "horror", "fantasy"]),
  });

  // 2. Add the schema to your request

  const client = new OpenAI({
    baseURL: "https://api.tokenfactory.nebius.com/v1/",
    apiKey: process.env.NEBIUS_API_KEY,
  });

  client.chat.completions.create({
    model: "openai/gpt-oss-120b",
    messages: [
      {
        role: "system",
        content: "I will give you an actor or actress, and you will respond with details of a real film they have starred in, according to the provided structure.",
      },
      {
        role: "user",
        content: "Jack Nicholson",
      },
    ],
    response_format: zodResponseFormat(Film, "film")
  })
  // 3. Work with the JSON output or a refusal
  .then((completion) => {
    const output = completion.choices[0].message;
    if (output.refusal) {
      // Handle refusal
      console.log(output.refusal);
    } else if (output.content) {
      try {
        const output_json = JSON.parse(output.content);
        console.log(`Film: ${output_json.title} (${output_json.year})`);
        // etc.
      } catch (e) {
        // Handle possible exceptions, e.g. invalid JSON
        console.log("An error occurred: ", e.message);
      }
    }
  });
  ```
</CodeGroup>

Example of a valid JSON schema:

```json expandable theme={null}
{
  "name": "meal_nutrition",
  "schema": {
    "type": "object",
    "properties": {
      "meal_name": {
        "type": "string",
        "description": "Name or description of the meal",
        "minLength": 1
      },
      "serving_size_g": {
        "type": "number",
        "description": "Serving size in grams",
        "minimum": 1
      },
      "nutrients": {
        "type": "object",
        "description": "Macronutrient and micronutrient content per serving",
        "properties": {
          "calories": {
            "type": "number",
            "description": "Energy in kilocalories (kcal)",
            "minimum": 0
          },
          "protein_g": {
            "type": "number",
            "description": "Protein in grams",
            "minimum": 0
          },
          "carbohydrates_g": {
            "type": "number",
            "description": "Carbohydrates in grams",
            "minimum": 0
          },
          "fat_g": {
            "type": "number",
            "description": "Total fat in grams",
            "minimum": 0
          },
          "fiber_g": {
            "type": "number",
            "description": "Dietary fiber in grams",
            "minimum": 0
          },
          "sugars_g": {
            "type": "number",
            "description": "Sugars in grams",
            "minimum": 0
          },
          "sodium_mg": {
            "type": "number",
            "description": "Sodium in milligrams",
            "minimum": 0
          }
        },
        "required": [
          "calories",
          "protein_g",
          "carbohydrates_g",
          "fat_g",
          "fiber_g",
          "sugars_g",
          "sodium_mg"
        ],
        "additionalProperties": false
      }
    },
    "required": [
      "meal_name",
      "serving_size_g",
      "nutrients"
    ],
    "additionalProperties": false
  },
  "strict": true
}
```

## Arbitrary JSON object

The model will produce a valid JSON object without following any specific schema.\
Make a request to the Nebius Token Factory API with `{"type": "json_object"}` in `response_format`:

<CodeGroup>
  ```python Python expandable theme={null}
  import os
  import json
  from openai import OpenAI

  client = OpenAI(
      base_url="https://api.tokenfactory.nebius.com/v1/",
      api_key=os.environ.get("NEBIUS_API_KEY"),
  )

  completion = client.chat.completions.create(
      model="openai/gpt-oss-120b",
      # 1. Instruct the model to produce JSON. In this example, we do it 
      # in the system prompt; you can do it in a user message instead
      messages=[
          {
              "role": "system",
              "content": (
                  "I will give you an actor or actress, and you will respond "
                  "with details of a real film they have starred in, using JSON "
                  "as the format."
              )
          },
          {
              "role": "user",
              "content": "Jack Nicholson"
          }
      ],
      # 2. Set the response format to `json_object`
      response_format={
          "type": "json_object"
      }
  )

  # 3. Work with the JSON output (it should be valid but is not guaranteed 
  # to have any predetermined fields) or a refusal

  output = completion.choices[0].message
  if output.refusal:
      # Handle refusal
      print(output.refusal)
  elif output.content:
      try:
          output_json = json.loads(output.content)
          print(output_json)
      except Exception as e:
          # Handle possible exceptions, e.g. invalid JSON
          print(e)
          pass
  ```

  ```bash cURL expandable theme={null}
   # 1. Instruct the model to produce JSON. In this example, we do it in the system prompt; 
   # you can do it in a user message instead
   # 2. Set the response format to `json_object`

  export OUTPUT=$(curl 'https://api.tokenfactory.nebius.com/v1/chat/completions' \
    -X 'POST' \
    -H 'Content-Type: application/json' \
    -H 'Accept: */*' \
    -H "Authorization: Bearer $NEBIUS_API_KEY" \
    --data-binary '
      {
        "model": "mistralai/Mistral-Nemo-Instruct-2407",
        "messages": [
          {
            "role": "system",
            "content": "I will give you an actor or actress, and you will respond with details of a real film they have starred in, using JSON as the format."
          },
          {
            "role": "user",
            "content": "Jack Nicholson"
          }
        ],
        "response_format": {
          "type": "json_object"
        }
      }
    '
  )

  # 3. Work with the JSON output (it should be valid but is not guaranteed 
  # to have any predetermined fields) or a refusal

  echo $OUTPUT | jq '
    .choices[0].message.refusal 
    // (.choices[0].message.content | fromjson)
  '
  ```

  ```javascript JavaScript expandable theme={null}
  import OpenAI from 'openai';

  const client = new OpenAI({
    baseURL: 'https://api.tokenfactory.nebius.com/v1/',
    apiKey: process.env.NEBIUS_API_KEY,
  });

  client.chat.completions.create({
    model: "mistralai/Mistral-Nemo-Instruct-2407",
    // 1. Instruct the model to produce JSON. In this example, we do it in the system prompt; 
    // you can do it in a user message instead
    messages: [
      {
        role: "system",
        content: "I will give you an actor or actress, and you will respond with details of a real film they have starred in, using JSON as the format.",
      },
      {
        role: "user",
        content: "Jack Nicholson",
      },
    ],
    // 2. Set the response format to `json_object`
    response_format: {
      type: "json_object",
    },
  })
  // 3. Work with the JSON output (it should be valid but is not guaranteed 
  // to have any predetermined fields) or a refusal
  .then((completion) => {
    const output = completion.choices[0].message;
    if (output.refusal) {
      // Handle refusal
      console.log(output.refusal);
    } else if (output.content) {
      try {
        const output_json = JSON.parse(output.content);
        console.log(output_json);
      } catch (e) {
      // Handle possible exceptions, e.g. invalid JSON
      console.log("An error occurred: ", e.message);
      }
    }
  });
  ```
</CodeGroup>

<Tip>
  Use **JSON schema instead of JSON objects** whenever you need **strict JSON structure**
</Tip>

## Try it in our Cookbook

<Card title="Strucrured output - Cookbook" icon="github" iconType="regular" color="#000000" horizontal="false" href="https://github.com/nebius/token-factory-cookbook/blob/main/guided_json.ipynb" />

## Structured output in Playground

You can explore the structured output capabilities of models directly in the Playground:

1. **Go to the "Model Parameters" section.**
2. ****`In the "Response format" dropdown, select JSON object/schema.`****
3. *(Optional)* **Provide a strict JSON schema** if you want the model to follow a specific structure.
4. **Enter your prompt and run it** to test whether the model returns the expected JSON output for your use case.
