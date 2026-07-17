# FastMCP v3 Modern Capabilities Reference

Comprehensive guide to modern FastMCP v3 (2026) capabilities based on official documentation.

## Overview

FastMCP v3 is a production-ready MCP framework with 26k+ GitHub stars. It powers 70% of MCP servers. Key changes from older versions:
- Migrated to MCP Python SDK v2 (July 2026)
- Native resources/prompts (no monkeypatching needed)
- FastMCPApp for interactive UIs
- Modern transforms system

## FastMCP vs FastMCPApp

| Aspect | FastMCP | FastMCPApp |
|--------|---------|------------|
| Purpose | Server-only | Interactive apps with UI |
| Entry points | `@mcp.tool()` | `@app.ui()` |
| Backend tools | Regular tools | `@app.tool()` |
| Visibility | All tools visible | Tools can be UI-only |
| Use case | Standard MCP servers | Apps needing forms/callbacks |

## Server Setup

### Basic FastMCP Server

```python
from fastmcp import FastMCP

mcp = FastMCP(
    "MyServer",
    instructions="How to use this server",
    version="1.0.0",
)

@mcp.tool()
def my_tool(a: int, b: int) -> int:
    """Add two numbers"""
    return a + b

@mcp.resource("data://config")
def get_config() -> dict:
    return {"theme": "dark"}

@mcp.prompt()
def analyze_data(data: list[float]) -> str:
    return f"Please analyze: {data}"

if __name__ == "__main__":
    mcp.run()
```

### Server Configuration Options

```python
FastMCP(
    name="ServerName",
    instructions="Usage instructions",
    version="1.0.0",
    website_url="https://...",
    icons=[Icon(...)],
    # Composition
    tools=[...],
    auth=OAuthProvider(...),
    middleware=[...],
    providers=[...],
    transforms=[...],
    lifespan=Lifespan(...),
    # Behavior
    on_duplicate="warn|error|replace|ignore",
    strict_input_validation=False,
    mask_error_details=True,
    list_page_size=100,
    tasks=True,
    client_log_level="info",
    dereference_schemas=True,
    # Handlers
    sampling_handler=...,
    session_state_store=...,
)
```

### Running Servers

```python
# STDIO (default)
mcp.run()

# HTTP transport
mcp.run(transport="http", host="127.0.0.1", port=9000)

# With custom routes
@mcp.custom_route("/health", methods=["GET"])
async def health(request: Request) -> PlainTextResponse:
    return PlainTextResponse("OK")
```

## Mounting & Composition

```python
weather = FastMCP("Weather")
calendar = FastMCP("Calendar")

main = FastMCP("Main")
main.mount(weather, namespace="weather")
main.mount(calendar, namespace="calendar")

# Results: weather_get_data, calendar_get_data
```

## Resources

### Decorator Syntax

```python
@mcp.resource("data://config")
def get_config() -> dict:
    """Returns server configuration"""
    return {"setting": "value"}

@mcp.resource("data://{user_id}/profile")
def get_profile(user_id: str) -> dict:
    """Get user profile by ID"""
    return {"id": user_id, "name": "..."}
```

### Return Types

```python
# String → TextResourceContents (mime_type: text/plain)
@mcp.resource("data://text")
def text_resource() -> str:
    return "Hello"

# Bytes → BlobResourceContents (specify mime_type!)
@mcp.resource("data://image", mime_type="image/png")
def image_resource() -> bytes:
    return b"..."

# Explicit control
from fastmcp import ResourceResult, ResourceContent

@mcp.resource("data://mixed")
def mixed_resource() -> ResourceResult:
    return ResourceResult(
        contents=[
            ResourceContent(content="Text", mime_type="text/plain"),
            ResourceContent(content=b"Binary", mime_type="application/octet-stream"),
        ],
        meta={"cache-control": "no-store"},
    )
```

### Resource Templates (RFC 6570)

```python
# Path parameters (required)
@mcp.resource("user://{user_id}/profile")
def user_profile(user_id: str) -> dict:
    return {"id": user_id}

# Wildcard parameters
@mcp.resource("files://{path*}")
def read_file(path: str) -> str:
    return open(path).read()

# Query parameters (optional)
@mcp.resource("data://{id}?format={format}&limit={limit}")
def get_data(id: str, format: str = "json", limit: int = 10) -> dict:
    return {"id": id, "format": format, "limit": limit}

# Multiple templates for one function
@mcp.resource("users://email/{email}")
@mcp.resource("users://name/{name}")
def find_user(email: str = None, name: str = None) -> dict:
    ...
```

### Resource Annotations

```python
@mcp.resource(
    "data://config",
    annotations=Annotations(
        readOnlyHint=True,
        idempotentHint=True,
    )
)
def config() -> dict:
    ...
```

## Prompts

### Decorator Syntax

```python
@mcp.prompt()
def welcome_message() -> str:
    """Welcome message for new users"""
    return "Welcome to our service!"

@mcp.prompt()
def analyze_data(data_uri: str, analysis_type: str = "summary") -> str:
    """
    Analyze a dataset with the specified method.
    
    Args:
        data_uri: URI of the data to analyze
        analysis_type: Type of analysis (summary, detailed, statistical)
    """
    return f"Analyze {data_uri} using {analysis_type}"
```

### Return Types

```python
# Single message
@mcp.prompt()
def greet(name: str) -> str:
    return f"Hello, {name}!"

# Multiple messages
from fastmcp import Message, PromptResult

@mcp.prompt()
def conversation(name: str) -> list[Message | str]:
    return [
        Message(content=f"Hello, {name}!", role="user"),
        Message(content="How can I help?", role="assistant"),
    ]

# Full control
@mcp.prompt()
def system_prompt() -> PromptResult:
    return PromptResult(
        messages=[
            Message(content="You are a helpful assistant", role="system"),
        ],
        description="System configuration prompt",
    )
```

### Typed Arguments

```python
from dataclasses import dataclass

@dataclass
class UserData:
    name: str
    age: int

@mcp.prompt()
def analyze_user(user: UserData) -> str:
    """Analyze user data"""
    return f"Analyzing {user.name}, age {user.age}"

# Server infers JSON schema, clients pass JSON strings
```

## Modern Transforms System

### Tool Search Transform

Replaces large catalogs with on-demand search. Creates `search_tools` and `call_tool` synthetic tools.

```python
from fastmcp.server.transforms import BM25SearchTransform, RegexSearchTransform

# BM25 (natural language queries)
mcp.add_transform(BM25SearchTransform(max_results=5))

# Regex (pattern matching)
mcp.add_transform(RegexSearchTransform(max_results=10))

# With pinned tools
mcp.add_transform(
    BM25SearchTransform(max_results=5),
    always_visible=["critical_tool"],
)

# Custom names
mcp.add_transform(
    BM25SearchTransform(),
    search_name="find_tools",
    call_name="execute_tool",
)
```

### Namespace Transform

Prefixes component names to prevent conflicts.

```python
from fastmcp.server.transforms import Namespace

# Via mount (most common)
main.mount(weather, namespace="weather")

# Direct transform
mcp.add_transform(Namespace("api"))
# Tool: greet → api_greet
# Resource: data://info → data://api/info
```

### Visibility Transform

Dynamic enable/disable at runtime.

```python
# Disable components
mcp.disable(keys=["tool:delete_everything"])
mcp.disable(tags={"admin", "dangerous"})

# Enable with allowlist mode
mcp.enable(tags={"public"}, only=True)

# Per-session visibility (v3.0+)
@mcp.tool()
def unlock_premium(ctx: Context) -> str:
    await ctx.enable_components(tags={"premium_analysis"})
    return "Premium unlocked!"

@mcp.tool()
def reset_features(ctx: Context) -> str:
    await ctx.reset_visibility()
    return "Features reset"
```

### ResourcesAsTools Transform

Exposes resources to tool-only clients.

```python
from fastmcp.server.transforms import ResourcesAsTools

mcp.add_transform(ResourcesAsTools(mcp))

# Client sees:
# - list_resources(uri: Optional[str]) → JSON of resources
# - read_resource(uri: str) → resource content
```

### PromptsAsTools Transform

Exposes prompts to tool-only clients.

```python
from fastmcp.server.transforms import PromptsAsTools

mcp.add_transform(PromptsAsTools(mcp))

# Client sees:
# - list_prompts() → JSON of prompts
# - get_prompt(name: str, arguments: Optional[dict]) → rendered messages
```

### Code Mode Transform (Experimental)

Collapses tools into discovery + execute meta-tools.

```python
from fastmcp.experimental.transforms.code_mode import (
    CodeMode,
    Search,
    GetSchemas,
    MontySandboxProvider,
)

mcp.add_transform(
    CodeMode(
        discovery_tools=[Search(), GetSchemas()],
        sandbox=MontySandboxProvider(limits={"max_duration_secs": 30}),
    )
)
```

## FastMCPApp (Interactive Tools)

### Basic Pattern

```python
from fastmcp import FastMCPApp
from prefab_ui import Column, DataTable, Text

app = FastMCPApp("ContactsApp")

@app.ui()
async def contacts_app() -> PrefabApp:
    return PrefabApp(
        state={"selected": None},
        children=[
            Column(
                DataTable(
                    rows=contacts,
                    on_row_click=SetState("selected", Rx("$event")),
                ),
                If(STATE.selected) >> Text(Rx("selected.name")),
            )
        ],
    )
```

### Entry Points vs Backend Tools

```python
# Entry point - visible to model (default)
@app.ui()
def main_app() -> PrefabApp:
    return PrefabApp(...)

# Backend tool - UI-only by default
@app.tool()
def save_contact(name: str, email: str) -> list[dict]:
    """Save contact to database"""
    return contacts

# Both model and UI
@app.tool(model=True)
def critical_tool(param: str) -> str:
    return f"Result: {param}"
```

### Calling Backend from UI

```python
from prefab_ui import CallTool, SetState

# Call with result handling
CallTool(
    save_contact,
    on_success=SetState("saved", True),
    on_error=SetState("error", ERROR),
)

# Direct state update
CallTool(search_tool, result_key="search_results")

# Async with loading state
CallTool(
    save_contact,
    on_click=[SetState("loading", True), CallTool(...)],
)
```

### Forms

```python
from prefab_ui import Form, FormField
from pydantic import BaseModel

# Manual form
Form(
    on_submit=CallTool(save_contact),
    children=[
        FormField(name="name", label="Name"),
        FormField(name="email", label="Email"),
    ],
)

# From Pydantic model
class ContactForm(BaseModel):
    name: str
    email: str
    priority: Literal["low", "medium", "high"] = "medium"

Form.from_model(ContactForm, on_submit=CallTool(save_contact))
```

### Mounting

```python
mcp = FastMCP("Main")
app = FastMCPApp("MyApp")

mcp.add_provider(app)

# Or standalone
app.run()
```

## Client Operations

### Basic Usage

```python
from fastmcp import Client

async with Client("my_server.py") as client:
    # Call tools
    result = await client.call_tool("add", {"a": 5, "b": 3})
    print(result.data)  # 8
    
    # Read resources
    content = await client.read_resource("data://config")
    
    # Get prompts
    messages = await client.get_prompt("welcome")
```

### Task API (Background Tasks)

```python
# Start background task
task = await client.call_tool("slow_task", {"duration": 10}, task=True)

# Subscribe to updates
task.on_status_change(lambda status: print(f"Progress: {status.statusMessage}"))

# Wait for result
result = await task.result()

# Check status
status = await task.status()

# Cancel
await task.cancel()
```

### Progress Monitoring

```python
async def progress_handler(progress: float, total: float | None, message: str | None):
    if total:
        print(f"{(progress/total)*100:.1f}% - {message}")
    else:
        print(f"{progress} - {message}")

client = Client("server.py", progress_handler=progress_handler)
```

### Logging

```python
from fastmcp.client.logging import LogMessage

async def log_handler(message: LogMessage):
    print(f"[{message.level}] {message.data.get('msg')}")

client = Client("server.py", log_handler=log_handler)
```

### Elicitation

```python
from fastmcp.client.elicitation import ElicitResult, ElicitRequestParams, RequestContext

async def elicit_handler(
    message: str,
    response_type: type | None,
    params: ElicitRequestParams,
    context: RequestContext,
) -> ElicitResult | object:
    user_input = input(f"{message}: ")
    if not user_input:
        return ElicitResult(action="decline")
    return ElicitResult(action="accept", content=response_type(value=user_input))

client = Client("server.py", elicitation_handler=elicit_handler)
```

## CLI Commands

```bash
# Run server
fastmcp run server.py
fastmcp run https://example.com/mcp

# Dev/preview
fastmcp dev apps          # Preview app tools
fastmcp dev inspector     # MCP Inspector

# Install
fastmcp install server.py --client claude_desktop
fastmcp install server.py --client cursor

# Inspect
fastmcp inspect server.py
fastmcp list server.py
fastmcp call server.py add a=5 b=3

# Discover
fastmcp discover          # Find configured servers
fastmcp generate-cli server.py  # Generate CLI

# Auth
fastmcp auth cimd         # Create/validate CIMD docs
```

## Key Differences from Legacy Pattern

| Legacy Pattern | Modern Pattern |
|----------------|---------------|
| Manual monkeypatching for resources/prompts | Native `@mcp.resource()` and `@mcp.prompt()` decorators |
| `mcp.add_resource_template()` | `@mcp.resource("uri://{param}")` |
| Optional FastMCPApp | First-class `FastMCPApp` with `@app.ui()` and `@app.tool()` |
| Manual visibility filtering | Built-in `mcp.enable()`/`mcp.disable()` transforms |
| Separate search packages | `BM25SearchTransform`/`RegexSearchTransform` built-in |
| Custom resources-as-tools | `ResourcesAsTools` transform |
| Custom prompts-as-tools | `PromptsAsTools` transform |

## Related Pages

- [[FastMCP Architecture]]
- [[MCP Server Implementation]]
- [[web-search-mcp Current State]]
