"""Prompt builders.

All prompt wording lives in the active domain profile (see
``domain_profile.py`` and ``profiles/<name>/profile.json``). This module only
selects structural variants (open vs RAG-only) and renders templates with
their runtime fields.
"""

from domain_profile import get_prompt


def get_conversation_summary_prompt() -> str:
    return get_prompt("conversation_summary")


def get_rewrite_query_prompt() -> str:
    return get_prompt("rewrite_query")


def get_orchestrator_prompt(rag_only: bool = False) -> str:
    variant = "rag_only" if rag_only else "open"
    knowledge_rule = get_prompt(f"orchestrator_knowledge_{variant}")
    sources_fallback = get_prompt(f"orchestrator_sources_{variant}")
    return get_prompt(
        "orchestrator",
        knowledge_rule=knowledge_rule,
        sources_fallback=sources_fallback,
    )


def get_fallback_response_prompt(rag_only: bool = False) -> str:
    variant = "rag_only" if rag_only else "open"
    source_integrity_rule = get_prompt(f"fallback_rules_{variant}")
    sources_warning = get_prompt(f"fallback_warning_{variant}")
    return get_prompt(
        "fallback_response",
        source_integrity_rule=source_integrity_rule,
        sources_warning=sources_warning,
    )


def get_context_compression_prompt() -> str:
    return get_prompt("context_compression")


def get_aggregation_prompt() -> str:
    return get_prompt("aggregation")


def get_internet_search_prompt(context: str, question: str, history_context: str = "") -> str:
    hist_section = (
        f"\nConversation Context (for reference):\n{history_context.strip()}\n"
        if history_context.strip() else ""
    )
    return get_prompt(
        "internet_search_answer",
        hist_section=hist_section,
        context=context,
        question=question,
    )


def get_simple_rag_answer_prompt(context: str, question: str) -> str:
    return get_prompt("simple_rag_answer", context=context, question=question)


def get_internet_no_results_prompt(question: str) -> str:
    return get_prompt("internet_no_results_answer", question=question)
