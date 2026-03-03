# AVERROA Account Intelligence Radar
## Consultant Summary

**Author:** Nasser
**Date:** 2026-02-28
**Classification:** Internal

---

## Problem Solved

Sales and business development teams waste significant time on untargeted outreach —
reaching out to the wrong companies, with no intelligence on their priorities,
decision-makers, or active initiatives.

This tool solves that by turning a company name **or** a geographic region + sector
into a structured intelligence report in under two minutes. The output maps exactly
what a consultant needs before a first call: who runs the company, what they are
focused on, and what strategic triggers (ERP, AI, expansion, sustainability) create
an opening for a conversation.

---

## Architecture Decisions

The pipeline is deliberately three-layered, with each layer owning one responsibility:

```
[Layer 1 — Discovery]   SerpAPI
        ↓
    Google search results (structured JSON)
        ↓
[Layer 2 — Decision]    Gemini Flash 2.0 → Groq Llama 3.3 70B → organic fallback
        ↓
    2-3 highest-signal URLs selected by LLM
        ↓
[Layer 3 — Extraction]  Firecrawl Extract API (async, schema-guided)
        ↓
    Structured JSON + Markdown report in /reports/
```

**Why three separate APIs instead of one?**
Each layer specialises. SerpAPI is faster and cheaper than asking an LLM to browse
the web. The LLM adds judgment (filtering social media, selecting investor relations
pages). Firecrawl provides clean, structured extraction without brittle HTML parsing.

**Why Gemini Flash + Groq as a chain?**
Both are free-tier. Gemini Flash 2.0 (Google AI Studio) is used as the primary LLM
at 15 RPM with a built-in throttle. Groq (Llama 3.3 70B, 30 RPM) catches any 429
rate-limit failures automatically. A final organic fallback means the pipeline never
stops cold, even if both LLMs are unavailable.

**Why not DeepSeek?**
DeepSeek was evaluated but requires a paid credit balance. Given that Gemini Flash
provides comparable reasoning quality at zero cost, DeepSeek was replaced in the
final implementation to eliminate the 402 failure mode entirely rather than just
handling it gracefully.

**Geography mode design:**
Geography mode runs sector-specific discovery queries first, extracts company names
via LLM (with a post-filter to exclude global multinationals that are not
headquartered in the target region), then feeds each company through the same
three-layer pipeline. This reuses all existing logic and produces the same report
format per company, plus a geography index file.

**Web application (Option B):**
A FastAPI backend exposes a job-based async API. The frontend streams live pipeline
logs via Server-Sent Events (SSE) with automatic reconnect logic (up to 3 attempts
with job-status fallback polling). The objective prompt is presented as interactive
checkboxes rather than a free-text field — this removes prompt injection risk and
guides users toward structured, reproducible intelligence requests.

---

## Key Risks and Mitigations

| Risk | Mitigation |
|---|---|
| Gemini 429 (rate limit) | Built-in throttle (4.5s gap between calls); auto-fallback to Groq |
| Groq 429 (rate limit) | Falls back to top 2 organic search results |
| Firecrawl async job timeout | Polls up to 24× over ~2 minutes; returns clear timeout error with evidence links preserved |
| LLM returns invalid JSON | `json.JSONDecodeError` caught at every parse site; fallback to organic results |
| Social media / LinkedIn crawled | Hard-blocked before LLM and Firecrawl layers; `rel="noopener noreferrer"` on all outbound links |
| API keys leaked in logs | Keys masked to last 4 chars at startup (OWASP); never logged elsewhere |
| API keys committed to repository | `.env` is `.gitignore`d; `.env.example` contains only variable names |
| robots.txt violations | `urllib.robotparser` checks any direct URL before fetch; Firecrawl handles its own compliance |
| Stale or hallucinated data | Every claim linked to source URLs via `evidence_links`; Firecrawl only extracts from live pages |
| Prompt injection via text input | Objective prompt replaced by checkbox UI; all text inputs sanitized (stripped of `< > " ' \``) and length-limited before submission |
| SSE connection drop on long jobs | Client-side reconnect with up to 3 retries; falls back to polling `/api/jobs/{id}` on final failure |
| In-memory job store lost on restart | Acknowledged limitation; SQLite-backed persistence is the recommended next step |
| Unauthenticated API endpoints | Acceptable for internal/demo use; API key middleware is the recommended hardening step |

---

## What I Would Improve Next

1. **Persistent job store** — replace the in-memory dict with SQLite + a TTL-based
   cache to survive server restarts and avoid re-running Firecrawl on the same
   company within 24 hours.

2. **API authentication** — add a simple API key middleware (or OAuth2 via FastAPI)
   to prevent unauthorised use of Firecrawl and SerpAPI credits.

3. **Rate limiting on the API endpoints** — add per-IP request throttling to the
   FastAPI layer to prevent abuse.

4. **Contact hypothesis engine** — for each company, suggest likely decision-maker
   titles (CIO, VP of Operations) with a pre-built LinkedIn search query string
   (no scraping — just a constructed search URL).

5. **Trigger signals enrichment** — add a second Firecrawl pass on news sources
   (press releases, business news) to surface recent announcements: funding rounds,
   new contracts, leadership changes, RFPs.

6. **Per-field evidence links** — currently all source URLs are listed at the report
   level; a richer implementation would tie each extracted claim to the specific URL
   it came from, enabling one-click verification per data point.

7. **Export to CRM** — a Salesforce / HubSpot push button to create an account and
   contact record directly from the report, closing the loop between intelligence
   gathering and outreach execution.