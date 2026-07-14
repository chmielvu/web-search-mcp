---
name: ts-api-docs-first
description: "Always fetch official API documentation before guessing request/response formats or blaming APIs for failures"
condition: ["brd_json|format.*raw|data_format|payload_base|zone.*not.*support|JSON output is not supported", "BrightDataError|BraveError|DeGoogError|provider_health.*failure"]
scope: ["tool:write(*.py)", "tool:bash"]
---

STOP. Before guessing API request/response formats, blaming providers for timeouts, or iterating with different payload combinations: fetch the official API documentation via Context7 (`resolve-library-id` → `query-docs`) or read the docs URL directly with `read`.

Never assume an API is broken, slow, or timing out without first verifying the documented request contract (method, endpoint, payload fields, headers, response shape).

Never iterate blindly with `format:raw` vs `format:json`, `brd_json=1`, `data_format` etc. — read the docs, implement the documented contract exactly, then test once.

If a provider returns an error, capture the full response (status, content-type, headers, body) and compare it to the documented response schema before concluding the API is broken.