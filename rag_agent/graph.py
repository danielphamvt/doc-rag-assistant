from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode

from rag_agent.nodes_edges import (
    aggregate_answers,
    collect_answer,
    compress_context,
    direct_answer,
    direct_tool_answer,
    fallback_response,
    is_greeting,
    orchestrator,
    request_clarification,
    rewrite_query,
    route_after_orchestrator_call,
    route_after_rewrite,
    should_compress_context,
    simple_rag_answer,
    summarize_history,
)
from rag_agent.state import State
from rag_agent.tools import search_documents
from skills.internet_search.skill import internet_search

checkpointer = InMemorySaver()

# --- BUILD FLATTENED MAIN GRAPH ---
graph_builder = StateGraph(State)

# Add standard nodes
graph_builder.add_node(direct_answer)
graph_builder.add_node(summarize_history)
graph_builder.add_node(rewrite_query)
graph_builder.add_node(request_clarification)
graph_builder.add_node(simple_rag_answer)
graph_builder.add_node(direct_tool_answer)
graph_builder.add_node(aggregate_answers)

# Add agent nodes directly to the main graph
graph_builder.add_node("orchestrator", orchestrator)
graph_builder.add_node("agent_tools", ToolNode([search_documents, internet_search], messages_key="agent_messages"))
graph_builder.add_node("compress_context", compress_context)
graph_builder.add_node("fallback_response", fallback_response)
graph_builder.add_node("should_compress_context", should_compress_context)
graph_builder.add_node("collect_answer", collect_answer)

# Routing and lifecycle edges
graph_builder.add_conditional_edges(START, is_greeting)
graph_builder.add_edge("direct_answer", END)
graph_builder.add_edge("summarize_history", "rewrite_query")
graph_builder.add_conditional_edges("rewrite_query", route_after_rewrite)
graph_builder.add_edge("request_clarification", "rewrite_query")

# Agent loop edges
graph_builder.add_conditional_edges(
    "orchestrator", 
    route_after_orchestrator_call, 
    {"tools": "agent_tools", "fallback_response": "fallback_response", "collect_answer": "collect_answer"}
)
graph_builder.add_edge("agent_tools", "should_compress_context")
graph_builder.add_edge("compress_context", "orchestrator")
graph_builder.add_edge("fallback_response", "collect_answer")

# Parallel joins to aggregate_answers
graph_builder.add_edge("collect_answer", "aggregate_answers")
graph_builder.add_edge("simple_rag_answer", "aggregate_answers")
graph_builder.add_edge("direct_tool_answer", "aggregate_answers")
graph_builder.add_edge("aggregate_answers", END)

# Compile with memory and interrupt on clarification
agent_graph = graph_builder.compile(
    checkpointer=checkpointer, 
    interrupt_before=["request_clarification"]
)
