"""Tests for the domain profile system.

The default profile (vietnamese_law) must load cleanly, expose every key the
pipeline consumes, and render all prompt templates without format errors.
"""
import string

from domain_profile import DEFAULT_PROFILE, PROFILE, PROFILE_NAME, get_prompt

REQUIRED_SECTIONS = [
    "name", "response_language", "source_display_names", "source_keywords",
    "chunk_separators", "complex_query_keywords", "greetings", "small_talk",
    "internet_search", "markers", "strings", "prompts",
]

REQUIRED_PROMPTS = [
    "conversation_summary", "rewrite_query", "orchestrator",
    "orchestrator_knowledge_open", "orchestrator_sources_open",
    "orchestrator_knowledge_rag_only", "orchestrator_sources_rag_only",
    "fallback_response", "fallback_rules_open", "fallback_warning_open",
    "fallback_rules_rag_only", "fallback_warning_rag_only",
    "context_compression", "aggregation", "internet_search_answer",
    "simple_rag_answer", "internet_no_results_answer", "clarification_fallback",
]

REQUIRED_STRINGS = [
    "status_analyzing", "status_retrieving", "status_thinking",
    "status_answering", "status_processing", "error_notice",
    "retrieval_no_results", "forced_tool_empty_query", "no_answers_generated",
    "unable_to_answer", "no_query_fallback", "internet_no_results",
    "internet_timeout", "internet_error",
]


def test_default_profile_loads_with_all_sections():
    assert PROFILE_NAME == "eu_ai_act"
    for section in REQUIRED_SECTIONS:
        assert section in PROFILE, f"missing profile section: {section}"


def test_all_required_prompt_templates_present():
    for key in REQUIRED_PROMPTS:
        assert key in PROFILE["prompts"], f"missing prompt template: {key}"
        assert PROFILE["prompts"][key].strip()


def test_all_required_runtime_strings_present():
    for key in REQUIRED_STRINGS:
        assert key in PROFILE["strings"], f"missing runtime string: {key}"
        assert PROFILE["strings"][key].strip()


def test_marker_configuration_is_consistent():
    markers = PROFILE["markers"]
    assert markers["reasoning_primary"] in markers["reasoning"]
    assert markers["conclusion_primary"] in markers["conclusion"]
    assert markers["reasoning_closing"].strip()


def test_get_prompt_renders_with_runtime_fields():
    # Every template renders with sentinel values for its placeholders
    # (markers + language are injected by the loader itself).
    sentinel = {"context": "CTX", "question": "Q", "hist_section": "HIST",
                "knowledge_rule": "KR", "sources_fallback": "SF",
                "source_integrity_rule": "SIR", "sources_warning": "SW"}
    for key in REQUIRED_PROMPTS:
        template = PROFILE["prompts"][key]
        needed = {f for _, f, _, _ in string.Formatter().parse(template) if f}
        assert get_prompt(key, **{k: v for k, v in sentinel.items() if k in needed}) != ""


def test_generic_defaults_cover_every_key():
    # The generic fallback profile alone must be enough to run the pipeline,
    # so every consumed key must exist in DEFAULT_PROFILE too.
    for section in REQUIRED_SECTIONS:
        assert section in DEFAULT_PROFILE
    for key in REQUIRED_PROMPTS:
        assert key in DEFAULT_PROFILE["prompts"]
    for key in REQUIRED_STRINGS:
        assert key in DEFAULT_PROFILE["strings"]


def test_missing_profile_falls_back_to_generic(tmp_path, monkeypatch):
    import domain_profile
    monkeypatch.setattr(domain_profile, "PROFILE_NAME", "does_not_exist")
    monkeypatch.setattr(domain_profile, "PROFILES_DIR", tmp_path)
    profile = domain_profile._load_profile()
    assert profile["name"] == "generic"
