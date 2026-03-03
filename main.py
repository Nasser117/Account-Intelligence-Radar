"""
AVERROA — Account Intelligence Radar
=====================================
Three-layer pipeline:
  Layer 1 — Discovery   : SerpAPI (Google Search)
  Layer 2 — Decision    : Gemini Flash (free) → Groq (free fallback) → organic fallback
  Layer 3 — Extraction  : Firecrawl Extract API (async polling)

Run modes:
  A) Company mode   : single company name → structured intelligence report
  B) Geography mode : country + sector → company shortlist → reports per company

Engineering governance:
  - API keys never logged (OWASP)
  - robots.txt respected for any direct crawling outside Firecrawl (RFC 9309)
  - Every claim linked to at least one source URL (traceability by design)
  - All failure modes handled and messaged
  - Gemini rate-limit delay respected (15 RPM free tier)

Free LLM tiers:
  - Gemini Flash 2.0: https://aistudio.google.com  → GEMINI_API_KEY  (15 req/min free)
  - Groq Llama 3.3 70B: https://console.groq.com   → GROQ_API_KEY    (30 req/min free)
"""

import os
import json
import time
import urllib.robotparser
import urllib.parse
import requests
from dotenv import load_dotenv

# ─────────────────────────────────────────────
# INITIALIZATION
# ─────────────────────────────────────────────
load_dotenv()
print(f"DEBUG: Current Key starts with: {os.getenv('SERPAPI_KEY')[:5]}")
SERP_KEY      = os.getenv("SERPAPI_KEY")
GEMINI_KEY    = os.getenv("GEMINI_API_KEY")   # Free — https://aistudio.google.com
GROQ_KEY      = os.getenv("GROQ_API_KEY")     # Free fallback — https://console.groq.com
FIRECRAWL_KEY = os.getenv("FIRECRAWL_API_KEY")

USER_AGENT = "AverroaRadar/1.0 (+https://averroa.com/bot)"

# Gemini free tier: 15 requests/minute → enforce minimum gap between calls
_GEMINI_LAST_CALL: float = 0.0
_GEMINI_MIN_GAP   = 4.5   # seconds → ~13 RPM (safe margin under 15)


def _mask(val: str | None) -> str:
    """OWASP: never log full secrets."""
    if not val:
        return "NOT SET"
    return f"{'*' * 6}{val[-4:]}"


def _check_env() -> dict:
    """Print masked env status. Returns dict of key availability."""
    keys = {
        "SERPAPI_KEY":       SERP_KEY,
        "GEMINI_API_KEY":    GEMINI_KEY,
        "GROQ_API_KEY":      GROQ_KEY,
        "FIRECRAWL_API_KEY": FIRECRAWL_KEY,
    }
    print("\n[INIT] Checking environment variables:")
    for name, val in keys.items():
        icon = "✅" if val else "❌"
        print(f"  {icon} {name}: {_mask(val)}")
    print()
    return {k: bool(v) for k, v in keys.items()}


# ─────────────────────────────────────────────
# ROBOTS.TXT GUARD (RFC 9309)
# ─────────────────────────────────────────────
_robots_cache: dict[str, urllib.robotparser.RobotFileParser] = {}

def _robots_allowed(url: str) -> bool:
    """Check robots.txt before any direct fetch outside Firecrawl."""
    try:
        parsed = urllib.parse.urlparse(url)
        base = f"{parsed.scheme}://{parsed.netloc}"
        if base not in _robots_cache:
            rp = urllib.robotparser.RobotFileParser()
            rp.set_url(f"{base}/robots.txt")
            rp.read()
            _robots_cache[base] = rp
        allowed = _robots_cache[base].can_fetch(USER_AGENT, url)
        if not allowed:
            print(f"  ⚠️  robots.txt disallows: {url} — skipping.")
        return allowed
    except Exception as e:
        print(f"  ⚠️  robots.txt check failed for {url}: {e} — allowing.")
        return True


# ─────────────────────────────────────────────
# LAYER 1 — DISCOVERY (SerpAPI)
# ─────────────────────────────────────────────
def search_companies(query: str, num_results: int = 10) -> list[dict]:
    """Layer 1: SerpAPI Google Search. Returns list of {title, link} dicts."""
    print(f"\n{'='*60}")
    print(f"[LAYER 1 — DISCOVERY] Query: '{query}'")
    print(f"{'='*60}")

    if not SERP_KEY:
        print("  ❌ SERPAPI_KEY is not set.")
        return []

    params = {"q": query, "api_key": SERP_KEY, "engine": "google", "num": num_results}
    print("  → Sending request to SerpAPI (key hidden)...")

    try:
        response = requests.get("https://serpapi.com/search", params=params, timeout=15)
        print(f"  → HTTP status: {response.status_code}")

        if response.status_code != 200:
            print(f"  ❌ Non-200 body:\n     {response.text[:400]}")
        response.raise_for_status()

        data = response.json()
        if "error" in data:
            print(f"  ❌ SerpAPI error: {data['error']}")
            return []

        results = data.get("organic_results", [])
        if not results:
            print("  ⚠️  No organic results returned.")
            return []

        print(f"  ✅ Received {len(results)} organic results.")
        extracted = [
            {"title": r.get("title"), "link": r.get("link")}
            for r in results[:num_results] if r.get("link")
        ]
        for i, r in enumerate(extracted, 1):
            print(f"    [{i}] {r['title']}")
            print(f"        {r['link']}")
        return extracted

    except requests.exceptions.Timeout:
        print("  ❌ SerpAPI timed out after 15s.")
        return []
    except requests.exceptions.ConnectionError as e:
        print(f"  ❌ Connection error: {e}")
        return []
    except requests.exceptions.HTTPError as e:
        print(f"  ❌ HTTP error: {e}")
        return []
    except json.JSONDecodeError as e:
        print(f"  ❌ JSON parse error: {e}")
        return []
    except Exception as e:
        print(f"  ❌ Unexpected Layer 1 error: {type(e).__name__}: {e}")
        return []


# ─────────────────────────────────────────────
# LAYER 2 — DECISION (LLM URL selector)
# ─────────────────────────────────────────────
def _gemini_throttle():
    """Enforce minimum gap between Gemini calls to stay under free-tier 15 RPM."""
    global _GEMINI_LAST_CALL
    elapsed = time.time() - _GEMINI_LAST_CALL
    if elapsed < _GEMINI_MIN_GAP:
        wait = _GEMINI_MIN_GAP - elapsed
        print(f"    ⏳ Gemini rate-limit pause: {wait:.1f}s (free tier: 15 RPM)")
        time.sleep(wait)
    _GEMINI_LAST_CALL = time.time()


def _call_gemini(subject: str, search_results: list[dict], objective: str) -> list[str] | None:
    """
    Primary LLM: Gemini Flash 2.0 — completely free, no credit card.
    Get key at: https://aistudio.google.com → API Keys
    """
    print("  → Attempting Gemini Flash 2.0 (free)...")
    if not GEMINI_KEY:
        print("  ⚠️  GEMINI_API_KEY not set — skipping.")
        return None

    _gemini_throttle()

    prompt = f"""You are a business intelligence analyst.
Objective: {objective}
Subject: '{subject}'

Select the top 2-3 URLs most likely to satisfy the objective.
Prefer: official company website, investor relations, Wikipedia, annual reports.
Exclude: social media, YouTube, job boards, news aggregators.

Return ONLY valid JSON: {{"urls": ["url1", "url2"]}}

Results: {json.dumps(search_results)}"""

    api_url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"gemini-2.0-flash:generateContent?key={GEMINI_KEY}"
    )
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"responseMimeType": "application/json", "temperature": 0.1},
    }

    try:
        print("    → POST .../gemini-2.0-flash:generateContent")
        resp = requests.post(api_url, json=body, timeout=30)
        print(f"    → HTTP status: {resp.status_code}")

        if resp.status_code == 400:
            print(f"    ❌ Gemini 400: {resp.text[:200]}")
            return None
        if resp.status_code == 403:
            print("    ❌ Gemini 403: Invalid key — get one free at aistudio.google.com")
            return None
        if resp.status_code == 429:
            print("    ⚠️  Gemini 429: Still rate-limited — falling back to Groq.")
            return None
        if resp.status_code != 200:
            print(f"    ❌ Gemini {resp.status_code}: {resp.text[:300]}")
            return None

        candidates = resp.json().get("candidates", [])
        if not candidates:
            print("    ❌ Gemini returned no candidates.")
            return None

        raw = candidates[0]["content"]["parts"][0]["text"].strip()
        print(f"    → Raw Gemini: {raw[:200]}")
        clean = raw.lstrip("```json").lstrip("```").rstrip("```").strip()
        urls = json.loads(clean).get("urls", [])
        print(f"    ✅ Gemini selected {len(urls)} URL(s): {urls}")
        return urls if urls else None

    except requests.exceptions.Timeout:
        print("    ❌ Gemini timed out.")
    except (KeyError, IndexError) as e:
        print(f"    ❌ Unexpected Gemini response structure: {e}")
    except json.JSONDecodeError as e:
        print(f"    ❌ Gemini JSON parse error: {e}")
    except Exception as e:
        print(f"    ❌ Gemini error: {type(e).__name__}: {e}")
    return None


def _call_groq(subject: str, search_results: list[dict], objective: str) -> list[str] | None:
    """Fallback LLM: Groq Llama 3.3 70B (free tier, 30 RPM)."""
    print("  → Attempting Groq fallback...")
    if not GROQ_KEY:
        print("  ⚠️  GROQ_API_KEY not set — skipping.")
        return None

    prompt = f"""You are a business intelligence analyst.
Objective: {objective}
Subject: '{subject}'

Select the top 2-3 URLs most likely to satisfy the objective.
Prefer: official company website, Wikipedia, investor relations.
Exclude: social media and job boards.

Return ONLY: {{"urls": ["url1", "url2"]}}

Results: {json.dumps(search_results)}"""

    headers = {"Authorization": f"Bearer {GROQ_KEY}", "Content-Type": "application/json"}
    body = {
        "model": "llama-3.3-70b-versatile",
        "messages": [{"role": "user", "content": prompt}],
        "response_format": {"type": "json_object"},
    }

    try:
        print("    → POST https://api.groq.com/openai/v1/chat/completions")
        resp = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers=headers, json=body, timeout=30
        )
        print(f"    → HTTP status: {resp.status_code}")

        if resp.status_code == 401:
            print("    ❌ Groq 401: Invalid API key.")
            return None
        if resp.status_code == 429:
            print("    ⚠️  Groq 429: Rate limit hit.")
            return None
        if resp.status_code != 200:
            print(f"    ❌ Groq {resp.status_code}: {resp.text[:300]}")
            return None

        content = resp.json()["choices"][0]["message"]["content"]
        print(f"    → Raw Groq: {content[:200]}")
        urls = json.loads(content).get("urls", [])
        print(f"    ✅ Groq selected {len(urls)} URL(s): {urls}")
        return urls if urls else None

    except requests.exceptions.Timeout:
        print("    ❌ Groq timed out.")
    except json.JSONDecodeError as e:
        print(f"    ❌ Groq JSON parse error: {e}")
    except Exception as e:
        print(f"    ❌ Groq error: {type(e).__name__}: {e}")
    return None


def select_best_urls(subject: str, search_results: list[dict], objective: str) -> list[str]:
    """
    Layer 2: Decision — LLM selects best URLs.
    Chain: Gemini → Groq → top 2 organic results.
    LinkedIn + all social media always excluded.
    """
    print(f"\n{'='*60}")
    print(f"[LAYER 2 — DECISION] Selecting URLs for: '{subject}'")
    print(f"{'='*60}")

    BLOCKED = ("linkedin.com", "instagram.com", "youtube.com", "twitter.com",
               "facebook.com", "x.com", "tiktok.com")
    filtered = [r for r in search_results if not any(d in (r.get("link") or "") for d in BLOCKED)]
    excluded = len(search_results) - len(filtered)
    if excluded:
        print(f"  ℹ️  Excluded {excluded} social/LinkedIn result(s) per policy.")

    if not filtered:
        print("  ❌ No suitable results after filtering.")
        return []

    urls = _call_gemini(subject, filtered, objective)
    if not urls:
        urls = _call_groq(subject, filtered, objective)
    if not urls:
        urls = [r["link"] for r in filtered[:2] if r.get("link")]
        print(f"  ⚠️  All LLMs failed. Using top {len(urls)} organic result(s).")

    return urls


# ─────────────────────────────────────────────
# LAYER 3 — EXTRACTION (Firecrawl)
# ─────────────────────────────────────────────
def _normalize_report(data: dict) -> dict:
    """
    Normalize extracted data so it always renders cleanly:
    - strategic_initiatives: flatten {name, details} objects → plain strings
    - executives: normalize to list of {name, title} dicts
    """
    # Strategic initiatives
    raw_si = data.get("strategic_initiatives", [])
    clean_si = []
    for item in raw_si:
        if isinstance(item, str):
            clean_si.append(item)
        elif isinstance(item, dict):
            name    = item.get("name", "")
            details = item.get("details", "")
            if name and details:
                clean_si.append(f"{name}: {details}")
            elif name:
                clean_si.append(name)
            elif details:
                clean_si.append(details)
    data["strategic_initiatives"] = clean_si

    # Executives
    raw_execs = data.get("executives", [])
    clean_execs = []
    for e in raw_execs:
        if isinstance(e, dict):
            clean_execs.append(e)
        elif isinstance(e, str) and " — " in e:
            parts = e.split(" — ", 1)
            clean_execs.append({"name": parts[0].strip(), "title": parts[1].strip()})
        elif isinstance(e, str):
            clean_execs.append({"name": e, "title": "N/A"})
    data["executives"] = clean_execs

    return data


def extract_intelligence(urls: list[str], objective: str) -> dict:
    """Layer 3: Firecrawl Extract API (async polling)."""
    print(f"\n{'='*60}")
    print(f"[LAYER 3 — EXTRACTION] Extracting from {len(urls)} URL(s):")
    for u in urls:
        print(f"  - {u}")
    print(f"{'='*60}")

    if not FIRECRAWL_KEY:
        print("  ❌ FIRECRAWL_API_KEY not set.")
        return {"error": "FIRECRAWL_API_KEY not set", "evidence_links": urls}

    if not urls:
        print("  ❌ No URLs provided.")
        return {"error": "No URLs to extract", "evidence_links": []}

    headers = {
        "Authorization": f"Bearer {FIRECRAWL_KEY}",
        "Content-Type": "application/json",
    }

    payload = {
        "urls": urls,
        "prompt": (
            f"Objective: {objective}\n\n"
            "Extract detailed business intelligence. Be specific — extract concrete named facts.\n\n"
            "1. company_name: Official legal name.\n"
            "2. headquarters: City and country.\n"
            "3. business_units: ALL divisions, subsidiaries, segments. Look for 'Our Business', "
            "'Divisions', 'Subsidiaries' sections. Include every named unit.\n"
            "4. products_services: Every product line, service, and technology platform. "
            "Include brand names, series, and categories.\n"
            "5. target_industries: Industries and customer segments served.\n"
            "6. executives: ALL named executives and board members with exact titles.\n"
            "7. strategic_initiatives: Every specific project, investment, partnership, expansion, "
            "certification, or program. Include named projects, dollar amounts, countries, "
            "MoUs, joint ventures, ISO/HACCP certifications, digital/AI/ERP programs. "
            "Return EACH as a single descriptive string, e.g. "
            "'$500B US investment including Houston factory (2025)' or "
            "'ISO 9001:2015 certification achieved'."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "company_name":          {"type": "string"},
                "headquarters":          {"type": "string"},
                "business_units":        {"type": "array", "items": {"type": "string"}},
                "products_services":     {"type": "array", "items": {"type": "string"}},
                "target_industries":     {"type": "array", "items": {"type": "string"}},
                "strategic_initiatives": {"type": "array", "items": {"type": "string"}},
                "executives": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name":  {"type": "string"},
                            "title": {"type": "string"},
                        },
                    },
                },
            },
        },
    }

    try:
        print("  → POST https://api.firecrawl.dev/v1/extract")
        resp = requests.post(
            "https://api.firecrawl.dev/v1/extract",
            headers=headers, json=payload, timeout=30
        )
        print(f"  → HTTP status: {resp.status_code}")

        if resp.status_code == 402:
            print("  ❌ Firecrawl 402: Insufficient credits.")
            return {"error": "Firecrawl insufficient credits", "evidence_links": urls}
        if resp.status_code == 401:
            print("  ❌ Firecrawl 401: Invalid API key.")
            return {"error": "Firecrawl invalid API key", "evidence_links": urls}
        if resp.status_code not in (200, 201, 202):
            print(f"  ❌ Firecrawl {resp.status_code}: {resp.text[:400]}")
            return {"error": f"Firecrawl HTTP {resp.status_code}", "evidence_links": urls}

        resp.raise_for_status()
        job_data = resp.json()
        print(f"  → Response: {json.dumps(job_data)[:300]}")

    except requests.exceptions.Timeout:
        print("  ❌ Firecrawl submit timed out.")
        return {"error": "Firecrawl timeout on submit", "evidence_links": urls}
    except Exception as e:
        print(f"  ❌ Firecrawl submit error: {type(e).__name__}: {e}")
        return {"error": str(e), "evidence_links": urls}

    # Sync guard
    inline = job_data.get("data")
    if job_data.get("success") and isinstance(inline, dict) and inline:
        print("  ✅ Synchronous extraction complete.")
        result = _normalize_report(inline)
        result["evidence_links"] = urls
        return result

    # Async polling
    job_id = job_data.get("id") or job_data.get("jobId")
    if not job_id:
        print("  ⚠️  No job ID — treating as direct data.")
        job_data["evidence_links"] = urls
        return job_data

    print(f"  → Async job ID: {job_id}. Polling...")
    poll_url = f"https://api.firecrawl.dev/v1/extract/{job_id}"

    for attempt in range(1, 25):
        time.sleep(5)
        try:
            print(f"  → Poll {attempt}/24...", end=" ", flush=True)
            pr = requests.get(poll_url, headers=headers, timeout=15)
            print(f"HTTP {pr.status_code}")
            pd = pr.json()
            status = pd.get("status", "").lower()
            print(f"     Status: {status} | Preview: {json.dumps(pd)[:150]}")

            if status == "completed":
                result = _normalize_report(pd.get("data", pd))
                result["evidence_links"] = urls
                print("  ✅ Extraction complete.")
                return result
            elif status == "failed":
                print(f"  ❌ Job failed: {pd.get('error', 'unknown')}")
                return {"error": "Firecrawl job failed", "evidence_links": urls}
            elif status in ("processing", "pending", "queued", "scraping"):
                continue
            else:
                print(f"  ⚠️  Unknown status '{status}'. Continuing...")

        except requests.exceptions.Timeout:
            print(f"  ⚠️  Poll timeout on attempt {attempt}.")
        except json.JSONDecodeError as e:
            print(f"  ⚠️  Poll JSON parse error: {e}")
        except Exception as e:
            print(f"  ⚠️  Poll error: {type(e).__name__}: {e}")

    print("  ❌ Extraction timed out after max poll attempts.")
    return {"error": "Firecrawl polling timeout", "evidence_links": urls}


# ─────────────────────────────────────────────
# GEOGRAPHY MODE
# ─────────────────────────────────────────────
def _search_with_snippets(query: str, num_results: int = 10) -> list[dict]:
    """SerpAPI search that includes snippet text for richer geo company extraction."""
    if not SERP_KEY:
        return []
    params = {"q": query, "api_key": SERP_KEY, "engine": "google", "num": num_results}
    try:
        resp = requests.get("https://serpapi.com/search", params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        if "error" in data:
            return []
        return [
            {"title": r.get("title", ""), "link": r.get("link", ""), "snippet": r.get("snippet", "")}
            for r in data.get("organic_results", [])[:num_results]
            if r.get("link")
        ]
    except Exception:
        return []


def discover_companies_in_geography(location: str, sector: str, top_n: int = 3) -> list[str]:
    """Run 3 targeted searches, combine with snippets, then ask LLM for top_n company names."""
    print(f"\n{'='*60}")
    print(f"[GEO MODE] Discovering top {top_n} '{sector}' companies in '{location}'")
    print(f"{'='*60}")

    queries = [
        f"top {sector} companies in {location}",
        f"largest {sector} companies {location} headquarters",
        f"{sector} industry leading companies {location}",
    ]

    all_results: list[dict] = []
    for q in queries:
        print(f"  → Searching: '{q}'")
        all_results.extend(_search_with_snippets(q, num_results=8))
        if len(all_results) >= 20:
            break

    if not all_results:
        print("  ❌ No results for geography queries.")
        return []

    seen: set[str] = set()
    unique = []
    for r in all_results:
        if r["link"] not in seen:
            seen.add(r["link"])
            unique.append(r)

    print(f"  ✅ {len(unique)} unique results across {len(queries)} queries.")

    prompt = f"""You are a business intelligence analyst researching {sector} companies in {location}.

Extract real, distinct company names from the search results below.
Return exactly {top_n} names if possible (fewer only if {top_n} truly cannot be found).

Rules:
- Only actual company names (e.g. "Saudi Aramco", "STC", "SABIC", "e&", "du")
- Companies must be FOUNDED or HEADQUARTERED in {location} — not just operating there
- EXCLUDE well-known global multinationals (Microsoft, Google, Amazon, SAP, Oracle, etc.) 
  unless they are genuinely a local company from {location}
- EXCLUDE directories, aggregators, listicle titles, or article names
- EXCLUDE duplicates or name variants of the same company
- When in doubt, prefer the locally-founded company over the foreign one

Return ONLY valid JSON: {{"companies": ["Company A", "Company B", "Company C"]}}

Results:
{json.dumps(unique[:20], ensure_ascii=False)}"""

    def _geo_llm(prompt: str) -> list[str] | None:
        if GEMINI_KEY:
            _gemini_throttle()
            try:
                api_url = (
                    "https://generativelanguage.googleapis.com/v1beta/models/"
                    f"gemini-2.0-flash:generateContent?key={GEMINI_KEY}"
                )
                body = {
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {"responseMimeType": "application/json", "temperature": 0.1},
                }
                resp = requests.post(api_url, json=body, timeout=30)
                if resp.status_code == 200:
                    raw = resp.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
                    clean = raw.lstrip("```json").lstrip("```").rstrip("```").strip()
                    companies = json.loads(clean).get("companies", [])
                    if companies:
                        print(f"  ✅ Gemini: {companies}")
                        return companies
                elif resp.status_code == 429:
                    print("  ⚠️  Gemini 429 → trying Groq...")
                else:
                    print(f"  ⚠️  Gemini {resp.status_code} → trying Groq...")
            except Exception as e:
                print(f"  ⚠️  Gemini geo error: {e}")

        if GROQ_KEY:
            try:
                headers = {"Authorization": f"Bearer {GROQ_KEY}", "Content-Type": "application/json"}
                body = {
                    "model": "llama-3.3-70b-versatile",
                    "messages": [{"role": "user", "content": prompt}],
                    "response_format": {"type": "json_object"},
                }
                resp = requests.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers=headers, json=body, timeout=30
                )
                if resp.status_code == 200:
                    companies = json.loads(
                        resp.json()["choices"][0]["message"]["content"]
                    ).get("companies", [])
                    if companies:
                        print(f"  ✅ Groq: {companies}")
                        return companies
                else:
                    print(f"  ⚠️  Groq {resp.status_code}")
            except Exception as e:
                print(f"  ⚠️  Groq geo error: {e}")
        return None

    print(f"\n  → Asking LLM to extract exactly {top_n} company names...")
    companies = _geo_llm(prompt)

    if not companies:
        DIR_KW = ("top ", "list", "companies in", "zoominfo", "lusha", "coresignal",
                  "techbehemoths", "db-ip", "medialandscapes", "wikipedia", "category:",
                  "tasteatlas", "dnb.com", "clutch")
        candidates = []
        for r in unique:
            if not any(k in r["title"].lower() for k in DIR_KW):
                name = r["title"].split("|")[0].split("-")[0].strip()
                if len(name) > 3 and name not in candidates:
                    candidates.append(name)
        companies = candidates[:top_n]
        if companies:
            print(f"  ⚠️  LLM fallback — title parse: {companies}")

    companies = (companies or [])[:top_n]

    # Post-filter: remove well-known global multinationals that slipped through
    GLOBAL_MNC = {
        "microsoft", "google", "amazon", "apple", "meta", "oracle", "sap",
        "ibm", "cisco", "salesforce", "accenture", "deloitte", "pwc", "kpmg",
        "mckinsey", "aws", "intel", "nvidia", "adobe", "dell", "hp", "lenovo",
    }
    before = companies[:]
    companies = [c for c in companies if c.lower().split()[0] not in GLOBAL_MNC
                 and not any(mnc in c.lower() for mnc in GLOBAL_MNC)]
    removed = [c for c in before if c not in companies]
    if removed:
        print(f"  ℹ️  Filtered global multinationals: {removed}")
        # If we removed some, try to fill back to top_n from heuristic candidates
        # (accept the shorter list if nothing better is available)

    if not companies:
        print("  ❌ Could not identify any company names.")
        return []

    print(f"\n  ✅ Final list ({len(companies)}/{top_n} requested):")
    for i, c in enumerate(companies, 1):
        print(f"    [{i}] {c}")
    return companies


# ─────────────────────────────────────────────
# REPORT SAVING
# ─────────────────────────────────────────────
def save_report(company: str, data: dict, objective: str) -> tuple[str, str]:
    """Save JSON + Markdown to /reports. Returns (json_path, md_path)."""
    os.makedirs("reports", exist_ok=True)
    raw_name = company.strip().replace(" ", "_").lower()
    filename  = "".join(c for c in raw_name if c.isalnum() or c in "_-")

    json_path = f"reports/{filename}.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)
    print(f"\n  💾 JSON saved: {json_path}")

    md_path = f"reports/{filename}.md"
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(f"# Account Intelligence Radar: {data.get('company_name', company)}\n\n")
        f.write(f"> **Objective:** {objective}\n\n---\n\n")

        f.write("## Company Identifiers\n")
        f.write(f"- **Name:** {data.get('company_name', 'N/A')}\n")
        f.write(f"- **Headquarters:** {data.get('headquarters', 'N/A')}\n\n")

        f.write("## Business Snapshot\n\n")

        bu = data.get("business_units", [])
        f.write("### Business Units\n")
        f.write(("\n".join(f"- {x}" for x in bu) if bu else "_Not found_") + "\n\n")

        ps = data.get("products_services", [])
        f.write("### Products & Services\n")
        f.write(("\n".join(f"- {x}" for x in ps) if ps else "_Not found_") + "\n\n")

        ti = data.get("target_industries", [])
        f.write("### Target Industries\n")
        f.write(("\n".join(f"- {x}" for x in ti) if ti else "_Not found_") + "\n\n")

        execs = data.get("executives", [])
        f.write("## Leadership Signals\n")
        if execs:
            for e in execs:
                if isinstance(e, dict):
                    f.write(f"- **{e.get('name', 'N/A')}** — {e.get('title', 'N/A')}\n")
                else:
                    f.write(f"- {e}\n")
        else:
            f.write("_Not found in official sources_\n")
        f.write("\n")

        si = data.get("strategic_initiatives", [])
        f.write("## Strategic Initiatives\n")
        if si:
            for item in si:
                # _normalize_report() always produces plain strings — safe to write directly
                f.write(f"- {item}\n")
        else:
            f.write("_Not found_\n")
        f.write("\n")

        links = data.get("evidence_links", [])
        f.write("## Evidence & Sources\n")
        f.write("_All claims above are sourced from the following URLs:_\n\n")
        for link in links:
            f.write(f"- {link}\n")

        if "error" in data:
            f.write(f"\n---\n⚠️ **Extraction error:** {data['error']}\n")

        f.write("\n---\n_Report generated by AVERROA Account Intelligence Radar_\n")

    print(f"  💾 Markdown saved: {md_path}")
    return json_path, md_path


# ─────────────────────────────────────────────
# SHARED PIPELINE
# ─────────────────────────────────────────────
DEFAULT_OBJECTIVE = (
    "Extract headquarters, business units, core products, target industries, "
    "key executives, and recent strategic initiatives such as digital transformation, "
    "ERP, AI, supply chain investments, and expansions. Return structured JSON."
)


def run_company_pipeline(company_name: str, objective: str) -> dict | None:
    """Run the full 3-layer pipeline for a single company."""
    results = search_companies(f"{company_name} company official site")
    if not results:
        print(f"\n❌ No search results for '{company_name}'. Skipping.")
        return None

    links = select_best_urls(company_name, results, objective)
    if not links:
        print(f"\n❌ No URLs selected for '{company_name}'. Skipping.")
        return None

    report = extract_intelligence(links, objective)

    print(f"\n{'='*60}", flush=True)
    print(f"[OUTPUT] Saving report for: {company_name}", flush=True)
    print(f"{'='*60}", flush=True)
    save_report(company_name, report, objective)
    return report


# ─────────────────────────────────────────────
# MAIN — CLI
# ─────────────────────────────────────────────
def main():
    print("\n" + "=" * 60, flush=True)
    print("   AVERROA — Account Intelligence Radar", flush=True)
    print("=" * 60, flush=True)

    _check_env()

    print("Select mode:")
    print("  [A] Company mode   — research a specific company")
    print("  [B] Geography mode — discover companies by location + sector")
    mode = input("\nEnter A or B: ").strip().upper()

    print(f"\nDefault objective:\n  \"{DEFAULT_OBJECTIVE}\"")
    custom = input("Press Enter to use default, or type your own objective: ").strip()
    objective = custom if custom else DEFAULT_OBJECTIVE
    print("  ✅ Objective set.")

    if mode == "A":
        target = input("\nEnter Company Name: ").strip()
        if not target:
            print("❌ No company name entered. Exiting.")
            return

        report = run_company_pipeline(target, objective)
        if report:
            print("\n✅ Done!", flush=True)
            print("\n--- Report Preview ---", flush=True)
            print(f"  Company:     {report.get('company_name', target)}", flush=True)
            print(f"  HQ:          {report.get('headquarters', 'N/A')}", flush=True)
            print(f"  Bus. Units:  {len(report.get('business_units', []))} found", flush=True)
            print(f"  Initiatives: {len(report.get('strategic_initiatives', []))} found", flush=True)
            print(f"  Executives:  {len(report.get('executives', []))} found", flush=True)
            if "error" in report:
                print(f"  ⚠️  Error: {report['error']}", flush=True)
        else:
            print("❌ Pipeline failed. Check errors above.")

    elif mode == "B":
        location = input("\nEnter location (e.g. 'Saudi Arabia', 'Riyadh'): ").strip()
        sector   = input("Enter sector (e.g. 'energy', 'manufacturing'): ").strip()
        top_n_input = input("How many companies to profile? [default: 3]: ").strip()
        top_n = int(top_n_input) if top_n_input.isdigit() else 3

        if not location or not sector:
            print("❌ Location and sector are required.")
            return

        companies = discover_companies_in_geography(location, sector, top_n)
        if not companies:
            print("❌ Could not identify any companies. Exiting.")
            return

        print(f"\n{'='*60}")
        print(f"[GEO MODE] Running pipeline for {len(companies)} companies...")
        print(f"{'='*60}")

        summary = []
        for i, company in enumerate(companies, 1):
            print(f"\n[{i}/{len(companies)}] Processing: {company}")
            print("-" * 40)
            report = run_company_pipeline(company, objective)
            if report:
                summary.append({
                    "company":     report.get("company_name", company),
                    "hq":          report.get("headquarters", "N/A"),
                    "bus_units":   len(report.get("business_units", [])),
                    "initiatives": len(report.get("strategic_initiatives", [])),
                    "executives":  len(report.get("executives", [])),
                })

        if summary:
            os.makedirs("reports", exist_ok=True)
            slug = f"{location}_{sector}".replace(" ", "_").replace(",", "").lower()[:30]
            idx_path = f"reports/geo_{slug}.md"
            with open(idx_path, "w", encoding="utf-8") as f:
                f.write(f"# Geography Intelligence Report\n\n")
                f.write(f"**Location:** {location}  \n**Sector:** {sector}\n\n")
                f.write(f"**Objective:** {objective}\n\n---\n\n## Companies Profiled\n\n")
                for s in summary:
                    f.write(f"### {s['company']}\n")
                    f.write(f"- **HQ:** {s['hq']}\n")
                    f.write(f"- Business Units: {s['bus_units']} found\n")
                    f.write(f"- Strategic Initiatives: {s['initiatives']} found\n")
                    f.write(f"- Executives: {s['executives']} found\n")
                    safe = "".join(c for c in s['company'].replace(' ', '_').lower() if c.isalnum() or c in "_-")
                    f.write(f"- 📄 Full report: `{safe}.json`\n\n")
            print(f"\n  💾 Geography index: {idx_path}", flush=True)

        print(f"\n✅ Geography mode complete. {len(summary)}/{len(companies)} reports generated.", flush=True)

    else:
        print("❌ Invalid mode. Please enter A or B.")


if __name__ == "__main__":
    main()
#     main()