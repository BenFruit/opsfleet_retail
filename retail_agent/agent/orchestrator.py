import json
import logging
from time import monotonic
from uuid import uuid4

from langgraph.graph import END, START, StateGraph
from langsmith import tracing_context

from retail_agent.agent.models import AgentLimits, AgentState, TurnResult
from retail_agent.data.query_service import QueryService, QueryServiceError
from retail_agent.llm.analyst import Analyst
from retail_agent.llm.client import describe_model_error
from retail_agent.safety.result_guard import ResultGuardError, check_text, make_observation, remove_markdown_tables, render_observation
from retail_agent.security.access import get_allowed_brands


logger = logging.getLogger(__name__)


class AnalyticalAgent:
    """One trusted manager/session per instance; memory is bounded and in-process."""

    def __init__(
        self, analyst: Analyst, query_service: QueryService, *, manager_id: str,
        limits: AgentLimits | None = None,
    ):
        self.allowed_brands = sorted(get_allowed_brands(manager_id))
        self.authorized_brand_count = len(self.allowed_brands)
        self.analyst = analyst
        self.query_service = query_service
        self.manager_id = manager_id
        self.limits = limits if limits is not None else AgentLimits()
        self.history: list[dict] = []
        self.first_question: str | None = None
        self.history_truncated = False

        graph = StateGraph(AgentState)
        graph.add_node("plan", self._plan)
        graph.add_node("query", self._query)
        graph.add_edge(START, "plan")
        graph.add_conditional_edges("plan", self._after_plan, {"query": "query", "end": END})
        graph.add_conditional_edges("query", lambda state: "end" if state["answer"] else "plan", {
            "plan": "plan", "end": END,
        })
        self.graph = graph.compile()  # No persistence/checkpointer or external callbacks.

    def reset(self) -> None:
        self.history.clear()
        self.first_question = None
        self.history_truncated = False

    def ask(self, question: str) -> TurnResult:
        if not isinstance(question, str) or not question.strip():
            return TurnResult("Please enter an analysis question.", 0, 0, 0)
        if len(question) > self.limits.max_input_chars:
            return TurnResult("The question is too long. Please shorten it.", 0, 0, 0)
        try:
            check_text(question)
        except ResultGuardError:
            return TurnResult("Please remove personal contact details before sending the question.", 0, 0, 0)
        if self.first_question is None:
            self.first_question = question

        state: AgentState = {
            "question": question, "history": list(self.history), "observations": [],
            "action": None, "answer": "", "queries": 0, "model_calls": 0,
            "consecutive_errors": 0, "scan_bytes_used": 0, "turn_id": uuid4().hex,
        }
        started = monotonic()
        try:
            # LangGraph can otherwise trace full state if LANGSMITH_TRACING is set.
            # Disable implicit external tracing until a sanitized exporter exists.
            with tracing_context(enabled=False):
                result = self.graph.invoke(state, config={
                    "recursion_limit": 2 * self.limits.max_model_calls + 5,
                    "callbacks": [],
                })
        except Exception:
            logger.warning("agent_turn_failed", extra={"turn_id": state["turn_id"], "error_code": "graph_failed"})
            return TurnResult("Analysis could not be completed. Please try again.", 0, 0, 0)

        turn = {
            "question": question, "answer": result["answer"][:8000],
            "observations": result["observations"][-2:],
        }
        while turn["observations"] and len(json.dumps(turn, ensure_ascii=False)) > self.limits.max_history_chars:
            turn["observations"].pop(0)
        self.history.append(turn)
        while self.history and (
            len(self.history) > self.limits.max_history_turns
            or len(json.dumps(self.history, ensure_ascii=False)) > self.limits.max_history_chars
        ):
            self.history.pop(0)
            self.history_truncated = True
        logger.info("agent_turn_completed", extra={
            "turn_id": state["turn_id"], "query_count": result["queries"],
            "model_calls": result["model_calls"], "scan_bytes_used": result["scan_bytes_used"],
            "duration_ms": round((monotonic() - started) * 1000),
        })
        return TurnResult(result["answer"], result["queries"], result["model_calls"], result["scan_bytes_used"])

    def _plan(self, state: AgentState) -> dict:
        if state["model_calls"] >= self.limits.max_model_calls:
            return {"answer": self._partial(state, "The model-step budget was exhausted.")}
        started = monotonic()
        try:
            action = self.analyst.decide(
                question=state["question"], history=state["history"],
                observations=state["observations"],
                access_context={"authorized_brands": self.allowed_brands},
                session_context={
                    "first_question": self.first_question,
                    "history_truncated": self.history_truncated,
                },
                budget={
                    "sql_attempts": self.limits.max_queries - state["queries"],
                    "scan_bytes": max(0, self.limits.max_scan_bytes - state["scan_bytes_used"]),
                },
            )
            if action.action == "answer":
                check_text(action.answer)
            logger.info("agent_model_completed", extra={
                "turn_id": state["turn_id"], "action": action.action,
                "duration_ms": round((monotonic() - started) * 1000),
                **self.analyst.llm.last_usage,
            })
        except Exception as error:
            if isinstance(error, ResultGuardError):
                code, message, metadata = (
                    "model_output_withheld", "The model response was withheld by the privacy guard.",
                    {"error_type": "ResultGuardError"},
                )
            else:
                code, message, metadata = describe_model_error(error)
            logger.warning("agent_model_failed", extra={
                "turn_id": state["turn_id"], "error_code": code,
                "duration_ms": round((monotonic() - started) * 1000), **metadata,
            })
            return {
                "model_calls": state["model_calls"] + 1,
                "answer": self._partial(state, f"[{code}] {message}"),
            }
        update = {"action": action, "model_calls": state["model_calls"] + 1}
        if action.action == "answer":
            checked = [item for item in state["observations"] if item["status"] in {"ok", "empty"}]
            if state["observations"] and not checked:
                # An answer following only failures cannot pretend to deliver
                # fresh results (including "I will fetch it" placeholder answers).
                update["answer"] = self._partial(state, "No new query completed successfully.")
            else:
                update["answer"] = action.answer
                if checked:
                    update["answer"] = remove_markdown_tables(action.answer) or "Here are the checked results."
                    update["answer"] += (
                        f"\n\nScope: authorized brands only (configured: {self.authorized_brand_count})."
                        "\n\nChecked query results:\n" + "\n\n".join(
                            f"Result {index}:\n{render_observation(item)}"
                            for index, item in enumerate(checked, start=1)
                        )
                    )
        elif state["queries"] >= self.limits.max_queries or state["scan_bytes_used"] >= self.limits.max_scan_bytes:
            update["answer"] = self._partial(state, "The query budget was exhausted.")
        return update

    @staticmethod
    def _after_plan(state: AgentState) -> str:
        return "end" if state["answer"] else "query"

    def _query(self, state: AgentState) -> dict:
        action = state["action"]
        remaining = self.limits.max_scan_bytes - state["scan_bytes_used"]
        # Reserve the full per-query ceiling when an execution failure leaves
        # actual billing unknown. Never blindly replay an ambiguous timeout.
        reserved = min(remaining, self.query_service.limits.maximum_bytes_billed)
        spent = reserved
        try:
            if action.action == "demographic":
                result = self.query_service.execute_demographic(action.demographic, manager_id=self.manager_id, scan_budget_bytes=remaining)
            else:
                result = self.query_service.execute(action.sql, manager_id=self.manager_id, scan_budget_bytes=remaining)
            spent = max(result.estimated_bytes, result.bytes_billed or 0, result.bytes_processed or 0)
            observation = make_observation(action.sql, result, max_chars=self.limits.max_observation_chars)
            errors = 0
        except QueryServiceError as error:
            observation = {"status": "error", "code": error.code, "message": str(error)}
            if action.action == "query":
                try:
                    observation["attempted_sql"] = check_text(action.sql)[:self.limits.max_observation_chars // 2]
                    observation["attempted_sql_truncated"] = len(action.sql) > self.limits.max_observation_chars // 2
                except ResultGuardError:
                    pass  # Do not echo suspicious literals back into model input.
            errors = state["consecutive_errors"] + 1
            if error.stage in {"authorization", "validation", "dry_run"} or error.code in {"access_denied", "invalid_sql", "cost_limit", "estimate_unavailable"}:
                spent = 0  # These errors happen before job execution.
        except ResultGuardError:
            observation = {"status": "error", "code": "unsafe_result", "message": "Result was withheld by the output guard."}
            errors = state["consecutive_errors"] + 1
        except Exception:
            observation = {"status": "error", "code": "query_failed", "message": "The query could not be completed."}
            errors = state["consecutive_errors"] + 1

        update = {
            "observations": [*state["observations"], observation],
            "queries": state["queries"] + 1,
            "consecutive_errors": errors,
            "scan_bytes_used": state["scan_bytes_used"] + spent,
        }
        logger.info("agent_observation", extra={
            "turn_id": state["turn_id"], "status": observation["status"],
            "error_code": observation.get("code"), "query_count": update["queries"],
        })
        if errors >= self.limits.max_consecutive_errors or observation.get("code") in {
            "access_denied", "unsafe_result", "timeout", "backend_unavailable", "backend_denied", "rate_limited",
        }:
            update["answer"] = self._partial({**state, **update}, "Analysis stopped after a query failure.")
        return update

    @staticmethod
    def _partial(state: AgentState, reason: str) -> str:
        successes = [item for item in state["observations"] if item["status"] == "ok"]
        text = f"I could not fully complete the analysis. {reason}"
        failures = [item for item in state["observations"] if item["status"] == "error"]
        if failures:
            last = failures[-1]
            text += f"\nLast query issue: [{last['code']}] {last['message']}"
        if successes:
            text += "\nLast checked result (may be partial):\n" + render_observation(successes[-1], max_rows=10)
        return text
