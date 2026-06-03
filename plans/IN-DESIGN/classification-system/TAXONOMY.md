# Query Classification Taxonomy

**Status**: Proposed — to be implemented as part of unified query understanding redesign.
**See also**: plans/unified-query-understanding-plan.md

---

## Domain Taxonomy (2-level hierarchical)

### AI/ML/Coding
- **AI/ML** — artificial intelligence, machine learning, deep learning, LLMs, model training, inference, fine-tuning
- **DataScience** — statistics, data analysis, data processing, visualization, ETL, analytics
- **Web** — web development, frontend, backend, APIs, frameworks, CMS, web performance
- **Research** — academic papers, literature reviews, methodology, experiments, citations
- **Learning** — tutorials, courses, study guides, certifications, educational content
- **Infra** — DevOps, cloud, networking, security, databases, CI/CD, deployment, SRE
- **Mixed** — cross-domain tech queries that don't fit a single subdomain

### Social-Media
- **personal** — personal branding, profiles, account management, social presence
- **growth** — audience growth, engagement strategies, content strategy, analytics
- **monitoring** — social listening, brand monitoring, sentiment tracking, trend detection

### Digital-Humanities
- **political-social** — digital politics, social movements, online discourse, civic tech
- **historical** — digital history, archival, digitization, historical analysis
- **Mixed** — cross-domain humanities queries that don't fit a single subdomain

### Services
- **AI-Cloud** — cloud AI services, managed ML, API platforms, serverless AI
- **Other** — general services, SaaS, platforms, utilities not covered above

### News
Flat domain — no subdomains. Current events, breaking news, press coverage.

### Natural-Science
Flat domain — no subdomains. Physics, chemistry, biology, earth science, astronomy.

### Default
Catch-all for anything that doesn't fit any other domain.

---

## Task Type Taxonomy (2-level hierarchical) ONLY AN EXAMPLE TODO

### informational
- **define** — "what is X", "definition of X"
- **explain** — "how does X work", "why does X happen"
- **explore** — "tell me about X", broad open-ended inquiry
- **verify** — "is it true that X", fact-checking

### actionable
- **implement** — build, create, set up something new
- **fix** — repair, debug, troubleshoot, resolve a problem
- **follow_steps** — how-to, tutorial, follow step-by-step instructions
- **book_or_buy** — purchase, reserve, book services or travel
- **comply** — meet regulations, legal requirements, standards

### evaluation
- **compare** — evaluate alternatives, pros/cons, A vs B
- **recommend** — "best X", "top X", reviews, rankings
- **choose** — product selection, decision support

### navigation
- **find_place** — locate business, service, location
- **find_person** — contact info, profile, who is X
- **find_resource** — specific document, file, page, dataset

### monitoring
- **track_status** — order, delivery, application, process status
- **check_price** — stock, crypto, product price, rates
- **get_updates** — changelog, news feed, latest changes

---

## Additional Axes (derived from search program, not classified separately) TODO

| Axis | Values | Purpose |
|---|---|---|
| Granularity | precise / moderate / broad | Variant depth and rewriting aggressiveness |
| Freshness | realtime / recent / evergreen | Provider weighting, TTL, recency signals |
| Complexity | atomic / compound / complex | Decomposition trigger and depth |
| Confidence | float 0.0–1.0 | Fallback aggressiveness per axis |

---

## Search Provider Functions TODO

The 9 search provider functions that FunctionGemma selects and parameterizes:

1. **keyword_search** — exact-match for specific terms, identifiers, error codes
2. **semantic_search** — conceptual/neural search for understanding and exploration
3. **docs_search** — official documentation, API references, specifications
4. **community_search** — forums, Q&A, discussions, opinions, experiences
5. **academic_search** — papers, preprints, research publications
6. **news_search** — current events, breaking news, recent articles
7. **commerce_search** — products, reviews, pricing, comparisons
8. **reference_search** — encyclopedic, factual, definitional (Wikipedia, dictionaries)
9. **local_search** — places, businesses, services, geographic queries