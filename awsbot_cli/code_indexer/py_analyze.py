from __future__ import annotations

import ast
from dataclasses import dataclass


@dataclass(frozen=True)
class PyDef:
    name: str
    qualname: str
    kind: str  # function | class | method
    start_line: int
    end_line: int


@dataclass(frozen=True)
class PyRef:
    name: str
    line: int
    col: int
    context: str


@dataclass(frozen=True)
class PyCall:
    caller_qualname: str
    callee: str
    line: int


def _get_line(lines: list[str], lineno: int) -> str:
    if 1 <= lineno <= len(lines):
        return lines[lineno - 1]
    return ""


def _callee_str(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parts = []
        cur: ast.AST = node
        while isinstance(cur, ast.Attribute):
            parts.append(cur.attr)
            cur = cur.value
        if isinstance(cur, ast.Name):
            parts.append(cur.id)
        parts.reverse()
        return ".".join(parts)
    return "<call>"


class Analyzer(ast.NodeVisitor):
    def __init__(self, src: str):
        self.src = src
        self.lines = src.splitlines()
        self.defs: list[PyDef] = []
        self.refs: list[PyRef] = []
        self.calls: list[PyCall] = []
        self._scope: list[str] = []
        self._current_callable: list[str] = []

    def _qualname(self, name: str) -> str:
        return ".".join(self._scope + [name]) if self._scope else name

    def visit_ClassDef(self, node: ast.ClassDef):
        qn = self._qualname(node.name)
        self.defs.append(
            PyDef(
                node.name,
                qn,
                "class",
                node.lineno,
                getattr(node, "end_lineno", node.lineno),
            )
        )
        self._scope.append(node.name)
        self.generic_visit(node)
        self._scope.pop()

    def _visit_function(self, node: ast.AST, name: str):
        qn = self._qualname(name)
        kind = "method" if self._scope else "function"
        self.defs.append(
            PyDef(
                name,
                qn,
                kind,
                getattr(node, "lineno", 1),
                getattr(node, "end_lineno", getattr(node, "lineno", 1)),
            )
        )
        self._scope.append(name)
        self._current_callable.append(qn)
        self.generic_visit(node)
        self._current_callable.pop()
        self._scope.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef):
        self._visit_function(node, node.name)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef):
        self._visit_function(node, node.name)

    def visit_Name(self, node: ast.Name):
        if isinstance(node.ctx, ast.Load):
            line = getattr(node, "lineno", 1)
            col = getattr(node, "col_offset", 0)
            lt = _get_line(self.lines, line)
            start = max(0, col - 40)
            end = min(len(lt), col + len(node.id) + 40)
            ctx = lt[start:end]
            self.refs.append(PyRef(node.id, int(line), int(col), ctx))
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call):
        if self._current_callable:
            caller = self._current_callable[-1]
            callee = _callee_str(node.func)
            self.calls.append(PyCall(caller, callee, int(getattr(node, "lineno", 1))))
        self.generic_visit(node)


def analyze_python_source(text: str) -> tuple[list[PyDef], list[PyRef], list[PyCall]]:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return [], [], []
    an = Analyzer(text)
    an.visit(tree)
    return an.defs, an.refs, an.calls
