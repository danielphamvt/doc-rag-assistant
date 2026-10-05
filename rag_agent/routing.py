"""Pure routing predicates for the agent graph.

Kept free of heavy imports (config, LLM, embeddings) so they can be
unit-tested without loading models or requiring a running provider. The
greeting set comes from the active domain profile, which is also light.
"""
import re
import unicodedata

from domain_profile import PROFILE

# Small-talk messages answered directly without retrieval.
# Matched against the FULL normalized message, never as a substring:
# short domain questions often contain "hi" inside other words
# (e.g. "khi" in Vietnamese, NFD-decomposed words) and must not be misrouted.
GREETINGS = frozenset(PROFILE["greetings"])

_PUNCT_RE = re.compile(r"^[\s!.,?~…]+|[\s!.,?~…]+$")


def normalize_message(text: str) -> str:
    """Lowercase, NFC-normalize (macOS NFD input) and trim punctuation."""
    text = unicodedata.normalize("NFC", text or "").strip().lower()
    return _PUNCT_RE.sub("", text)


def is_greeting_message(text: str) -> bool:
    """True when the whole message is a greeting, thanks or farewell."""
    return normalize_message(text) in GREETINGS
