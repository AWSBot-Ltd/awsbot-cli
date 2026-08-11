from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from qdrant_client.models import Filter, FieldCondition, MatchValue

from .config import Config
from .db import connect, fetch_all
from .embedder import Embedder
from .qdrant_store import QdrantStore
from .indexer import workspace_id_for
from .rerank import Reranker


@dataclass
class SearchEngine:
    cfg: Config
    db_path: Path

    def __post_init__(self) -> None:
        self._embedder = Embedder()
        self._store = QdrantStore(
            self.cfg.qdrant_host,
            self.cfg.qdrant_port,
            self.cfg.qdrant_collection,
            self._embedder.dim(),
        )

    # ----------------------------
    # Filters
    # ----------------------------

    def _qfilter(self, workspace_id: str, filters: dict[str, Any] | None) -> Filter:
        must = [
            FieldCondition(key="workspace_id", match=MatchValue(value=workspace_id))
        ]
        if filters:
            if lang := filters.get("language"):
                must.append(
                    FieldCondition(key="language", match=MatchValue(value=lang))
                )
            if chunk_type := filters.get("chunk_type"):
                must.append(
                    FieldCondition(key="chunk_type", match=MatchValue(value=chunk_type))
                )
            if symbol := filters.get("symbol_name"):
                must.append(
                    FieldCondition(key="symbol_name", match=MatchValue(value=symbol))
                )
            if git_ref := filters.get("git_ref"):
                must.append(
                    FieldCondition(key="git_ref", match=MatchValue(value=git_ref))
                )
            if file_path := filters.get("file_path"):
                must.append(
                    FieldCondition(key="file_path", match=MatchValue(value=file_path))
                )
        return Filter(must=must)

    def _sql_filters(
        self, filters: dict[str, Any] | None, path_prefix: str | None
    ) -> tuple[str, list[Any]]:
        where = []
        params: list[Any] = []

        if filters:
            if lang := filters.get("language"):
                where.append("language = ?")
                params.append(lang)
            if chunk_type := filters.get("chunk_type"):
                where.append("chunk_type = ?")
                params.append(chunk_type)
            if symbol := filters.get("symbol_name"):
                where.append("symbol_name = ?")
                params.append(symbol)
            if git_ref := filters.get("git_ref"):
                where.append("git_ref = ?")
                params.append(git_ref)
            if file_path := filters.get("file_path"):
                where.append("file_path = ?")
                params.append(file_path)

        if path_prefix:
            pref = path_prefix.strip().lstrip("/")
            where.append("file_path LIKE ?")
            params.append(f"{pref}%")

        clause = (" AND " + " AND ".join(where)) if where else ""
        return clause, params

    # ----------------------------
    # Lexical search (FTS5 BM25)
    # ----------------------------

    def lexical_search(
        self,
        repo_root: Path,
        query: str,
        top_k: int = 8,
        filters: dict[str, Any] | None = None,
        path_prefix: str | None = None,
    ) -> list[dict[str, Any]]:
        wid = workspace_id_for(repo_root)
        conn = connect(self.db_path)

        clause, params = self._sql_filters(filters, path_prefix)

        # FTS5 MATCH uses its own query syntax; keep it simple by quoting if needed.
        match_q = query.strip()
        if not match_q:
            return []

        sql = (
            "SELECT file_path, start_line, end_line, language, chunk_type, symbol_name, git_ref, "
            "substr(text, 1, 800) AS preview, text AS full_text, bm25(chunk_fts) AS bm25_score "
            "FROM chunk_fts "
            "WHERE chunk_fts MATCH ? AND workspace_id = ?"
            + clause
            + " ORDER BY bm25_score ASC LIMIT ?"
        )
        rows = fetch_all(conn, sql, tuple([match_q, wid] + params + [int(top_k)]))

        out: list[dict[str, Any]] = []
        for r in rows:
            # bm25: lower is better; convert to higher-is-better in [0,1-ish]
            bm25 = float(r.get("bm25_score", 0.0))
            lex = 1.0 / (1.0 + max(0.0, bm25))
            out.append(
                {
                    "score": lex,
                    "file_path": r.get("file_path"),
                    "start_line": int(r.get("start_line", 0) or 0),
                    "end_line": int(r.get("end_line", 0) or 0),
                    "language": r.get("language"),
                    "chunk_type": r.get("chunk_type"),
                    "symbol_name": r.get("symbol_name"),
                    "git_ref": r.get("git_ref"),
                    "preview": r.get("preview") or "",
                    "_text": r.get("full_text") or "",
                    "_source": "lexical",
                }
            )
        return out

    # ----------------------------
    # Semantic search (Qdrant)
    # ----------------------------

    def semantic_search(
        self,
        repo_root: Path,
        query: str,
        top_k: int = 8,
        filters: dict[str, Any] | None = None,
        path_prefix: str | None = None,
    ) -> list[dict[str, Any]]:
        wid = workspace_id_for(repo_root)
        qv = self._embedder.embed_query(query)

        # Query more, then apply optional prefix trimming client-side.
        limit = min(60, max(top_k, top_k * 6))
        results = self._store.query(
            qv, limit=limit, qfilter=self._qfilter(wid, filters)
        )

        if path_prefix:
            pref = path_prefix.strip().lstrip("/")
            results = [
                r
                for r in results
                if str((r.get("payload") or {}).get("file_path") or "").startswith(pref)
            ]

        out: list[dict[str, Any]] = []
        for r in results[:top_k]:
            p = r.get("payload") or {}
            out.append(
                {
                    "score": float(r.get("score", 0.0)),
                    "file_path": p.get("file_path"),
                    "start_line": int(p.get("start_line", 0) or 0),
                    "end_line": int(p.get("end_line", 0) or 0),
                    "language": p.get("language"),
                    "chunk_type": p.get("chunk_type"),
                    "symbol_name": p.get("symbol_name"),
                    "git_ref": p.get("git_ref"),
                    "preview": (p.get("text") or "")[:800],
                    "_text": (p.get("text") or ""),
                    "_source": "semantic",
                }
            )
        return out

    # ----------------------------
    # Hybrid search + optional rerank
    # ----------------------------

    def hybrid_search(
        self,
        repo_root: Path,
        query: str,
        top_k: int = 8,
        filters: dict[str, Any] | None = None,
        path_prefix: str | None = None,
        alpha: float = 0.7,
        use_rerank: bool = False,
        rerank_model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2",
    ) -> list[dict[str, Any]]:
        # Collect candidates
        sem = self.semantic_search(
            repo_root,
            query,
            top_k=min(40, max(top_k, top_k * 4)),
            filters=filters,
            path_prefix=path_prefix,
        )
        lex = self.lexical_search(
            repo_root,
            query,
            top_k=min(40, max(top_k, top_k * 4)),
            filters=filters,
            path_prefix=path_prefix,
        )

        # Merge by location key
        def key(h: dict[str, Any]) -> tuple:
            return (
                h.get("git_ref"),
                h.get("file_path"),
                int(h.get("start_line") or 0),
                int(h.get("end_line") or 0),
            )

        merged: dict[tuple, dict[str, Any]] = {}

        # Normalize semantic scores into ~[0,1] (cosine sim already close; clamp)
        def clamp01(x: float) -> float:
            if x != x:
                return 0.0
            return max(0.0, min(1.0, x))

        for h in sem:
            k = key(h)
            merged[k] = dict(h)
            merged[k]["_sem"] = clamp01(float(h.get("score", 0.0)))
            merged[k]["_lex"] = 0.0

        for h in lex:
            k = key(h)
            if k not in merged:
                merged[k] = dict(h)
                merged[k]["_sem"] = 0.0
                merged[k]["_lex"] = clamp01(float(h.get("score", 0.0)))
            else:
                merged[k]["_lex"] = max(
                    merged[k].get("_lex", 0.0), clamp01(float(h.get("score", 0.0)))
                )
                # keep richer text if semantic had empty
                if not merged[k].get("_text") and h.get("_text"):
                    merged[k]["_text"] = h.get("_text")

        alpha = float(alpha)
        alpha = max(0.0, min(1.0, alpha))
        beta = 1.0 - alpha

        candidates = list(merged.values())
        for c in candidates:
            c["_hybrid"] = alpha * float(c.get("_sem", 0.0)) + beta * float(
                c.get("_lex", 0.0)
            )

        # Sort by hybrid score first
        candidates.sort(key=lambda x: float(x.get("_hybrid", 0.0)), reverse=True)
        candidates = candidates[: max(top_k * 5, top_k)]

        # Optional rerank with cross-encoder (if installed)
        if use_rerank:
            rr = Reranker(model_name=rerank_model)
            if rr.available():
                passages = [
                    (c.get("_text") or c.get("preview") or "")[:4000]
                    for c in candidates
                ]
                scores = rr.rerank(query, passages)
                for c, s in zip(candidates, scores):
                    c["_rerank"] = float(s)
                # Combine: mostly rerank, but keep some hybrid signal to break ties
                candidates.sort(
                    key=lambda x: (
                        float(x.get("_rerank", 0.0)),
                        float(x.get("_hybrid", 0.0)),
                    ),
                    reverse=True,
                )

        # Final format: expose a single `score` and drop internals
        out: list[dict[str, Any]] = []
        for c in candidates[:top_k]:
            out.append(
                {
                    "score": float(
                        c.get("_rerank", c.get("_hybrid", c.get("score", 0.0)))
                    ),
                    "file_path": c.get("file_path"),
                    "start_line": c.get("start_line"),
                    "end_line": c.get("end_line"),
                    "language": c.get("language"),
                    "chunk_type": c.get("chunk_type"),
                    "symbol_name": c.get("symbol_name"),
                    "git_ref": c.get("git_ref"),
                    "preview": c.get("preview") or "",
                    "source": {
                        "semantic": float(c.get("_sem", 0.0)),
                        "lexical": float(c.get("_lex", 0.0)),
                    },
                }
            )
        return out

    def py_symbol_references(
        self,
        repo_root: Path,
        symbol_name: str,
        git_ref: str | None = None,
        path_prefix: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        """Best-effort python references by name."""
        wid = workspace_id_for(repo_root)
        conn = connect(self.db_path)

        where = ["workspace_id=?", "symbol_name=?"]
        params: list[Any] = [wid, symbol_name]
        if git_ref:
            where.append("git_ref=?")
            params.append(git_ref)
        if path_prefix:
            pref = path_prefix.strip().lstrip("/")
            where.append("file_path LIKE ?")
            params.append(f"{pref}%")

        sql = (
            "SELECT file_path, line, col, context, git_ref FROM py_refs "
            "WHERE " + " AND ".join(where) + " "
            "ORDER BY file_path, line LIMIT ?"
        )
        params.append(int(limit))
        return fetch_all(conn, sql, tuple(params))

    def py_callgraph(
        self,
        repo_root: Path,
        symbol_id: str,
        depth: int = 1,
        direction: str = "out",
        git_ref: str | None = None,
        limit_per_hop: int = 50,
    ) -> dict[str, Any]:
        """Return a callgraph neighborhood (python best-effort)."""
        wid = workspace_id_for(repo_root)
        conn = connect(self.db_path)

        if not git_ref:
            r0 = fetch_all(
                conn,
                "SELECT git_ref FROM py_symbols WHERE symbol_id=? AND workspace_id=? LIMIT 1",
                (symbol_id, wid),
            )
            if r0:
                git_ref = r0[0]["git_ref"]

        if not git_ref:
            return {
                "nodes": [],
                "edges": [],
                "error": "git_ref not found for symbol_id",
            }

        direction = (direction or "out").lower()
        depth = max(1, int(depth))

        nodes: dict[str, dict[str, Any]] = {}
        edges: list[dict[str, Any]] = []

        def add_node(sid: str):
            if sid in nodes:
                return
            r = fetch_all(
                conn,
                "SELECT symbol_id, file_path, symbol_name, qualname, symbol_kind, start_line, end_line, git_ref "
                "FROM py_symbols WHERE symbol_id=? AND workspace_id=? AND git_ref=? LIMIT 1",
                (sid, wid, git_ref),
            )
            nodes[sid] = r[0] if r else {"symbol_id": sid, "missing": True}

        add_node(symbol_id)
        frontier = {symbol_id}

        for _ in range(depth):
            new_frontier = set()
            for sid in list(frontier):
                if direction in ("out", "both"):
                    out_rows = fetch_all(
                        conn,
                        "SELECT from_symbol_id, to_name, to_symbol_id, call_line FROM py_call_edges "
                        "WHERE workspace_id=? AND git_ref=? AND from_symbol_id=? LIMIT ?",
                        (wid, git_ref, sid, int(limit_per_hop)),
                    )
                    for r in out_rows:
                        to_id = r.get("to_symbol_id")
                        edges.append(
                            {
                                "from": sid,
                                "to": to_id or r.get("to_name"),
                                "to_symbol_id": to_id,
                                "to_name": r.get("to_name"),
                                "call_line": r.get("call_line"),
                            }
                        )
                        if to_id:
                            add_node(to_id)
                            new_frontier.add(to_id)

                if direction in ("in", "both"):
                    in_rows = fetch_all(
                        conn,
                        "SELECT from_symbol_id, to_name, to_symbol_id, call_line FROM py_call_edges "
                        "WHERE workspace_id=? AND git_ref=? AND to_symbol_id=? LIMIT ?",
                        (wid, git_ref, sid, int(limit_per_hop)),
                    )
                    for r in in_rows:
                        frm = r.get("from_symbol_id")
                        edges.append(
                            {
                                "from": frm,
                                "to": sid,
                                "to_symbol_id": sid,
                                "to_name": r.get("to_name"),
                                "call_line": r.get("call_line"),
                            }
                        )
                        if frm:
                            add_node(frm)
                            new_frontier.add(frm)

            frontier = new_frontier
            if not frontier:
                break

        return {"nodes": list(nodes.values()), "edges": edges, "git_ref": git_ref}

    # ----------------------------
    # Symbols (python-only for now)
    # ----------------------------

    def symbol_find(
        self,
        repo_root: Path,
        name: str,
        language: str | None = "python",
        path_prefix: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        if language and language != "python":
            return []

        wid = workspace_id_for(repo_root)
        conn = connect(self.db_path)

        sql = "SELECT file_path, symbol_name, symbol_kind, start_line, end_line FROM symbols WHERE workspace_id=? AND symbol_name LIKE ?"
        rows = fetch_all(conn, sql, (wid, f"%{name}%"))

        if path_prefix:
            pref = path_prefix.strip().lstrip("/")
            rows = [r for r in rows if str(r.get("file_path") or "").startswith(pref)]

        return rows[: int(limit)]
