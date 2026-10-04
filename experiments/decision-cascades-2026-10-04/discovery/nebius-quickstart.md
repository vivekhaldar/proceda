> ## Documentation Index
> Fetch the complete documentation index at: https://docs.tokenfactory.nebius.com/llms.txt
> Use this file to discover all available pages before exploring further.

# Quickstart

> Welcome to Nebius Token Factory

## Start building now

<CodeGroup>
  ```python Python expandable theme={null}
  import os
  from openai import OpenAI

  client = OpenAI(
      base_url="https://api.tokenfactory.nebius.com/v1/",
      api_key=os.environ.get("NEBIUS_API_KEY")
  )

  response = client.chat.completions.create(
      model="deepseek-ai/DeepSeek-R1-0528",
      messages=[
          {
              "role": "system",
              "content": """You are a helpful assistant"""
          },
          {
              "role": "user",
              "content": [
                  {
                      "type": "text",
                      "text": """hello"""
                  }
              ]
          }
      ]
  )

  print(response.to_json())
  ```

  ```javascript JavaScript expandable theme={null}
  const OpenAI = require('openai');

  const client = new OpenAI({
      baseURL: 'https://api.tokenfactory.nebius.com/v1/',
      apiKey: process.env.NEBIUS_API_KEY,
  });

  client.chat.completions.create({
      "model": "deepseek-ai/DeepSeek-R1-0528",
      "messages": [
          {
              "role": "system",
              "content": "You are a helpful assistant"
          },
          {
              "role": "user",
              "content": [
                  {
                      "type": "text",
                      "text": "hello"
                  }
              ]
          }
      ]
  })
      .then((response) => console.log(response));
  ```

  ```bash cURL expandable theme={null}
  curl 'https://api.tokenfactory.nebius.com/v1/chat/completions' \
      -X 'POST' \
      -H 'Content-Type: application/json' \
      -H 'Accept: */*' \
      -H "Authorization: Bearer $NEBIUS_API_KEY" \
      --data-binary '{"model":"deepseek-ai/DeepSeek-R1-0528","messages":[{"role":"system","content":"You are a helpful assistant"},{"role":"user","content":[{"type":"text","text":"hello"}]}]}'
  ```
</CodeGroup>

<AccordionGroup>
  <Accordion title="Get API key" icon="key">
    1. Go to [https://tokenfactory.nebius.com](https://tokenfactory.nebius.com/), create an account
    2. Log in using your Google or GitHub account
    3. For authentication, [get an API key](https://docs.tokenfactory.nebius.com/api-reference/introduction#authentication)
  </Accordion>

  <Accordion title="Check our Cookbook" icon="github">
    To explore code examples and get reusable snippets, explore our official cookbook with solutions, examples and demo apps:

    [https://github.com/nebius/token-factory-cookbook](https://github.com/nebius/token-factory-cookbook)
  </Accordion>
</AccordionGroup>

## Explore

**Nebius Token Factory** is your all-in-one platform for working with large language models (LLMs) — from quick experimentation to production deployment. Test and compare models in an intuitive **playground**, or integrate them into your applications via an **OpenAI-compatible API** for **inference and fine-tuning**, and extend capabilities through **seamless integrations with popular frameworks**.

<CardGroup cols={2}>
  <Card title="Inference" icon="bolt" href="/ai-models-inference/overview">
    Send requests such as prompts, chats or images, and get responses
  </Card>

  <Card title="Third-party integrations" icon="chart-network" href="/integrations/overview">
    Build inference into applications that are based on supported third-party integrations
  </Card>

  <Card title="Fine-tuning" icon="layer-group" href="/fine-tuning/overview">
    Train models by using datasets to increase output accuracy, enhance model reliability and save costs on prompting
  </Card>

  <Card title="API" icon="code" href="/api-reference/introduction">
    Learn how to authenticate and make requests to the inference API
  </Card>
</CardGroup>
