# Task 2 — Full-request review: read, display and chat

- Base: `bb7ba8a`. Implementation commit: `7dd72a2d7a2a8050e39fad8bdaa6680a7195d14f`.
- The Korean/English detail, list, chart and operations views distinguish reviewed v2 recommendations from historical opinions and no opinion. Historical and unavailable opinions are not votes in current recommendation rates or officer-agreement comparisons. Replaying a saved legacy metric projection does not promote it to a reviewed vote.
- Detail shows the full-request conclusion, evidence and next checks. An otherwise eligible request above the cap explains why full approval is not possible. Eligibility by itself does not establish approval. Request, policy ceiling, evidence amounts and officer decisions remain separate.
- Historical prose, proposed structures, failed draft/error text and review comments stay in the underlying audit record. The display, chat snapshot and pretranslation paths do not publish them. Current text containing explicit counteroffers (even negated ones) is filtered. New grievance replies do not use historical counterfactuals; previously generated replies that could have incorporated them are kept as audit records, not replayed as advice.
- Chat distinguishes no opinion from a historical opinion, filters amount advice in questions/history and checks model answers before release. It permits standalone grounded monetary facts and otherwise falls back to a full-request explanation, including an over-cap reason or R108 next check where applicable. No changes to policy rules, officer decision validation, disbursement or repayment.
- Offline preview opinions validated against the shared contracts: four reviewed approvals, one deferral, one nonapproval and one historical conditional. The preview has English explanations and next checks without a translation service.

## Verification

Used `C:\Users\jungwoonlee\orca\projects\ama-agent-04\.venv\Scripts\python.exe` in the requested worktree. Red tests reproduced legacy/current mixing, rejected-draft exposure, ungrounded and indirect/negated monetary advice, a misrepresented policy cap and archived metric votes before their fixes.

- Impacted console, chat, underwriting, notices, pretranslation, grievance, operations and metrics suites: **881 passed**, one existing FastAPI/Starlette TestClient deprecation warning.
- Full `python -m pytest` on the committed SHA: **5,590 passed, 140 skipped, 1 failed**, one deprecation warning. The only failure is the existing README hub-link test below.
- Baseline documentation failure verified against `bb7ba8a`: `tests/docs/test_docs.py::test_readme_links_every_hub_doc`. The unchanged root README omits `docs/deck/capstone.script.md`, `docs/demo/demo-script.md`, `docs/demo/fallback.md`, and `docs/demo/rehearsal.md`; all four already existed at the base.

## Limits

Offline verification only: no Azure, live model calls, push, deployment or data migration. Monetary language guards are conservative; they can replace a legitimate free-form answer with the reviewed full-request explanation. They are not a formal proof about every possible natural-language paraphrase. The operational agreement metric compares decision direction, not officer-approved amount equality.

## Follow-up review

- **Reproduced:** Before the fix, 11 review cases failed: contradictory or invented opinions (including an indirect recommendation), swapped amount labels or currency, and an invalid v2 grievance assessment promoted by projection. Two further tests failed before server-rendered, field-specific KRW facts were introduced.
- **Chat:** The model now selects evidence paths, not publishable prose. In Korean and English the server renders the stored full-request opinion (or historical/no opinion) and binds each selected fact to its snapshot field, unit and label. A missing officer approval cannot acquire an amount from policy. Unknown or mixed prose/path output is discarded. This supersedes the first-pass free-text output guard; input guards still sanitize history and questions.
- **Grievances:** Classify the original before projection. Keep the unmodified snapshot under `record.assessment_audit` while exposing only the published assessment, a legacy marker or no assessment to the UI, reply writer and translation extractor. Existing records are not rewritten.
- **Verification:** 199 impacted tests passed. Full offline suite: **5,601 passed, 140 skipped, 1 failed**, with the same TestClient deprecation warning. The only failure is the previously confirmed baseline `test_readme_links_every_hub_doc` described above.
- **Limits:** No live deployments or model calls. The model may select irrelevant evidence; the app can show a verified conclusion without additional evidence rather than inventing an answer. Historical grievance snapshots already missing original fields cannot be reconstructed by this write-side fix.
