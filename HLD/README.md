# Documentation map

Keep these three levels separate in the final submission:

1. **Implemented prototype:** [../README.md](../README.md) for setup/demo and
   [QUERY_SAFETY.md](QUERY_SAFETY.md) for the current two-tool security path.
2. **Confirmed requirements:** [STAKEHOLDER_POLICY.md](STAKEHOLDER_POLICY.md),
   [task.txt](task.txt). These describe client policy, not our SQL limitations.
3. **Production target:** the Mermaid diagrams are proposals. The final technical
   explanation must justify identity/entitlements, database permissions, compute,
   storage, knowledge retrieval/update, deletion, preferences, persona changes,
   resilience, evaluation and PII-safe observability. Do not mark planned services
   or controls as implemented.

## Historical reference

[archive/sql-demographic-validator-v1.md](archive/sql-demographic-validator-v1.md)
preserves the old aggregate-policy and SQL-validator source plus the former
execution/result contract. It explains why we moved from checking model-authored
demographic SQL to generating that SQL from typed inputs.

The old implementation is an explored alternative, **not automatically the ideal
production design**. The production HLD should explain tradeoffs and stronger
data-layer enforcement rather than presenting a handwritten parser as a formal
security boundary. HANDOFF.md is an older conversation snapshot; follow its
update pointers and inspect current code before relying on module-status notes.
