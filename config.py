import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

DATA_DIR = BASE_DIR / "data"
WRITABLE = Path("/tmp/hawkai") if os.getenv("VERCEL") else BASE_DIR
LOG_DIR = Path(os.getenv("HAWKAI_LOG_DIR", WRITABLE / "logs"))
CACHE_DIR = Path(os.getenv("HAWKAI_CACHE_DIR", WRITABLE / "cache"))

# gemini-2.5-flash is retired for new keys and 2.5-pro is too slow for chat.
GEN_MODEL = os.getenv("HAWKAI_GEN_MODEL", "gemini-3.8-flash")
FAST_MODEL = os.getenv("HAWKAI_FAST_MODEL", "gemini-3.8-flash")
LITE_MODEL = os.getenv("HAWKAI_LITE_MODEL", "gemini-3.5-flash-lite")
EMBED_MODEL = os.getenv("HAWKAI_EMBED_MODEL", "gemini-embedding-2")
EMBED_DIM = int(os.getenv("HAWKAI_EMBED_DIM", "1536"))

NUM_OPTIONS = int(os.getenv("HAWKAI_NUM_OPTIONS", "5"))
NUM_CANDIDATES = int(os.getenv("HAWKAI_NUM_CANDIDATES", "14"))
MIN_RELEVANCE = int(os.getenv("HAWKAI_MIN_RELEVANCE", "5"))

EXPLAIN_THINKING = int(os.getenv("HAWKAI_EXPLAIN_THINKING", "1024"))
CHAT_THINKING = int(os.getenv("HAWKAI_CHAT_THINKING", "1024"))

SUGGESTIONS = os.getenv("HAWKAI_SUGGESTIONS", "on").lower() != "off"

MAX_MESSAGE_CHARS = 2000
MAX_HISTORY_TURNS = 24
