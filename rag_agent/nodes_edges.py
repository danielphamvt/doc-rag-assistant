from typing import Literal

import tiktoken
from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    RemoveMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.runnables import RunnableConfig
from langgraph.types import Command, Send

from config import (
    COMPRESS_TOKEN_THRESHOLD,
    MAX_HISTORY_MESSAGES,
    MAX_HISTORY_TOKENS,
    MAX_ITERATIONS,
    MAX_TOOL_CALLS,
    TOKEN_GROWTH_FACTOR,
    llm,
)
from domain_profile import PROFILE, get_prompt
from rag_agent.prompts import (
    get_aggregation_prompt,
    get_context_compression_prompt,
    get_conversation_summary_prompt,
    get_fallback_response_prompt,
    get_internet_no_results_prompt,
    get_internet_search_prompt,
    get_orchestrator_prompt,
    get_rewrite_query_prompt,
    get_simple_rag_answer_prompt,
)
from rag_agent.routing import is_greeting_message, normalize_message
from rag_agent.state import AgentState, QueryAnalysis, State
from rag_agent.tools import search_documents
from skills.internet_search.skill import internet_search

llm_with_tools = llm.bind_tools([search_documents, internet_search])

try:
    _ENCODING = tiktoken.encoding_for_model("gpt-4")
except Exception:
    _ENCODING = tiktoken.get_encoding("cl100k_base")


def estimate_context_tokens(messages: list) -> int:
    return sum(len(_ENCODING.encode(str(msg.content))) for msg in messages if hasattr(msg, 'content') and msg.content)

def format_history_lines(messages: list, keep: int = 6) -> str:
    """Join recent messages as 'Role: content' lines, skipping tool calls."""
    relevant = [
        m for m in messages
        if isinstance(m, (HumanMessage, AIMessage)) and not getattr(m, "tool_calls", None)
    ]
    return "".join(
        f"{'User' if isinstance(m, HumanMessage) else 'Assistant'}: {content_to_text(m.content)}\n"
        for m in relevant[-keep:]
    )

def _turn_reset(state: State) -> dict:
    """Per-turn resets shared by every rewrite_query exit path that starts research:
    clears the previous turn's sub-agent state so it cannot leak into the new branches."""
    return {
        "agent_answers": [{"__reset__": True}],
        "agent_messages": [RemoveMessage(id=m.id) for m in state.get("agent_messages", [])],
        "tool_call_count": {"__reset__": True},
        "iteration_count": {"__reset__": True},
        "retrieval_keys": {"__reset__": True},
        "context_summary": "",
    }

# --- DIRECT ANSWER ROUTER ---
def is_greeting(state: State) -> Literal["direct_answer", "summarize_history"]:
    # Full-message match only: substring matching misrouted short domain
    # questions that merely contain a greeting-like substring.
    if is_greeting_message(state["messages"][-1].content):
        return "direct_answer"
    return "summarize_history"

def direct_answer(state: State):
    msg = normalize_message(state["messages"][-1].content)
    small_talk = PROFILE["small_talk"]
    if any(t in msg for t in small_talk["thanks_triggers"]):
        ans = small_talk["thanks_response"]
    elif any(t in msg for t in small_talk["farewell_triggers"]):
        ans = small_talk["farewell_response"]
    else:
        ans = small_talk["default_response"]
    return {"messages": [AIMessage(content=ans)]}

# --- MAIN GRAPH NODES & EDGES ---
def summarize_history(state: State):
    messages = state["messages"]
    
    if len(messages) <= MAX_HISTORY_MESSAGES and estimate_context_tokens(messages) <= MAX_HISTORY_TOKENS:
        return {"conversation_summary": ""}

    history_lines = format_history_lines(messages[:-1])
    if not history_lines:
        return {"conversation_summary": ""}

    conversation = "Conversation history:\n" + history_lines

    summary_response = llm.with_config(temperature=0.2).invoke([SystemMessage(content=get_conversation_summary_prompt()), HumanMessage(content=conversation)])
    return {"conversation_summary": content_to_text(summary_response.content), "agent_answers": [{"__reset__": True}]}

def rewrite_query(state: State):
    last_message = state["messages"][-1]
    conversation_summary = state.get("conversation_summary", "")

    query_text = last_message.content.strip()
    forced_tool = None
    if query_text.startswith("/internet_search"):
        parts = query_text.split(" ", 1)
        query_text = parts[1] if len(parts) > 1 else ""
        forced_tool = "internet_search"
    elif query_text.startswith("/search_documents"):
        parts = query_text.split(" ", 1)
        query_text = parts[1] if len(parts) > 1 else ""
        forced_tool = "search_documents"

    if forced_tool:
        if not query_text:
            return {"questionIsClear": False, "messages": [AIMessage(content=PROFILE["strings"]["forced_tool_empty_query"])]}
        return {
            "questionIsClear": True,
            "messages": [],
            "originalQuery": last_message.content,
            "rewrittenQuestions": [{"query": query_text, "complexity": "complex", "forced_tool": forced_tool}],
            **_turn_reset(state),
        }

    # Optimization 1: Skip rewrite if it's the first question
    if not conversation_summary.strip() and len(state["messages"]) <= 1:
        # Rule-based router for the first question to save an LLM call entirely.
        query_text = last_message.content.lower()
        is_complex = any(kw in query_text for kw in PROFILE["complex_query_keywords"]) or len(query_text) > 100
        
        complexity = "complex" if is_complex else "simple"
        return {
            "questionIsClear": True,
            "messages": [],
            "originalQuery": last_message.content,
            "rewrittenQuestions": [{"query": last_message.content, "complexity": complexity, "forced_tool": None}],
            **_turn_reset(state),
        }

    recent_history_text = ""
    if not conversation_summary.strip() and len(state["messages"]) > 1:
        # History is too short to be summarized, pass raw recent messages
        history_lines = format_history_lines(state["messages"][:-1])
        if history_lines:
            recent_history_text = "Recent Conversation History:\n" + history_lines + "\n"

    history_context_out = (f"Conversation Summary:\n{conversation_summary}\n\n" if conversation_summary.strip() else "") + recent_history_text
    context_section = history_context_out + f"User Query:\n{last_message.content}\n"

    llm_with_structure = llm.with_config(temperature=0.1).with_structured_output(QueryAnalysis)
    
    try:
        response = llm_with_structure.invoke([SystemMessage(content=get_rewrite_query_prompt()), HumanMessage(content=context_section)])
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("LLM structured output validation failed: %s", e)
        # Fallback gracefully if JSON parsing fails
        clarification_msg = get_prompt("clarification_fallback")
        return {"questionIsClear": False, "messages": [AIMessage(content=clarification_msg)], "history_context": history_context_out}

    if response and getattr(response, "questions", None) and getattr(response, "is_clear", False):
        # Keep the last 10 messages (5 pairs of questions and answers) as context
        messages_to_keep = state["messages"][-10:]
        delete_old = [RemoveMessage(id=m.id) for m in state["messages"] if m not in messages_to_keep and not isinstance(m, SystemMessage)]
        return {
            "questionIsClear": True,
            "messages": delete_old,
            "originalQuery": last_message.content,
            "rewrittenQuestions": [q.model_dump() for q in response.questions],
            "history_context": history_context_out,
            **_turn_reset(state),
        }

    clarification = response.clarification_needed if response.clarification_needed and len(response.clarification_needed.strip()) > 10 else get_prompt("clarification_fallback")
    return {"questionIsClear": False, "messages": [AIMessage(content=clarification)], "history_context": history_context_out}

def request_clarification(state: State):
    return {}

def route_after_rewrite(state: State) -> Literal["request_clarification", "orchestrator", "simple_rag_answer", "direct_tool_answer"]:
    if not state.get("questionIsClear", False):
        return "request_clarification"
    else:
        routes = []
        hist_ctx = state.get("history_context", "")
        for idx, subquery in enumerate(state["rewrittenQuestions"]):
            q_text = subquery.get("query", "")
            ft = subquery.get("forced_tool")
            comp = subquery.get("complexity", "complex")

            if ft == "internet_search":
                # Internet search: single-shot lookup
                routes.append(Send("direct_tool_answer", {"question": q_text, "question_index": idx, "forced_tool": ft, "history_context": hist_ctx}))
            elif ft == "search_documents":
                # RAG-only: use orchestrator for multi-step reasoning, but restricted to search_documents only
                routes.append(Send("orchestrator", {"question": q_text, "question_index": idx, "forced_tool": ft, "history_context": hist_ctx}))
            elif comp == "simple":
                routes.append(Send("simple_rag_answer", {"question": q_text, "question_index": idx, "history_context": hist_ctx}))
            else:
                routes.append(Send("orchestrator", {"question": q_text, "question_index": idx, "history_context": hist_ctx}))
        return routes

def aggregate_answers(state: State, config: RunnableConfig):
    answers = state.get("agent_answers")
    if not answers:
        return {"messages": [AIMessage(content=PROFILE["strings"]["no_answers_generated"])]}

    # Single sub-query: the synthesis LLM call is redundant — return the answer directly.
    if len(answers) == 1:
        return {"messages": [AIMessage(content=answers[0].get("answer", ""))]}

    sorted_answers = sorted(answers, key=lambda x: x["index"])

    formatted_answers = ""
    for i, ans in enumerate(sorted_answers, start=1):
        formatted_answers += (f"\nAnswer {i}:\n"f"{ans['answer']}\n")

    conversation_summary = state.get("conversation_summary", "")
    recent_history_text = format_history_lines(state["messages"][:-1])

    history_context = ""
    if conversation_summary.strip() or recent_history_text:
        history_context = f"Conversation Context:\n{conversation_summary}\n{recent_history_text}\n"

    user_message = HumanMessage(content=f"""{history_context}Original user question: {state["originalQuery"]}\nRetrieved answers:{formatted_answers}""")
    synthesis_response = llm.invoke([SystemMessage(content=get_aggregation_prompt()), user_message], config)
    return {"messages": [AIMessage(content=content_to_text(synthesis_response.content))]}

# --- AGENT SUBGRAPH NODES & EDGES ---
def simple_rag_answer(state: State, config: RunnableConfig):
    question = state["question"]
    hist_ctx = state.get("history_context", "")

    # 1. Direct Search
    context = search_documents.invoke({"query": question})

    if hist_ctx:
        context = hist_ctx + "\n" + context

    # 2. Simple QA Prompt
    prompt = get_simple_rag_answer_prompt(context, question)

    response = llm.invoke([HumanMessage(content=prompt)], config)

    return {
        "agent_answers": [{"index": state["question_index"], "question": state["question"], "answer": content_to_text(response.content)}]
    }

def direct_tool_answer(state: State, config: RunnableConfig):
    question = state["question"]
    hist_ctx = state.get("history_context", "")

    result = internet_search.invoke({"query": question})

    # If the internet search returned nothing or failed, do NOT prepend old
    # history: the LLM could otherwise answer from stale question data and
    # contradict the actual result.
    no_results_msg = PROFILE["strings"]["internet_no_results"]
    has_results = result and "[INTERNET_DATA_START]" in result and no_results_msg not in result
    if not has_results:
        no_result_msg = get_internet_no_results_prompt(question)
        return {
            "agent_answers": [{"index": state["question_index"], "question": question, "answer": no_result_msg}]
        }

    prompt = get_internet_search_prompt(result, question, history_context=hist_ctx)

    response = llm.invoke([HumanMessage(content=prompt)], config)

    return {
        "agent_answers": [{"index": state["question_index"], "question": question, "answer": content_to_text(response.content)}]
    }

def orchestrator(state: AgentState, config: RunnableConfig):
    forced_tool = state.get("forced_tool")
    rag_only = forced_tool == "search_documents"
    r_marker = PROFILE["markers"]["reasoning_primary"]
    c_marker = PROFILE["markers"]["conclusion_primary"]
    if forced_tool == "internet_search":
        bound_llm = llm.bind_tools([internet_search])
    elif forced_tool == "search_documents":
        bound_llm = llm.bind_tools([search_documents])
    else:
        bound_llm = llm_with_tools

    context_summary = state.get("context_summary", "").strip()
    hist_ctx = state.get("history_context", "").strip()
    sys_msg = SystemMessage(content=get_orchestrator_prompt(rag_only=rag_only))
    
    combined_ctx = ""
    if context_summary:
        combined_ctx += f"[COMPRESSED CONTEXT FROM PRIOR RESEARCH]\n\n{context_summary}\n\n"
    if hist_ctx:
        combined_ctx += f"[CONVERSATION HISTORY]\n\n{hist_ctx}\n\n"
        
    summary_injection = (
        [HumanMessage(content=combined_ctx.strip())]
        if combined_ctx else []
    )
    if not state.get("agent_messages"):
        human_msg = HumanMessage(content=state["question"])
        tool_name_to_force = forced_tool if forced_tool else "search_documents"
        force_search = HumanMessage(content=f"YOU MUST CALL '{tool_name_to_force}' AS THE FIRST STEP TO ANSWER THIS QUESTION.\n\nCRITICAL: You MUST start your response with EXACTLY '{r_marker} ' and write a short explanation BEFORE calling the tool. OR use EXACTLY '{c_marker} ' if giving the final answer!")
        response = bound_llm.invoke([sys_msg] + summary_injection + [human_msg, force_search], config)
        return {"agent_messages": [human_msg, response], "tool_call_count": len(response.tool_calls or []), "iteration_count": 1, "forced_tool": forced_tool}

    format_reminder = HumanMessage(content=f"CRITICAL: You MUST start your response with EXACTLY '{r_marker} ' and write a short explanation BEFORE calling a tool. OR use EXACTLY '{c_marker} ' if giving the final answer!")
    response = bound_llm.invoke([sys_msg] + summary_injection + state["agent_messages"] + [format_reminder], config)
    # Don't save the format_reminder to state to avoid history clutter
    tool_calls = response.tool_calls if hasattr(response, "tool_calls") else []
    return {"agent_messages": [response], "tool_call_count": len(tool_calls) if tool_calls else 0, "iteration_count": 1, "forced_tool": forced_tool}

def route_after_orchestrator_call(state: AgentState) -> Literal["tools", "fallback_response", "collect_answer"]:
    iteration = state.get("iteration_count", 0)
    tool_count = state.get("tool_call_count", 0)

    if iteration >= MAX_ITERATIONS or tool_count > MAX_TOOL_CALLS:
        return "fallback_response"

    last_message = state["agent_messages"][-1]
    tool_calls = getattr(last_message, "tool_calls", None) or []

    if not tool_calls:
        return "collect_answer"
    
    return "tools"

def fallback_response(state: AgentState, config: RunnableConfig):
    forced_tool = state.get("forced_tool")
    rag_only = forced_tool == "search_documents"
    
    seen = set()
    unique_contents = []
    for m in state["agent_messages"]:
        if isinstance(m, ToolMessage) and m.content not in seen:
            unique_contents.append(m.content)
            seen.add(m.content)

    context_summary = state.get("context_summary", "").strip()

    context_parts = []
    if context_summary:
        context_parts.append(f"## Compressed Research Context (from prior iterations)\n\n{context_summary}")
    if unique_contents:
        context_parts.append(
            "## Retrieved Data (current iteration)\n\n" +
            "\n\n".join(f"--- DATA SOURCE {i} ---\n{content}" for i, content in enumerate(unique_contents, 1))
        )

    context_text = "\n\n".join(context_parts) if context_parts else "No data was retrieved from the documents."

    # agent_messages[0] is the human question injected by the orchestrator;
    # fall back through the state fields if it is missing.
    agent_msgs = state.get("agent_messages", [])
    actual_query = (content_to_text(agent_msgs[0].content) if agent_msgs else "") or state.get("question") or state.get("originalQuery") or PROFILE["strings"]["no_query_fallback"]

    prompt_content = (
        f"USER QUERY: {actual_query}\n\n"
        f"{context_text}\n\n"
        f"INSTRUCTION:\nPlease answer the user query based on the system prompt rules."
    )
    response = llm.invoke([SystemMessage(content=get_fallback_response_prompt(rag_only=rag_only)), HumanMessage(content=prompt_content)], config)
    return {"agent_messages": [response]}

def should_compress_context(state: AgentState) -> Command[Literal["compress_context", "orchestrator"]]:
    messages = state["agent_messages"]

    new_ids: set[str] = set()
    for msg in reversed(messages):
        if isinstance(msg, AIMessage) and getattr(msg, "tool_calls", None):
            for tc in msg.tool_calls:
                if tc["name"] == "search_documents":
                    query = tc["args"].get("query", "")
                    if query:
                        new_ids.add(f"search::{query}")
            break

    updated_ids = state.get("retrieval_keys", set()) | new_ids

    current_token_messages = estimate_context_tokens(messages)
    current_token_summary = estimate_context_tokens([HumanMessage(content=state.get("context_summary", ""))])
    current_tokens = current_token_messages + current_token_summary

    max_allowed = COMPRESS_TOKEN_THRESHOLD + int(current_token_summary * TOKEN_GROWTH_FACTOR)

    goto = "compress_context" if current_tokens > max_allowed else "orchestrator"
    return Command(update={"retrieval_keys": updated_ids}, goto=goto)

def compress_context(state: AgentState):
    messages = state["agent_messages"]
    existing_summary = state.get("context_summary", "").strip()

    if not messages:
        return {}

    conversation_text = f"USER QUESTION:\n{state.get('question')}\n\nConversation to compress:\n\n"
    if existing_summary:
        conversation_text += f"[PRIOR COMPRESSED CONTEXT]\n{existing_summary}\n\n"

    for msg in messages[1:]:
        if isinstance(msg, AIMessage):
            tool_calls_info = ""
            if getattr(msg, "tool_calls", None):
                calls = ", ".join(f"{tc['name']}({tc['args']})" for tc in msg.tool_calls)
                tool_calls_info = f" | Tool calls: {calls}"
            conversation_text += f"[ASSISTANT{tool_calls_info}]\n{msg.content or '(tool call only)'}\n\n"
        elif isinstance(msg, ToolMessage):
            tool_name = getattr(msg, "name", "tool")
            conversation_text += f"[TOOL RESULT — {tool_name}]\n{msg.content}\n\n"

    summary_response = llm.invoke([SystemMessage(content=get_context_compression_prompt()), HumanMessage(content=conversation_text)])
    new_summary = summary_response.content

    retrieved_ids: set[str] = state.get("retrieval_keys", set())
    if retrieved_ids:
        search_queries = sorted(r.replace("search::", "") for r in retrieved_ids if r.startswith("search::"))
        block = "\n\n---\n**Already executed (do NOT repeat):**\n"
        block += "Search queries already run:\n" + "\n".join(f"- {q}" for q in search_queries) + "\n"
        new_summary += block

    return {"context_summary": new_summary, "agent_messages": [RemoveMessage(id=m.id) for m in messages[1:]]}

def content_to_text(content) -> str:
    """Coerce message content (plain str or provider block list) to plain text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            b.get("text", "") if isinstance(b, dict) and b.get("type") == "text"
            else (b if isinstance(b, str) else "")
            for b in content
        )
    return str(content) if content else ""

def collect_answer(state: AgentState):
    last_message = state["agent_messages"][-1]
    is_valid = isinstance(last_message, AIMessage) and last_message.content and not last_message.tool_calls

    answer = PROFILE["strings"]["unable_to_answer"]
    if is_valid:
        answer = content_to_text(last_message.content)

    return {
        "agent_answers": [{"index": state["question_index"], "question": state["question"], "answer": answer}]
    }
