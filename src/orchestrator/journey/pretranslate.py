"""변경 피드로 데이터 문구를 영어로 미리 번역해 translations 컨테이너에 쌓는다.

문서가 쓰이면 허용 목록의 한국어 문구를 뽑아 가명처리하고(원천을 못 읽으면 그 owner는 건너뛴다), 기계 번역한 뒤
토큰이 그대로인 결과만 저장한다. 어떤 실패도 예외로 올리지 않고 개수로만 보고한다(변경 피드가 재시도로 멈추지 않게).
로그에는 개수만 남기고 문구(원문·마스킹)는 남기지 않는다.
"""

import logging
from collections.abc import Mapping, Sequence
from typing import Any

from agents.data_assembly import case_entities
from agents.pretranslate import (
    active_scope, batches, extract, load_case_raw, owner_of, tokens_kept, translation_doc, translation_id, translation_scope,
)
from journey.activities import Deps
from shared.pseudonymize import Pseudonymizer

log = logging.getLogger("journey.pretranslate")

TRANSLATIONS = "translations"
_COUNTS = ("owners", "texts", "translated", "skipped", "rejected", "failed", "input_tokens", "output_tokens")


def _by_owner(source: str, docs: Sequence[Mapping[str, Any]]) -> dict[str, list[Mapping[str, Any]]]:
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for doc in docs:
        if owner := owner_of(source, doc):
            grouped.setdefault(owner, []).append(doc)
    return grouped


def _owner(deps: Deps, source: str, owner: str, docs: Sequence[Mapping[str, Any]], report: dict[str, int], chars: list[int]) -> None:
    entries = [(doc, extract(source, doc)) for doc in docs]
    extracted = list(dict.fromkeys(text for _, texts in entries for text in texts))
    if not extracted:
        return
    try:
        def read(container: str, ident: str):
            return deps.store.read(container, ident, ident)

        parent = "applications" if source in ("applications", "grievances") else "loans"
        current = read(parent, owner)
        scope = translation_scope(parent, current or {})
        if source == parent:
            entries = [(d, texts) for d, texts in entries if translation_scope(parent, d) == scope]
        if source == "alerts":
            replay = ((current or {}).get("monitoring") or {}).get("replay_id")
            entries = [(d, texts) for d, texts in entries if replay and d.get("replay_id") == replay]
        eligible = list(dict.fromkeys(text for _, texts in entries for text in texts))
        stale = len(extracted) - len(eligible)
        report["texts"] += stale
        report["skipped"] += stale
        extracted = eligible
        if not extracted:
            return
        case = (load_case_raw(parent, current, deps.raw, read)
                if current and active_scope(scope, lambda cid: read("consents", cid)) else None)
        if case is None:
            report["texts"] += len(extracted)
            report["skipped"] += len(extracted)
            return
        masker = Pseudonymizer(deps.pseudonym_key, case_entities(*case))
    except Exception as e:  # noqa: BLE001 - 원천을 못 읽으면 평문 전송 대신 건너뛴다
        # 검증 오류 메시지에 원천 값(상호·대표자·적요)이 들어가므로 종류만 남긴다.
        log.error(
            "pretranslate raw load failed source=%s owner=%s texts=%d error=%s",
            source, owner, len(extracted), type(e).__name__,
        )
        report["texts"] += len(extracted)
        report["failed"] += len(extracted)
        return
    masked = list(dict.fromkeys(masker.mask(text) for text in extracted))
    report["texts"] += len(masked)
    chars[0] += sum(len(m) for m in masked)
    pending: list[str] = []
    for text in masked:
        try:
            exists = deps.store.read(TRANSLATIONS, translation_id(text), owner) is not None
        except Exception:  # noqa: BLE001
            log.warning("pretranslate store read failed source=%s owner=%s", source, owner)
            report["failed"] += 1
            continue
        if exists:
            report["skipped"] += 1
        else:
            pending.append(text)
    for batch in batches(pending):
        if not active_scope(scope, lambda cid: read("consents", cid)):
            report["skipped"] += len(batch)
            continue
        _translate_batch(deps, source, owner, batch, report)


def _translate_batch(deps: Deps, source: str, owner: str, batch: list[str], report: dict[str, int]) -> None:
    try:
        result = deps.translator.translate(batch)
        if len(result.translations) != len(batch):
            raise ValueError("translation count mismatch")
    except Exception as e:  # noqa: BLE001 - 한도·차단·오류는 그 배치만 건너뛴다(오류 본문은 남기지 않는다)
        log.warning("pretranslate batch failed source=%s owner=%s items=%d error=%s", source, owner, len(batch), type(e).__name__)
        report["failed"] += len(batch)
        return
    report["input_tokens"] += result.input_tokens
    report["output_tokens"] += result.output_tokens
    for masked, en in zip(batch, result.translations, strict=True):
        if not en or not tokens_kept(masked, en):
            report["rejected"] += 1
            continue
        try:
            deps.store.upsert(
                TRANSLATIONS, translation_doc(owner, source, masked, en, deps.translate_model, deps.clock())
            )
        except Exception:  # noqa: BLE001
            log.warning("pretranslate store write failed source=%s owner=%s", source, owner)
            report["failed"] += 1
        else:
            report["translated"] += 1


def handle_changes(deps: Deps, source: str, docs: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    """변경된 문서의 데이터 문구를 번역해 저장한다. 절대 예외를 던지지 않는다."""
    report = dict.fromkeys(_COUNTS, 0)
    if deps.translator is None:
        return report
    chars = [0]
    try:
        grouped = _by_owner(source, docs)
        report["owners"] = len(grouped)
        for owner, owner_docs in grouped.items():
            try:
                _owner(deps, source, owner, owner_docs, report, chars)
            except Exception as e:  # noqa: BLE001
                log.error("pretranslate owner failed source=%s owner=%s error=%s", source, owner, type(e).__name__)
    except Exception as e:  # noqa: BLE001
        log.error("pretranslate failed source=%s error=%s", source, type(e).__name__)
    log.info(
        "pretranslate source=%s owners=%d texts=%d chars=%d translated=%d skipped=%d rejected=%d failed=%d tokens=%d/%d",
        source, report["owners"], report["texts"], chars[0], report["translated"], report["skipped"],
        report["rejected"], report["failed"], report["input_tokens"], report["output_tokens"],
    )
    return report
