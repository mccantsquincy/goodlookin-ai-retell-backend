"""Small grounded FAQ retriever for our fictional roofing demo.

This is retrieval, not generative RAG yet: an LLM in Retell may phrase the
retrieved approved answer, but it must not invent missing business facts.
"""

import re

from app.business import BusinessConfig

STOP = {"a", "an", "the", "is", "are", "do", "does", "i", "you", "we", "for", "of", "to", "with", "and", "what", "how"}


def tokens(value: str) -> set[str]:
    return {word for word in re.findall(r"[a-z0-9]+", value.casefold()) if word not in STOP}


def search_knowledge(config: BusinessConfig, question: str, limit: int = 3) -> dict:
    terms = tokens(question)
    candidates = []
    for index, faq in enumerate(config.approved_faqs):
        question_text = str(faq.get("question", ""))
        answer = str(faq.get("answer", ""))
        if not question_text or not answer:
            continue
        overlap = len(terms & tokens(question_text))
        if overlap:
            candidates.append((overlap, index, question_text, answer))
    candidates.sort(key=lambda item: (-item[0], item[1]))
    matches = [
        {"source_id": f"approved_faq:{index}", "question": question_text, "answer": answer}
        for _, index, question_text, answer in candidates[:limit]
    ]
    return {
        "found": bool(matches),
        "matches": matches,
        "instruction": (
            "Answer using only approved matches. If the question isn't answered by the sources, "
            "say you cannot confirm and offer an office callback."
        ),
    }


def service_area_check(config: BusinessConfig, state: str, postal_code: str, service_type: str | None) -> dict:
    """Business decisions belong to code, not the knowledge model."""
    normalized_state = state.strip().upper()
    zip5 = postal_code.strip()
    if not re.fullmatch(r"\d{5}", zip5):
        return {"status": "needs_review", "reason_code": "INVALID_POSTAL_CODE"}
    if not config.eligible_states or not config.eligible_postal_codes:
        return {"status": "needs_review", "reason_code": "POLICY_NOT_CONFIGURED"}
    if normalized_state not in {name.upper() for name in config.eligible_states}:
        return {"status": "outside_area", "reason_code": "STATE_OUTSIDE_CONFIGURED_AREA"}
    if zip5 not in config.eligible_postal_codes:
        return {"status": "outside_area", "reason_code": "POSTAL_CODE_OUTSIDE_CONFIGURED_AREA"}
    if not service_type:
        return {"status": "needs_review", "reason_code": "SERVICE_NOT_SPECIFIED"}
    if service_type.casefold().strip() not in {x.casefold() for x in config.eligible_services}:
        return {"status": "needs_review", "reason_code": "SERVICE_NOT_CONFIGURED"}
    return {"status": "in_area", "reason_code": "CONFIGURED_MATCH"}
