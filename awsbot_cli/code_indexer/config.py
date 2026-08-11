from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import List


@dataclass(frozen=True)
class Config:
    # Security: only allow indexing inside these directories (colon-separated).
    allowed_roots: List[Path]

    # Storage
    data_dir: Path

    # Qdrant
    qdrant_host: str
    qdrant_port: int
    qdrant_collection: str

    # Embeddings / chunking
    max_chunk_chars: int
    max_fallback_lines: int
    fallback_overlap_lines: int


def load_config() -> Config:
    allowed = os.getenv("MCP_ALLOWED_ROOTS", "").strip()
    if allowed:
        roots = [
            Path(p).expanduser().resolve() for p in allowed.split(":") if p.strip()
        ]
    else:
        # Safe-ish default: current working directory only.
        roots = [Path.cwd().resolve()]

    data_dir = (
        Path(os.getenv("MCP_CODE_INDEX_DATA_DIR", "./.mcp_index"))
        .expanduser()
        .resolve()
    )

    return Config(
        allowed_roots=roots,
        data_dir=data_dir,
        qdrant_host=os.getenv("QDRANT_HOST", "127.0.0.1"),
        qdrant_port=int(os.getenv("QDRANT_PORT", "6333")),
        qdrant_collection=os.getenv("QDRANT_COLLECTION", "code_chunks"),
        max_chunk_chars=int(os.getenv("MCP_MAX_CHUNK_CHARS", "8000")),
        max_fallback_lines=int(os.getenv("MCP_MAX_FALLBACK_LINES", "220")),
        fallback_overlap_lines=int(os.getenv("MCP_FALLBACK_OVERLAP_LINES", "40")),
    )
