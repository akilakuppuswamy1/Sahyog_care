"""Hybrid retrieval with a hard patient filter (deck p.24, p.26, p.31).

The patient filter is a SQL WHERE clause on every candidate query, plus a final check in code. It is
never part of a prompt, and a model never supplies the patient id: it comes from the auth context.
  * Vector half : cosine on bge-m3 embeddings (pgvector).
  * Keyword half: Postgres full-text ('simple' config) over the query with abbreviations expanded,
    because "Hb kam hai kya" was the weakest case for embeddings (AI log, 2026-11-02).
  * Fusion      : reciprocal rank fusion (RRF), k = 60.
Chunks flagged `injection` are excluded from what a model may read.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from services.records.embed import Embedder, to_pgvector

# Patient words -> report words. Lower-case; applied token by token. Extend from eval misses.
ABBREVIATIONS = {
    "hb": ["haemoglobin", "hemoglobin"], "hgb": ["haemoglobin"], "haemoglobin": ["hb"], "hemoglobin": ["haemoglobin", "hb"],
    "wbc": ["wbc", "white"], "rbc": ["red"], "plt": ["platelet"], "platelets": ["platelet"],
    "sugar": ["glucose", "hba1c"], "diabetes": ["hba1c", "glucose"], "a1c": ["hba1c"],
    "cholesterol": ["cholesterol", "ldl", "hdl"], "lipid": ["cholesterol", "triglycerides"],
    "thyroid": ["tsh", "t4"], "liver": ["alt", "ast", "bilirubin", "liver"], "sgpt": ["alt"], "sgot": ["ast"],
    "kidney": ["creatinine", "urea"], "vit": ["vitamin"], "xray": ["x-ray", "findings"],
    "x-ray": ["findings", "impression"], "knee": ["knee", "joint"], "ecg": ["sinus", "rhythm", "heart"],
    "heart": ["ecg", "sinus", "rhythm"], "ultrasound": ["liver", "echogenicity"], "sonography": ["liver"],
    "interval": ["reference"],
    # Hinglish
    "khoon": ["haemoglobin", "blood"], "sharkar": ["glucose"], "shakkar": ["glucose"], "cheeni": ["glucose"],
    "ghutna": ["knee"], "ghutne": ["knee"], "jigar": ["liver"], "dil": ["heart", "ecg"],
}
STOP = {"is", "my", "the", "a", "an", "of", "in", "what", "was", "are", "do", "does", "i", "me", "kya", "hai",
        "mera", "meri", "mere", "kam", "zyada", "ka", "ki", "ke", "how", "and", "or", "to", "it", "report", "test",
        "kitna", "kitni", "kitne", "aaya", "aayi", "batao", "bataiye", "mummy", "papa", "maa", "mother", "mothers",
        "aur",
        "father", "fathers", "her", "his", "level", "levels", "value", "values", "result", "results", "show",
        "shows", "say", "says", "tell", "normal", "okay", "ok", "low", "high", "please", "can", "you", "latest",
        "last", "nikla", "nikli", "nikle", "second", "2nd", "page", "first", "1st", "third", "3rd",
        "fourth", "4th", "fifth", "5th", "quote", "unit", "units", "reference", "cutoff", "without",
        "diagnosis"}
RRF_K = 60
PAGE_HINT = re.compile(
        r"\b(?:(first|1st|1|second|2nd|2|third|3rd|3|fourth|4th|4|fifth|5th|5)\s+page|"
        r"page\s+(first|1st|1|second|2nd|2|third|3rd|3|fourth|4th|4|fifth|5th|5))\b",
        re.I,
)
PAGE_NUMBERS = {
        "first": 1, "1st": 1, "1": 1, "second": 2, "2nd": 2, "2": 2,
        "third": 3, "3rd": 3, "3": 3, "fourth": 4, "4th": 4, "4": 4,
        "fifth": 5, "5th": 5, "5": 5,
}


@dataclass
class Hit:
    chunk_id: str
    doc_id: str
    patient_id: str
    page: int
    section: str
    text: str
    title: str | None
    report_date: str | None
    vscore: float
    kscore: float
    rrf: float


def _tokens(question: str) -> list[str]:
    q = re.sub(r"['’]s\b", "", question.lower())   # "mother's" -> "mother"
    return re.findall(r"[a-z0-9][a-z0-9-]*", q)


def page_hint(question: str) -> int | None:
    """Return a requested page number, if the question names one."""
    match = PAGE_HINT.search(question)
    if not match:
        return None
    return PAGE_NUMBERS[match.group(1) or match.group(2).lower()]


def term_groups(question: str) -> list[set[str]]:
    """One set per topic word: the word plus its report-side abbreviations."""
    groups: list[set[str]] = []
    for t in _tokens(question):
        if t in STOP:
            continue
        g = {re.sub(r"[^a-z0-9]", "", w) for w in [t, *ABBREVIATIONS.get(t, [])]}
        g.discard("")
        if g and g not in groups:
            groups.append(g)
    return groups


def coverage(question: str, text: str) -> float:
    """Share of the question's topic words that appear in the text (0.0 when it has none)."""
    groups = term_groups(question)
    if not groups:
        return 0.0
    low = text.lower()
    words = set(re.findall(r"[a-z0-9]+", low)) | {re.sub(r"[^a-z0-9]", "", w) for w in low.split()}
    return sum(1 for g in groups if g & words) / len(groups)


def query_terms(question: str) -> list[str]:
    toks = [t for t in _tokens(question) if t not in STOP]
    out: list[str] = []
    for t in toks:
        for w in [t, *ABBREVIATIONS.get(t, [])]:
            w = re.sub(r"[^a-z0-9]", "", w)
            if w and w not in out:
                out.append(w)
    return out


def tsquery(terms: list[str]) -> str:
    """OR of the terms: a Hinglish question shares few words with an English report, so AND would miss."""
    return " | ".join(terms)


def fuse(vector: list[tuple[str, float]], keyword: list[tuple[str, float]]) -> dict[str, float]:
    scores: dict[str, float] = {}
    for ranked in (vector, keyword):
        for rank, (cid, _) in enumerate(ranked, 1):
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (RRF_K + rank)
    return scores


class PostgresRetriever:
    def __init__(self, conn, embedder: Embedder):
        self.conn, self.embedder = conn, embedder

    def search(self, patient_id: str, question: str, k: int = 6, candidates: int = 20) -> list[Hit]:
        qv = to_pgvector(self.embedder.embed([question])[0])
        requested_page = page_hint(question)
        page_filter = " AND page = %s" if requested_page is not None else ""
        vec = self.conn.execute(
            f"""SELECT chunk_id, 1 - (embedding <=> %s::vector) FROM doc_chunk
                WHERE patient_id = %s AND embedding IS NOT NULL AND NOT ('injection' = ANY (flags)){page_filter}
                ORDER BY embedding <=> %s::vector LIMIT %s""",
            (qv, patient_id, *((requested_page,) if requested_page is not None else ()), qv, candidates)).fetchall()
        terms = query_terms(question)
        kw = self.conn.execute(
            f"""SELECT chunk_id, ts_rank(tsv, to_tsquery('simple', %s)) FROM doc_chunk
                WHERE patient_id = %s AND NOT ('injection' = ANY (flags)){page_filter}
                  AND tsv @@ to_tsquery('simple', %s)
                ORDER BY 2 DESC LIMIT %s""",
            (tsquery(terms), patient_id, *((requested_page,) if requested_page is not None else ()),
             tsquery(terms), candidates)).fetchall() if terms else []
        fused = fuse([(c, float(s)) for c, s in vec], [(c, float(s)) for c, s in kw])
        top = sorted(fused, key=fused.get, reverse=True)[:k]
        if not top:
            return []
        vmap, kmap = dict(vec), dict(kw)
        rows = self.conn.execute(
            """SELECT c.chunk_id, c.doc_id, c.patient_id, c.page, c.section, c.text, d.title, d.report_date::text
                 FROM doc_chunk c JOIN document d USING (doc_id)
                WHERE c.chunk_id = ANY (%s) AND c.patient_id = %s""", (top, patient_id)).fetchall()
        by_id = {r[0]: r for r in rows}
        hits = [Hit(*by_id[c], vscore=float(vmap.get(c, 0.0)), kscore=float(kmap.get(c, 0.0)), rrf=fused[c])
                for c in top if c in by_id]
        assert all(h.patient_id == patient_id for h in hits), "scope leak"   # second line behind the SQL filter
        return hits


class InMemoryRetriever:
    """Same contract as PostgresRetriever, over a list of chunks. Unit tests only."""
    def __init__(self, chunks: list[dict], embedder: Embedder):
        self.chunks, self.embedder = chunks, embedder
        self.vectors = embedder.embed([c["text"] for c in chunks]) if chunks else []

    def search(self, patient_id: str, question: str, k: int = 6, candidates: int = 20) -> list[Hit]:
        q = self.embedder.embed([question])[0]
        requested_page = page_hint(question)
        own = [(c, v) for c, v in zip(self.chunks, self.vectors)
               if c["patient_id"] == patient_id and "injection" not in c.get("flags", []) and
               (requested_page is None or c["page"] == requested_page)]
        vec = sorted(((c["chunk_id"], sum(a * b for a, b in zip(q, v))) for c, v in own), key=lambda x: -x[1])[:candidates]
        terms = set(query_terms(question))
        kw = [(c["chunk_id"], float(len(terms & set(re.findall(r"[a-z0-9]+", c["text"].lower())))))
              for c, _ in own]
        kw = sorted([x for x in kw if x[1] > 0], key=lambda x: -x[1])[:candidates]
        fused = fuse(vec, kw)
        vmap, kmap, by_id = dict(vec), dict(kw), {c["chunk_id"]: c for c, _ in own}
        top = sorted(fused, key=fused.get, reverse=True)[:k]
        return [Hit(c, by_id[c]["doc_id"], by_id[c]["patient_id"], by_id[c]["page"], by_id[c]["section"],
                    by_id[c]["text"], by_id[c].get("title"), by_id[c].get("report_date"),
                    vmap.get(c, 0.0), kmap.get(c, 0.0), fused[c]) for c in top]
