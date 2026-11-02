"""PDF -> pages -> sections -> chunks, with the two input guards W2 needs (deck p.26, p.31):

1. Identifiers never enter a chunk. The PATIENT DETAILS block is dropped, and any chunk in which the
   identifier patterns still find something is flagged `phi`, so it can be withheld or reviewed.
2. Uploaded text is scanned for injected instructions. A hit flags the chunk `injection`; flagged chunks
   are stored (the patient owns the document) but never sent to a model (deck p.34 red-team PDF).

Scanned PDFs: a page with no text layer is reported as `needs_ocr`. OCR itself (Tesseract) is not
wired at Core; the document is marked failed with a reason the patient can see (E3-1).
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

from services.agent.w1.guards import has_identifier

DROP_SECTIONS = {"PATIENT DETAILS"}
HEADER = re.compile(r"^[A-Z0-9][A-Z0-9 ()/&,.-]{2,60}$")
TITLE = re.compile(r"^SAHYOG DIAGNOSTICS\b")
FOOTER = re.compile(r"^Page \d+ of \d+$")
MAX_CHARS = 1200   # deck p.22: about 1,200 characters a chunk

INJECTION = re.compile(
    r"ignore\s+(all\s+|any\s+)?(previous|prior|above)\s+(instructions|rules)|\bsystem\s*:|you\s+are\s+now\b|"
    r"\badmin(istrator)?\s+mode\b|\bdeveloper\s+mode\b|disregard\s+(the|your)\s+(rules|instructions)|"
    r"(show|list|retrieve|reveal)\s+.{0,40}\b(all|every|other)\s+patients?\b|"
    r"\bPT-[A-Z0-9]+-\d{3,}\b",            # a patient id inside report text: never legitimate in a patient upload
    re.I,
)


@dataclass
class Chunk:
    chunk_id: str
    doc_id: str
    page: int
    section: str
    text: str
    flags: list[str] = field(default_factory=list)

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()


def read_pdf_pages(path: Path) -> list[str]:
    from pypdf import PdfReader
    return [(p.extract_text() or "") for p in PdfReader(str(path)).pages]


def sections(page_text: str) -> list[tuple[str, list[str]]]:
    out: list[tuple[str, list[str]]] = []
    current, body = "BODY", []
    for raw in page_text.splitlines():
        line = raw.strip()
        if not line or TITLE.match(line) or FOOTER.match(line):
            continue
        if HEADER.match(line) and not re.search(r"\d+\s*(g/dL|mg|%|U/L)", line):
            if body:
                out.append((current, body))
            current, body = line, []
        else:
            body.append(line)
    if body:
        out.append((current, body))
    return [(h, b) for h, b in out if h not in DROP_SECTIONS]


def chunk_document(doc_id: str, pages: list[str]) -> tuple[list[Chunk], list[int]]:
    """Return (chunks, pages_needing_ocr)."""
    chunks: list[Chunk] = []
    needs_ocr = [i for i, t in enumerate(pages, 1) if len(t.strip()) < 20]
    for pno, text in enumerate(pages, 1):
        if pno in needs_ocr:
            continue
        for sec, lines in sections(text):
            buf: list[str] = []
            for line in lines + [None]:
                if line is not None and len("\n".join(buf + [line])) <= MAX_CHARS:
                    buf.append(line)
                    continue
                if buf:
                    body = f"{sec}\n" + "\n".join(buf)
                    flags = []
                    if INJECTION.search(body):
                        flags.append("injection")
                    if has_identifier(body):
                        flags.append("phi")
                    chunks.append(Chunk(f"{doc_id}-p{pno}-c{len(chunks) + 1}", doc_id, pno, sec, body, flags))
                buf = [line] if line is not None else []
    return chunks, needs_ocr
