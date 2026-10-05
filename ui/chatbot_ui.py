import logging
from pathlib import Path

import gradio as gr

from config import CHATBOT_AVATAR
from db import chat_store
from domain_profile import PROFILE

logger = logging.getLogger(__name__)

UI_DIR = Path(__file__).parent

# Load CSS template
CSS_PATH = UI_DIR / "static" / "css" / "style.css"
with open(CSS_PATH, "r", encoding="utf-8") as f:
    custom_css = f.read()

# Load JS template (input autofocus + reasoning-box state + forced light mode)
JS_PATH = UI_DIR / "static" / "js" / "focus.js"
with open(JS_PATH, "r", encoding="utf-8") as f:
    focus_js = f.read()

# Load Header HTML template
HEADER_PATH = UI_DIR / "templates" / "header.html"
with open(HEADER_PATH, "r", encoding="utf-8") as f:
    HEADER_HTML = f.read().format(
        profile_name=PROFILE.get("name", "RAG Assistant"),
        profile_description=PROFILE.get("description", "Document Assistant")
    )

# Load Chatbot Placeholder HTML template
PLACEHOLDER_PATH = UI_DIR / "templates" / "chatbot_placeholder.html"
with open(PLACEHOLDER_PATH, "r", encoding="utf-8") as f:
    CHATBOT_PLACEHOLDER = f.read().format(
        profile_name=PROFILE.get("name", "RAG Assistant"),
        profile_description=PROFILE.get("description", "Document Assistant")
    )

# Load Footer HTML template
FOOTER_PATH = UI_DIR / "templates" / "footer.html"
with open(FOOTER_PATH, "r", encoding="utf-8") as f:
    FOOTER_HTML = f.read()

# Load Profile pill template (sidebar bottom)
PROFILE_PATH = UI_DIR / "templates" / "profile.html"
with open(PROFILE_PATH, "r", encoding="utf-8") as f:
    PROFILE_HTML = f.read()

# Floating expand button, shown only while the sidebar is collapsed
EXPAND_PATH = UI_DIR / "templates" / "expand.html"
with open(EXPAND_PATH, "r", encoding="utf-8") as f:
    EXPAND_HTML = f.read()

# Gemini-style plus menu template (source selection popup)
PLUS_MENU_PATH = UI_DIR / "templates" / "plus_menu.html"
with open(PLUS_MENU_PATH, "r", encoding="utf-8") as f:
    PLUS_MENU_HTML = f.read()

# --- THEME ---
# Light-only theme: every *_dark variant is pinned to its light value so the
# UI renders light even when the OS/browser prefers dark mode.
_LIGHT = dict(
    body_background_fill="#ffffff",
    body_text_color="#0f172a",
    body_text_color_subdued="#64748b",
    background_fill_primary="#ffffff",
    background_fill_secondary="#f7f7f8",
    block_background_fill="#ffffff",
    block_border_color="#ececec",
    block_label_background_fill="#ffffff",
    block_label_text_color="#475569",
    block_title_text_color="#0f172a",
    input_background_fill="#f7f7f8",
    input_border_color="#ececec",
    button_primary_background_fill="#e47451",
    button_primary_text_color="#ffffff",
    button_secondary_background_fill="#ffffff",
    button_secondary_text_color="#334155",
    button_cancel_background_fill="#ffffff",
    button_cancel_text_color="#334155",
    border_color_primary="#e2e8f0",
    border_color_accent="#e47451",
    checkbox_background_color_selected="#eef2ff",
    checkbox_label_text_color="#334155",
    table_even_background_fill="#f8fafc",
    table_odd_background_fill="#ffffff",
    stat_background_fill="#ffffff",
    code_background_fill="#f6f8fa",
)
theme = gr.themes.Soft(
    primary_hue=gr.themes.colors.indigo,
    secondary_hue=gr.themes.colors.blue,
    neutral_hue=gr.themes.colors.slate,
    radius_size=gr.themes.sizes.radius_lg,
    font=["-apple-system", "BlinkMacSystemFont", "San Francisco", "Helvetica Neue", "sans-serif"],
).set(
    block_shadow="0 1px 3px rgba(15, 23, 42, 0.08)",
    **_LIGHT,
    **{f"{k}_dark": v for k, v in _LIGHT.items()},
)

# Generic sample questions demonstrating the agent's capabilities on any corpus
EXAMPLES = [
    "What are the four main risk categories defined in the EU AI Act?",
    "Which specific AI practices are considered 'unacceptable risk' and are completely prohibited?",
    "What is the definition of a 'General-Purpose AI' (GPAI) model according to the Act?",
    "What are the main obligations for providers of 'High-Risk' AI systems before placing them on the EU market?",
    "Are open-source AI models exempt from the EU AI Act? What are the specific conditions for this exemption?",
    "What obligations do deployers (users) of high-risk AI systems have in the workplace?",
    "What are the maximum fines (in percentage of global turnover and in Euros) for violating the prohibited AI practices?",
    "Is the fine structure different for SMEs and startups compared to large corporations?",
    "I am a startup developing an AI system for sorting resumes and evaluating job applicants in Europe. What risk category does my system fall into, and what compliance steps do I need to take?",
    "Compare the regulatory obligations of a 'Provider' versus a 'Deployer' of a high-risk AI system.",
    "If a company based in the United States provides an AI system that is used by citizens inside the European Union, does the EU AI Act apply to them?",
]

def create_demo(chat_fn):
    head_script = f"""<script src="/static/js/focus.js"></script>
<script>
{focus_js}
</script>"""
    with gr.Blocks(
        title="Agentic Document RAG Assistant",
        theme=theme,
        css=custom_css,
        head=head_script,
        fill_height=True,
        fill_width=True
    ) as demo:

        # Conversation state: active conversation id + a counter that bumps to
        # re-render the sidebar history list
        conv_id_state = gr.State(None)
        conv_refresh = gr.State(0)

        with gr.Row(elem_id="app-shell"):
            # --- Left sidebar: brand, new chat, skill selector, sample questions,
            #     searchable persisted history, footer, profile ---
            with gr.Column(elem_id="sidebar", scale=0, min_width=0):
                gr.HTML(HEADER_HTML)

                new_chat_btn = gr.Button("New chat", elem_id="new-chat-btn")

                with gr.Accordion("Sample questions", open=False, elem_id="sample-questions"):
                    with gr.Column(elem_classes="sidebar-list"):
                        example_buttons = [
                            gr.Button(ex, elem_classes="sidebar-item") for ex in EXAMPLES
                        ]

                gr.HTML('<div class="sidebar-label">Chats</div>')
                conv_search = gr.Textbox(
                    placeholder="Search conversations...",
                    show_label=False,
                    container=False,
                    elem_id="conv-search",
                )

                # --- Persisted conversation history (newest first, capped by the store) ---
                # chatbot/history below are late-bound: this renders at app load,
                # after the whole Blocks layout exists.
                @gr.render(inputs=[conv_refresh, conv_search, conv_id_state])
                def recent_conversations(_tick, query, active_cid):
                    with gr.Column(elem_classes="sidebar-list conv-list"):
                        items = chat_store.list_recent()
                        if query and query.strip():
                            q = query.strip().lower()
                            items = [(t, c) for (t, c) in items if q in t.lower()]
                        if not items:
                            empty = "No matching conversations" if (query and query.strip()) else "No conversations yet"
                            gr.HTML(f'<div class="conv-empty">{empty}</div>')
                        for title, cid in items:
                            is_active = (active_cid == cid)
                            row_classes = "conv-row active" if is_active else "conv-row"
                            with gr.Row(elem_classes=row_classes):
                                title_btn = gr.Button(title, elem_classes="sidebar-item conv-title", scale=1, min_width=0)
                                title_btn.click(
                                    fn=lambda c=cid: _load_messages(c),
                                    outputs=[chatbot, history, conv_id_state],
                                )
                                del_btn = gr.Button(
                                    "",
                                    elem_classes="conv-delete",
                                    scale=0,
                                    min_width=30,
                                    size="sm",
                                )
                                del_btn.click(
                                    fn=lambda r, cur, c=cid: _delete_conversation(r, cur, c),
                                    inputs=[conv_refresh, conv_id_state],
                                    outputs=[conv_refresh, chatbot, history, conv_id_state],
                                )

                gr.HTML(FOOTER_HTML)
                gr.HTML(PROFILE_HTML)

            # --- Main column: chat + input ---
            with gr.Column(elem_id="main", scale=1):
                gr.HTML(EXPAND_HTML)

                chatbot = gr.Chatbot(
                    elem_id="chat-box",
                    show_label=False,
                    placeholder=CHATBOT_PLACEHOLDER,
                    avatar_images=(None, CHATBOT_AVATAR),
                    scale=1,
                    type="messages"
                )

                with gr.Column(elem_id="custom-input-container", scale=0):
                    with gr.Row(elem_classes="input-row"):
                        plus_menu = gr.HTML(PLUS_MENU_HTML, elem_id="input-plus-menu-wrapper")
                        command_chip = gr.HTML('<div id="command-chip-container" style="display: none;"></div>', elem_id="command-chip-wrapper")
                        chat_input = gr.Textbox(
                            placeholder="Ask anything about your document corpus...",
                            scale=7,
                            autofocus=True,
                            show_label=False,
                            container=False,
                            elem_id="chat-input"
                        )
                        submit_btn = gr.Button("", elem_id="submit-btn", scale=0, min_width=45)
                        stop_btn = gr.Button("", elem_id="stop-btn", scale=0, min_width=45, visible=False)
                    source_mode = gr.Textbox(value="All sources", visible=False, elem_id="selected-source-mode")

        # 3. State and event wiring
        history = gr.State([])

        def _load_messages(cid):
            msgs = chat_store.get_messages(cid)
            return msgs, msgs, cid

        def _delete_conversation(refresh, current, cid):
            try:
                chat_store.delete_conversation(cid)
            except Exception as e:
                logger.warning("Failed to delete conversation %s: %s", cid, e)
            if current == cid:
                # Deleted the open conversation: fall back to a fresh chat
                return refresh + 1, [], [], None
            return refresh + 1, gr.update(), gr.update(), gr.update()

        new_chat_btn.click(
            fn=lambda: ([], [], None, "All sources"),
            outputs=[chatbot, history, conv_id_state, source_mode],
            js="() => { if (window.resetSourceMode) window.resetSourceMode(); }"
        )

        # Sidebar sample questions insert into the input box
        for btn, ex in zip(example_buttons, EXAMPLES):
            btn.click(fn=lambda x=ex: x, outputs=[chat_input])


        # Show/hide the send button based on input content
        toggle_btn_js = """
        function(text) {
            let btn = document.getElementById("submit-btn");
            if (btn) {
                if (!text || text.trim() === '') {
                    btn.style.opacity = '0.5';
                    btn.style.pointerEvents = 'none';
                } else {
                    btn.style.opacity = '1';
                    btn.style.pointerEvents = 'auto';
                }
            }
        }
        """
        chat_input.change(fn=None, inputs=[chat_input], outputs=[], js=toggle_btn_js)

        def user_act(choice, user_msg, hist, conv_id):
            if not (user_msg or "").strip():
                return user_msg or "", hist, conv_id, gr.update(), gr.update()

            cmd = ""
            if choice and "RAG" in choice: cmd = "/search_documents "
            elif choice and "Internet" in choice: cmd = "/internet_search "
            text = cmd + user_msg

            if conv_id is None:
                try:
                    conv_id = chat_store.create_conversation(chat_store.make_title(text))
                except Exception as e:
                    logger.warning("Failed to create conversation: %s", e)
            if conv_id is not None:
                try:
                    chat_store.add_message(conv_id, "user", text, plain=text)
                except Exception as e:
                    logger.warning("Failed to persist user turn: %s", e)

            hist.append({"role": "user", "content": text})
            hist.append({"role": "assistant", "content": ""})
            # Swap Send for Stop while the reply streams
            return "", hist, conv_id, gr.update(visible=True), gr.update(visible=False)

        def bot_act(hist, conv_id):
            # A pending turn always ends with an empty assistant message; if it
            # does not, the submit was a no-op (e.g. blank input)
            if not hist or hist[-1]["content"] != "":
                yield hist
                return

            user_msg = hist[-2]["content"]
            # chat_fn yields response string chunk by chunk
            response = ""
            for chunk in chat_fn(user_msg, hist[:-2], conv_id):
                response = chunk
                hist[-1]["content"] = response
                yield hist

        def restore_buttons():
            return gr.update(visible=False), gr.update(visible=True)

        def stop_generation(hist, refresh):
            # Cancelled mid-stream: drop the pending empty assistant marker so
            # no blank reply row is left behind, then restore Send
            if hist and hist[-1].get("role") == "assistant" and hist[-1].get("content") == "":
                hist = hist[:-1]
            return hist, hist, gr.update(visible=False), gr.update(visible=True), refresh + 1

        # Wire Enter key and Send button; after each exchange, bump the counter
        # so the sidebar history list re-renders with the latest conversations
        act_outputs = [chat_input, history, conv_id_state, stop_btn, submit_btn]
        sub_evt1 = chat_input.submit(user_act, [source_mode, chat_input, history, conv_id_state], act_outputs, queue=False).then(
            bot_act, [history, conv_id_state], chatbot
        ).then(restore_buttons, None, [stop_btn, submit_btn])
        sub_evt2 = submit_btn.click(user_act, [source_mode, chat_input, history, conv_id_state], act_outputs, queue=False).then(
            bot_act, [history, conv_id_state], chatbot
        ).then(restore_buttons, None, [stop_btn, submit_btn])
        sub_evt1.then(lambda n: n + 1, conv_refresh, conv_refresh)
        sub_evt2.then(lambda n: n + 1, conv_refresh, conv_refresh)
        stop_btn.click(
            stop_generation,
            [history, conv_refresh],
            [chatbot, history, stop_btn, submit_btn, conv_refresh],
            cancels=[sub_evt1, sub_evt2],
        )

    return demo
