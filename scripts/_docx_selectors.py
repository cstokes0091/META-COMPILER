"""Selector parsing for `scripts/edit_document.py`.

Grammar:
    paragraph:N            -> paragraph index N (0-based)
    paragraph:N,run:M      -> run M inside paragraph N
    text:"..."             -> first paragraph whose text contains the substring
    xpath:...              -> XPath returning the first <w:p>; tied back to the
                              paragraph whose underlying element matches.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Selector:
    kind: str  # "paragraph" | "paragraph_run" | "text" | "xpath"
    paragraph_index: Optional[int] = None
    run_index: Optional[int] = None
    text_query: Optional[str] = None
    xpath_expr: Optional[str] = None


def parse_selector(expr: str) -> Selector:
    expr = expr.strip()
    if expr.startswith("text:"):
        rest = expr[len("text:") :].strip()
        if len(rest) >= 2 and rest.startswith('"') and rest.endswith('"'):
            rest = rest[1:-1]
        if not rest:
            raise ValueError(f"text selector requires a non-empty query: {expr!r}")
        return Selector(kind="text", text_query=rest)
    if expr.startswith("xpath:"):
        rest = expr[len("xpath:") :].strip()
        if not rest:
            raise ValueError(f"xpath selector requires an expression: {expr!r}")
        return Selector(kind="xpath", xpath_expr=rest)
    if expr.startswith("paragraph:"):
        body = expr[len("paragraph:") :].strip()
        run_idx: Optional[int] = None
        if "," in body:
            head, tail = body.split(",", 1)
            tail = tail.strip()
            if not tail.startswith("run:"):
                raise ValueError(f"unrecognized suffix in selector: {expr!r}")
            try:
                run_idx = int(tail[len("run:") :].strip())
            except ValueError as exc:
                raise ValueError(f"run index must be an integer: {expr!r}") from exc
            body = head.strip()
        try:
            para_idx = int(body)
        except ValueError as exc:
            raise ValueError(f"paragraph index must be an integer: {expr!r}") from exc
        return Selector(
            kind="paragraph_run" if run_idx is not None else "paragraph",
            paragraph_index=para_idx,
            run_index=run_idx,
        )
    raise ValueError(
        f"unrecognized selector: {expr!r} "
        '(expected paragraph:N, paragraph:N,run:M, text:"...", or xpath:...)'
    )


def resolve_selector(doc, selector: Selector):
    """Return (paragraph, run_or_none) for the resolved selector."""
    paragraphs = list(doc.paragraphs)
    if selector.kind in ("paragraph", "paragraph_run"):
        idx = selector.paragraph_index or 0
        if idx < 0 or idx >= len(paragraphs):
            raise IndexError(
                f"paragraph index {idx} out of range (0..{len(paragraphs) - 1})"
            )
        para = paragraphs[idx]
        if selector.kind == "paragraph_run":
            runs = list(para.runs)
            run_idx = selector.run_index or 0
            if run_idx < 0 or run_idx >= len(runs):
                raise IndexError(
                    f"run index {run_idx} out of range in paragraph {idx} "
                    f"(0..{len(runs) - 1})"
                )
            return para, runs[run_idx]
        return para, None
    if selector.kind == "text":
        for para in paragraphs:
            if selector.text_query in para.text:
                return para, None
        raise LookupError(f"no paragraph contains text: {selector.text_query!r}")
    if selector.kind == "xpath":
        from docx.oxml.ns import qn

        body = doc.element.body
        results = body.xpath(selector.xpath_expr)
        if not results:
            raise LookupError(f"xpath returned no results: {selector.xpath_expr!r}")
        target = results[0]
        if target.tag != qn("w:p"):
            raise TypeError(f"xpath must resolve to w:p, got {target.tag}")
        for para in paragraphs:
            if para._p is target:
                return para, None
        raise LookupError(
            f"xpath result not in document.paragraphs: {selector.xpath_expr!r}"
        )
    raise ValueError(f"unknown selector kind: {selector.kind}")
