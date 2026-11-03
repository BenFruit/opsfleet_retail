# Design and implementation map

Read [HANDOFF.md](HANDOFF.md) for current status and continuation, then
[PRODUCTION_HLD.md](PRODUCTION_HLD.md) for the complete proposed production design.
Root [AGENTS.md](../AGENTS.md) contains repository working instructions;
[README.md](../README.md) contains setup and CLI usage.

## Requirements and current implementation

- [task.txt](task.txt): original assignment; its illustrative pip command is not this repository's setup.
- [STAKEHOLDER_POLICY.md](STAKEHOLDER_POLICY.md): confirmed privacy/access clarifications.
- [QUERY_SAFETY.md](QUERY_SAFETY.md): unified SQL, supported subset, contributor checks and repair budgets.
- [SAVED_REPORTS.md](SAVED_REPORTS.md): snapshot storage, search and strict deletion confirmation.
- [USER_MEMORY.md](USER_MEMORY.md): explicit persistence, embeddings, known failures and future retrieval.
- [GOLDEN_KNOWLEDGE.md](GOLDEN_KNOWLEDGE.md): trio format, ingestion/update loop, retrieval and calibration.
- [EVALUATION.md](EVALUATION.md): answer/retrieval evaluation, A/B results, findings and release gates.
- [PERSONA_MANAGEMENT.md](PERSONA_MANAGEMENT.md): Google Doc MVP and proposed admin workflow.
- [OBSERVABILITY_AND_ACCESS.md](OBSERVABILITY_AND_ACCESS.md): local tracing and deferred privacy/access work.

## Production proposals — not implemented

[PRODUCTION_HLD.md](PRODUCTION_HLD.md) covers Golden Knowledge, reviewed system
learning, evaluation/UX, services, security, reliability and rollout.
[REQUEST_SAFETY_CLASSIFIER.md](REQUEST_SAFETY_CLASSIFIER.md) expands the optional
teacher-labeled/distilled classifier proposal; it is not an authorization boundary.

## Diagrams

Open `.mmd` files in a Mermaid-compatible viewer:

- [architecture-overview.mmd](architecture-overview.mmd): implemented local system.
- [agent-loop.mmd](agent-loop.mmd): implemented bounded planning loop.
- [report-deletion.mmd](report-deletion.mmd): implemented destructive-operation flow.
- [security-pii.mmd](security-pii.mmd): implemented SQL/result boundaries and residual gaps.
- [production-hld.mmd](production-hld.mmd): proposed production services.

## Historical alternative

[archive/sql-demographic-validator-v1.md](archive/sql-demographic-validator-v1.md)
preserves an earlier validator. A later typed demographic builder was also tried
and removed. Current code uses unified SQL with structural demographic checks;
neither historical approach is the current contract or a formal privacy proof.
