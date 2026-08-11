from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP, Context
from mcp.server.session import ServerSession

from .config import load_config
from .security import normalize_repo_root, normalize_rel_file
from .indexer import index_repo, index_paths, workspace_id_for
from .jobs import JobManager
from .search import SearchEngine
from .db import connect, fetch_all, fetch_one

log = logging.getLogger("mcp-code-indexer")
logging.basicConfig(level=logging.INFO)

cfg = load_config()
cfg.data_dir.mkdir(parents=True, exist_ok=True)
db_path = (cfg.data_dir / "metadata.db").resolve()

mcp = FastMCP(name="Local Code Indexer")

job_mgr = JobManager(cfg, db_path)
search_engine = SearchEngine(cfg, db_path)


def _stats_for_workspace(wid: str, git_ref: str | None = None) -> dict[str, Any]:
    conn = connect(db_path)
    if git_ref:
        files = fetch_one(
            conn,
            "SELECT COUNT(*) AS n FROM files_snap WHERE workspace_id=? AND git_ref=?",
            (wid, git_ref),
        )
        symbols = fetch_one(
            conn,
            "SELECT COUNT(*) AS n FROM symbols_snap WHERE workspace_id=? AND git_ref=?",
            (wid, git_ref),
        )
        pysyms = fetch_one(
            conn,
            "SELECT COUNT(*) AS n FROM py_symbols WHERE workspace_id=? AND git_ref=?",
            (wid, git_ref),
        )
        refs = fetch_one(
            conn,
            "SELECT COUNT(*) AS n FROM py_refs WHERE workspace_id=? AND git_ref=?",
            (wid, git_ref),
        )
    else:
        files = fetch_one(
            conn, "SELECT COUNT(*) AS n FROM files_snap WHERE workspace_id=?", (wid,)
        )
        symbols = fetch_one(
            conn, "SELECT COUNT(*) AS n FROM symbols_snap WHERE workspace_id=?", (wid,)
        )
        pysyms = fetch_one(
            conn, "SELECT COUNT(*) AS n FROM py_symbols WHERE workspace_id=?", (wid,)
        )
        refs = fetch_one(
            conn, "SELECT COUNT(*) AS n FROM py_refs WHERE workspace_id=?", (wid,)
        )
    jobs = fetch_one(
        conn, "SELECT COUNT(*) AS n FROM jobs WHERE workspace_id=?", (wid,)
    )
    snaps = fetch_one(
        conn, "SELECT COUNT(*) AS n FROM snapshots WHERE workspace_id=?", (wid,)
    )
    return {
        "files_indexed": int((files or {}).get("n", 0)),
        "symbols_indexed": int((symbols or {}).get("n", 0)),
        "py_symbols_indexed": int((pysyms or {}).get("n", 0)),
        "py_refs_indexed": int((refs or {}).get("n", 0)),
        "snapshots": int((snaps or {}).get("n", 0)),
        "jobs_total": int((jobs or {}).get("n", 0)),
    }


@mcp.tool()
async def index_init(repo_root: str, ctx: Context[ServerSession, None, None]) -> dict:
    """
    Index an entire repo_root locally (incremental by file hash).
    Returns: { job_id, workspace_id }
    """
    rr = normalize_repo_root(repo_root, cfg.allowed_roots)
    wid = workspace_id_for(rr)

    await ctx.info(f"Indexing start: {rr}")
    job_id = job_mgr.create_job(
        workspace_id=wid,
        runner=lambda cb: index_repo(cfg, rr, db_path, job_cb=cb),
        message="index_init",
    )
    return {"job_id": job_id, "workspace_id": wid}


@mcp.tool()
async def index_refresh(
    repo_root: str, rel_paths: list[str], ctx: Context[ServerSession, None, None]
) -> dict:
    """
    Incremental index for a list of *relative* paths in the repo.
    Returns: { job_id, workspace_id }
    """
    rr = normalize_repo_root(repo_root, cfg.allowed_roots)
    wid = workspace_id_for(rr)

    # normalize paths (relative)
    clean: list[str] = []
    for p in rel_paths:
        p = p.strip().lstrip("/")
        if not p or p.startswith(".."):
            continue
        clean.append(p)

    await ctx.info(f"Index refresh start: {rr} paths={len(clean)}")
    job_id = job_mgr.create_job(
        workspace_id=wid,
        runner=lambda cb: index_paths(cfg, rr, db_path, clean, job_cb=cb),
        message="index_refresh",
    )
    return {"job_id": job_id, "workspace_id": wid}


@mcp.tool()
def index_status(job_id: str) -> dict:
    """
    Get status for a running/finished job.
    """
    row = job_mgr.status(job_id)
    if not row:
        return {"error": "job not found"}
    # include result if available
    result = job_mgr.result_if_done(job_id)
    if result is not None:
        row["result"] = result
    return row


@mcp.tool()
def index_stats(repo_root: str, git_ref: str | None = None) -> dict:
    """
    High-level stats for a repo workspace.
    """
    rr = normalize_repo_root(repo_root, cfg.allowed_roots)
    wid = workspace_id_for(rr)
    return {
        "workspace_id": wid,
        "repo_root": str(rr),
        "git_ref": git_ref,
        "stats": _stats_for_workspace(wid, git_ref),
    }


@mcp.tool()
def codebase_search(
    repo_root: str,
    query: str,
    top_k: int = 8,
    filters: dict[str, Any] | None = None,
    path_prefix: str | None = None,
    mode: str = "hybrid",  # "semantic" | "lexical" | "hybrid"
    alpha: float = 0.7,  # hybrid weight: semantic (alpha) vs lexical (1-alpha)
    use_rerank: bool = False,
    rerank_model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2",
) -> dict:
    """
    Search over the indexed codebase.

    mode:
      - "semantic": vector search (Qdrant)
      - "lexical": BM25 keyword search (SQLite FTS5)
      - "hybrid": merges both, weighted by alpha

    filters may include: language, chunk_type, symbol_name, git_ref, file_path
    path_prefix filters by file path prefix (e.g. "src/")
    """
    rr = normalize_repo_root(repo_root, cfg.allowed_roots)
    mode_l = (mode or "hybrid").strip().lower()

    if mode_l == "semantic":
        hits = search_engine.semantic_search(
            rr, query=query, top_k=int(top_k), filters=filters, path_prefix=path_prefix
        )
    elif mode_l == "lexical":
        hits = search_engine.lexical_search(
            rr, query=query, top_k=int(top_k), filters=filters, path_prefix=path_prefix
        )
        # strip private text fields if any
        for h in hits:
            h.pop("_text", None)
            h.pop("_source", None)
    else:
        hits = search_engine.hybrid_search(
            rr,
            query=query,
            top_k=int(top_k),
            filters=filters,
            path_prefix=path_prefix,
            alpha=float(alpha),
            use_rerank=bool(use_rerank),
            rerank_model=rerank_model,
        )

    # For semantic/lexical, remove internals too
    for h in hits:
        h.pop("_text", None)
        h.pop("_source", None)

    return {"matches": hits}


@mcp.tool()
def codebase_fetch(
    repo_root: str, file_path: str, start_line: int = 1, end_line: int = 200
) -> dict:
    """
    Fetch raw file contents by line range (safe, bounded).
    file_path must be relative to repo_root.
    """
    rr = normalize_repo_root(repo_root, cfg.allowed_roots)
    abs_path = normalize_rel_file(rr, file_path)

    start = max(1, int(start_line))
    end = max(start, int(end_line))
    end = min(end, start + 600)  # hard safety cap

    text = abs_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    sl = min(start, len(text) + 1)
    el = min(end, len(text))
    snippet = "\n".join(text[sl - 1 : el])
    return {
        "file_path": Path(file_path).as_posix(),
        "start_line": sl,
        "end_line": el,
        "text": snippet,
    }


@mcp.tool()
def symbol_find(
    repo_root: str,
    name: str,
    language: str | None = "python",
    path_prefix: str | None = None,
    limit: int = 50,
) -> dict:
    """
    Find symbols (currently: python-only).
    """
    rr = normalize_repo_root(repo_root, cfg.allowed_roots)
    rows = search_engine.symbol_find(
        rr, name=name, language=language, path_prefix=path_prefix, limit=int(limit)
    )
    return {"symbols": rows}


def run_stdio() -> None:
    # Stdio transport (recommended for local MCP servers)
    mcp.run()


@mcp.tool()
def symbol_references(
    repo_root: str,
    symbol_name: str,
    git_ref: str | None = None,
    path_prefix: str | None = None,
    limit: int = 200,
) -> dict:
    """Python best-effort references for a symbol by name."""
    rr = normalize_repo_root(repo_root, cfg.allowed_roots)
    rows = search_engine.py_symbol_references(
        rr,
        symbol_name=symbol_name,
        git_ref=git_ref,
        path_prefix=path_prefix,
        limit=int(limit),
    )
    return {"references": rows}


@mcp.tool()
def callgraph(
    repo_root: str,
    symbol_id: str,
    depth: int = 1,
    direction: str = "out",
    git_ref: str | None = None,
) -> dict:
    """Python best-effort callgraph neighborhood starting from a symbol_id."""
    rr = normalize_repo_root(repo_root, cfg.allowed_roots)
    return search_engine.py_callgraph(
        rr, symbol_id=symbol_id, depth=int(depth), direction=direction, git_ref=git_ref
    )


@mcp.tool()
def git_list_snapshots(repo_root: str, limit: int = 50) -> dict:
    """List indexed git snapshots for this repo."""
    rr = normalize_repo_root(repo_root, cfg.allowed_roots)
    wid = workspace_id_for(rr)
    conn = connect(db_path)
    rows = fetch_all(
        conn,
        "SELECT git_ref, indexed_at FROM snapshots WHERE workspace_id=? ORDER BY indexed_at DESC LIMIT ?",
        (wid, int(limit)),
    )
    return {"workspace_id": wid, "snapshots": rows}
