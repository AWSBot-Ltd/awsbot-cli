from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Tuple

SCHEMA = r"""
-- Workspace registry
CREATE TABLE IF NOT EXISTS workspaces (
  workspace_id TEXT PRIMARY KEY,
  repo_root TEXT NOT NULL,
  created_at TEXT NOT NULL
);

-- Snapshot registry (git_ref == commit sha or "working_tree")
CREATE TABLE IF NOT EXISTS snapshots (
  workspace_id TEXT NOT NULL,
  git_ref TEXT NOT NULL,
  indexed_at TEXT NOT NULL,
  PRIMARY KEY (workspace_id, git_ref)
);

-- Snapshot-aware file manifest
CREATE TABLE IF NOT EXISTS files_snap (
  workspace_id TEXT NOT NULL,
  git_ref TEXT NOT NULL,
  file_path TEXT NOT NULL,
  sha256 TEXT NOT NULL,
  mtime REAL NOT NULL,
  size INTEGER NOT NULL,
  language TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (workspace_id, git_ref, file_path)
);

-- Generic symbols extracted by chunkers (python AST + tree-sitter)
CREATE TABLE IF NOT EXISTS symbols_snap (
  workspace_id TEXT NOT NULL,
  git_ref TEXT NOT NULL,
  file_path TEXT NOT NULL,
  symbol_name TEXT NOT NULL,
  symbol_kind TEXT NOT NULL,
  start_line INTEGER NOT NULL,
  end_line INTEGER NOT NULL,
  PRIMARY KEY (workspace_id, git_ref, file_path, symbol_name, symbol_kind, start_line)
);

-- Python enriched symbols (stable symbol_id per definition)
CREATE TABLE IF NOT EXISTS py_symbols (
  symbol_id TEXT PRIMARY KEY,
  workspace_id TEXT NOT NULL,
  git_ref TEXT NOT NULL,
  file_path TEXT NOT NULL,
  symbol_name TEXT NOT NULL,
  qualname TEXT NOT NULL,
  symbol_kind TEXT NOT NULL,
  start_line INTEGER NOT NULL,
  end_line INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_py_symbols_name ON py_symbols(workspace_id, git_ref, symbol_name);
CREATE INDEX IF NOT EXISTS idx_py_symbols_file ON py_symbols(workspace_id, git_ref, file_path);

-- Python references (best-effort resolution by name)
CREATE TABLE IF NOT EXISTS py_refs (
  workspace_id TEXT NOT NULL,
  git_ref TEXT NOT NULL,
  symbol_name TEXT NOT NULL,
  file_path TEXT NOT NULL,
  line INTEGER NOT NULL,
  col INTEGER NOT NULL,
  context TEXT,
  PRIMARY KEY (workspace_id, git_ref, symbol_name, file_path, line, col)
);

CREATE INDEX IF NOT EXISTS idx_py_refs_name ON py_refs(workspace_id, git_ref, symbol_name);

-- Python callgraph edges (best-effort)
CREATE TABLE IF NOT EXISTS py_call_edges (
  workspace_id TEXT NOT NULL,
  git_ref TEXT NOT NULL,
  from_symbol_id TEXT NOT NULL,
  to_name TEXT NOT NULL,
  to_symbol_id TEXT,
  call_line INTEGER,
  PRIMARY KEY (workspace_id, git_ref, from_symbol_id, to_name, call_line, to_symbol_id)
);

CREATE INDEX IF NOT EXISTS idx_py_edges_from ON py_call_edges(workspace_id, git_ref, from_symbol_id);
CREATE INDEX IF NOT EXISTS idx_py_edges_toid ON py_call_edges(workspace_id, git_ref, to_symbol_id);

-- Lexical search index (FTS5 / BM25)
CREATE VIRTUAL TABLE IF NOT EXISTS chunk_fts USING fts5(
  workspace_id UNINDEXED,
  git_ref UNINDEXED,
  file_path UNINDEXED,
  start_line UNINDEXED,
  end_line UNINDEXED,
  language UNINDEXED,
  chunk_type UNINDEXED,
  symbol_name UNINDEXED,
  text,
  tokenize='unicode61'
);

-- Job tracking
CREATE TABLE IF NOT EXISTS jobs (
  job_id TEXT PRIMARY KEY,
  workspace_id TEXT NOT NULL,
  state TEXT NOT NULL,
  progress REAL NOT NULL,
  processed_files INTEGER NOT NULL,
  total_files INTEGER NOT NULL,
  message TEXT,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  error TEXT
);

-- Legacy tables (kept if already present; unused by new code)
CREATE TABLE IF NOT EXISTS files (
  workspace_id TEXT NOT NULL,
  file_path TEXT NOT NULL,
  sha256 TEXT NOT NULL,
  mtime REAL NOT NULL,
  size INTEGER NOT NULL,
  language TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (workspace_id, file_path)
);

CREATE TABLE IF NOT EXISTS symbols (
  workspace_id TEXT NOT NULL,
  file_path TEXT NOT NULL,
  symbol_name TEXT NOT NULL,
  symbol_kind TEXT NOT NULL,
  start_line INTEGER NOT NULL,
  end_line INTEGER NOT NULL,
  PRIMARY KEY (workspace_id, file_path, symbol_name, symbol_kind, start_line)
);
"""


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def fetch_one(
    conn: sqlite3.Connection, sql: str, params: Tuple[Any, ...]
) -> Optional[Dict[str, Any]]:
    cur = conn.execute(sql, params)
    row = cur.fetchone()
    return dict(row) if row else None


def fetch_all(
    conn: sqlite3.Connection, sql: str, params: Tuple[Any, ...]
) -> list[Dict[str, Any]]:
    cur = conn.execute(sql, params)
    return [dict(r) for r in cur.fetchall()]


def execute(conn: sqlite3.Connection, sql: str, params: Tuple[Any, ...]) -> None:
    conn.execute(sql, params)
    conn.commit()


def executemany(
    conn: sqlite3.Connection, sql: str, rows: Iterable[Tuple[Any, ...]]
) -> None:
    conn.executemany(sql, list(rows))
    conn.commit()
