"""Local-only egress de-identification. Never serialize the returned token map."""
from __future__ import annotations

from functools import lru_cache
import re
from threading import RLock
import time

from presidio_analyzer import AnalyzerEngine, Pattern, PatternRecognizer, RecognizerRegistry
from presidio_analyzer.nlp_engine import NlpEngineProvider
from services.agent.w1.guards import has_identifier


PATTERNS = {
    "PHONE": r"(?<!\d)(?:\+?91[ -]?)?[6-9]\d{4}[ -]?\d{5}(?!\d)",
    "AADHAAR": r"(?<!\d)\d{4}[ -]?\d{4}[ -]?\d{4}(?!\d)",
    "ABHA": r"(?<!\d)(?:\d{14}|\d{2}-\d{4}-\d{4}-\d{4})(?!\d)",
    "ABHA_ADDRESS": r"\b[\w.+-]+@(?:abdm|sbx)\b",
    "PAN": r"\b[A-Z]{5}\d{4}[A-Z]\b",
    "PIN": r"\b[1-9]\d{5}\b",
    "PATIENT_ID": r"\b(?:PT-[A-Z0-9-]+|MRN[ :#-]*[A-Z0-9-]*\d[A-Z0-9-]*)\b",
    "EMAIL": r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b",
    "DOB": r"\b(?:\d{1,2}[/.-]\d{1,2}[/.-]\d{4}|\d{4}-\d{2}-\d{2}|\d{1,2}\s+(?i:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+\d{4})\b",
    "PERSON": r"(?i:\b(?:(?:patient(?: name)?|name)\s*[:=]|my name is|I am)\s*)([A-Z][a-z]+(?: [A-Z][a-z]+){1,3})",
}
_LOCK = RLock()


def verhoeff_valid(value: str) -> bool:
    digits = re.sub(r"\D", "", value)
    if len(digits) != 12 or digits[0] in "01":
        return False
    d = ((0,1,2,3,4,5,6,7,8,9),(1,2,3,4,0,6,7,8,9,5),(2,3,4,0,1,7,8,9,5,6),(3,4,0,1,2,8,9,5,6,7),(4,0,1,2,3,9,5,6,7,8),(5,9,8,7,6,0,4,3,2,1),(6,5,9,8,7,1,0,4,3,2),(7,6,5,9,8,2,1,0,4,3),(8,7,6,5,9,3,2,1,0,4),(9,8,7,6,5,4,3,2,1,0))
    p = ((0,1,2,3,4,5,6,7,8,9),(1,5,7,6,2,8,3,0,9,4),(5,8,0,3,7,9,6,1,4,2),(8,9,1,6,0,4,3,5,2,7),(9,4,5,3,1,2,6,8,7,0),(4,2,8,6,5,7,3,9,0,1),(2,7,9,3,8,0,6,4,1,5),(7,0,4,6,9,1,3,2,5,8))
    check = 0
    for i, digit in enumerate(reversed(digits)):
        check = d[check][p[i % 8][int(digit)]]
    return check == 0


class AadhaarRecognizer(PatternRecognizer):
    def validate_result(self, pattern_text):
        return verhoeff_valid(pattern_text)


@lru_cache(maxsize=1)
def analyzer() -> AnalyzerEngine:
    # Explicit local model: no runtime downloads and no remote NLP service.
    import spacy
    if not spacy.util.is_package("en_core_web_sm"):
        raise RuntimeError("Install the pinned local en_core_web_sm model before enabling egress")
    nlp = NlpEngineProvider(nlp_configuration={"nlp_engine_name": "spacy",
        "ner_model_configuration": {"labels_to_ignore": ["CARDINAL", "ORDINAL", "QUANTITY", "PERCENT", "MONEY", "PRODUCT", "WORK_OF_ART", "EVENT", "LAW", "LANGUAGE", "FAC"]}, "models": [
        {"lang_code": "en", "model_name": "en_core_web_sm"}]}).create_engine()
    # This boundary uses NER and explicit patterns, not POS, dependency parsing,
    # or lemma-based context boosting. Keep NER's representations intact.
    pipeline = nlp.nlp["en"]
    # en_core_web_sm's NER has its own embedded tok2vec; the separate pipeline
    # tok2vec feeds the disabled tagger/parser and would duplicate inference.
    for name in list(pipeline.pipe_names):
        if name != "ner":
            pipeline.disable_pipe(name)
    registry = RecognizerRegistry()
    registry.load_predefined_recognizers(nlp_engine=nlp)
    for kind, regex in PATTERNS.items():
        cls = AadhaarRecognizer if kind == "AADHAAR" else PatternRecognizer
        registry.add_recognizer(cls(supported_entity=kind, patterns=[Pattern(kind, regex, 0.85)],
                                    global_regex_flags=re.MULTILINE))
    return AnalyzerEngine(nlp_engine=nlp, registry=registry)


def deidentify(text: str, token_map: dict[str, str]) -> str:
    if not text:
        return text
    with _LOCK:
        # Use our local email pattern: Presidio's default email validator invokes
        # tldextract, whose default suffix-list refresh can use the network.
        results = analyzer().analyze(text=text, language="en", entities=list(PATTERNS), score_threshold=0.5)
    # Longest first resolves overlapping phone/PIN/ABHA and name detections.
    selected = []
    for result in sorted(results, key=lambda r: (-(r.end-r.start), -r.score, r.start)):
        if not any(result.start < end and result.end > start for start, end, _ in selected):
            kind = {"EMAIL_ADDRESS": "EMAIL", "PHONE_NUMBER": "PHONE"}.get(result.entity_type, result.entity_type)
            selected.append((result.start, result.end, kind))
    for start, end, kind in sorted(selected, reverse=True):
        value = text[start:end]
        token = next((k for k, v in token_map.items() if v == value), None)
        if token is None:
            token = f"<{kind}_{1 + sum(k.startswith('<' + kind + '_') for k in token_map)}>"
            token_map[token] = value
        text = text[:start] + token + text[end:]
    return text


def prepare_egress(system: str, user: str):
    started = time.perf_counter()
    mapping: dict[str, str] = {}
    system_out = deidentify(system, mapping)
    user_out = deidentify(user, mapping)
    if any(has_identifier(t) or any(re.search(p, t, re.I) for k, p in PATTERNS.items() if k != "PERSON")
           for t in (system_out, user_out)):
        from services.agent.llm import ModelError
        raise ModelError("identifier survived scrubbing; refusing to send")
    return system_out, user_out, mapping, (time.perf_counter() - started) * 1000


def reidentify(value, mapping):
    if isinstance(value, str):
        return re.sub(r"<[A-Z_]+_\d+>", lambda m: mapping.get(m.group(), m.group()), value)
    if isinstance(value, list):
        return [reidentify(item, mapping) for item in value]
    if isinstance(value, dict):
        return {reidentify(k, mapping): reidentify(v, mapping) for k, v in value.items()}
    return value
