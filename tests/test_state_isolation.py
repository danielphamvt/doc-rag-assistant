"""Regression tests for agent state isolation.

Guards the fix that gives the agent a PRIVATE `agent_messages` channel so its
internal reasoning never leaks into the parent graph's user-visible `messages`. These
tests are LLM-free (no Ollama required) — they assert structure and pure-function behavior.
"""
from langchain_core.messages import AIMessage

from rag_agent.graph import agent_graph
from rag_agent.nodes_edges import aggregate_answers, content_to_text
from rag_agent.state import accumulate_or_reset


def test_graph_has_separate_channels():
    """Verify that both messages and agent_messages channels exist in the main graph and are separate."""
    channels = set(agent_graph.channels.keys())
    assert "messages" in channels, "graph is missing main `messages` channel"
    assert "agent_messages" in channels, "graph is missing private `agent_messages` channel"


def test_accumulate_or_reset_resets_on_sentinel():
    """The per-turn reset sentinel clears accumulated answers."""
    existing = [{"index": 0, "answer": "old"}]
    assert accumulate_or_reset(existing, [{"__reset__": True}]) == []
    # Normal accumulation still appends.
    assert accumulate_or_reset(existing, [{"index": 1, "answer": "new"}]) == [
        {"index": 0, "answer": "old"},
        {"index": 1, "answer": "new"},
    ]


def test_aggregate_single_answer_skips_llm():
    """One sub-query returns the answer verbatim without an LLM synthesis call."""
    state = {"agent_answers": [{"index": 0, "question": "q", "answer": "direct answer"}], "originalQuery": "q"}
    # config is unused on the single-answer short-circuit path.
    result = aggregate_answers(state, config=None)
    msg = result["messages"][0]
    assert isinstance(msg, AIMessage)
    assert msg.content == "direct answer"


def test_aggregate_empty_answers_guard():
    """No answers → graceful message, no crash, no LLM call."""
    result = aggregate_answers({"agent_answers": [], "originalQuery": "q"}, config=None)
    assert "No answers were generated." in result["messages"][0].content


def test_content_to_text_coerces_block_lists():
    """Provider content can arrive as a block list; coerce to plain text."""
    assert content_to_text("plain") == "plain"
    assert content_to_text([{"type": "text", "text": "a"}, {"type": "tool_use"}, "b"]) == "ab"
    assert content_to_text([]) == ""
    assert content_to_text(None) == ""
