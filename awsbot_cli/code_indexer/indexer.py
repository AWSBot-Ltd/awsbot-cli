from __future__ import annotations

import os
import uuid
import datetime as dt
from pathlib import Path

from qdrant_client.models import PointStruct

from .config import Config
from .db import connect, fetch_one, fetch_all, execute, executemany
from .ignore import load_ignore_spec, should_ignore
from .hashing import sha256_file, sha256_text
from .chunkers import guess_language, chunk_python, chunk_fallback
from .embedder import Embedder
from .qdrant_store import QdrantStore
from .py_analyze import analyze_python_source
from .ids import make_symbol_id


def workspace_id_for(repo_root: Path) -> str:
    # stable id
    return sha256_text(str(repo_root))


def discover_files(repo_root: Path, ignore_spec) -> list[Path]:
    files: list[Path] = []
    for root, dirs, filenames in os.walk(repo_root):
        root_path = Path(root)
        # Skip hidden dirs quickly (except .github etc)
        dirs[:] = [
            d
            for d in dirs
            if d not in [".git", ".mcp_index", "__pycache__"]
            and not d.endswith(".egg-info")
        ]
        for fn in filenames:
            p = root_path / fn
            if p.is_symlink():
                continue
            if not p.is_file():
                continue
            rel = p.relative_to(repo_root).as_posix()
            if should_ignore(rel, ignore_spec):
                continue
            # Ignore obvious binaries by extension
            if p.suffix.lower() in {
                ".png",
                ".jpg",
                ".jpeg",
                ".gif",
                ".webp",
                ".ico",
                ".pdf",
                ".zip",
                ".tar",
                ".gz",
                ".7z",
            }:
                continue
            # Ignore huge files
            try:
                if p.stat().st_size > 1_000_000:  # 1MB
                    continue
            except OSError:
                continue
            files.append(p)
    return files


def safe_read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


def get_git_ref(repo_root: Path) -> str:
    # Best-effort; no user-controlled args.
    import subprocess

    try:
        r = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(repo_root),
            check=True,
            capture_output=True,
            text=True,
            timeout=2.0,
        )
        return r.stdout.strip()[:40] or "working_tree"
    except Exception:
        return "working_tree"


def index_repo(
    cfg: Config,
    repo_root: Path,
    db_path: Path,
    job_cb=None,  # callable(progress, processed, total, message)
) -> dict:
    conn = connect(db_path)
    wid = workspace_id_for(repo_root)
    now = dt.datetime.utcnow().isoformat()

    execute(
        conn,
        "INSERT OR IGNORE INTO workspaces(workspace_id, repo_root, created_at) VALUES(?,?,?)",
        (wid, str(repo_root), now),
    )

    ignore_spec = load_ignore_spec(repo_root)
    files = discover_files(repo_root, ignore_spec)
    total = len(files)
    git_ref = get_git_ref(repo_root)

    # Register snapshot
    execute(
        conn,
        "INSERT OR REPLACE INTO snapshots(workspace_id, git_ref, indexed_at) VALUES(?,?,?)",
        (wid, git_ref, now),
    )

    # Embedder + Qdrant store
    embedder = Embedder()
    store = QdrantStore(
        cfg.qdrant_host, cfg.qdrant_port, cfg.qdrant_collection, embedder.dim()
    )

    processed = 0
    changed = 0

    for f in files:
        processed += 1
        rel = f.relative_to(repo_root).as_posix()
        try:
            st = f.stat()
        except OSError:
            continue
        lang = guess_language(f)
        file_hash = sha256_file(f)

        row = fetch_one(
            conn,
            "SELECT sha256, mtime, size FROM files_snap WHERE workspace_id=? AND git_ref=? AND file_path=?",
            (wid, git_ref, rel),
        )
        if (
            row
            and row["sha256"] == file_hash
            and float(row["mtime"]) == float(st.st_mtime)
            and int(row["size"]) == int(st.st_size)
        ):
            # unchanged
            if job_cb and processed % 25 == 0:
                job_cb(
                    processed / max(1, total),
                    processed,
                    total,
                    f"Scanning (unchanged): {rel}",
                )
            continue

        # Changed: reindex this file
        changed += 1
        text = safe_read_text(f)

        # Chunk
        if lang == "python":
            chunks, symbols = chunk_python(rel, text, cfg.max_chunk_chars)
        else:
            chunks = chunk_fallback(
                rel, text, lang, cfg.max_fallback_lines, cfg.fallback_overlap_lines
            )
            symbols = []

        # Delete old points for this file+git_ref, then upsert new ones
        store.delete_file(wid, rel, git_ref)

        passages = [c.text for c in chunks]
        vecs = embedder.embed_passages(passages) if passages else []

        points: list[PointStruct] = []
        for c, v in zip(chunks, vecs):
            pid = str(
                uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"{wid}:{git_ref}:{c.file_path}:{c.start_line}:{c.end_line}:{sha256_text(c.text)}",
                )
            )
            payload = {
                "workspace_id": wid,
                "git_ref": git_ref,
                "file_path": c.file_path,
                "language": c.language,
                "chunk_type": c.chunk_type,
                "symbol_name": c.symbol_name,
                "start_line": c.start_line,
                "end_line": c.end_line,
                "text": c.text,
                "chunk_sha256": sha256_text(c.text),
                "updated_at": now,
            }
            points.append(PointStruct(id=pid, vector=v, payload=payload))
        store.upsert_chunks(points)

        # Python enrichment: symbol ids, references, callgraph (best-effort)
        if lang == "python":
            py_defs, py_refs, py_calls = analyze_python_source(text)

            # Replace existing python symbols/refs for this file+snapshot
            execute(
                conn,
                "DELETE FROM py_symbols WHERE workspace_id=? AND git_ref=? AND file_path=?",
                (wid, git_ref, rel),
            )
            execute(
                conn,
                "DELETE FROM py_refs WHERE workspace_id=? AND git_ref=? AND file_path=?",
                (wid, git_ref, rel),
            )
            # Remove edges originating from any symbol in this file (snapshot)
            execute(
                conn,
                "DELETE FROM py_call_edges WHERE workspace_id=? AND git_ref=? AND from_symbol_id IN "
                "(SELECT symbol_id FROM py_symbols WHERE workspace_id=? AND git_ref=? AND file_path=?)",
                (wid, git_ref, wid, git_ref, rel),
            )

            def_rows = []
            qual_to_id = {}
            for d in py_defs:
                sid = make_symbol_id(
                    wid,
                    git_ref,
                    rel,
                    d.qualname,
                    d.kind,
                    int(d.start_line),
                    int(d.end_line),
                )
                qual_to_id[d.qualname] = sid
                def_rows.append(
                    (
                        sid,
                        wid,
                        git_ref,
                        rel,
                        d.name,
                        d.qualname,
                        d.kind,
                        int(d.start_line),
                        int(d.end_line),
                    )
                )

            if def_rows:
                executemany(
                    conn,
                    "INSERT OR REPLACE INTO py_symbols(symbol_id,workspace_id,git_ref,file_path,symbol_name,qualname,symbol_kind,start_line,end_line)"
                    " VALUES(?,?,?,?,?,?,?,?,?)",
                    def_rows,
                )

            if py_refs:
                ref_rows = [
                    (wid, git_ref, r.name, rel, int(r.line), int(r.col), r.context)
                    for r in py_refs
                ]
                executemany(
                    conn,
                    "INSERT OR REPLACE INTO py_refs(workspace_id,git_ref,symbol_name,file_path,line,col,context) VALUES(?,?,?,?,?,?,?)",
                    ref_rows,
                )

            if py_calls and def_rows:
                # Same-file name map
                name_to_ids_local = {}
                for sid, _, _, _, nm, qn, kind, sl, el in def_rows:
                    name_to_ids_local.setdefault(nm, []).append(sid)

                # Snapshot-global name map (unique only)
                global_rows = fetch_all(
                    conn,
                    "SELECT symbol_name, symbol_id FROM py_symbols WHERE workspace_id=? AND git_ref=?",
                    (wid, git_ref),
                )
                name_to_ids_global = {}
                for gr in global_rows:
                    name_to_ids_global.setdefault(gr["symbol_name"], []).append(
                        gr["symbol_id"]
                    )

                edge_rows = []
                for c in py_calls:
                    from_id = qual_to_id.get(c.caller_qualname)
                    if not from_id:
                        continue
                    to_name = c.callee
                    base = to_name.split(".")[-1]
                    to_id = None
                    if base in name_to_ids_local and len(name_to_ids_local[base]) == 1:
                        to_id = name_to_ids_local[base][0]
                    elif (
                        base in name_to_ids_global
                        and len(name_to_ids_global[base]) == 1
                    ):
                        to_id = name_to_ids_global[base][0]
                    edge_rows.append(
                        (wid, git_ref, from_id, to_name, to_id, int(c.line))
                    )

                if edge_rows:
                    executemany(
                        conn,
                        "INSERT OR REPLACE INTO py_call_edges(workspace_id,git_ref,from_symbol_id,to_name,to_symbol_id,call_line)"
                        " VALUES(?,?,?,?,?,?)",
                        edge_rows,
                    )

        # Keep SQLite lexical index in sync (FTS5)
        execute(
            conn,
            "DELETE FROM chunk_fts WHERE workspace_id=? AND file_path=? AND git_ref=?",
            (wid, rel, git_ref),
        )
        if chunks:
            executemany(
                conn,
                "INSERT INTO chunk_fts(workspace_id,git_ref,file_path,start_line,end_line,language,chunk_type,symbol_name,text)"
                " VALUES(?,?,?,?,?,?,?,?,?)",
                [
                    (
                        wid,
                        git_ref,
                        c.file_path,
                        int(c.start_line),
                        int(c.end_line),
                        c.language,
                        c.chunk_type,
                        c.symbol_name,
                        c.text,
                    )
                    for c in chunks
                ],
            )

        # Update DB records
        execute(
            conn,
            "INSERT INTO files_snap(workspace_id,git_ref,file_path,sha256,mtime,size,language,updated_at) VALUES(?,?,?,?,?,?,?,?) "
            "ON CONFLICT(workspace_id,git_ref,file_path) DO UPDATE SET sha256=excluded.sha256, mtime=excluded.mtime,"
            " size=excluded.size, language=excluded.language, updated_at=excluded.updated_at",
            (
                wid,
                git_ref,
                rel,
                file_hash,
                float(st.st_mtime),
                int(st.st_size),
                lang,
                now,
            ),
        )

        # Replace symbols for this file
        execute(
            conn,
            "DELETE FROM symbols_snap WHERE workspace_id=? AND git_ref=? AND file_path=?",
            (wid, git_ref, rel),
        )
        if symbols:
            executemany(
                conn,
                "INSERT OR IGNORE INTO symbols_snap(workspace_id,git_ref,file_path,symbol_name,symbol_kind,start_line,end_line)"
                " VALUES(?,?,?,?,?,?,?)",
                [
                    (wid, git_ref, rel, sname, skind, sstart, send)
                    for (sname, skind, sstart, send) in symbols
                ],
            )

        if job_cb:
            job_cb(
                processed / max(1, total),
                processed,
                total,
                f"Indexed: {rel} ({len(points)} chunks)",
            )
        else:
            pass

    return {
        "workspace_id": wid,
        "repo_root": str(repo_root),
        "git_ref": git_ref,
        "files_total": total,
        "files_changed": changed,
    }


def index_paths(
    cfg: Config,
    repo_root: Path,
    db_path: Path,
    rel_paths: list[str],
    job_cb=None,
) -> dict:
    # Minimal incremental indexing for specific paths (relative). Falls back to full file reindex.
    conn = connect(db_path)
    wid = workspace_id_for(repo_root)
    now = dt.datetime.utcnow().isoformat()
    execute(
        conn,
        "INSERT OR IGNORE INTO workspaces(workspace_id, repo_root, created_at) VALUES(?,?,?)",
        (wid, str(repo_root), now),
    )

    embedder = Embedder()
    store = QdrantStore(
        cfg.qdrant_host, cfg.qdrant_port, cfg.qdrant_collection, embedder.dim()
    )
    git_ref = get_git_ref(repo_root)

    execute(
        conn,
        "INSERT OR REPLACE INTO snapshots(workspace_id, git_ref, indexed_at) VALUES(?,?,?)",
        (wid, git_ref, now),
    )

    total = len(rel_paths)
    processed = 0
    changed = 0

    for rel in rel_paths:
        processed += 1
        f = (repo_root / rel).resolve()
        try:
            f.relative_to(repo_root)
        except ValueError:
            continue
        if not f.exists() or not f.is_file():
            continue

        try:
            st = f.stat()
        except OSError:
            continue
        lang = guess_language(f)
        file_hash = sha256_file(f)

        row = fetch_one(
            conn,
            "SELECT sha256, mtime, size FROM files_snap WHERE workspace_id=? AND git_ref=? AND file_path=?",
            (wid, git_ref, rel),
        )
        if (
            row
            and row["sha256"] == file_hash
            and float(row["mtime"]) == float(st.st_mtime)
            and int(row["size"]) == int(st.st_size)
        ):
            if job_cb:
                job_cb(processed / max(1, total), processed, total, f"Unchanged: {rel}")
            continue

        changed += 1
        text = safe_read_text(f)
        if lang == "python":
            chunks, symbols = chunk_python(rel, text, cfg.max_chunk_chars)
        else:
            chunks = chunk_fallback(
                rel, text, lang, cfg.max_fallback_lines, cfg.fallback_overlap_lines
            )
            symbols = []

        store.delete_file(wid, rel, git_ref)
        passages = [c.text for c in chunks]
        vecs = embedder.embed_passages(passages) if passages else []

        points: list[PointStruct] = []
        for c, v in zip(chunks, vecs):
            pid = str(
                uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"{wid}:{git_ref}:{c.file_path}:{c.start_line}:{c.end_line}:{sha256_text(c.text)}",
                )
            )
            payload = {
                "workspace_id": wid,
                "git_ref": git_ref,
                "file_path": c.file_path,
                "language": c.language,
                "chunk_type": c.chunk_type,
                "symbol_name": c.symbol_name,
                "start_line": c.start_line,
                "end_line": c.end_line,
                "text": c.text,
                "chunk_sha256": sha256_text(c.text),
                "updated_at": now,
            }
            points.append(PointStruct(id=pid, vector=v, payload=payload))
        store.upsert_chunks(points)

        # Python enrichment: symbol ids, references, callgraph (best-effort)
        if lang == "python":
            py_defs, py_refs, py_calls = analyze_python_source(text)

            # Replace existing python symbols/refs for this file+snapshot
            execute(
                conn,
                "DELETE FROM py_symbols WHERE workspace_id=? AND git_ref=? AND file_path=?",
                (wid, git_ref, rel),
            )
            execute(
                conn,
                "DELETE FROM py_refs WHERE workspace_id=? AND git_ref=? AND file_path=?",
                (wid, git_ref, rel),
            )
            # Remove edges originating from any symbol in this file (snapshot)
            execute(
                conn,
                "DELETE FROM py_call_edges WHERE workspace_id=? AND git_ref=? AND from_symbol_id IN "
                "(SELECT symbol_id FROM py_symbols WHERE workspace_id=? AND git_ref=? AND file_path=?)",
                (wid, git_ref, wid, git_ref, rel),
            )

            def_rows = []
            qual_to_id = {}
            for d in py_defs:
                sid = make_symbol_id(
                    wid,
                    git_ref,
                    rel,
                    d.qualname,
                    d.kind,
                    int(d.start_line),
                    int(d.end_line),
                )
                qual_to_id[d.qualname] = sid
                def_rows.append(
                    (
                        sid,
                        wid,
                        git_ref,
                        rel,
                        d.name,
                        d.qualname,
                        d.kind,
                        int(d.start_line),
                        int(d.end_line),
                    )
                )

            if def_rows:
                executemany(
                    conn,
                    "INSERT OR REPLACE INTO py_symbols(symbol_id,workspace_id,git_ref,file_path,symbol_name,qualname,symbol_kind,start_line,end_line)"
                    " VALUES(?,?,?,?,?,?,?,?,?)",
                    def_rows,
                )

            if py_refs:
                ref_rows = [
                    (wid, git_ref, r.name, rel, int(r.line), int(r.col), r.context)
                    for r in py_refs
                ]
                executemany(
                    conn,
                    "INSERT OR REPLACE INTO py_refs(workspace_id,git_ref,symbol_name,file_path,line,col,context) VALUES(?,?,?,?,?,?,?)",
                    ref_rows,
                )

            if py_calls and def_rows:
                # Same-file name map
                name_to_ids_local = {}
                for sid, _, _, _, nm, qn, kind, sl, el in def_rows:
                    name_to_ids_local.setdefault(nm, []).append(sid)

                # Snapshot-global name map (unique only)
                global_rows = fetch_all(
                    conn,
                    "SELECT symbol_name, symbol_id FROM py_symbols WHERE workspace_id=? AND git_ref=?",
                    (wid, git_ref),
                )
                name_to_ids_global = {}
                for gr in global_rows:
                    name_to_ids_global.setdefault(gr["symbol_name"], []).append(
                        gr["symbol_id"]
                    )

                edge_rows = []
                for c in py_calls:
                    from_id = qual_to_id.get(c.caller_qualname)
                    if not from_id:
                        continue
                    to_name = c.callee
                    base = to_name.split(".")[-1]
                    to_id = None
                    if base in name_to_ids_local and len(name_to_ids_local[base]) == 1:
                        to_id = name_to_ids_local[base][0]
                    elif (
                        base in name_to_ids_global
                        and len(name_to_ids_global[base]) == 1
                    ):
                        to_id = name_to_ids_global[base][0]
                    edge_rows.append(
                        (wid, git_ref, from_id, to_name, to_id, int(c.line))
                    )

                if edge_rows:
                    executemany(
                        conn,
                        "INSERT OR REPLACE INTO py_call_edges(workspace_id,git_ref,from_symbol_id,to_name,to_symbol_id,call_line)"
                        " VALUES(?,?,?,?,?,?)",
                        edge_rows,
                    )

        # Keep SQLite lexical index in sync (FTS5)
        execute(
            conn,
            "DELETE FROM chunk_fts WHERE workspace_id=? AND file_path=? AND git_ref=?",
            (wid, rel, git_ref),
        )
        if chunks:
            executemany(
                conn,
                "INSERT INTO chunk_fts(workspace_id,git_ref,file_path,start_line,end_line,language,chunk_type,symbol_name,text)"
                " VALUES(?,?,?,?,?,?,?,?,?)",
                [
                    (
                        wid,
                        git_ref,
                        c.file_path,
                        int(c.start_line),
                        int(c.end_line),
                        c.language,
                        c.chunk_type,
                        c.symbol_name,
                        c.text,
                    )
                    for c in chunks
                ],
            )

        execute(
            conn,
            "INSERT INTO files_snap(workspace_id,git_ref,file_path,sha256,mtime,size,language,updated_at) VALUES(?,?,?,?,?,?,?,?) "
            "ON CONFLICT(workspace_id,git_ref,file_path) DO UPDATE SET sha256=excluded.sha256, mtime=excluded.mtime,"
            " size=excluded.size, language=excluded.language, updated_at=excluded.updated_at",
            (
                wid,
                git_ref,
                rel,
                file_hash,
                float(st.st_mtime),
                int(st.st_size),
                lang,
                now,
            ),
        )

        execute(
            conn,
            "DELETE FROM symbols_snap WHERE workspace_id=? AND git_ref=? AND file_path=?",
            (wid, git_ref, rel),
        )
        if symbols:
            executemany(
                conn,
                "INSERT OR IGNORE INTO symbols_snap(workspace_id,git_ref,file_path,symbol_name,symbol_kind,start_line,end_line)"
                " VALUES(?,?,?,?,?,?,?)",
                [
                    (wid, git_ref, rel, sname, skind, sstart, send)
                    for (sname, skind, sstart, send) in symbols
                ],
            )

        if job_cb:
            job_cb(
                processed / max(1, total),
                processed,
                total,
                f"Indexed: {rel} ({len(points)} chunks)",
            )

    return {
        "workspace_id": wid,
        "repo_root": str(repo_root),
        "git_ref": git_ref,
        "paths_total": total,
        "paths_changed": changed,
    }
