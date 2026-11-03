"""Agent wiring shared by the chat CLI and the answer-evaluation harness.

Both entry points build the agent the same way, so the evaluation measures the
agent users actually get. Credentials and identity are resolved by the caller;
this module only connects the parts.
"""

import os

from retail_agent.agent.orchestrator import AnalyticalAgent
from retail_agent.golden.repository import GoldenRepository
from retail_agent.golden.service import GoldenKnowledge
from retail_agent.llm.analyst import Analyst
from retail_agent.memory.repository import MemoryRepository
from retail_agent.memory.service import UserMemory
from retail_agent.reports.repository import ReportRepository
from retail_agent.reports.service import ReportLibrary


def golden_retrieval_enabled() -> bool:
    return os.getenv("GOLDEN_RETRIEVAL_ENABLED", "true").lower() == "true"


def build_agent(llm, schema, query_service, *, manager_id: str, entitlements, connect, embedder,
                golden: bool, memory: bool) -> AnalyticalAgent:
    """One agent per manager session; connect is a storage connection factory."""
    return AnalyticalAgent(
        Analyst(llm, schema), query_service,
        manager_id=manager_id, entitlements=entitlements,
        reports=ReportLibrary(ReportRepository(connect), embedder, manager_id=manager_id, entitlements=entitlements),
        memory=UserMemory(MemoryRepository(connect), embedder, manager_id=manager_id) if memory else None,
        # The agent reads the index with its own read-only grant; ingestion is a separate job.
        golden=GoldenKnowledge(GoldenRepository(connect), embedder) if golden else None,
    )
