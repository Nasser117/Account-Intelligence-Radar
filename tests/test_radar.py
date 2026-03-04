"""
Unit tests for AVERROA Account Intelligence Radar
Covers: URL filtering, JSON parsing, report saving, normalize_report, OWASP masking, input sanitization
Run: python -m pytest tests/ -v
"""

import json
import os
import pytest
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import main


# ─────────────────────────────────────────────
# URL FILTERING
# ─────────────────────────────────────────────
class TestUrlFiltering:
    SAMPLE_RESULTS = [
        {"title": "Aramco Official",         "link": "https://www.aramco.com"},
        {"title": "Aramco LinkedIn",         "link": "https://www.linkedin.com/company/aramco"},
        {"title": "Aramco Instagram",        "link": "https://www.instagram.com/aramco"},
        {"title": "Aramco YouTube",          "link": "https://www.youtube.com/aramco"},
        {"title": "Aramco Wikipedia",        "link": "https://en.wikipedia.org/wiki/Aramco"},
        {"title": "Aramco Investor Rel.",    "link": "https://www.aramco.com/en/investors"},
        {"title": "Aramco Twitter",          "link": "https://twitter.com/aramco"},
        {"title": "Aramco Facebook",         "link": "https://www.facebook.com/aramco"},
    ]
    BLOCKED = ("linkedin.com","instagram.com","youtube.com","twitter.com","facebook.com")

    def _filter(self, results):
        return [r for r in results if not any(d in (r.get("link") or "") for d in self.BLOCKED)]

    def test_linkedin_excluded(self):
        assert not any("linkedin.com" in r["link"] for r in self._filter(self.SAMPLE_RESULTS))

    def test_instagram_excluded(self):
        assert not any("instagram.com" in r["link"] for r in self._filter(self.SAMPLE_RESULTS))

    def test_youtube_excluded(self):
        assert not any("youtube.com" in r["link"] for r in self._filter(self.SAMPLE_RESULTS))

    def test_twitter_excluded(self):
        assert not any("twitter.com" in r["link"] for r in self._filter(self.SAMPLE_RESULTS))

    def test_official_site_kept(self):
        assert "https://www.aramco.com" in [r["link"] for r in self._filter(self.SAMPLE_RESULTS)]

    def test_wikipedia_kept(self):
        assert "https://en.wikipedia.org/wiki/Aramco" in [r["link"] for r in self._filter(self.SAMPLE_RESULTS)]

    def test_investor_relations_kept(self):
        assert "https://www.aramco.com/en/investors" in [r["link"] for r in self._filter(self.SAMPLE_RESULTS)]

    def test_correct_count(self):
        # 5 blocked → 3 remaining
        assert len(self._filter(self.SAMPLE_RESULTS)) == 3

    def test_all_social_blocked_returns_empty(self):
        only_social = [r for r in self.SAMPLE_RESULTS if any(d in r["link"] for d in self.BLOCKED)]
        assert self._filter(only_social) == []

    def test_empty_input_returns_empty(self):
        assert self._filter([]) == []

    def test_none_link_handled(self):
        results = [{"title": "No link", "link": None}]
        assert len(self._filter(results)) == 1


# ─────────────────────────────────────────────
# JSON PARSING
# ─────────────────────────────────────────────
class TestJsonParsing:
    def test_valid_url_list(self):
        result = json.loads('{"urls": ["https://a.com", "https://b.com"]}').get("urls", [])
        assert result == ["https://a.com", "https://b.com"]

    def test_valid_company_list(self):
        result = json.loads('{"companies": ["Aramco", "SABIC"]}').get("companies", [])
        assert result == ["Aramco", "SABIC"]

    def test_missing_urls_key_returns_empty(self):
        assert json.loads('{"result": ["https://x.com"]}').get("urls", []) == []

    def test_empty_urls_list(self):
        assert json.loads('{"urls": []}').get("urls", []) == []

    def test_invalid_json_raises(self):
        with pytest.raises(json.JSONDecodeError):
            json.loads("not valid json")

    def test_markdown_fenced_json_fails(self):
        with pytest.raises(json.JSONDecodeError):
            json.loads('```json\n{"urls": ["https://x.com"]}\n```')

    def test_stripped_markdown_parses(self):
        content = '```json\n{"urls": ["https://x.com"]}\n```'
        clean = content.replace("```json", "").replace("```", "").strip()
        assert json.loads(clean).get("urls") == ["https://x.com"]

    def test_valid_extraction_schema(self):
        payload = {
            "company_name": "Aramco",
            "headquarters": "Dhahran, Saudi Arabia",
            "business_units": ["Upstream", "Downstream"],
            "products_services": ["Crude oil", "Natural gas"],
            "target_industries": ["Energy"],
            "strategic_initiatives": ["Net zero by 2050"],
            "executives": [{"name": "Amin H. Nasser", "title": "President & CEO"}],
        }
        parsed = json.loads(json.dumps(payload))
        assert parsed["company_name"] == "Aramco"
        assert len(parsed["business_units"]) == 2
        assert parsed["executives"][0]["name"] == "Amin H. Nasser"

    def test_evidence_links_present(self):
        data = {"company_name": "Test", "evidence_links": ["https://a.com", "https://b.com"]}
        assert len(data["evidence_links"]) == 2


# ─────────────────────────────────────────────
# NORMALIZE REPORT
# ─────────────────────────────────────────────
class TestNormalizeReport:
    """Tests for _normalize_report — the most complex transformation in the pipeline."""

    def test_string_initiatives_passed_through(self):
        data = {"strategic_initiatives": ["Initiative A", "Initiative B"], "executives": []}
        result = main._normalize_report(data)
        assert result["strategic_initiatives"] == ["Initiative A", "Initiative B"]

    def test_dict_initiative_with_name_and_details(self):
        data = {"strategic_initiatives": [{"name": "AI Program", "details": "ML at scale"}], "executives": []}
        result = main._normalize_report(data)
        assert result["strategic_initiatives"] == ["AI Program: ML at scale"]

    def test_dict_initiative_name_only(self):
        data = {"strategic_initiatives": [{"name": "AI Program", "details": ""}], "executives": []}
        result = main._normalize_report(data)
        assert result["strategic_initiatives"] == ["AI Program"]

    def test_dict_initiative_details_only(self):
        data = {"strategic_initiatives": [{"name": "", "details": "Some details"}], "executives": []}
        result = main._normalize_report(data)
        assert result["strategic_initiatives"] == ["Some details"]

    def test_empty_initiative_dict_excluded(self):
        data = {"strategic_initiatives": [{"name": "", "details": ""}], "executives": []}
        result = main._normalize_report(data)
        assert result["strategic_initiatives"] == []

    def test_mixed_initiative_types(self):
        data = {
            "strategic_initiatives": ["Plain string", {"name": "Dict item", "details": "detail"}],
            "executives": []
        }
        result = main._normalize_report(data)
        assert len(result["strategic_initiatives"]) == 2
        assert result["strategic_initiatives"][0] == "Plain string"
        assert result["strategic_initiatives"][1] == "Dict item: detail"

    def test_exec_dict_passthrough(self):
        data = {"strategic_initiatives": [], "executives": [{"name": "Alice", "title": "CEO"}]}
        result = main._normalize_report(data)
        assert result["executives"][0] == {"name": "Alice", "title": "CEO"}

    def test_exec_string_with_separator_parsed(self):
        data = {"strategic_initiatives": [], "executives": ["Alice Smith — CEO"]}
        result = main._normalize_report(data)
        assert result["executives"][0]["name"] == "Alice Smith"
        assert result["executives"][0]["title"] == "CEO"

    def test_exec_plain_string_gets_na_title(self):
        data = {"strategic_initiatives": [], "executives": ["Bob Jones"]}
        result = main._normalize_report(data)
        assert result["executives"][0]["name"] == "Bob Jones"
        assert result["executives"][0]["title"] == "N/A"

    def test_empty_executives_list(self):
        data = {"strategic_initiatives": [], "executives": []}
        result = main._normalize_report(data)
        assert result["executives"] == []

    def test_missing_keys_do_not_raise(self):
        data = {}
        result = main._normalize_report(data)
        assert result["strategic_initiatives"] == []
        assert result["executives"] == []


# ─────────────────────────────────────────────
# REPORT SAVING
# ─────────────────────────────────────────────
class TestReportSaving:
    SAMPLE_DATA = {
        "company_name": "Test Company",
        "headquarters": "Riyadh, Saudi Arabia",
        "business_units": ["Unit A", "Unit B"],
        "products_services": ["Product 1", "Service 2"],
        "target_industries": ["Energy", "Tech"],
        "strategic_initiatives": ["Digital transformation", "AI adoption"],
        "executives": [{"name": "Jane Doe", "title": "CEO"}, {"name": "John Smith", "title": "CFO"}],
        "evidence_links": ["https://testcompany.com", "https://en.wikipedia.org/wiki/test"],
    }
    OBJECTIVE = "Extract headquarters, business units, executives, and strategic initiatives."

    def test_json_file_created(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        main.save_report("Test Company", self.SAMPLE_DATA, self.OBJECTIVE)
        assert (tmp_path / "reports" / "test_company.json").exists()

    def test_md_file_created(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        main.save_report("Test Company", self.SAMPLE_DATA, self.OBJECTIVE)
        assert (tmp_path / "reports" / "test_company.md").exists()

    def test_json_content_valid(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        main.save_report("Test Company", self.SAMPLE_DATA, self.OBJECTIVE)
        loaded = json.loads((tmp_path / "reports" / "test_company.json").read_text())
        assert loaded["company_name"] == "Test Company"
        assert loaded["headquarters"] == "Riyadh, Saudi Arabia"

    def test_md_contains_company_name(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        main.save_report("Test Company", self.SAMPLE_DATA, self.OBJECTIVE)
        assert "Test Company" in (tmp_path / "reports" / "test_company.md").read_text(encoding="utf-8")

    def test_md_contains_evidence_links(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        main.save_report("Test Company", self.SAMPLE_DATA, self.OBJECTIVE)
        assert "https://testcompany.com" in (tmp_path / "reports" / "test_company.md").read_text(encoding="utf-8")

    def test_filename_spaces_replaced(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        main.save_report("Saudi Aramco", self.SAMPLE_DATA, self.OBJECTIVE)
        assert (tmp_path / "reports" / "saudi_aramco.json").exists()

    def test_overwrite_on_second_run(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        main.save_report("Test Company", self.SAMPLE_DATA, self.OBJECTIVE)
        modified = {**self.SAMPLE_DATA, "headquarters": "Dubai, UAE"}
        main.save_report("Test Company", modified, self.OBJECTIVE)
        loaded = json.loads((tmp_path / "reports" / "test_company.json").read_text(encoding="utf-8"))
        assert loaded["headquarters"] == "Dubai, UAE"

    def test_empty_lists_render_not_found(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        sparse = {**self.SAMPLE_DATA, "business_units": [], "executives": []}
        main.save_report("Test Company", sparse, self.OBJECTIVE)
        assert "_Not found_" in (tmp_path / "reports" / "test_company.md").read_text(encoding="utf-8")

    def test_error_field_rendered_in_md(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        with_error = {**self.SAMPLE_DATA, "error": "Firecrawl polling timeout"}
        main.save_report("Test Company", with_error, self.OBJECTIVE)
        assert "Firecrawl polling timeout" in (tmp_path / "reports" / "test_company.md").read_text(encoding="utf-8")


# ─────────────────────────────────────────────
# INPUT SANITIZATION (mirrors frontend logic in Python for server-side enforcement)
# ─────────────────────────────────────────────
class TestInputSanitization:
    """
    These tests validate the sanitization logic that should be applied
    server-side before any user input reaches the pipeline.
    The frontend already strips < > " ' ` — these tests document the expected behaviour.
    """

    DANGEROUS_CHARS = ['<', '>', '"', "'", '`']

    def _sanitize(self, s: str) -> str:
        """Mirror of the JS sanitize() function."""
        for ch in self.DANGEROUS_CHARS:
            s = s.replace(ch, '')
        return s.strip()

    def test_script_tag_stripped(self):
        assert '<script>' not in self._sanitize('<script>alert(1)</script>')

    def test_angle_brackets_removed(self):
        result = self._sanitize('<Company Name>')
        assert '<' not in result and '>' not in result

    def test_quotes_removed(self):
        result = self._sanitize('"Company" \'Name\'')
        assert '"' not in result and "'" not in result

    def test_backtick_removed(self):
        assert '`' not in self._sanitize('`injection`')

    def test_normal_name_unchanged(self):
        assert self._sanitize('Saudi Aramco') == 'Saudi Aramco'

    def test_whitespace_trimmed(self):
        assert self._sanitize('  Almarai  ') == 'Almarai'

    def test_empty_string_returns_empty(self):
        assert self._sanitize('') == ''

    def test_mixed_injection_attempt(self):
        payload = '"><img src=x onerror=alert(1)>'
        result = self._sanitize(payload)
        assert '<' not in result and '>' not in result and '"' not in result


# ─────────────────────────────────────────────
# OWASP KEY MASKING
# ─────────────────────────────────────────────
class TestOWASPSafety:
    def test_mask_hides_most_of_key(self):
        masked = main._mask("sk-abc123def456xyz9584")
        assert "sk-abc123def456xyz" not in masked
        assert "9584" in masked

    def test_mask_none_returns_not_set(self):
        assert main._mask(None) == "NOT SET"

    def test_mask_short_key(self):
        assert "abcd" in main._mask("abcd")

    def test_mask_format(self):
        masked = main._mask("secretkey1234")
        assert masked.startswith("*")
        assert masked.endswith("1234")