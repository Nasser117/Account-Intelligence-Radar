# AVERROA — Account Intelligence Radar

> **Built by Experts. Delivered with Precision.**  
>A three-layer Python interface that generates structured business intelligence reports from a company name or a geographic region.

---

## What It Does

Turns a company name or a city + sector into an actionable intelligence report — headquarters, business units, products, key executives, and strategic initiatives — with every claim linked to a verifiable source URL.

```
Input: "Saudi Aramco"
  ↓ Layer 1 — SerpAPI         : discovers relevant web sources
  ↓ Layer 2 — Gemini / Groq : selects best 2-3 URLs by business objective
  ↓ Layer 3 — Firecrawl       : extracts structured JSON from those URLs
Output: reports/saudi_aramco.json + reports/saudi_aramco.md
```

---

## Setup

### Prerequisites
- Python 3.10+
- API keys for: SerpAPI, Firecrawl, and at least one of Gemini or Groq (preferably groq)

### Installation

```powershell
# 1. Clone or unzip the project
cd account-intelligence-radar

# 2. Create and activate virtual environment
python -m venv venv
.\venv\Scripts\Activate.ps1        # Windows PowerShell
# source venv/bin/activate          # macOS / Linux

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure API keys
copy .env.example .env
# Open .env and fill in your real keys
```

---

## Configuration (`.env`)

```
SERPAPI_KEY=your_serpapi_key_here
GEMINI_API_KEY=your_gemini_api_key_here
GROQ_API_KEY=your_groq_key_here
FIRECRAWL_API_KEY=your_firecrawl_key_here
```

| Variable | Required | Provider | Notes |
|---|---|---|---|
| `SERPAPI_KEY` | ✅ | https://serpapi.com | Discovery layer |
| `GEMINI_API_KEY` | ✅ (free) | https://aistudio.google.com → "Get API Key" | Primary LLM, completely free, no credit card |
| `GROQ_API_KEY` | Optional | https://console.groq.com | Free fallback LLM |
| `FIRECRAWL_API_KEY` | ✅ | https://firecrawl.dev | Extraction layer |

At least one LLM key (Gemini or Groq) is recommended. If both fail, the tool falls back to the top organic search results automatically.

---

## Run

### Option A — CLI (PowerShell)

```powershell
python main.py
```

You will be prompted to choose a mode, enter an objective, and then company name or location + sector.

### Option B — Web Application ✨ (recommended)

```powershell
uvicorn api:app --host 0.0.0.0 --port 8000 --reload
```

Then open **http://localhost:8000** in your browser.

The web UI provides:
- Mode tabs (Company / Geography)
- Live terminal-style log stream as the pipeline runs
- Expandable result cards with stats, executives, initiatives, and source links
- ⬇ JSON and ⬇ Markdown download buttons per company report

---

## Output

All reports saved to `/reports/`:

| File | Format | Contents |
|---|---|---|
| `<company>.json` | JSON | Machine-readable structured data |
| `<company>.md` | Markdown | Human-readable summary with sourced claims |
| `geo_<location>_<sector>.md` | Markdown | Geography mode index of all profiled companies |

**Design note:** Reports are overwritten on each run (latest-wins). This is intentional — the tool is for fresh outreach intelligence, not historical archiving.

---

## Report Structure

Each report contains at minimum:

1. **Company Identifiers** — name, headquarters
2. **Business Snapshot** — business units, products & services, target industries
3. **Leadership Signals** — executives with titles (official sources only)
4. **Strategic Initiatives** — transformation, ERP, AI, expansions, sustainability
5. **Evidence & Sources** — all source URLs tied to extracted claims

---



## Architecture
See [`architecture.html`](./architecture.html) for the full visual diagram.




## Engineering Standards

| Standard | Implementation |
|---|---|
| `robots.txt` compliance (RFC 9309) | `urllib.robotparser` checks any direct URL; Firecrawl handles its own |
| OWASP logging | API keys masked to last 4 chars; never logged in full |
| Traceability by design | Every report includes `evidence_links` tying claims to URLs |
| Failure modes handled | SerpAPI errors, Gemini 429, Groq 429, Firecrawl timeouts — all caught with clear messages |
| No LinkedIn scraping | Social media filtered before LLM and Firecrawl |
| Secrets management | `.env` file; `.env.example` committed (no real keys) |

---

## Running Tests

```powershell
pip install pytest
python -m pytest tests/ -v
```

Tests cover:
- URL filtering (social media / LinkedIn exclusion)
- JSON parsing robustness (valid, empty, invalid, markdown-fenced)
- Input sanitization (XSS and injection characters)
- Report saving (file creation, content, overwrite behaviour)
- OWASP key masking

---

## Project Structure

```
account-intelligence-radar/
├── main.py                  # Core pipeline (3 layers + 2 modes)
├── requirements.txt
├── .env.example
├── README.md
├── CONSULTANT_SUMMARY.md    # One-page consultant summary
├── tests/
│   └── test_radar.py        # Unit tests
├── architecture_diagram.html        # Visual system architecture diagram
└── reports/                 # Generated reports (gitignored)
    ├── example_1.json
    ├── example_1.md
    ├── example_2.json
    ├── example_2.md
    └── geo_example_3.md
```

---

## Common Errors

| Error | Cause | Fix |
|---|---|---|
| `SERPAPI_KEY not set` | Missing env var | Add to `.env` |
| `SerpAPI 401` | Invalid key | Check at serpapi.com |
| `Gemini 429` | Rate limit hit | Automatically waits and retries; falls back to Groq |
| `Groq 429` | Rate limited | Wait, or pay as you go |
| `Firecrawl 402` | No credits | Top up at firecrawl.dev |
| `Polling timeout` | Slow extraction | Increase `max_attempts` in `extract_intelligence()` |
| `No companies found (geo mode)` | Vague sector query | Use more specific keywords |

---

## Sample Outputs

See `/reports/` for sample outputs:
- `lego.json` / `lego.md` — global consumer brand (LEGO Group, Denmark)
- `saudi_aramco.json` / `saudi_aramco.md` — KSA-based company (Saudi Aramco, Dhahran)


## Demo
▶ [Watch the demo video](./output[x].mp4)
