import argparse
import logging
import os

from dotenv import load_dotenv

from retail_agent.agent.orchestrator import AnalyticalAgent
from retail_agent.data.bigquery_runner import BigQueryRunner
from retail_agent.data.query_service import QueryLimits, QueryService
from retail_agent.llm.analyst import Analyst
from retail_agent.llm.client import GeminiClient
from retail_agent.observability.logging import configure_logging
from retail_agent.observability.tracing import shutdown_tracing
from retail_agent.reports.embeddings import ReportEmbedder
from retail_agent.reports.repository import ReportRepository
from retail_agent.reports.service import ReportLibrary
from retail_agent.memory.repository import MemoryRepository
from retail_agent.memory.service import UserMemory
from retail_agent.schema.service import SchemaService
from retail_agent.security.access import get_allowed_brands, load_brand_access


logger = logging.getLogger("retail_agent.startup")

# Startup stage -> safe operator hint. The exception class name is shown, its text never is.
STARTUP_HINTS = {
    "logging": "Check that the log file directory is writable.",
    "access_config": "Copy access.example.json to access.local.json and edit the brands.",
    "gemini_client": "Check GEMINI_API_KEY and GEMINI_MODEL in .env.",
    "bigquery_client": "Run 'gcloud auth application-default login' and set GOOGLE_CLOUD_PROJECT.",
    "bigquery_schema": "Check Google ADC, the quota project and BigQuery API access to the public dataset.",
    "agent_init": "Check the optional reports/memory/persona settings in .env and the README.",
}


def run_chat(agent: AnalyticalAgent) -> None:
    print("Retail analyst. Commands: /exit, /reset, /help, /confirm, /cancel, /memories, /forget ID, /forget-all")
    print("Demo identity is fixed at startup. Do not enter personal contact details.")
    while True:
        try:
            question = input("\nYou: ").strip()
            if question.lower() in {"/exit", "/quit"}:
                return
            if question.lower() == "/reset":
                agent.reset()
                print("Conversation cleared.")
                continue
            if question.lower() == "/help":
                print("Ask about products, customer spending by ID, or sales over time.")
                print("Demographics use aggregate SQL with computed groups; small cohorts are suppressed.")
                print("Ask to save the last answer, find/list/open your reports, or preview deletion.")
                print("Deletion requires a separate /confirm; /cancel or any new question cancels it.")
                print("Explicitly ask to remember preferences; /memories lists them, /forget ID or /forget-all removes them.")
                continue
            if not question:
                continue
            result = agent.ask(question)
            print(f"\nAnalyst: {result.answer}")
            print(f"[SQL attempts: {result.queries}; model calls: {result.model_calls}]")
        except (EOFError, KeyboardInterrupt):
            print("\nSession ended.")
            return
        except Exception:
            # Last UI boundary: do not print backend exception text or traceback.
            print("This request could not be completed. You can ask another question.")


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Brand-scoped retail analysis chat")
    parser.add_argument("--project", default=os.getenv("GOOGLE_CLOUD_PROJECT"))
    parser.add_argument("--manager", default=os.getenv("RETAIL_MANAGER_ID", "manager_a"))
    parser.add_argument("--access-config", default="access.local.json")
    parser.add_argument("--log-file", default="logs/agent.jsonl")
    parser.add_argument("--min-cohort-size", type=int, default=5)
    args = parser.parse_args()
    if args.min_cohort_size < 2:
        parser.error("--min-cohort-size must be at least 2")

    llm = None
    runner = None
    stage = "logging"
    try:
        configure_logging(args.log_file)
        stage = "access_config"
        load_brand_access(args.access_config)
        get_allowed_brands(args.manager)
        for name in ("GEMINI_API_KEY", "GEMINI_MODEL"):
            if not os.getenv(name):
                print(f"Set {name} in your .env before starting the chat.")
                return 1
        stage = "gemini_client"
        llm = GeminiClient()
        stage = "bigquery_client"
        runner = BigQueryRunner(project_id=args.project)
        stage = "bigquery_schema"
        schema_service = SchemaService(runner)
        schema = {table: schema_service.get_table_schema(table) for table in (
            "orders", "order_items", "products", "users",
        )}
        stage = "agent_init"
        repository = ReportRepository()
        embedder = ReportEmbedder(llm)
        agent = AnalyticalAgent(
            Analyst(llm, schema),
            QueryService(runner, QueryLimits(minimum_cohort_size=args.min_cohort_size)),
            manager_id=args.manager,
            reports=ReportLibrary(repository, embedder, manager_id=args.manager),
            memory=UserMemory(MemoryRepository(repository.connection), embedder, manager_id=args.manager),
        )
        logger.info("startup_completed", extra={"model": llm.model, "fallback": llm.fallback_model is not None})
        run_chat(agent)
        return 0
    except FileNotFoundError as error:
        if stage == "access_config":
            print("Access config not found. Copy access.example.json to access.local.json and edit the brands.")
        else:
            _report_startup_failure(stage, error)
    except PermissionError as error:
        if stage == "access_config":
            print("This demo identity has no authorized brands. Check --manager and the access config.")
        else:
            _report_startup_failure(stage, error)
    except Exception as error:
        _report_startup_failure(stage, error)
    finally:
        shutdown_tracing()
        if llm is not None:
            llm.close()
        if runner is not None:
            runner.client.close()
    return 1


def _report_startup_failure(stage: str, error: Exception) -> None:
    logger.error("startup_failed", extra={"stage": stage, "error_type": type(error).__name__})
    print(f"Startup failed at stage '{stage}' ({type(error).__name__}). {STARTUP_HINTS[stage]}")


if __name__ == "__main__":
    raise SystemExit(main())
