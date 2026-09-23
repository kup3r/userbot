"""AST-based heuristic security scanner for dynamically loaded userbot modules.

The scanner is intentionally conservative: it can detect common dangerous
patterns, but static AST analysis cannot prove arbitrary Python code is safe.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from enum import IntEnum
from pathlib import Path
from typing import Iterable

from core.commands import CommandContext, command
from core.module import BaseModule


class Severity(IntEnum):
    INFO = 0
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    CRITICAL = 4


@dataclass(slots=True, frozen=True)
class Finding:
    severity: Severity
    title: str
    detail: str
    lineno: int
    code: str


@dataclass(slots=True)
class ScanReport:
    filename: str
    findings: list[Finding] = field(default_factory=list)

    @property
    def verdict(self) -> str:
        if any(f.severity >= Severity.CRITICAL for f in self.findings):
            return "🔴 ОПАСНО"
        if any(f.severity >= Severity.MEDIUM for f in self.findings):
            return "🟡 ПОДОЗРИТЕЛЬНО"
        return "🟢 БЕЗОПАСНО"

    @property
    def score(self) -> int:
        # 100 is a clean heuristic result. Findings lower it.
        penalty = 0
        for finding in self.findings:
            penalty += {
                Severity.INFO: 0,
                Severity.LOW: 5,
                Severity.MEDIUM: 15,
                Severity.HIGH: 30,
                Severity.CRITICAL: 60,
            }[finding.severity]
        return max(0, 100 - penalty)

    @property
    def blocked(self) -> bool:
        return any(f.severity >= Severity.CRITICAL for f in self.findings)


class SecurityScanner(ast.NodeVisitor):
    """Conservative detector for common dangerous/exfiltration patterns."""

    IMPORT_DANGER = {
        "subprocess": ("CRITICAL", "Импорт subprocess позволяет запускать процессы."),
        "ctypes": ("CRITICAL", "Импорт ctypes может выполнять нативный код."),
        "multiprocessing": ("CRITICAL", "Импорт multiprocessing может создавать дочерние процессы."),
        "signal": ("HIGH", "Импорт signal позволяет управлять сигналами процесса."),
        "atexit": ("HIGH", "atexit может регистрировать код на завершение процесса."),
    }

    DANGER_CALLS = {
        ("os", "system"): (Severity.CRITICAL, "os.system() может выполнить shell-команду."),
        ("os", "_exit"): (Severity.CRITICAL, "os._exit() мгновенно завершает worker."),
        ("os", "kill"): (Severity.CRITICAL, "os.kill() может завершить worker."),
        ("sys", "exit"): (Severity.CRITICAL, "sys.exit() может завершить worker."),
        ("signal", "kill"): (Severity.CRITICAL, "signal.kill() может завершить worker."),
        ("signal", "raise_signal"): (Severity.CRITICAL, "signal.raise_signal() может завершить worker."),
        ("multiprocessing", "Process"): (Severity.HIGH, "Создание дочернего процесса из custom-модуля запрещено."),
        ("atexit", "register"): (Severity.MEDIUM, "Регистрация shutdown hook усложняет безопасную выгрузку модуля."),
        ("os", "popen"): (Severity.CRITICAL, "os.popen() может выполнить shell-команду."),
        ("subprocess", "run"): (Severity.CRITICAL, "subprocess.run() запускает внешний процесс."),
        ("subprocess", "Popen"): (Severity.CRITICAL, "subprocess.Popen() запускает внешний процесс."),
        ("subprocess", "call"): (Severity.CRITICAL, "subprocess.call() запускает внешний процесс."),
        ("subprocess", "check_call"): (Severity.CRITICAL, "subprocess.check_call() запускает внешний процесс."),
        ("subprocess", "check_output"): (Severity.CRITICAL, "subprocess.check_output() запускает внешний процесс."),
        ("shutil", "rmtree"): (Severity.CRITICAL, "shutil.rmtree() удаляет дерево файлов."),
        ("builtins", "eval"): (Severity.CRITICAL, "eval() выполняет динамический Python-код."),
        ("builtins", "exec"): (Severity.CRITICAL, "exec() выполняет динамический Python-код."),
        ("builtins", "__import__"): (Severity.CRITICAL, "__import__() выполняет динамический импорт."),
        ("builtins", "exit"): (Severity.CRITICAL, "exit() может завершить worker."),
        ("builtins", "quit"): (Severity.CRITICAL, "quit() может завершить worker."),
    }

    NETWORK_FUNCS = {
        "requests.get",
        "requests.post",
        "requests.put",
        "requests.patch",
        "requests.delete",
        "requests.request",
        "requests.Session.get",
        "requests.Session.post",
        "aiohttp.ClientSession",
        "urllib.request.urlopen",
        "urllib.request.Request",
    }

    WEBHOOK_RE = re.compile(
        r"https?://(?:"
        r"(?:discord(?:app)?\.com/api/webhooks/)"
        r"|(?:api\.telegram\.org/bot[^/\s]+/)"
        r"|(?:pastebin\.com/(?:api|raw)/)"
        r")",
        re.IGNORECASE,
    )

    SENSITIVE_PATH_RE = re.compile(
        r"(?:^|[/\\])(?:\.env|config\.py|[^/\\\s]+\.session)$",
        re.IGNORECASE,
    )

    SENSITIVE_TOKEN_RE = re.compile(
        r"\b(?:API_ID|API_HASH|STRING_SESSION|SESSION_STRING)\b",
        re.IGNORECASE,
    )

    IMPORT_ALIASES = {
        "requests": "requests",
        "aiohttp": "aiohttp",
        "urllib": "urllib",
        "subprocess": "subprocess",
        "os": "os",
        "shutil": "shutil",
    }

    def __init__(self, source: str, filename: str):
        self.source = source
        self.filename = filename
        self.lines = source.splitlines()
        self.findings: list[Finding] = []
        self.imported_names: set[str] = set()
        self.network_nodes: list[ast.Call] = []
        self.sensitive_nodes: list[ast.AST] = []

    @classmethod
    def scan(cls, source: str, filename: str = "<module>") -> ScanReport:
        try:
            tree = ast.parse(source, filename=filename)
        except SyntaxError as exc:
            line = exc.lineno or 1
            code = cls._snippet_from_source(source, line)
            return ScanReport(
                filename=filename,
                findings=[
                    Finding(
                        Severity.CRITICAL,
                        "Синтаксическая ошибка",
                        f"{exc.msg} (строка {line})",
                        line,
                        code,
                    )
                ],
            )

        scanner = cls(source, filename)
        scanner.visit(tree)
        scanner._post_process()
        return ScanReport(filename=filename, findings=scanner.findings)

    @staticmethod
    def _snippet_from_source(source: str, lineno: int) -> str:
        lines = source.splitlines()
        if 1 <= lineno <= len(lines):
            return lines[lineno - 1].strip()
        return "<строка недоступна>"

    def _snippet(self, node: ast.AST) -> str:
        lineno = getattr(node, "lineno", 1)
        return self._snippet_from_source(self.source, lineno)[:180]

    @staticmethod
    def _call_name(node: ast.Call) -> str:
        fn = node.func
        if isinstance(fn, ast.Name):
            return fn.id
        if isinstance(fn, ast.Attribute):
            parts: list[str] = []
            current: ast.AST | None = fn
            while isinstance(current, ast.Attribute):
                parts.append(current.attr)
                current = current.value
            if isinstance(current, ast.Name):
                parts.append(current.id)
            return ".".join(reversed(parts))
        return ""

    @staticmethod
    def _literal_strings(node: ast.AST) -> Iterable[str]:
        for child in ast.walk(node):
            if isinstance(child, ast.Constant) and isinstance(child.value, str):
                yield child.value

    def _add(
        self,
        severity: Severity,
        title: str,
        detail: str,
        node: ast.AST,
    ) -> None:
        self.findings.append(
            Finding(
                severity=severity,
                title=title,
                detail=detail,
                lineno=getattr(node, "lineno", 1),
                code=self._snippet(node),
            )
        )

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            root = alias.name.split(".", 1)[0]
            self.imported_names.add(root)
            if root == "subprocess":
                self._add(
                    Severity.CRITICAL,
                    "Опасный импорт",
                    "Модуль импортирует subprocess.",
                    node,
                )
            elif root == "ctypes":
                self._add(
                    Severity.HIGH,
                    "Опасный импорт",
                    "Модуль импортирует ctypes.",
                    node,
                )
            elif root in {"requests", "aiohttp", "urllib"}:
                self._add(
                    Severity.LOW,
                    "Сетевой клиент",
                    f"Обнаружен импорт {root}; это не означает вредоносность, "
                    "но сетевые операции будут дополнительно проверены.",
                    node,
                )
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        root = (node.module or "").split(".", 1)[0]
        if root:
            self.imported_names.add(root)
        if root == "subprocess":
            self._add(
                Severity.CRITICAL,
                "Опасный импорт",
                "Модуль импортирует subprocess.",
                node,
            )
        elif root == "ctypes":
            self._add(
                Severity.HIGH,
                "Опасный импорт",
                "Модуль импортирует ctypes.",
                node,
            )
        elif root in {"requests", "aiohttp", "urllib"}:
            self._add(
                Severity.LOW,
                "Сетевой клиент",
                f"Обнаружен импорт {root}; сетевые операции будут дополнительно проверены.",
                node,
            )
        self.generic_visit(node)

    def visit_Raise(self, node: ast.Raise) -> None:
        if node.exc is not None:
            text = ast.unparse(node.exc) if hasattr(ast, "unparse") else ""
            if "SystemExit" in text or "KeyboardInterrupt" in text:
                self._add(Severity.CRITICAL, "Process-level exception", "Модуль явно поднимает исключение, способное остановить worker.", node)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        name = self._call_name(node)

        if name in {"eval", "exec", "__import__", "exit", "quit"}:
            fake_name = f"builtins.{name}"
            severity, detail = self.DANGER_CALLS[("builtins", name)]
            self._add(severity, "Опасный вызов", detail, node)

        else:
            parts = name.split(".")
            if len(parts) >= 2:
                key = (parts[-2], parts[-1])
                if key in self.DANGER_CALLS:
                    severity, detail = self.DANGER_CALLS[key]
                    self._add(severity, "Опасный вызов", detail, node)

        if name in self.NETWORK_FUNCS or (
            any(name.startswith(prefix) for prefix in ("requests.", "aiohttp.", "urllib."))
        ):
            self.network_nodes.append(node)

            for literal in self._literal_strings(node):
                if self.WEBHOOK_RE.search(literal):
                    self._add(
                        Severity.CRITICAL,
                        "Webhook / внешняя отправка",
                        "Найден внешний Discord/Telegram/Pastebin endpoint в сетевом вызове.",
                        node,
                    )
                elif re.match(r"https?://", literal, re.IGNORECASE):
                    self._add(
                        Severity.MEDIUM,
                        "Внешний HTTP endpoint",
                        "Модуль выполняет сетевой запрос по URL. Проверь назначение endpoint.",
                        node,
                    )

        if name in {"open", "pathlib.Path.open", "Path.open"}:
            for literal in self._literal_strings(node):
                if self.SENSITIVE_PATH_RE.search(literal):
                    self.sensitive_nodes.append(node)
                    self._add(
                        Severity.CRITICAL,
                        "Чтение чувствительного файла",
                        f"Обнаружена попытка открыть чувствительный путь: {literal}",
                        node,
                    )

        if name in {"os.getenv", "getenv"}:
            for literal in self._literal_strings(node):
                if self.SENSITIVE_TOKEN_RE.fullmatch(literal.strip()):
                    self.sensitive_nodes.append(node)
                    self._add(
                        Severity.HIGH,
                        "Доступ к секретной переменной",
                        f"Модуль читает переменную {literal.strip()}.",
                        node,
                    )

        # .read_text(), .read_bytes() with a literal sensitive path:
        if name.endswith((".read_text", ".read_bytes")):
            for literal in self._literal_strings(node):
                if self.SENSITIVE_PATH_RE.search(literal):
                    self._add(
                        Severity.CRITICAL,
                        "Чтение чувствительного файла",
                        f"Обнаружено чтение чувствительного пути: {literal}",
                        node,
                    )

        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant) -> None:
        if isinstance(node.value, str):
            value = node.value
            if self.WEBHOOK_RE.search(value):
                self._add(
                    Severity.HIGH,
                    "Подозрительный webhook URL",
                    "В коде присутствует внешний webhook/API endpoint.",
                    node,
                )
            if self.SENSITIVE_PATH_RE.search(value):
                self._add(
                    Severity.MEDIUM,
                    "Чувствительный путь",
                    f"В коде встречается чувствительный путь: {value}",
                    node,
                )
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if self.SENSITIVE_TOKEN_RE.fullmatch(node.id):
            self.sensitive_nodes.append(node)
        self.generic_visit(node)

    def _post_process(self) -> None:
        # Heuristic exfiltration detector: sensitive symbols inside/near network calls.
        for call in self.network_nodes:
            blob = ast.unparse(call) if hasattr(ast, "unparse") else self._snippet(call)
            if self.SENSITIVE_TOKEN_RE.search(blob):
                self._add(
                    Severity.CRITICAL,
                    "Возможная утечка чувствительных данных",
                    "Секретная переменная используется внутри сетевого вызова.",
                    call,
                )

        # Direct module imports that are commonly used for file/network access.
        if "requests" in self.imported_names or "aiohttp" in self.imported_names:
            # Do not make a simple HTTP client import red; it is only informational.
            pass


def format_report(report: ScanReport, limit: int = 20) -> str:
    lines = [
        f"🛡 <b>Security Scan</b>: <code>{_escape(report.filename)}</code>",
        f"Вердикт: <b>{report.verdict}</b>",
        f"Эвристический рейтинг: <b>{report.score}/100</b>",
        f"Найдено: <b>{len(report.findings)}</b>",
    ]

    if not report.findings:
        lines.append("")
        lines.append("✅ Опасных паттернов по правилам сканера не найдено.")
        lines.append("ℹ️ Это статическая проверка, а не гарантия безопасности.")
        return "\n".join(lines)

    lines.append("")
    for index, finding in enumerate(report.findings[:limit], start=1):
        lines.extend(
            [
                f"<b>{index}. {finding.severity.name}</b> — {_escape(finding.title)}",
                f"Строка: <code>{finding.lineno}</code>",
                _escape(finding.detail),
                f"<code>{_escape(finding.code)}</code>",
                "",
            ]
        )

    if len(report.findings) > limit:
        lines.append(f"… и ещё {len(report.findings) - limit}.")

    if report.blocked:
        lines.append("⛔ <b>Загрузка заблокирована: обнаружена критическая угроза.</b>")
    else:
        lines.append("ℹ️ Сканер эвристический: отсутствие находок не доказывает безопасность.")

    return "\n".join(lines)


def _escape(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


class Module(BaseModule):
    """Provides the .scan command for AST inspection."""

    name = "Security"
    description = "Эвристический AST-анализ Python-модулей перед загрузкой."
    version = "1.0.0"
    category = "Security"

    @command("scan", aliases=("modscan", "checkmod"))
    async def scan(self, ctx: CommandContext) -> None:
        """Сканирует .py-файл из reply или локально загруженный модуль."""
        loader_module = self.loader.loaded.get("loader")
        loader = loader_module.instance if loader_module else None

        path = None
        filename = None

        if ctx.message.reply_to_message and ctx.message.reply_to_message.document:
            document = ctx.message.reply_to_message.document
            filename = document.file_name or "module.py"
            if not filename.lower().endswith(".py"):
                await ctx.message.reply_text("❌ Ответь .scan на .py файл.")
                return

            import tempfile
            tmp_dir = Path(self.loader.package)
            tmp_dir.mkdir(parents=True, exist_ok=True)
            path = Path(tempfile.mktemp(prefix=".scan_", suffix=".py", dir=tmp_dir))
            try:
                downloaded = await ctx.message.reply_to_message.download(file_name=str(path))
                if downloaded:
                    path = Path(downloaded)
                source = path.read_text(encoding="utf-8-sig", errors="replace")
            finally:
                path.unlink(missing_ok=True)

        elif ctx.raw_args.strip() and loader:
            raw_name = ctx.arg(0).lower().removesuffix(".py")
            existing = self.loader.loaded.get(raw_name)
            module_file = self.loader.module_file(raw_name)
            if existing or module_file:
                if module_file is None or not module_file.exists():
                    await ctx.message.reply_text("❌ Файл модуля не найден.")
                    return
                filename = module_file.name
                source = module_file.read_text(encoding="utf-8-sig", errors="replace")
            else:
                await ctx.message.reply_text("Использование: .scan в reply на .py файл или .scan module_name")
                return
        else:
            await ctx.message.reply_text("Использование: .scan в reply на .py файл или .scan module_name")
            return

        report = SecurityScanner.scan(source, filename or "module.py")
        await ctx.message.reply_text(format_report(report), quote=True)
