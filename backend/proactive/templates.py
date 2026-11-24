"""Friday-authored text for the morning digest (Phase 12 D6).

Everything here is fixed Turkish wording filled with probe counts and D1
labels -- never connector, tool or model text. The template digest is the
fallback whenever the model step is unavailable, refused, or fails the
number check, so it must read well on its own.
"""

# The section headings and per-probe fact names, in D6 order.
SECTION_TITLES = {
    "approvals": "Onaylar",
    "calendar": "Takvim",
    "tasks": "Görevler",
    "sessions": "Kodlama oturumları",
    "harness": "Harness işleri",
    "learning": "Öğrenme kartı",
    "nodes": "Mac bağlantısı",
    "budgets": "Bütçeler",
    "n8n": "n8n sonuçları",
    "followups": "Takip mesajları",
    "skills": "Beceri önerileri",
}

PROBE_FACT_NAMES = {
    "approvals.pending": "Bekleyen onay",
    "memory.calendar_today": "Bugünkü takvim öğesi",
    "tasks.due_today": "Bugün biten görev",
    "tasks.overdue": "Geciken görev",
    "memory.sessions_waiting": "İzin bekleyen oturum",
    "harness.waiting_gates": "Onay kapısında bekleyen iş",
    "memory.learning_card": "Öğrenme önerisi",
    "nodes.link": "Bağlantı sorunu",
    "ai_core.budgets": "Sınırına yaklaşan bütçe",
}

# One sentence per probe: `{n}` is the count, `{labels}` the joined labels.
PROBE_SENTENCES = {
    "approvals.pending": "{n} onay seni bekliyor.",
    "memory.calendar_today": "Bugün takviminde {n} öğe var: {labels}.",
    "tasks.due_today": "{n} görevin bugün bitiyor: {labels}.",
    "tasks.overdue": "{n} görevin gecikmiş.",
    "memory.sessions_waiting": "{n} kodlama oturumu iznini bekliyor.",
    "harness.waiting_gates": "{n} harness işi onay kapısında.",
    "memory.learning_card": "Öğrenme kartında {n} öneri var.",
    "nodes.link": "Mac bağlantısında sorun var: {labels}.",
    "ai_core.budgets": "{n} bütçe sınırına yaklaştı.",
}
GENERIC_SENTENCE = "{name}: {n}."
OPENING = "Günaydın."
CLOSING = "Ayrıntılar Bugün sayfasında."
EMPTY = "Bugün için brifing yok."
LABELS_SHOWN = 3


def fact_name(probe: str) -> str:
    return PROBE_FACT_NAMES.get(probe, probe)


def section_title(section: str) -> str:
    return SECTION_TITLES.get(section, section)


def _sentence(entry) -> str:
    labels = ", ".join(item["label"] for item in entry["items"][:LABELS_SHOWN])
    template = PROBE_SENTENCES.get(entry["probe"])
    if template is None or ("{labels}" in template and not labels):
        return GENERIC_SENTENCE.format(name=fact_name(entry["probe"]), n=entry["count"])
    return template.format(n=entry["count"], labels=labels)


def render(sections, *, max_chars: int) -> str:
    """The template digest: an opening, one sentence per non-empty probe in
    section order, a closing; whole sentences only, within `max_chars`."""
    sentences = [_sentence(entry) for section in sections for entry in section["entries"]]
    if not sentences:
        return EMPTY
    text = OPENING
    for sentence in sentences:
        candidate = f"{text} {sentence}"
        if len(candidate) + len(CLOSING) + 1 > max_chars:
            break
        text = candidate
    closing = f"{text} {CLOSING}"
    return closing if len(closing) <= max_chars else text[:max_chars]
