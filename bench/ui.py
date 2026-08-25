"""Benchmark presentation + logging.

Two separate concerns, each with the right tool:
  - PRESENTATION (results): rich -- live progress, aligned/colored tables, summaries.
    This is a dashboard, not a log; logging is the wrong tool for it.
  - DIAGNOSTICS (events): stdlib logging -- leveled, timestamped phase/warn/error lines,
    to the console (via rich's handler) AND a persistent run.log file.

Result rows still go to the markdown FILE (machine-readable) in parallel with the
rich table on screen. rich degrades cleanly when stdout is not a TTY.
"""
import logging
from rich.console import Console
from rich.table import Table as _RichTable
from rich.logging import RichHandler

console = Console()
log = logging.getLogger("bench")


def setup_logging(logfile=None, level=logging.INFO):
    """Console diagnostics via rich; full trail to logfile if given. Call once per run."""
    log.setLevel(level)
    log.handlers.clear()
    ch = RichHandler(console=console, show_time=True, show_path=False, markup=True,
                     rich_tracebacks=True)
    ch.setLevel(level)
    log.addHandler(ch)
    if logfile:
        fh = logging.FileHandler(logfile)
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)-5s %(message)s"))
        log.addHandler(fh)
    return log


def phase(title):
    """A visible section header for a benchmark phase (also logged)."""
    console.rule(f"[bold blue]{title}", align="left")
    log.debug("phase: %s", title)


class Table:
    """A rich results table that also appends markdown rows to a file.
    Buffers rows and re-renders live so columns stay aligned as data arrives."""

    def __init__(self, title, headers, mdfile=None, justify=None):
        self.title = title
        self.headers = headers
        self.md = mdfile
        self.justify = justify or (["right"] * len(headers))
        self.rows = []
        if self.md:
            self.md.write("| " + " | ".join(headers) + " |\n")
            self.md.write("|" + "|".join("---" for _ in headers) + "|\n")
            self.md.flush()

    def add(self, cells, style=None):
        self.rows.append(([str(c) for c in cells], style))
        if self.md:
            self.md.write("| " + " | ".join(str(c) for c in cells) + " |\n")
            self.md.flush()

    def render(self):
        t = _RichTable(title=self.title, title_justify="left", header_style="dim")
        for h, j in zip(self.headers, self.justify):
            t.add_column(h, justify=j)
        for cells, style in self.rows:
            t.add_row(*cells, style=style)
        console.print(t)


def status_text(ok):
    return "[green]pass[/]" if ok is True else ("[red]FAIL[/]" if ok is False else "[yellow]err[/]")


class ItemProgress:
    """Live per-item progress for a scored loop: shows [i/n], last result, running acc.
    Use as a context manager wrapping the loop; call .update(...) per item."""
    def __init__(self, label, n):
        self.label, self.n = label, n
        self._status = console.status(f"{label}: starting...")

    def __enter__(self):
        self._status.__enter__(); return self

    def __exit__(self, *exc):
        self._status.__exit__(*exc)

    def update(self, i, ok, running_acc, extra=""):
        self._status.update(
            f"{self.label}  [{i}/{self.n}] {status_text(ok)}  "
            f"acc [bold]{running_acc:.1f}%[/]  [dim]{extra}[/]")


def summary(title, passed, total, extra=""):
    color = "green" if passed == total else ("yellow" if passed else "red")
    console.print(f"[bold {color}]{title}: {passed}/{total}[/]  [dim]{extra}[/]")
    log.info("%s: %d/%d %s", title, passed, total, extra)
