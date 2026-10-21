"""Extract resume PDF text into a markdown file the ranker reads.

Usage:
    python scripts/extract_resumes.py resume.pdf -o profiles/me.resume.md
    python scripts/extract_resumes.py "Resume=resume.pdf" "CV=cv.pdf" -o profiles/me.resume.md

Each PDF becomes one `## Heading` section (heading defaults to the file name).
NOTE: PDF extraction can glue words together; check the output for spacing. Re-running
this script OVERWRITES hand fixes in the output file.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from pypdf import PdfReader


def pdf_text(path: Path) -> str:
    return "\n".join((page.extract_text() or "") for page in PdfReader(path).pages).strip()


def parse_input(arg: str) -> tuple[str, Path]:
    if "=" in arg:
        heading, _, path = arg.partition("=")
        return heading.strip(), Path(path)
    return Path(arg).stem, Path(arg)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pdfs", nargs="+", help="PDF path, or Heading=path.pdf")
    ap.add_argument("-o", "--output", required=True, type=Path, help="markdown file to write")
    args = ap.parse_args(argv)
    parts = []
    for arg in args.pdfs:
        heading, path = parse_input(arg)
        parts.append(f"## {heading}\n\n{pdf_text(path)}\n")
    args.output.write_text("\n".join(parts), encoding="utf-8")
    print(f"wrote {args.output}")


if __name__ == "__main__":
    main()
