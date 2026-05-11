# GitHub GraphQL Tuning

Date: `2026-04-21`

Status: separate feature line for future implementation, not merged into the main web-search refactor plan.

Scope: practitioner/community guidance first, plus live probes run against GitHub GraphQL with the locally configured token. Official docs were used only as secondary sanity checks earlier; they are not the primary basis of this note.

## Why This Is Separate

The main refactor is about web search boundaries, retrieval quality, and fetch separation. GitHub GraphQL tuning is valuable, but it introduces its own concerns:

- GitHub-specific query templates
- pagination strategy
- issue/discussion hydration flow
- rate-limit and cost observability
- a possible dedicated GitHub provider/resolver lane

That makes it better as a parallel feature track rather than something hidden inside the general web-search redesign.

## Practitioner And Community Takeaways

### 1. Page one connection at a time

The most consistent community advice is to treat every connection as its own pagination stream.

- Good fit: `repository -> issues`
- Good fit: `repository -> discussion -> comments`
- Risky fit: `repository -> discussions -> comments -> replies` all deeply paginated in one loop

Source:
- Stack Overflow, “GitHub API v4: How can I traverse with pagination? (GraphQL)”  
  https://stackoverflow.com/questions/48116781/github-api-v4-how-can-i-traverse-with-pagination-graphql

Why it matters here:
- our MCP should not try to build one giant “fetch everything” GitHub query
- we should use staged hydration instead

### 2. There is no single magic cursor for nested pagination

The best explanation from the community is that nested connections cannot be collapsed into one universal cursor without ambiguity. You need separate follow-up queries depending on which branch you want to continue.

Source:
- Stack Overflow, “Github GraphQL v4 API nested pagination (Multiple pagination cursors can not be followed in a single query)”  
  https://stackoverflow.com/questions/59192308/github-graphql-v4-api-nested-pagination-multiple-pagination-cursors-can-not-be

Why it matters here:
- if we add GitHub discussion/comment extraction, the runtime should page:
  - discussions first
  - then comments for one discussion
  - then replies for one comment thread

### 3. Discovery and hydration should be different query shapes

In practice, `search(type: DISCUSSION, ...)` is useful for discovery, but direct repository fields are better for deterministic fetches.

Community evidence:
- tigawanna gist shows practical use of `search(...)` for discovery and calls out the extra client complexity once pagination starts  
  https://gist.github.com/tigawanna/87b0d7e10e3af7fde620c1019df41362

Live probe evidence:
- `search(type: DISCUSSION, query:"repo:vercel/next.js", first:2)` returned useful discussion candidates
- `repository(owner,name) { discussion(number: ...) { ... } }` worked cleanly for structured hydration

Recommendation:
- use `search(...)` to find candidates
- use `repository.discussion(number:)` or `repository.issues(...)` to hydrate content

### 4. Keep nested `first` values small

Practitioner examples and client code patterns strongly imply that shallow, bounded requests are the sustainable path. Large nested fan-out is where cost and client complexity go bad.

Recommendation:
- top-level discovery: `first: 5-20`
- comments/replies hydration: `first: 10-20`
- only expand when there is a concrete need

### 5. Search capability should be verified, not assumed

Community Q&A around GitHub GraphQL search limitations is a useful reminder that the GraphQL surface does not always match user expectations or REST/UI behavior.

Source:
- Stack Overflow, “Search for code in GitHub using GraphQL (v4 API)”  
  https://stackoverflow.com/questions/45382069/search-for-code-in-github-using-graphql-v4-api

Why it matters here:
- for MCP routing we should probe and encapsulate supported GitHub GraphQL paths instead of assuming parity

## Live Probe Notes

These were run successfully on `2026-04-21` using the locally configured token from `.env`.

### Successful auth + rate limit probe

Query:

```graphql
query {
  viewer { login }
  rateLimit {
    limit
    remaining
    used
    cost
    resetAt
  }
}
```

Observed result:

- viewer: `chmielvu`
- limit: `5000`
- remaining: `4999`
- used: `1`
- cost: `1`
- resetAt: `2026-04-21T12:08:23Z`

Implication:
- tight GitHub GraphQL queries can be extremely cheap
- we should still instrument cost and headers in development

### Successful repository issues probe

Query shape:

```graphql
query RepoIssues($owner: String!, $name: String!) {
  repository(owner: $owner, name: $name) {
    issues(first: 2, states: OPEN, orderBy: { field: UPDATED_AT, direction: DESC }) {
      totalCount
      pageInfo { hasNextPage endCursor }
      nodes {
        number
        title
        url
        comments { totalCount }
        labels(first: 5) { nodes { name } }
      }
    }
  }
  rateLimit { cost remaining }
}
```

Observed result:

- worked against `exa-labs/exa-mcp-server`
- returned issue metadata as expected
- query cost stayed at `1`

### Successful discussion discovery probe

Query shape:

```graphql
query DiscussionSearch($query: String!) {
  search(type: DISCUSSION, query: $query, first: 2) {
    issueCount
    pageInfo { hasNextPage endCursor }
    edges {
      node {
        ... on Discussion {
          number
          title
          url
          upvoteCount
          isAnswered
          repository { nameWithOwner }
          comments(first: 1) { totalCount }
        }
      }
    }
  }
  rateLimit { cost remaining }
}
```

Observed result:

- `repo:vercel/next.js` returned valid discussion candidates
- `issueCount` was not useful as an authoritative discussion count

Implication:
- use the `edges/node` payload for discovery
- do not lean on `issueCount` as a truth source for discussion inventory

### Successful direct discussion hydration probe

Query shape:

```graphql
query RepoDiscussion($owner: String!, $name: String!, $number: Int!) {
  repository(owner: $owner, name: $name) {
    discussion(number: $number) {
      title
      url
      upvoteCount
      comments(first: 2) {
        pageInfo { hasNextPage endCursor }
        nodes {
          body
          replies(first: 2) {
            pageInfo { hasNextPage endCursor }
            nodes { body }
          }
        }
      }
    }
  }
  rateLimit { cost remaining }
}
```

Observed result:

- worked against `vercel/next.js` discussion `93044`
- comments and replies came back in the expected structure

Implication:
- `repository.discussion(number:)` is the right hydration path for a future GitHub discussion resolver

## Recommended Query Patterns

### Pattern A: discussion discovery

Use when the user asks broad questions like:

- “find relevant GitHub discussions about X”
- “search repo discussions for Y”

Template:

```graphql
query RepoDiscussionDiscovery($q: String!, $first: Int!, $after: String) {
  search(type: DISCUSSION, query: $q, first: $first, after: $after) {
    pageInfo { hasNextPage endCursor }
    edges {
      node {
        ... on Discussion {
          number
          title
          url
          upvoteCount
          isAnswered
          repository { nameWithOwner }
        }
      }
    }
  }
}
```

### Pattern B: deterministic discussion hydrate

Use after discovery when we already know `owner/name/number`.

```graphql
query RepoDiscussionHydrate($owner: String!, $name: String!, $number: Int!) {
  repository(owner: $owner, name: $name) {
    discussion(number: $number) {
      title
      body
      url
      comments(first: 10) {
        pageInfo { hasNextPage endCursor }
        nodes {
          body
          replies(first: 10) {
            pageInfo { hasNextPage endCursor }
            nodes { body }
          }
        }
      }
    }
  }
}
```

### Pattern C: issue list hydrate

Use for repo-specific troubleshooting lanes.

```graphql
query RepoIssuesPage($owner: String!, $name: String!, $first: Int!, $after: String) {
  repository(owner: $owner, name: $name) {
    issues(first: $first, after: $after, states: OPEN, orderBy: { field: UPDATED_AT, direction: DESC }) {
      pageInfo { hasNextPage endCursor }
      nodes {
        number
        title
        url
        comments { totalCount }
      }
    }
  }
}
```

## Keep / Avoid Rules

Keep:

- keep one cursor per connection
- keep discovery separate from hydration
- keep nested `first` values conservative
- keep `pageInfo` on every connection we may continue later
- keep rate-limit instrumentation in debug/dev templates

Avoid:

- avoid giant multi-connection “one shot” GitHub queries
- avoid using `search(...)` as the final hydrate path when direct repository fields are available
- avoid assuming search counts are authoritative for all object types
- avoid deep reply/comment expansion unless the user explicitly needs it

## Suggested Future Implementation Line

If this becomes a dedicated feature, the clean shape is:

1. `github_search_discussions`
2. `github_get_discussion`
3. `github_get_issue`
4. optional shared pagination helpers and normalized GitHub result models

This would let the main web-search module stay provider-agnostic while GitHub GraphQL becomes a specialized enhancement path.

## Source List

- Stack Overflow, pagination traversal  
  https://stackoverflow.com/questions/48116781/github-api-v4-how-can-i-traverse-with-pagination-graphql
- Stack Overflow, nested pagination limitations  
  https://stackoverflow.com/questions/59192308/github-graphql-v4-api-nested-pagination-multiple-pagination-cursors-can-not-be
- Stack Overflow, search limitations  
  https://stackoverflow.com/questions/45382069/search-for-code-in-github-using-graphql-v4-api
- tigawanna gist, practical GitHub GraphQL patterns  
  https://gist.github.com/tigawanna/87b0d7e10e3af7fde620c1019df41362

