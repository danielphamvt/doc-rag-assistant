# Prompt Templates — `profiles/vietnamese_law/prompts/`

Every file in this directory is a **system prompt template** for one stage of
the RAG pipeline. They are plain text: edit them with any editor, no code
changes required. The pipeline loads them at startup via
`domain_profile.py` (selected by the `RAG_PROFILE` env variable).

This file (`README.md`) and any file starting with `_` are **not** loaded as
templates — they are documentation and partials only.

## Directory map

| File | Used by (graph node) | Purpose |
|---|---|---|
| `conversation_summary.md` | `summarize_history` | Compresses old conversation turns into a short summary so multi-turn questions stay answerable within the context budget. |
| `rewrite_query.md` | `rewrite_query` | Analyzes the user question (with history context) and rewrites it into self-contained sub-queries with a complexity rating. Drives the structured `QueryAnalysis` output. |
| `orchestrator.md` | `orchestrator` | The main agent prompt: researcher persona, tool-use rules, answer structure ("### Kết luận:" first), citation discipline, and the mandatory reasoning/conclusion markers. |
| `orchestrator_knowledge_open.md` | → injected into `orchestrator.md` | Rule-4 fragment for **open mode** (all sources): the model may fall back to its own knowledge, with a mandatory warning notice. |
| `orchestrator_knowledge_rag_only.md` | → injected into `orchestrator.md` | Rule-4 fragment for **RAG-only mode** (`/search_documents`): answering outside the retrieved documents is forbidden. |
| `orchestrator_sources_open.md` | → injected into `orchestrator.md` | Rule-10 fragment: exact Sources block to use in open mode when answering from general knowledge. |
| `orchestrator_sources_rag_only.md` | → injected into `orchestrator.md` | Rule-10 fragment: exact Sources block when no relevant documents were found. |
| `simple_rag_answer.md` | `simple_rag_answer` | Single-pass answer for simple factual questions (one retrieval, no agent loop). |
| `fallback_response.md` | `fallback_response` | Synthesizes a final answer from the compressed context when the agent hits its iteration/tool budget. |
| `fallback_rules_open.md` / `fallback_rules_rag_only.md` | → injected into `fallback_response.md` | Rule-1 fragments (source-integrity policy per mode). |
| `fallback_warning_open.md` / `fallback_warning_rag_only.md` | → injected into `fallback_response.md` | Sources-warning fragments per mode. |
| `context_compression.md` | `compress_context` | Compresses the agent's working context (tool results so far) into a structured summary so the research loop can continue inside the token budget. |
| `aggregation.md` | `aggregate_answers` | Merges the answers of parallel sub-queries into one natural, cited response. |
| `internet_search_answer.md` | `direct_tool_answer` | Answers using ONLY internet search results (when the user forces `/internet_search`). |
| `internet_no_results_answer.md` | `direct_tool_answer` | Canned response shown when the internet search returns nothing. |
| `clarification_fallback.md` | `rewrite_query` | Default clarification question when the user's message is too ambiguous and the model gave no explanation. |

## Variant composition

`orchestrator.md` and `fallback_response.md` contain the placeholders
`{knowledge_rule}` / `{sources_fallback}` and `{source_integrity_rule}` /
`{sources_warning}`. At render time the code fills them with one of the
`*_open` or `*_rag_only` fragments, depending on the mode the user chose
(dropdown "All sources" vs "RAG"). If you reword the fragments, keep them
self-contained sentences that read correctly in place of the placeholder.

## Placeholders

| Placeholder | Provided by | Meaning |
|---|---|---|
| `{reasoning_primary}` | loader (from `profile.json` → `markers`) | The marker the model must start reasoning steps with (here `[SUY LUẬN]`). |
| `{conclusion_primary}` | loader (from `profile.json` → `markers`) | The marker for final answers (here `[KẾT LUẬN]`). |
| `{response_language}` | loader (from `profile.json`) | Language the assistant must reply in (here `Vietnamese`). |
| `{context}` | pipeline | Retrieved document context / search results. |
| `{question}` | pipeline | The (rewritten) user question. |
| `{hist_section}` | pipeline | Optional conversation-history block for internet answers. |

## How to edit safely

1. **Keep every `{placeholder}` intact** — a missing or renamed placeholder
   raises an error the first time that prompt renders.
2. **Markers are a contract.** `[SUY LUẬN]` / `[KẾT LUẬN]` must appear via
   `{reasoning_primary}` / `{conclusion_primary}` and stay consistent with the
   `markers` section of `../profile.json` — the streaming UI parser strips
   exactly those markers to split reasoning from the final answer.
3. **The file content is the prompt, byte for byte** — including the trailing
   newline and any blank lines. A trailing newline you add or remove becomes
   part of what the LLM sees.
4. **Restart the app** after editing (`make run` again) — templates are read
   once at import time.
5. **Verify** with the test suite (no LLM needed):

   ```bash
   pytest tests/test_domain_profile.py -v   # all templates render cleanly
   ```

## Adding / removing templates

- A **new** `something.md` file becomes available under the template key
  `"something"` (file stem) — code that calls `get_prompt("something", ...)`
  can use it.
- **Deleting** a file does not break the app: the loader falls back to the
  generic English default for that key (declared in `domain_profile.py`).
- To override only some prompts in a **new domain**, copy just the files you
  want to change into the new profile's `prompts/` directory.
