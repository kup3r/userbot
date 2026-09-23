"""Central tenant-safe module store helpers.

The Control Bot uses this service to validate and publish modules into the
central PostgreSQL/SQLite database. The public FastAPI endpoints can then
serve a read-only index and source files to tenant stores.
"""

from __future__ import annotations

import ast
import hashlib
import io
import re
import tokenize
from dataclasses import dataclass
from typing import Any

NAME_RE = re.compile(r"^[a-z_][a-z0-9_]{1,29}$")
MAX_SOURCE = 2 * 1024 * 1024


@dataclass(slots=True, frozen=True)
class ScanFinding:
    severity: str
    line: int
    message: str


@dataclass(slots=True, frozen=True)
class SourceAnalysis:
    name: str
    version: str
    description: str
    category: str
    authors: tuple[str, ...]
    tags: tuple[str, ...]
    source: bytes
    sha256: str
    size: int
    findings: tuple[ScanFinding, ...]

    @property
    def blocked(self) -> bool:
        return any(item.severity == "CRITICAL" for item in self.findings)

    @property
    def score(self) -> int:
        weights = {"CRITICAL": 100, "HIGH": 25, "MEDIUM": 8, "LOW": 2}
        return min(100, sum(weights.get(item.severity, 1) for item in self.findings))


def _safe_literal(node: ast.AST | None, default: Any) -> Any:
    if node is None:
        return default
    try:
        value = ast.literal_eval(node)
    except Exception:
        return default
    if isinstance(value, (str, int, float, bool, list, tuple)) or value is None:
        return value
    return default


def _class_attrs(tree: ast.Module) -> tuple[ast.ClassDef | None, dict[str, Any]]:
    target: ast.ClassDef | None = None
    attrs: dict[str, Any] = {}
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "Module":
            target = node
            break
    if target is None:
        return None, attrs
    for stmt in target.body:
        if isinstance(stmt, ast.Assign):
            value = _safe_literal(stmt.value, None)
            for name in stmt.targets:
                if isinstance(name, ast.Name):
                    attrs[name.id] = value
        elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            attrs[stmt.target.id] = _safe_literal(stmt.value, None)
    return target, attrs


def _scan_ast(tree: ast.AST) -> list[ScanFinding]:
    findings: list[ScanFinding] = []
    risky_calls = {
        "eval": "Dynamic eval can execute arbitrary code.",
        "exec": "Dynamic exec can execute arbitrary code.",
        "compile": "Dynamic compile can execute arbitrary code.",
        "__import__": "Dynamic imports may evade static dependency review.",
    }
    blocked_imports = {"subprocess", "ctypes", "winreg", "marshal", "pickle"}
    shell_names = {"system", "popen", "Popen", "run", "call", "check_call", "check_output"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".", 1)[0]
                if root in blocked_imports:
                    findings.append(ScanFinding("CRITICAL", node.lineno, f"Import запрещённого модуля: {root}"))
        elif isinstance(node, ast.ImportFrom):
            root = str(node.module or "").split(".", 1)[0]
            if root in blocked_imports:
                findings.append(ScanFinding("CRITICAL", node.lineno, f"Import запрещённого модуля: {root}"))
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id in risky_calls:
                findings.append(ScanFinding("CRITICAL", node.lineno, risky_calls[node.func.id]))
            if isinstance(node.func, ast.Attribute) and node.func.attr in shell_names:
                base = getattr(node.func.value, "id", "")
                if base in {"os", "subprocess"}:
                    findings.append(ScanFinding("CRITICAL", node.lineno, f"Shell/process call: {base}.{node.func.attr}"))
        elif isinstance(node, ast.Assign):
            target_names = [t.id.upper() for t in node.targets if isinstance(t, ast.Name)]
            secret_names = {"API_HASH", "BOT_TOKEN", "SESSION", "SESSION_STRING", "PASSWORD", "SECRET_KEY"}
            if any(name in secret_names for name in target_names):
                value = _safe_literal(node.value, None)
                if isinstance(value, str) and value.strip() and not value.strip().startswith("${"):
                    findings.append(ScanFinding("CRITICAL", node.lineno, "Похожее на встроенный секрет значение в исходнике."))
        elif isinstance(node, ast.Attribute) and node.attr in {"system", "popen"}:
            findings.append(ScanFinding("HIGH", node.lineno, f"Process helper: .{node.attr}"))
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            text = node.value
            if re.search(r"(?:api[_-]?hash|bot[_-]?token|session(?:_string)?|password)\s*[:=]\s*[^\n ]+", text, re.I):
                findings.append(ScanFinding("HIGH", node.lineno, "Похожая на секрет строка в исходнике."))
    return findings


def analyze_source(source: bytes, filename: str, *, requested_name: str | None = None) -> SourceAnalysis:
    filename = str(filename or "").strip()
    if not filename.lower().endswith(".py"):
        raise ValueError("Нужен файл с расширением .py")
    if len(source) > MAX_SOURCE:
        raise ValueError("Модуль превышает лимит 2 MiB")
    try:
        text = source.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("Исходник должен быть UTF-8") from exc
    try:
        tree = ast.parse(text, filename=filename)
        compile(tree, filename, "exec")
    except SyntaxError as exc:
        line = f":{exc.lineno}" if exc.lineno else ""
        raise ValueError(f"SyntaxError{line}: {exc.msg}") from exc

    target, attrs = _class_attrs(tree)
    if target is None:
        raise ValueError("В исходнике отсутствует class Module")
    bases = {base.id for base in target.bases if isinstance(base, ast.Name)}
    if not (bases & {"BaseModule", "Module"}):
        raise ValueError("class Module должен наследоваться от BaseModule")

    # Tokenize once so suspicious bare control chars/unprintables are caught.
    findings = _scan_ast(tree)
    for token in tokenize.generate_tokens(io.StringIO(text).readline):
        if token.type == tokenize.ERRORTOKEN and token.string.strip():
            if not token.string.isspace() and any(ord(ch) < 9 for ch in token.string):
                findings.append(ScanFinding("MEDIUM", token.start[0], "Необычный управляющий символ."))

    raw_name = str(requested_name or filename.rsplit("/", 1)[-1].rsplit("\\", 1)[-1].rsplit(".", 1)[0]).strip().lower()
    if not NAME_RE.fullmatch(raw_name):
        raise ValueError("Имя магазина: 2–30 символов, только a-z, 0-9 и _; начинать с буквы/_")
    version = str(attrs.get("version") or "1.0.0")[:64]
    description = str(attrs.get("description") or "Без описания.")[:500]
    category = str(attrs.get("category") or "General")[:64]
    authors_raw = attrs.get("authors") or attrs.get("author") or ("Не указаны",)
    if isinstance(authors_raw, (list, tuple, set)):
        authors = tuple(str(item)[:80] for item in authors_raw if str(item).strip())[:8] or ("Не указаны",)
    else:
        authors = (str(authors_raw)[:80],)
    tags_raw = attrs.get("tags") or ()
    if isinstance(tags_raw, (list, tuple, set)):
        tags = tuple(str(item).strip().lower()[:32] for item in tags_raw if str(item).strip())[:12]
    else:
        tags = tuple(x.strip().lower()[:32] for x in str(tags_raw).split(",") if x.strip())[:12]
    return SourceAnalysis(
        name=raw_name,
        version=version,
        description=description,
        category=category,
        authors=authors,
        tags=tags,
        source=source,
        sha256=hashlib.sha256(source).hexdigest(),
        size=len(source),
        findings=tuple(findings),
    )


def scan_text_for_display(analysis: SourceAnalysis) -> str:
    if not analysis.findings:
        return "✅ Статический scanner: критических находок нет."
    lines = [f"Scanner score: <b>{analysis.score}</b>"]
    for item in analysis.findings[:12]:
        lines.append(f"• <b>{item.severity}</b> L{item.line}: {item.message}")
    if len(analysis.findings) > 12:
        lines.append(f"… ещё {len(analysis.findings) - 12}")
    return "\n".join(lines)


def normalize_plan(value: str | None) -> str:
    value = str(value or "basic").strip().lower()
    return value if value in {"basic", "pro", "premium"} else "basic"
