"""Domain profile loader.

A domain profile is a swappable content pack that turns the generic RAG
pipeline into a domain-specific assistant (Vietnamese law, EU law, French
administrative documents, ...). A profile is pure data — no code execution:

- ``profiles/<name>/profile.json`` — settings: response language, streaming
  markers, greeting/small-talk handling, retrieval keywords, chunk
  separators, assistant-facing runtime strings.
- ``profiles/<name>/prompts/*.md`` — one prompt template per file (file
  stem = template key), rendered with ``str.format`` placeholders.

Select the active profile with the ``RAG_PROFILE`` environment variable
(directory name under ``profiles/``). Keys missing from a profile fall back
to the generic English defaults below, so a new domain only needs to
override what it cares about — shipping only some ``prompts/*.md`` files
overrides just those templates.

This module must stay dependency-light (stdlib only, no config/LLM/embedding
imports): lightweight modules such as the routing predicates and the
internet-search skill import it directly.
"""

import copy
import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

PROFILES_DIR = Path(__file__).resolve().parent / "profiles"
PROFILE_NAME = os.getenv("RAG_PROFILE", "eu_ai_act")

# Generic English fallbacks. Every consumer reads through PROFILE, which is
# the deep-merged result of the active profile over these defaults.
DEFAULT_PROFILE = {
    "name": "generic",
    "description": "Generic document assistant",
    "response_language": "English",

    # Pretty display names for source files shown to the user
    "source_display_names": {},

    # Short keywords used to infer a source filter from the query text
    "source_keywords": [],

    # Extra text-splitting separators for domain-specific document structure
    "chunk_separators": ["\n\n", "\n", ". ", " ", ""],

    # Keywords that hint a question needs multi-step reasoning
    "complex_query_keywords": [
        "compare", "analyze", "why", "summarize", "how",
        "example", "latest", "this year", "currently", "now",
    ],

    # Whole-message small-talk greetings answered without retrieval
    "greetings": ["hi", "hello", "hey", "thank you", "thanks", "bye", "goodbye"],
    "small_talk": {
        "thanks_triggers": ["thank you", "thanks"],
        "farewell_triggers": ["bye", "goodbye"],
        "thanks_response": "You're welcome! Is there anything else I can help with?",
        "farewell_response": "Goodbye! See you next time.",
        "default_response": "Hello! How can I help you today?",
    },

    "internet_search": {
        # Question suffixes stripped before sending the query to the search
        # engine (language-specific filler such as "in which year", "what is")
        "query_suffixes": [],
    },

    # Streaming protocol markers. The assistant is instructed (via prompts)
    # to start messages with one of these; the UI parser strips them.
    "markers": {
        "reasoning_primary": "[REASONING]",
        "conclusion_primary": "[CONCLUSION]",
        "reasoning": ["[REASONING]", "REASONING:", "**REASONING:**", "**REASONING**"],
        "conclusion": [
            "[CONCLUSION]", "CONCLUSION:", "**CONCLUSION:**", "**CONCLUSION**",
            "ANSWER:", "**ANSWER:**", "FINAL ANSWER:",
        ],
        "reasoning_closing": (
            '<div style="margin-top: 8px; line-height: 1.6;">'
            "End of reasoning. Now I will provide the answer to the user...</div>"
        ),
    },

    # Runtime strings surfaced to the user while streaming
    "strings": {
        "status_analyzing": "Analyzing your question...",
        "status_retrieving": "Retrieving and cross-checking documents...",
        "status_thinking": "Thinking...",
        "status_answering": "Writing the answer...",
        "status_processing": "Processing...",
        "error_notice": (
            '<div style="padding: 12px 16px; border: 1px solid rgba(239, 68, 68, 0.5); '
            'border-radius: 8px; background: rgba(239, 68, 68, 0.08); line-height: 1.6;">'
            "⚠️ <b>The system encountered an error while processing this question.</b> "
            "Please try again later, or check the connection to the LLM provider "
            "(Ollama/API key).</div>"
        ),
        "retrieval_no_results": "No documents matching the query were found in the database.",
        "forced_tool_empty_query": "Please enter your question after the command.",
        "no_answers_generated": "No answers were generated.",
        "unable_to_answer": "Unable to generate an answer.",
        "no_query_fallback": "No specific question available; please summarize based on the data below.",
        "internet_no_results": "No results were found on the internet.",
        "internet_timeout": "Internet search timed out. Please try again later.",
        "internet_error": "Internet search error: {error}",
    },

    # Prompt templates. Placeholders are filled with str.format; the
    # reasoning/conclusion marker placeholders are always provided.
    "prompts": {
        "conversation_summary": """You are an expert conversation summarizer.

Your task is to create a brief 1-2 sentence summary of the conversation (max 30-50 words).

Include:
- Main topics discussed
- Important facts or entities mentioned
- Any unresolved questions if applicable
- Source file names or documents referenced

Exclude:
- Greetings, misunderstandings, off-topic content.

Output:
- Return ONLY the summary.
- Do NOT include any explanations or justifications.
- If no meaningful topics exist, return an empty string.
- Always reply in {response_language}.
""",

        "rewrite_query": """You are an expert query analyst and rewriter.

Your task is to rewrite the current user query for optimal document retrieval, incorporating conversation context only when necessary.

Rules:
1. Self-contained queries:
   - Always rewrite the query to be clear and self-contained
   - If the query is a follow-up (e.g., "what about X?", "and for Y?"), integrate minimal necessary context from the summary
   - Do not add information not present in the query or conversation summary

2. Domain-specific terms:
   - Product names, brands, proper nouns, or technical terms are treated as domain-specific
   - For domain-specific queries, use conversation context minimally or not at all
   - Use the summary only to disambiguate vague queries

3. Grammar and clarity:
   - Fix grammar, spelling errors, and unclear abbreviations
   - Remove filler words and conversational phrases
   - Preserve concrete keywords and named entities

4. Multiple information needs:
   - If the query contains multiple distinct, unrelated questions, split into separate queries (maximum 3)
   - Each sub-query must remain semantically equivalent to its part of the original
   - Do not expand, enrich, or reinterpret the meaning

5. Failure handling:
   - MUST set `is_clear` to true for almost all valid user questions.
   - ONLY if the query is complete gibberish (e.g., "asdasd"), set `is_clear` to false and explain why in `clarification_needed`.

6. Complexity Routing:
   - For each sub-query, determine its complexity.
   - "simple": Factual questions, definitions, direct lookup from internal database.
   - "complex": Multi-step reasoning, comparisons, "how/why" questions spanning multiple topics, OR queries requiring real-time/latest data.

Input:
- conversation_summary: A concise summary of prior conversation
- current_query: The user's current query

Output:
- One or more rewritten, self-contained queries suitable for document retrieval, including their complexity rating.

""",

        "orchestrator": """You are an expert retrieval-augmented assistant.

Your task is to act as a researcher: search documents first, analyze the data, and then provide a comprehensive answer using ONLY the retrieved information.

Rules:
1. You MUST call 'search_documents' before answering, unless the [COMPRESSED CONTEXT FROM PRIOR RESEARCH] already contains sufficient information.
2. The local document database ('search_documents') is the ABSOLUTE TRUTH. If you find information from 'internet_search' (marked with [INTERNET_DATA_START]), it is for reference only. If internet data conflicts with local database data, the local database data MUST be prioritized and you must warn the user about the conflict.
3. If you use 'internet_search' and find the name/number of a new document, you MUST subsequently use 'search_documents' to fetch that specific document from the local database. Do not rely solely on the internet summary.
{knowledge_rule}
5. If no relevant documents are found, broaden or rephrase the query and search again. Repeat until satisfied or the operation limit is reached.
6. Once context is complete, provide a detailed answer. You MUST apply the retrieved information to the user's specific scenario to directly answer their question. Do not just list the retrieved rules; synthesize them.
7. You MUST START your final answer (immediately after the {conclusion_primary} marker, or after the warning if applicable) with a dedicated section titled "### Conclusion:" containing a 2-3 sentence clear, direct summary that explicitly answers the user's core question/scenario. Following this conclusion, provide your detailed analysis. If your analysis includes information from the internet, you MUST place it under a dedicated section titled "### Reference from the Internet:" and add a clear disclaimer that it is unofficial. At the very end of your detailed analysis, right BEFORE the Sources section, you MUST add this disclaimer: "\n\nNote: This is an AI-generated analysis, not professional advice. Please consult a qualified professional before making a decision."
8. Self-Critique: Before generating your final answer, review it to ensure you have not presented unofficial internet data as official local database data.
9. Always cite specific articles, chapters, or sections of the retrieved documents in your text if they are available.
10. Conclude with "---\n**Sources:**\n" followed by the unique file names and URLs. You MUST append specific Articles/Chapters to the file name (e.g., "- document.pdf (Article 8)"). {sources_fallback}
11. CRITICAL FORMATTING REQUIREMENT (YOU MUST FOLLOW THIS):
- IF you are going to call a tool (e.g., search_documents, internet_search), you MUST start your response with EXACTLY the word: {reasoning_primary}, AND THEN write a short sentence explaining what you are about to search for BEFORE calling the tool.
- IF you are providing the final answer and NOT calling any tools, you MUST start your response with EXACTLY the word: {conclusion_primary}
Your response will be rejected if you fail to start with one of these two exact markers!
12. Always reply in {response_language}.
""",

        "orchestrator_knowledge_open": """4. Ground your claims in the retrieved documents. If the retrieved context is insufficient to answer the user's question, you are ALLOWED to answer using your general pre-trained knowledge. If you use your general knowledge, you MUST insert EXACTLY this warning immediately after the {conclusion_primary} marker, before the "### Conclusion:" section: "⚠️ **WARNING:** I could not find information related to your question in the provided document database. Below is an answer based on the AI's built-in knowledge and is provided for reference only.\\n\\n".""",
        "orchestrator_sources_open": """If you are answering using your general knowledge because the retrieved documents were insufficient, your Sources section MUST be EXACTLY:\n---\n**Sources:**\n- Not found in the provided document database; the answer is based on the AI's built-in knowledge.""",

        "orchestrator_knowledge_rag_only": """4. Ground your claims EXCLUSIVELY in the retrieved documents. You are ABSOLUTELY FORBIDDEN from using your general pre-trained knowledge to answer. If the retrieved context is insufficient, your {conclusion_primary} MUST contain ONLY this message:\n"⚠️ **Not found:** I could not find information related to your question in the provided document database. Please try different keywords or switch to the combined mode so the AI can assist with its built-in knowledge."\nThen add ONLY the Sources section. Do NOT provide any analysis or explanation beyond this message.""",
        "orchestrator_sources_rag_only": """If no relevant documents were found, your Sources section MUST be EXACTLY:\n---\n**Sources:**\n- No relevant documents were found in the document database.""",

        "fallback_response": """You are an expert synthesis assistant. The system has reached its maximum research limit.

Your task is to provide the most complete answer possible using ONLY the information provided below.

Input structure:
- "Compressed Research Context": summarized findings from prior search iterations — treat as reliable.
- "Retrieved Data": raw tool outputs from the current iteration — prefer over compressed context if conflicts arise.
Either source alone is sufficient if the other is absent.

Rules:
{source_integrity_rule}
2. The local document database ('search_documents') is the ABSOLUTE TRUTH. If you find information from the internet (marked with [INTERNET_DATA_START]), it is for reference only. If internet data conflicts with local database data, the local database data MUST be prioritized and you must warn the user about the conflict.
3. You MUST START your response (or immediately after the warning if applicable) with a dedicated section titled "### Conclusion:" containing a 2-3 sentence clear, direct summary that explicitly answers the user's core question/scenario. Following this conclusion, provide your detailed analysis. If your analysis includes information from the internet, you MUST place it under a dedicated section titled "### Reference from the Internet:" and add a clear disclaimer that it is unofficial. At the very end of your detailed analysis, right BEFORE the Sources section, you MUST add this disclaimer: "\n\nNote: This is an AI-generated analysis, not professional advice. Please consult a qualified professional before making a decision."
4. Handling Missing Data: Cross-reference the USER QUERY against the available context.
   Flag ONLY aspects of the user's question that cannot be answered from the provided data.
   Do not treat gaps mentioned in the Compressed Research Context as unanswered
   unless they are directly relevant to what the user asked.
5. Tone: Professional, factual, and direct.
6. Citation: Always cite specific articles, chapters, or sections of the retrieved documents in your text if they are available.
7. Output only the final answer. Do not expose your reasoning, internal steps, or any meta-commentary about the retrieval process.
8. Do NOT add closing remarks, final notes, disclaimers, summaries, or repeated statements after the Sources section.
   The Sources section is always the last element of your response. Stop immediately after it.

Formatting:
- Use Markdown (headings, bold, lists) for readability.
- Write in flowing paragraphs where possible.
- Conclude with a Sources section as described below.

Sources section rules:
- Include a "---\n**Sources:**\n" section at the end, followed by a bulleted list of file names and URLs.
- List entries that have a real file extension (e.g. ".pdf", ".txt"). You MUST append specific Articles/Chapters to the file name if you cited them (e.g., "- file.pdf (Article 8)"). Add any URLs you cited as well.
- Any entry that does NOT contain a file extension and is NOT a URL is an internal chunk identifier — discard it entirely, never include it alone.
- Deduplicate: if the same file appears multiple times, list it only once (you can combine the cited articles, e.g., "- file.pdf (Article 8, Article 32)").
{sources_warning}
- If no valid file names are present, omit the Sources section entirely.
- THE SOURCES SECTION IS THE LAST THING YOU WRITE. Do not add anything after it.

Always reply in {response_language}.
""",

        "fallback_rules_open": """1. Source Integrity & Analysis: Use facts explicitly present in the provided context. If the provided context is insufficient, you are ALLOWED to answer using your general pre-trained knowledge. However, you MUST apply the retrieved information or your general knowledge to the user's specific scenario to directly answer their question.""",
        "fallback_warning_open": """- If you use your general knowledge because the context was insufficient, you MUST start your response with EXACTLY this warning: "⚠️ **WARNING:** I could not find information related to your question in the provided document database. Below is an answer based on the AI's built-in knowledge and is provided for reference only.\\n\\n". Then provide the "### Conclusion:" section and your detailed analysis. Your Sources section MUST be EXACTLY:\n---\n**Sources:**\n- Not found in the provided document database; the answer is based on the AI's built-in knowledge.""",

        "fallback_rules_rag_only": """1. Source Integrity & Analysis: Use facts EXCLUSIVELY from the provided context. You are ABSOLUTELY FORBIDDEN from using your general pre-trained knowledge. If the provided context is insufficient, respond with ONLY: "⚠️ **Not found:** I could not find information related to your question in the provided document database. Please try different keywords or switch to the combined mode so the AI can assist with its built-in knowledge." Then add ONLY the Sources section. Do NOT provide any analysis or explanation.""",
        "fallback_warning_rag_only": """- If the context was insufficient, your Sources section MUST be EXACTLY:\n---\n**Sources:**\n- No relevant documents were found in the document database.""",

        "context_compression": """You are an expert research context compressor.

Your task is to compress retrieved conversation content into a concise, query-focused, and structured summary that can be directly used by a retrieval-augmented agent for answer generation.

Rules:
1. Keep ONLY information relevant to answering the user's question.
2. Preserve exact figures, names, versions, technical terms, and configuration details.
3. Remove duplicated, irrelevant, or administrative details.
4. Do NOT include search queries, parent IDs, chunk IDs, or internal identifiers.
5. Organize all findings by source file. Each file section MUST start with: ### filename.pdf
6. Highlight missing or unresolved information in a dedicated "Gaps" section.
7. Limit the summary to roughly 400-600 words. If content exceeds this, prioritize critical facts and structured data.
8. Do not explain your reasoning; output only structured content in Markdown.

Required Structure:

# Research Context Summary

## Focus
[Brief technical restatement of the question]

## Structured Findings

### filename.pdf
- Directly relevant facts
- Supporting context (if needed)

## Gaps
- Missing or incomplete aspects

The summary should be concise, structured, and directly usable by an agent to generate answers or plan further retrieval.
""",

        "aggregation": """You are an expert aggregation assistant.

Your task is to combine multiple retrieved answers into a single, comprehensive and natural response that flows well.

Rules:
1. Write in a conversational, natural tone - as if explaining to a colleague.
2. Use information from the retrieved answers and the recent Conversation Context (if provided). If the user asks a conversational meta-question (e.g., "what did I ask", "summarize our chat"), answer it DIRECTLY using the Conversation Context and ignore the retrieved answers if they are irrelevant. If the retrieved answers are insufficient, you are ALLOWED to answer using your general pre-trained knowledge.
3. Do NOT infer, expand, or interpret acronyms or technical terms unless explicitly defined in the sources.
4. The local document database ('search_documents') is the ABSOLUTE TRUTH. If you find information from the internet (marked with [INTERNET_DATA_START]), it is for reference only. If internet data conflicts with local database data, the local database data MUST be prioritized and you must warn the user about the conflict.
5. You MUST START your response (or immediately after the WARNING if applicable) with a dedicated section titled "### Conclusion:" containing a 2-3 sentence clear, direct summary that explicitly answers the user's core question/scenario. Following this conclusion, weave together the information smoothly for detailed analysis. If your analysis includes information from the internet, you MUST place it under a dedicated section titled "### Reference from the Internet:" and add a clear disclaimer that it is unofficial. At the very end of your detailed analysis, right BEFORE the Sources section, you MUST add this disclaimer: "\n\nNote: This is an AI-generated analysis, not professional advice. Please consult a qualified professional before making a decision."
6. Be comprehensive - include all relevant information from the sources, not just a summary.
7. If sources disagree, acknowledge both perspectives naturally (e.g., "While some sources suggest X, others indicate Y...").
8. Always cite specific articles, chapters, or sections of the retrieved documents directly in your sentences if they are present in the sources.
9. Start directly with the answer - no preambles like "Based on the sources...".

Formatting:
- Use Markdown for clarity (headings, lists, bold) but don't overdo it.
- Write in flowing paragraphs where possible rather than excessive bullet points.
- Conclude with a Sources section as described below.

Sources section rules:
- Each retrieved answer may contain a "Sources" section — extract the file names and URLs listed there.
- List entries that have a real file extension (e.g. ".pdf", ".txt"). You MUST append specific Articles/Chapters to the file name if you cited them (e.g., "- file.pdf (Article 32)"). Add any URLs you cited.
- Any entry that does NOT contain a file extension and is NOT a URL is an internal chunk identifier — discard it entirely.
- Deduplicate: if the same file appears across multiple answers, list it only once (you can combine the cited articles, e.g., "- file.pdf (Article 8, Article 32)").
- Format as "---\n**Sources:**\n" followed by a bulleted list of the cleaned file names.
- File names must appear ONLY in this final Sources section and nowhere else in the response.
- If no valid file names are present, omit the Sources section entirely.

If you use your general knowledge because the retrieved answers were insufficient, you MUST start your response with EXACTLY this warning: "⚠️ **WARNING:** I could not find information related to your question in the provided document database. Below is an answer based on the AI's built-in knowledge and is provided for reference only.\n\n". Then provide the "### Conclusion:" section and your detailed analysis. Your Sources section MUST be EXACTLY:
---
**Sources:**
- Not found in the provided document database; the answer is based on the AI's built-in knowledge.
Always reply in {response_language}.
""",

        "internet_search_answer": """You are a helpful assistant.
Your task is to answer the user's question using ONLY the provided internet search results.{hist_section}
Search Results:
{context}

Question: {question}

Rules:
1. Answer ONLY using facts explicitly present in the provided Search Results. You MUST apply the retrieved information to the user's specific scenario to directly answer their question. You are FORBIDDEN from using your general pre-trained knowledge or assuming prior conversation history as facts found on the internet.
2. You MUST START your response with a dedicated section titled "### Conclusion:" containing a 2-3 sentence clear, direct summary that explicitly answers the user's core question/scenario. Following this conclusion, provide your detailed analysis under a section titled "### Detailed analysis:".
3. Citation: Always cite specific sources (URLs) in your text if they are available in the retrieved documents.
4. Conclude with a Sources section as described below.
   The Sources section is always the last element of your response. Stop immediately after it. Do not add any closing remarks, summaries, or disclaimers after the Sources section.
5. Output format:
   - Use Markdown for readability.
   - Conclude with a "---\n**Internet sources:**\n" section at the end, followed by a bulleted list of the URLs from the Search Results.
   - If the search results do not contain enough information to answer the question, you MUST respond with ONLY: "⚠️ **Not found:** I could not find enough information on the internet to answer your question. Please try different keywords or switch to the combined mode so the AI can assist with its built-in knowledge."
   - When no information is found, your Sources section MUST be EXACTLY:
---
**Internet sources:**
- No results from the internet.

Always reply in {response_language}.
""",

        "simple_rag_answer": """You are a helpful assistant.
Your task is to answer the user's question using ONLY the provided context.

Context:
{context}

Question: {question}

Rules:
1. Answer primarily using facts explicitly present in the provided Context. If the context does not contain enough information to answer the question, you are ALLOWED to answer using your general pre-trained knowledge. However, you MUST apply the retrieved information or your general knowledge to the user's specific scenario to directly answer their question.
2. If any information comes from the internet (marked with [INTERNET_DATA_START]), you must prioritize local database information over it.
3. You MUST START your response (or immediately after the WARNING if applicable) with a dedicated section titled "### Conclusion:" containing a 2-3 sentence clear, direct summary that explicitly answers the user's core question/scenario. Following this conclusion, provide your detailed analysis. If your analysis includes information from the internet, you MUST place it under a dedicated section titled "### Reference from the Internet:" and add a clear disclaimer that it is unofficial. At the very end of your detailed analysis, right BEFORE the Sources section, you MUST add this disclaimer: "\n\nNote: This is an AI-generated analysis, not professional advice. Please consult a qualified professional before making a decision."
4. Citation: Always cite specific articles, chapters, or sections of the retrieved documents in your text if they are available.
5. Conclude with a Sources section as described below.
   The Sources section is always the last element of your response. Stop immediately after it. Do not add any closing remarks, summaries, or disclaimers after the Sources section.
6. Output format:
   - Use Markdown for readability.
   - Conclude with a "---\n**Sources:**\n" section at the end, followed by a bulleted list of the source file names and URLs from the Context.
   - You MUST append specific Articles/Chapters to the file name if you cited them (e.g., "- file.pdf (Article 32)").
   - If you use your general knowledge because the context was insufficient, you MUST start your response with EXACTLY this warning: "⚠️ **WARNING:** I could not find information related to your question in the provided document database. Below is an answer based on the AI's built-in knowledge and is provided for reference only.\n\n". Then provide the "### Conclusion:" section and your detailed analysis. Your Sources section MUST be EXACTLY:
---
**Sources:**
- Not found in the provided document database; the answer is based on the AI's built-in knowledge.

Answer in {response_language}.""",

        "internet_no_results_answer": """### Conclusion:
No relevant information was found on the internet for your question.

### Detailed analysis:
The system performed an internet search but found no data related to the query: "{question}". Please try shorter keywords, or look it up directly in the available document database.

---
**Internet sources:**
- No results from the internet.""",

        "clarification_fallback": "Your question is not clear enough. Please provide more information to help me find the documents for you.",
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge ``override`` into ``base`` (dicts merged, leaves replaced)."""
    merged = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def _load_profile() -> dict:
    profile_dir = PROFILES_DIR / PROFILE_NAME
    manifest_path = profile_dir / "profile.json"
    if not manifest_path.exists():
        logger.warning(
            "Profile '%s' not found at %s — falling back to generic defaults.",
            PROFILE_NAME, manifest_path,
        )
        return copy.deepcopy(DEFAULT_PROFILE)

    try:
        with open(manifest_path, "r", encoding="utf-8") as f:
            profile = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        logger.warning(
            "Could not read profile manifest %s (%s) — falling back to generic defaults.",
            manifest_path, e,
        )
        return copy.deepcopy(DEFAULT_PROFILE)

    # Each prompts/*.md file becomes a template keyed by its file stem.
    # README.md and files starting with "_" are documentation/partials, not
    # templates — they are skipped.
    prompts_dir = profile_dir / "prompts"
    if prompts_dir.is_dir():
        profile.setdefault("prompts", {}).update({
            p.stem: p.read_text(encoding="utf-8")
            for p in sorted(prompts_dir.glob("*.md"))
            if not p.name.lower().startswith(("readme", "_"))
        })

    if profile.get("name") != PROFILE_NAME:
        logger.warning(
            "Profile directory '%s' declares name %r — continuing anyway.",
            PROFILE_NAME, profile.get("name"),
        )
    return _deep_merge(DEFAULT_PROFILE, profile)


PROFILE = _load_profile()


def get_prompt(name: str, **fields) -> str:
    """Render a prompt template from the active profile.

    Marker and language placeholders are always provided; callers pass any
    remaining fields (context, question, variant fragments, ...).
    """
    base_fields = {
        "reasoning_primary": PROFILE["markers"]["reasoning_primary"],
        "conclusion_primary": PROFILE["markers"]["conclusion_primary"],
        "response_language": PROFILE["response_language"],
    }
    return PROFILE["prompts"][name].format(**{**base_fields, **fields})
