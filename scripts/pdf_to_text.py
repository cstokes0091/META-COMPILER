#!/usr/bin/env python3
"""Extract page-delimited text from a PDF for ingest preprocessing.

Usage:
    python scripts/pdf_to_text.py <file_path> [--output <output_path>]

This is a thin wrapper around the shared PDF extraction logic in
`scripts/read_document.py`. It exists to give the ingest pipeline a stable,
PDF-specific entrypoint without duplicating parsing behavior.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from read_document import read_pdf  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Extract page-delimited text from a PDF for ingest preprocessing."
    )
    parser.add_argument("file", help="Path to the PDF file")
    parser.add_argument(
        "--output",
        "-o",
        default=None,
        help="Write extracted text to this file instead of stdout",
    )
    args = parser.parse_args(argv)

    file_path = Path(args.file)
    if not file_path.exists():
        print(f"ERROR: File not found: {file_path}", file=sys.stderr)
        return 1
    if file_path.suffix.lower() != ".pdf":
        print(f"ERROR: Expected a .pdf file, got: {file_path.suffix}", file=sys.stderr)
        return 1

    try:
        text = read_pdf(file_path)
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
        print(f"Extracted PDF text written to {args.output}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())