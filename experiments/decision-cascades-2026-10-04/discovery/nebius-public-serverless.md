> ## Documentation Index
> Fetch the complete documentation index at: https://docs.tokenfactory.nebius.com/llms.txt
> Use this file to discover all available pages before exploring further.

# Public Serverless Endpoints

Public endpoints run on shared infrastructure and are not tied to a single data center. Instead of showing a specific region, their Region field displays **Global**: the processing location is decided dynamically and can change at any time, without notice.

### No region guarantee

Because the processing location can shift at any time, we don't guarantee that a public endpoint will keep running in any particular region, or that it will keep running in the same region it's in today. If your integration relies on a specific `base_url` that includes a region (e.g. `api.tokenfactory.us-central1.nebius.com`), that URL can stop working if the endpoint's processing region changes.

Public endpoints are best suited for testing and non-critical workloads where this variability is acceptable. They are not intended for production use cases that depend on a stable region.

### When you need a guaranteed region

If your use case requires assurance about where requests are processed, for compliance, latency, performance, or capacity reasons, deploy the model on a [**Dedicated endpoint**](https://docs.tokenfactory.nebius.com/ai-models-inference/dedicated-endpoints/overview) in the region of your choice. [**Dedicated endpoints**](https://docs.tokenfactory.nebius.com/ai-models-inference/dedicated-endpoints/overview) always process requests in the region they're deployed to, and their Region field continues to show that actual region.
