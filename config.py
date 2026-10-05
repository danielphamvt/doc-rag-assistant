import logging
import os

# Silence MallocStackLogging warnings on macOS (must be set before torch loads)
os.environ["MallocStackLogging"] = "0"
os.environ["MallocStackLoggingNoCompact"] = "1"

from pathlib import Path

from dotenv import load_dotenv
# Load environment variables from .env as early as possible
load_dotenv()

from langchain_huggingface import HuggingFaceEmbeddings
from langchain_qdrant.fastembed_sparse import FastEmbedSparse
from qdrant_client import QdrantClient

from domain_profile import PROFILE

logger = logging.getLogger(__name__)


def _env_int(name: str, default: int) -> int:
    """Read an int env var, falling back to the default if missing or malformed."""
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        logger.warning("Env %s=%r is not a valid integer, using default %s", name, os.getenv(name), default)
        return default


def _env_float(name: str, default: float) -> float:
    """Read a float env var, falling back to the default if missing or malformed."""
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        logger.warning("Env %s=%r is not a valid number, using default %s", name, os.getenv(name), default)
        return default


# --- Directories Configuration ---
RAG_PROFILE = os.getenv("RAG_PROFILE", "vietnamese_law")
DATA_DIR = os.getenv("DATA_DIR", "data")

DOCS_DIR = os.getenv("DOCS_DIR", f"{DATA_DIR}/docs_{RAG_PROFILE}")
MARKDOWN_DIR = os.getenv("MARKDOWN_DIR", f"{DATA_DIR}/markdown_docs_{RAG_PROFILE}")
PARENT_STORE_PATH = os.getenv("PARENT_STORE_PATH", f"{DATA_DIR}/parent_store_{RAG_PROFILE}")
QDRANT_PATH = os.getenv("QDRANT_PATH", f"{DATA_DIR}/qdrant_db_{RAG_PROFILE}")
CHILD_COLLECTION = os.getenv("CHILD_COLLECTION", f"child_chunks_{RAG_PROFILE}")

# --- Pipeline Thresholds ---
MAX_HISTORY_MESSAGES = _env_int("MAX_HISTORY_MESSAGES", 10)
MAX_HISTORY_TOKENS = _env_int("MAX_HISTORY_TOKENS", 10000)
COMPRESS_TOKEN_THRESHOLD = _env_int("COMPRESS_TOKEN_THRESHOLD", 100000)
TOKEN_GROWTH_FACTOR = _env_float("TOKEN_GROWTH_FACTOR", 0.9)
MAX_TOOL_CALLS = _env_int("MAX_TOOL_CALLS", 8)
MAX_ITERATIONS = _env_int("MAX_ITERATIONS", 5)
RECURSION_LIMIT = _env_int("RECURSION_LIMIT", 50)

# Ensure directories exist
os.makedirs(DOCS_DIR, exist_ok=True)
os.makedirs(MARKDOWN_DIR, exist_ok=True)
os.makedirs(PARENT_STORE_PATH, exist_ok=True)

# --- LLM Provider Configuration ---
# Options: "ollama", "openai", "anthropic", "google", "zai"
# Each provider's model can be overridden via env vars (see .env.example).
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "ollama")

if LLM_PROVIDER == "ollama":
    from langchain_ollama import ChatOllama
    # Local LLM pulled by the user
    llm = ChatOllama(model=os.getenv("OLLAMA_MODEL", "qwen3:4b-instruct-2507-q4_K_M"), temperature=0, streaming=True)
elif LLM_PROVIDER == "openai":
    from langchain_openai import ChatOpenAI
    # Requires OPENAI_API_KEY environment variable
    llm = ChatOpenAI(model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"), temperature=0, streaming=True)
elif LLM_PROVIDER == "anthropic":
    from langchain_anthropic import ChatAnthropic
    # Requires ANTHROPIC_API_KEY environment variable
    llm = ChatAnthropic(model=os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5-5"), temperature=0, streaming=True)
elif LLM_PROVIDER == "google":
    try:
        from langchain_google_genai import ChatGoogleGenerativeAI
    except ImportError as e:
        raise ImportError(
            "LLM_PROVIDER=google requires the optional Google provider package. "
            "Install it with: pip install langchain-google-genai "
            "(it is not in requirements.txt because its pydantic requirement "
            "conflicts with gradio 5.x)."
        ) from e
    # Requires GOOGLE_API_KEY environment variable
    llm = ChatGoogleGenerativeAI(model=os.getenv("GOOGLE_MODEL", "gemini-2.5-flash"), temperature=0, streaming=True)
elif LLM_PROVIDER == "zai":
    from langchain_anthropic import ChatAnthropic
    # Z AI exposes an Anthropic-compatible protocol
    zai_key = os.getenv("ANTHROPIC_AUTH_TOKEN")
    zai_url = os.getenv("ANTHROPIC_BASE_URL", "https://api.z.ai/api/anthropic")
    # Strip context-window suffixes injected by external tooling (e.g. "[1m]") —
    # the API only accepts plain model codes; the suffix causes a 400
    # "Unknown Model" error.
    zai_model = os.getenv("ANTHROPIC_MODEL", "glm-4.7").split("[")[0].strip() or "glm-4.7"

    llm = ChatAnthropic(
        model=zai_model,
        api_key=zai_key,
        base_url=zai_url,
        temperature=0,
        streaming=True
    )
else:
    raise ValueError(f"Unsupported LLM_PROVIDER: {LLM_PROVIDER}")

# --- Callback Logging Configuration ---
import datetime
import json

from langchain_core.callbacks import BaseCallbackHandler

LLM_PROMPT_LOG_DIR = os.getenv("LLM_PROMPT_LOG_PATH", "")

class JSONLoggerCallback(BaseCallbackHandler):
    def on_chat_model_start(self, serialized, messages, **kwargs):
        if not LLM_PROMPT_LOG_DIR:
            return
        try:
            # Create the directory if it does not exist yet
            os.makedirs(LLM_PROMPT_LOG_DIR, exist_ok=True)

            # File name pattern YYYY-MM-DD_HH-MM-SS_ffffff.json; microseconds
            # avoid collisions when several nodes run in parallel
            timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S_%f")
            filename = f"{timestamp}.json"
            filepath = os.path.join(LLM_PROMPT_LOG_DIR, filename)

            with open(filepath, "w", encoding="utf-8") as f:
                for message_list in messages:
                    log_data = []
                    for msg in message_list:
                        msg_dict = {"role": msg.type, "content": msg.content}
                        if hasattr(msg, "tool_calls") and msg.tool_calls:
                            msg_dict["tool_calls"] = msg.tool_calls
                        log_data.append(msg_dict)
                    # Human-readable format (indent=2)
                    f.write(json.dumps(log_data, ensure_ascii=False, indent=2))
        except Exception as e:
            logger.error("Error logging LLM prompt to %s: %s", LLM_PROMPT_LOG_DIR, e)

if LLM_PROMPT_LOG_DIR:
    # Assign to the callbacks attribute instead of with_config(): the config
    # would be lost across llm.bind_tools() / llm.with_structured_output()
    llm.callbacks = [JSONLoggerCallback()]


import unicodedata

# --- UI Configuration ---
CHATBOT_AVATAR = "images/chatbot.png"
SOURCE_NAMES = {}

# Map profile-declared display names, normalized in both NFC and NFD
for k, v in PROFILE["source_display_names"].items():
    v_clean = unicodedata.normalize('NFC', v)
    SOURCE_NAMES[unicodedata.normalize('NFC', k)] = v_clean
    SOURCE_NAMES[unicodedata.normalize('NFD', k)] = v_clean

# Dynamically populate display names from the docs folder
docs_path = Path(DOCS_DIR)
if docs_path.exists():
    for f in docs_path.iterdir():
        if f.is_file() and f.suffix.lower() in [".pdf", ".docx"]:
            # Clean name: replace underscores/dashes and format nicely
            clean_name = f.stem.replace("_", " ").replace("-", " ")
            if clean_name.islower():
                clean_name = clean_name.title()

            v_clean = unicodedata.normalize('NFC', clean_name)

            name_nfc = unicodedata.normalize('NFC', f.name)
            name_nfd = unicodedata.normalize('NFD', f.name)

            # Add mappings for both NFC/NFD keys
            SOURCE_NAMES[name_nfc] = v_clean
            SOURCE_NAMES[name_nfd] = v_clean

# --- Web Server Configuration ---
GRADIO_SERVER_NAME = os.getenv("GRADIO_SERVER_NAME", "127.0.0.1")
GRADIO_SERVER_PORT = _env_int("GRADIO_SERVER_PORT", 7860)

# --- Hardware Device Configuration ---
import torch


def get_optimal_device(model_type: str = "general") -> str:
    """
    Pick the optimal, thread-safe device for any environment:
    1. Env override first: RERANKER_DEVICE / EMBEDDING_DEVICE / DEVICE (if set)
    2. Auto-detect: CUDA (NVIDIA) -> MPS (Apple Silicon) -> CPU
    3. Apple Silicon quirk: the reranker defaults to CPU to avoid Metal
       command-buffer conflicts when LangGraph runs parallel branches.
    """
    env_override = os.getenv(f"{model_type.upper()}_DEVICE") or os.getenv("DEVICE")
    if env_override and env_override.strip().lower() != "auto":
        return env_override.strip().lower()

    # 1. NVIDIA GPU (Linux / Windows)
    if torch.cuda.is_available():
        return "cuda"

    # 2. Apple Silicon Mac (Metal Performance Shaders)
    if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
        # On Apple Silicon the CrossEncoder handles short token windows, and
        # CPU with SIMD/Accelerate is very fast (~0.1s) and 100% thread-safe.
        if model_type == "reranker":
            return "cpu"
        return "mps"

    # 3. CPU fallback (Docker, CI, cloud VMs without GPU)
    return "cpu"

device = get_optimal_device("embedding")
reranker_device = get_optimal_device("reranker")
logger.info("Hardware configuration - Embedding device: %s, Reranker device: %s", device, reranker_device)

# --- Retrieval Model Configuration ---
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "Qwen/Qwen3-Embedding-0.6B")
SPARSE_EMBEDDING_MODEL = os.getenv("SPARSE_EMBEDDING_MODEL", "Qdrant/bm25")
RERANKER_MODEL = os.getenv("RERANKER_MODEL", "BAAI/bge-reranker-base")
EMBEDDING_BATCH_SIZE = _env_int("EMBEDDING_BATCH_SIZE", 32)
# Child chunk candidates pulled from Qdrant before reranking
RETRIEVAL_CANDIDATES = _env_int("RETRIEVAL_CANDIDATES", 25)
# Default number of parent documents returned by the search_documents tool
DEFAULT_SEARCH_LIMIT = _env_int("DEFAULT_SEARCH_LIMIT", 3)

# --- Document Indexing (Chunking) Configuration ---
# Parent sizing is domain-tuned; see the active profile for the rationale.
CHILD_CHUNK_SIZE = _env_int("CHILD_CHUNK_SIZE", 400)
CHILD_CHUNK_OVERLAP = _env_int("CHILD_CHUNK_OVERLAP", 80)
MIN_PARENT_SIZE = _env_int("MIN_PARENT_SIZE", 500)   # Keep independent sections unmerged
MAX_PARENT_SIZE = _env_int("MAX_PARENT_SIZE", 1500)  # Split overly long sections
INDEX_BATCH_SIZE = _env_int("INDEX_BATCH_SIZE", 100)

# Dense embeddings using HuggingFace
dense_embeddings = HuggingFaceEmbeddings(
    model_name=EMBEDDING_MODEL,
    model_kwargs={
        "device": device,
        # Force float32 to prevent NaN vectors on Apple Silicon MPS
        "model_kwargs": {"torch_dtype": torch.float32}
    },
    # Keep batches small (32 instead of the default 256) to avoid MPS OOM
    encode_kwargs={"batch_size": EMBEDDING_BATCH_SIZE}
)
# Sparse embeddings using FastEmbed
sparse_embeddings = FastEmbedSparse(model_name=SPARSE_EMBEDDING_MODEL)

# --- Vector Database Configuration ---
# Qdrant client initialized with local path storage
client = QdrantClient(path=QDRANT_PATH)
