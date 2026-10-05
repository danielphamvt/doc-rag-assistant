from typing import Annotated, Any

from langgraph.graph import MessagesState
from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field
from typing_extensions import TypedDict


def accumulate_or_reset(existing: list[dict], new: list[dict]) -> list[dict]:
    if new and any(isinstance(item, dict) and item.get('__reset__') for item in new):
        return []
    return existing + new

def set_union_or_reset(a: set[str], b: set[str] | dict) -> set[str]:
    if isinstance(b, dict) and b.get("__reset__"):
        return set()
    if isinstance(b, set):
        return a | b
    return a

def add_or_reset(a: int, b: int | dict) -> int:
    if isinstance(b, dict) and b.get("__reset__"):
        return 0
    if isinstance(b, int):
        return a + b
    return a

def pick_last(a: Any, b: Any) -> Any:
    """Reducer that simply keeps the latest value, useful for concurrent branches returning the same key."""
    return b

class State(MessagesState):
    questionIsClear: bool = False
    conversation_summary: str = ""
    originalQuery: str = ""
    rewrittenQuestions: list[Any] = []
    agent_answers: Annotated[list[dict], accumulate_or_reset] = []
    # AgentState fields exposed on the shared state with pick_last reducers
    # to avoid InvalidUpdateError when several sub-queries run in parallel.
    question: Annotated[str, pick_last] = ""
    question_index: Annotated[int, pick_last] = 0
    context_summary: Annotated[str, pick_last] = ""
    tool_call_count: Annotated[int, add_or_reset] = 0
    iteration_count: Annotated[int, add_or_reset] = 0
    retrieval_keys: Annotated[set[str], set_union_or_reset] = set()
    agent_messages: Annotated[list, add_messages] = []
    forced_tool: Annotated[str | None, pick_last] = None
    history_context: Annotated[str, pick_last] = ""

# AgentState uses a PRIVATE message channel (`agent_messages`) instead of inheriting
# MessagesState. This keeps the subgraph's internal reasoning (tool calls, tool results,
# draft answers) out of the parent graph's user-visible `messages`. Only `agent_answers`
# crosses the boundary back to the parent (written by collect_answer).
class AgentState(TypedDict, total=False):
    agent_messages: Annotated[list, add_messages]
    tool_call_count: Annotated[int, add_or_reset]
    iteration_count: Annotated[int, add_or_reset]
    question: str
    question_index: int
    context_summary: str
    retrieval_keys: Annotated[set[str], set_union_or_reset]
    # The single key written back to the parent graph (by collect_answer). The parent's
    # accumulate_or_reset reducer governs the cross-boundary merge across parallel branches.
    agent_answers: list[dict]
    forced_tool: str | None
    history_context: str

class SubQuery(BaseModel):
    query: str = Field(description="The rewritten, self-contained question.")
    complexity: str = Field(description="Complexity rating. Use 'simple' for concept definitions or direct factual lookups; use 'complex' for multi-step analysis, comparisons, or topics with many exceptions.")
    forced_tool: str | None = Field(default=None, description="Tool forced by a slash command, if any.")

class QueryAnalysis(BaseModel):
    is_clear: bool = Field(default=True, description="Defaults to true (the question is clear). Return false only if the question is completely meaningless.")
    questions: list[SubQuery] = Field(description="List of rewritten questions and their complexities.")
    clarification_needed: str | None = Field(default="", description="If is_clear=false, explain why here. Otherwise leave empty.")
