"""lxml helpers for OOXML revision markup (`w:ins`, `w:del`) and comment-thread
plumbing that python-docx 1.2.0 does not expose.

Public API:
    next_revision_id(doc) -> int
    insert_tracked_text(paragraph, text, author, when=None) -> w:ins element
    delete_run_tracked(run, author, when=None) -> w:del element wrapping the run
    find_comment_runs(doc, comment_id) -> list[Run]
    iter_comment_anchors(doc) -> iter of (comment_id, [paragraph_index, ...])
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterator

from docx.oxml import OxmlElement
from docx.oxml.ns import qn


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def next_revision_id(doc) -> int:
    """Return next unused w:id across w:ins / w:del / w:moveFrom / w:moveTo."""
    body = doc.element.body
    ids: list[int] = []
    for tag in ("w:ins", "w:del", "w:moveFrom", "w:moveTo"):
        for elem in body.iter(qn(tag)):
            id_str = elem.get(qn("w:id"))
            if id_str is None:
                continue
            try:
                ids.append(int(id_str))
            except ValueError:
                continue
    return (max(ids) + 1) if ids else 1


def insert_tracked_text(paragraph, text: str, author: str, when: str | None = None):
    """Append a `<w:ins><w:r><w:t>text</w:t></w:r></w:ins>` to the paragraph.

    Returns the new w:ins element.
    """
    when = when or _now_iso()
    ins = OxmlElement("w:ins")
    ins.set(qn("w:id"), str(next_revision_id(paragraph.part.document)))
    ins.set(qn("w:author"), author)
    ins.set(qn("w:date"), when)
    run = OxmlElement("w:r")
    t = OxmlElement("w:t")
    t.text = text
    t.set(qn("xml:space"), "preserve")
    run.append(t)
    ins.append(run)
    paragraph._p.append(ins)
    return ins


def delete_run_tracked(run, author: str, when: str | None = None):
    """Wrap an existing run in `<w:del>`, converting `<w:t>` -> `<w:delText>`.

    Returns the new w:del element. Modifies the document tree in place.
    """
    when = when or _now_iso()
    run_elem = run._r
    parent = run_elem.getparent()
    index = parent.index(run_elem)
    parent.remove(run_elem)

    for t in list(run_elem.findall(qn("w:t"))):
        del_text = OxmlElement("w:delText")
        space = t.get(qn("xml:space"))
        if space is not None:
            del_text.set(qn("xml:space"), space)
        del_text.text = t.text or ""
        run_elem.replace(t, del_text)

    del_elem = OxmlElement("w:del")
    del_elem.set(qn("w:id"), str(next_revision_id(run.part.document)))
    del_elem.set(qn("w:author"), author)
    del_elem.set(qn("w:date"), when)
    del_elem.append(run_elem)
    parent.insert(index, del_elem)
    return del_elem


def iter_comment_anchors(doc) -> Iterator[tuple[int, list[int]]]:
    """Yield (comment_id, [paragraph_index, ...]) for every comment range.

    Comment range markers (`w:commentRangeStart` / `w:commentRangeEnd`) sit as
    siblings of `w:r` inside `w:p`, so we record the paragraph index where each
    marker appears and expand the inclusive range. Cross-paragraph comments
    therefore produce contiguous index lists.
    """
    paragraphs = list(doc.paragraphs)
    starts: dict[int, int] = {}
    ends: dict[int, int] = {}

    for para_idx, para in enumerate(paragraphs):
        for child in para._p.iter():
            tag = child.tag
            if tag == qn("w:commentRangeStart"):
                cid_str = child.get(qn("w:id"))
                if cid_str is None:
                    continue
                try:
                    starts[int(cid_str)] = para_idx
                except ValueError:
                    continue
            elif tag == qn("w:commentRangeEnd"):
                cid_str = child.get(qn("w:id"))
                if cid_str is None:
                    continue
                try:
                    ends[int(cid_str)] = para_idx
                except ValueError:
                    continue

    for cid in sorted(starts.keys() | ends.keys()):
        s = starts.get(cid, ends.get(cid))
        e = ends.get(cid, starts.get(cid))
        if s is None or e is None:
            continue
        yield cid, list(range(s, e + 1))


def find_comment_runs(doc, comment_id: int):
    """Return the list of python-docx Run objects between the
    `<w:commentRangeStart>` and `<w:commentRangeEnd>` markers for `comment_id`.
    """
    from docx.text.run import Run

    body = doc.element.body
    started = False
    runs: list = []
    paragraphs = list(doc.paragraphs)
    para_for_run: dict = {}
    for para in paragraphs:
        for r in para.runs:
            para_for_run[r._r] = para

    for elem in body.iter():
        tag = elem.tag
        if tag == qn("w:commentRangeStart"):
            cid = elem.get(qn("w:id"))
            if cid is not None and int(cid) == comment_id:
                started = True
        elif tag == qn("w:commentRangeEnd"):
            cid = elem.get(qn("w:id"))
            if cid is not None and int(cid) == comment_id and started:
                return runs
        elif started and tag == qn("w:r"):
            parent_para = para_for_run.get(elem)
            if parent_para is not None:
                runs.append(Run(elem, parent_para))
    return runs
