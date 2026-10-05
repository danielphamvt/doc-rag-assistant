import logging
import uuid
import warnings

import gradio as gr
from langchain_core.messages import AIMessage, HumanMessage

# Silence noisy downstream library warnings (transformers/torch) without hiding real errors
warnings.filterwarnings("ignore", category=FutureWarning)
logging.getLogger("huggingface_hub").setLevel(logging.ERROR)
logging.getLogger("urllib3").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

from pathlib import Path

from config import (
    GRADIO_SERVER_NAME,
    GRADIO_SERVER_PORT,
    RECURSION_LIMIT,
    SOURCE_NAMES,
)
from db import chat_store
from domain_profile import PROFILE
from rag_agent.graph import agent_graph
from rag_agent.nodes_edges import content_to_text

UI_DIR = Path(__file__).parent / "ui"

# Runtime strings and streaming markers come from the active domain profile
STRINGS = PROFILE["strings"]
MARKERS = PROFILE["markers"]

# Load HTML templates from ui/templates
with open(UI_DIR / "templates" / "spinner.html", "r", encoding="utf-8") as f:
    SPINNER_TEMPLATE = f.read()

with open(UI_DIR / "templates" / "reasoning.html", "r", encoding="utf-8") as f:
    REASONING_TEMPLATE = f.read()

import re


def clean_display_sources(text: str) -> str:
    if not text:
        return text
    # SOURCE_NAMES already holds both NFC and NFD keys for every file name
    for raw_name, display_name in SOURCE_NAMES.items():
        text = text.replace(raw_name, display_name)
    # Strip trailing docx/pdf extensions case-insensitively
    text = re.sub(r'\.(docx|pdf)\b', '', text, flags=re.IGNORECASE)
    return text

def create_thread_id():
    """Generate a unique thread ID for each conversation"""
    return {"configurable": {"thread_id": str(uuid.uuid4())}, "recursion_limit": RECURSION_LIMIT}

# Initialize the config with a fresh thread ID
thread_config = create_thread_id()


def close_open_divs(html: str) -> str:
    """Close any unclosed <div> tags (streams can be cut mid-tag)."""
    deficit = html.count("<div") - html.count("</div")
    return html + "</div>" * deficit if deficit > 0 else html


# Marker words the model uses to label reasoning vs conclusion output;
# accepted synonyms for parsing, in priority order
FINAL_MARKERS = MARKERS["conclusion"]
REASONING_MARKERS = MARKERS["reasoning"]

# Line appended when the reasoning block closes and the answer begins
REASONING_CLOSING_LINE = MARKERS["reasoning_closing"]

# Friendly error notice shown when the pipeline fails (instead of a traceback)
ERROR_NOTICE = STRINGS["error_notice"]


def finalize_reasoning(reasoning_html: str) -> str:
    """Close dangling <div> tags, then append the reasoning closing line."""
    return close_open_divs(reasoning_html) + REASONING_CLOSING_LINE


def get_display_html(reasoning_text, final_text, phase, waiting_text="", is_completed=False):
    html = ""
    # 1. Reasoning box
    if reasoning_text:
        closed_reasoning = close_open_divs(reasoning_text)
        open_attr = "open" if phase == "reasoning" else 'data-final="true"'
        html += REASONING_TEMPLATE.format(open_attr=open_attr, reasoning_content=closed_reasoning)
            
    # 2. Final answer
    if final_text:
        if html:
            html += "\n\n"
        html += final_text
        
    # 3. Spinner
    if not is_completed:
        if html:
            html += "\n\n"
        status = waiting_text if waiting_text else (STRINGS["status_answering"] if phase == "final" else STRINGS["status_processing"])
        html += SPINNER_TEMPLATE.format(status=status)

    return html


def chat(message, history, conv_id):
    """Gradio entry point: stream the response, turning system errors into friendly notices."""
    try:
        yield from _chat_stream(message, history, conv_id)
    except Exception:
        logger.exception("Chat pipeline failed")
        yield get_display_html("", ERROR_NOTICE, "final", is_completed=True)


def _seed_messages(conv_id):
    """Saved turns as LangChain messages, excluding the just-saved current user message."""
    msgs = []
    for role, plain in chat_store.plain_messages(conv_id)[:-1]:
        if role == "user":
            msgs.append(HumanMessage(content=plain))
        elif plain:
            msgs.append(AIMessage(content=plain))
    return msgs


def _chat_stream(message, history, conv_id):
    global thread_config

    # Each conversation owns its LangGraph thread ("chat-<id>"). Switching
    # conversations (new chat or one restored from the sidebar) drops the
    # outgoing thread and seeds the new one with the saved turns, so context
    # survives restarts and never leaks across conversations.
    want_thread = f"chat-{conv_id}"
    if thread_config["configurable"]["thread_id"] != want_thread:
        try:
            agent_graph.checkpointer.delete_thread(thread_config["configurable"]["thread_id"])
        except AttributeError:
            pass
        thread_config = {
            "configurable": {"thread_id": want_thread, "recursion_limit": RECURSION_LIMIT}
        }

    current_state = agent_graph.get_state(thread_config)

    if current_state.next:
        agent_graph.update_state(thread_config, {"messages": [HumanMessage(content=message.strip())]})
        inputs = None
    else:
        if not current_state.values.get("messages"):
            prior = _seed_messages(conv_id)
            if prior:
                agent_graph.update_state(thread_config, {"messages": prior})
        inputs = {"messages": [HumanMessage(content=message.strip())]}

    # Show the initial spinner
    yield get_display_html("", "", "waiting", STRINGS["status_analyzing"])

    reasoning_accumulated = ""
    final_accumulated = ""
    reasoning_steps_count = 0
    current_phase = "waiting"
    waiting_status = STRINGS["status_analyzing"]
    current_msg_id = None
    current_msg_phase = None
    msg_buffer = ""
    pending_reasoning_prefix = False
    reasoning_finalized = False

    def reasoning_step_prefix() -> str:
        """Number each reasoning step (1., 2., ...) with properly paired div tags."""
        nonlocal reasoning_steps_count
        reasoning_steps_count += 1
        if reasoning_steps_count == 1:
            return '<div style="margin-top: 8px; margin-bottom: 8px; line-height: 1.6;"><b>1. </b>'
        return '</div><div style="margin-bottom: 8px; line-height: 1.6;"><b>' + str(reasoning_steps_count) + '. </b>'

    def append_stream_text(text: str, phase: str):
        """Route text to the right lane: 'final' appends the answer, otherwise the reasoning box."""
        nonlocal reasoning_accumulated, final_accumulated, reasoning_finalized, pending_reasoning_prefix
        if not text:
            return
        if phase == "final":
            if reasoning_accumulated and not reasoning_finalized:
                reasoning_accumulated = finalize_reasoning(reasoning_accumulated)
                reasoning_finalized = True
            final_accumulated += text
        else:
            if pending_reasoning_prefix:
                reasoning_accumulated += reasoning_step_prefix()
                pending_reasoning_prefix = False
            reasoning_accumulated += text

    # Enable stream_mode="messages" to receive token-level chunks
    for chunk, metadata in agent_graph.stream(inputs, thread_config, stream_mode="messages"):
        node = metadata.get("langgraph_node", "")

        # Tool events switch to a retrieval waiting spinner
        if getattr(chunk, "type", "") in ["tool", "ToolMessage"]:
            current_phase = "waiting"
            waiting_status = STRINGS["status_retrieving"]
            display_response = get_display_html(reasoning_accumulated, final_accumulated, current_phase, waiting_status)
            display_response = clean_display_sources(display_response)
            yield display_response
            continue

        # Only stream output from content-producing nodes
        if node in ["direct_answer", "orchestrator", "fallback_response", "aggregate_answers", "simple_rag_answer", "rewrite_query", "direct_tool_answer"]:
            if getattr(chunk, "type", "") in ["ai", "AIMessageChunk"]:
                content_str = content_to_text(chunk.content)

                if content_str:
                    is_chunk = type(chunk).__name__ == "AIMessageChunk"
                    # Full (non-chunk) messages are only shown while nothing has
                    # been streamed yet; afterwards they duplicate visible content.

                    if not is_chunk and node not in ["direct_answer", "rewrite_query"] and (final_accumulated or msg_buffer):
                        continue

                    is_new_message = False
                    if current_msg_id != chunk.id:
                        if current_msg_id is not None:
                            append_stream_text(msg_buffer, "final" if current_msg_phase == "final" else "reasoning")
                            msg_buffer = ""
                        current_msg_id = chunk.id
                        current_msg_phase = None
                        is_new_message = True
 
                    # Classify output and detect reasoning/conclusion markers
                    if node == "orchestrator":
                        msg_buffer += content_str
                        content_str = ""

                        found_final = False
                        for marker in FINAL_MARKERS:
                            idx = msg_buffer.upper().find(marker)
                            if idx != -1:
                                current_msg_phase = "final"
                                msg_buffer = msg_buffer[:idx] + msg_buffer[idx + len(marker):]
                                found_final = True
                                break
                                
                        if not found_final:
                            for marker in REASONING_MARKERS:
                                idx = msg_buffer.upper().find(marker)
                                if idx != -1:
                                    current_msg_phase = "reasoning"
                                    msg_buffer = msg_buffer[:idx] + msg_buffer[idx + len(marker):]
                                    break
                            
                        # Hold back the last 30 chars in case a marker is split across chunks
                        if len(msg_buffer) > 30:
                            content_str = msg_buffer[:-30]
                            msg_buffer = msg_buffer[-30:]
                            
                        node_phase = current_msg_phase if current_msg_phase else "reasoning"
                    else:
                        node_phase = "final"
                        
                    current_phase = node_phase
                    
                    if is_new_message:
                        pending_reasoning_prefix = (node_phase == "reasoning")

                    if current_msg_phase == "final":
                        pending_reasoning_prefix = False
                            
                    append_stream_text(content_str, node_phase)
                    
                    # Yield the current display state
                    display_reasoning = reasoning_accumulated + msg_buffer if current_phase == "reasoning" else reasoning_accumulated
                    display_final = final_accumulated + msg_buffer if current_phase == "final" else final_accumulated
                    
                    display_response = get_display_html(
                        display_reasoning,
                        display_final,
                        current_phase,
                        waiting_text=STRINGS["status_thinking"]
                    )
                    
                    display_response = clean_display_sources(display_response)
                    yield display_response

    # Flush whatever is left in the buffer when the stream ends
    append_stream_text(msg_buffer, current_phase)
            
    # Final check to append the conclusion line if never appended
    if reasoning_accumulated and not reasoning_finalized:
        reasoning_accumulated = finalize_reasoning(reasoning_accumulated)
        reasoning_finalized = True

    # Close any open div tag in reasoning_accumulated at the very end
    reasoning_accumulated = close_open_divs(reasoning_accumulated)

    # Safe fallback: if final_accumulated is still empty, pull the answer from graph state
    if not final_accumulated:
        try:
            latest_state = agent_graph.get_state(thread_config)
            msgs = latest_state.values.get("messages", [])
            for m in reversed(msgs):
                if isinstance(m, AIMessage) and m.content:
                    final_text = content_to_text(m.content)
                    if final_text:
                        final_accumulated = final_text
                        break
            if not final_accumulated:
                ans_list = latest_state.values.get("agent_answers", [])
                if ans_list and ans_list[0].get("answer"):
                    final_accumulated = ans_list[0]["answer"]
        except Exception as e:
            logger.warning("Fallback message extraction failed: %s", e)

    display_response = get_display_html(reasoning_accumulated, final_accumulated, "final", is_completed=True)
    display_response = clean_display_sources(display_response)
    # Never persist an empty turn: the UI uses "" as the "pending reply" marker
    if not display_response:
        display_response = ERROR_NOTICE

    try:
        chat_store.add_message(conv_id, "assistant", display_response, plain=final_accumulated)
    except Exception as e:
        logger.warning("Failed to persist assistant turn: %s", e)

    yield display_response


from ui.chatbot_ui import create_demo

# Load Architecture page template (served at /architecture for demos)
with open(UI_DIR / "templates" / "architecture.html", "r", encoding="utf-8") as f:
    ARCHITECTURE_BODY = f.read()


def create_app():
    """Build FastAPI app: /architecture page + static assets, chatbot mounted at /."""
    import uvicorn
    from fastapi import FastAPI
    from fastapi.responses import HTMLResponse
    from fastapi.staticfiles import StaticFiles

    fastapi_app = FastAPI()

    # Static assets (mermaid.min.js for offline diagram rendering)
    fastapi_app.mount("/static", StaticFiles(directory=UI_DIR / "static"), name="static")

    @fastapi_app.get("/architecture", response_class=HTMLResponse)
    def architecture_page():
        # Compose a standalone page: diagrams render client-side from local mermaid.min.js
        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Agentic Document RAG · System Architecture</title>
<link rel="stylesheet" href="/static/css/architecture.css">
</head>
<body>
<a class="arch-back" href="/">&#8592; Back to Chat</a>
{ARCHITECTURE_BODY}
<script src="/static/js/mermaid.min.js"></script>
<script>mermaid.initialize({{ startOnLoad: true, theme: 'neutral', securityLevel: 'loose', flowchart: {{ useMaxWidth: true }} }});</script>
</body>
</html>"""
        return HTMLResponse(html)

    demo = create_demo(chat)
    # Chatbot at root; explicit /architecture and /static routes registered above take precedence
    fastapi_app = gr.mount_gradio_app(fastapi_app, demo, path="/")
    return fastapi_app, uvicorn


if __name__ == '__main__':
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    fastapi_app, uvicorn = create_app()
    uvicorn.run(fastapi_app, host=GRADIO_SERVER_NAME, port=GRADIO_SERVER_PORT)
