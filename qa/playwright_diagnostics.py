from __future__ import annotations

import ast
from contextlib import contextmanager
import linecache
import traceback
from types import TracebackType
from typing import Any, Callable, Iterator


def _safe_repr(value: Any, limit: int = 600) -> str:
    try:
        rendered = repr(value)
    except Exception as exc:  # pragma: no cover - defensive for unusual browser objects
        rendered = f"<repr failed: {type(exc).__name__}: {exc}>"
    if len(rendered) > limit:
        return f"{rendered[:limit]}…"
    return rendered


def _traceback_frames(exc: BaseException) -> list[TracebackType]:
    frames: list[TracebackType] = []
    current = exc.__traceback__
    while current is not None:
        frames.append(current)
        current = current.tb_next
    return frames


def _source_line(frame: TracebackType) -> str:
    return linecache.getline(frame.tb_frame.f_code.co_filename, frame.tb_lineno).strip()


def _diagnostic_frame(exc: BaseException) -> TracebackType | None:
    frames = _traceback_frames(exc)
    if not frames:
        return None

    assertion_frames = [frame for frame in frames if _source_line(frame).startswith("assert ")]
    if assertion_frames:
        return assertion_frames[-1]

    project_frames = [
        frame
        for frame in frames
        if "/qa/" in frame.tb_frame.f_code.co_filename or "/tests/" in frame.tb_frame.f_code.co_filename
    ]
    return project_frames[-1] if project_frames else frames[-1]


def _assertion_values(assertion: str, frame: TracebackType) -> dict[str, str]:
    if not assertion.startswith("assert "):
        return {}
    try:
        statement = ast.parse(assertion).body[0]
    except (SyntaxError, IndexError):
        return {}
    if not isinstance(statement, ast.Assert):
        return {}

    test = statement.test
    if isinstance(test, ast.Compare):
        nodes = [test.left, *test.comparators]
    elif isinstance(test, (ast.BoolOp,)):
        nodes = list(test.values)
    elif isinstance(test, ast.UnaryOp):
        nodes = [test.operand]
    else:
        nodes = [test]

    values: dict[str, str] = {}
    for node in nodes:
        expression = ast.get_source_segment(assertion, node) or ast.unparse(node)
        try:
            value = eval(
                compile(ast.Expression(node), frame.tb_frame.f_code.co_filename, "eval"),
                frame.tb_frame.f_globals,
                frame.tb_frame.f_locals,
            )
            values[expression] = _safe_repr(value)
        except Exception as exc:  # The traceback remains useful if a value cannot be re-read.
            values[expression] = f"<evaluation failed: {type(exc).__name__}: {exc}>"
    return values


def failure_details(exc: BaseException) -> dict[str, Any]:
    frame = _diagnostic_frame(exc)
    assertion = _source_line(frame) if frame else ""
    return {
        "type": type(exc).__name__,
        "message": str(exc),
        "file": frame.tb_frame.f_code.co_filename if frame else "",
        "line": frame.tb_lineno if frame else 0,
        "assertion": assertion,
        "values": _assertion_values(assertion, frame) if frame else {},
        "traceback": "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
    }


@contextmanager
def diagnostic_scope(close: Callable[[], Any]) -> Iterator[None]:
    try:
        yield
    except Exception as exc:
        exc.archbro_failure_details = failure_details(exc)
        raise
    finally:
        close()
