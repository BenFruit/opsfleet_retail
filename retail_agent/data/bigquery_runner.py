import logging
from dataclasses import dataclass, field
from itertools import islice
from typing import Optional, List, Dict, Any
import pandas as pd
from google.cloud import bigquery


@dataclass(frozen=True)
class QueryExecution:
    """Internal bounded result; not yet approved for delivery to an LLM."""

    rows: tuple[dict[str, Any], ...] = field(repr=False)
    truncated: bool
    bytes_processed: int | None = None
    bytes_billed: int | None = None


class BigQueryRunner:
    """A lean BigQuery client for executing SQL queries and returning DataFrame results."""
    
    def __init__(self, project_id: Optional[str] = None, dataset_id: Optional[str] = "bigquery-public-data.thelook_ecommerce") -> None:
        """Initialize BigQuery client.
        
        Args:
            project_id: Google Cloud project ID. If None, uses default credentials.
            dataset_id: BigQuery dataset ID. If None, uses default dataset.
        """
        logging.info("Initializing BigQuery client")
        try:
            self.client = bigquery.Client(project=project_id)
            self.dataset_id = dataset_id
            logging.info(f"BigQuery client initialized for dataset: {self.dataset_id}")
        except Exception as e:
            logging.error(f"Failed to initialize BigQuery client: {str(e)}")
            raise
    
    def execute_query(self, sql_query: str) -> pd.DataFrame:
        """Legacy unrestricted adapter; NEVER expose this method as an agent tool.
        
        Args:
            sql_query: The SQL query to execute.
            
        Returns:
            DataFrame containing the query results.
            
        Raises:
            Exception: If query execution fails.
        """
        try:
            logging.info(f"Executing BigQuery query")
            query_job = self.client.query(sql_query)
            df = query_job.result().to_dataframe()
            logging.info(f"Query completed successfully, returned {len(df)} rows")
            return df
        except Exception as e:
            logging.error(f"BigQuery execution failed: {str(e)}")
            raise 

    @staticmethod
    def _brand_parameters(allowed_brands: tuple[str, ...]) -> list:
        if not allowed_brands or any(not isinstance(brand, str) or not brand.strip() for brand in allowed_brands):
            raise PermissionError("Nonempty trusted brand entitlements are required")
        return [bigquery.ArrayQueryParameter("allowed_brands", "STRING", list(allowed_brands))]

    def dry_run(
        self,
        sql: str,
        *,
        allowed_brands: tuple[str, ...],
        api_timeout_seconds: float,
    ) -> int | None:
        """Estimate the SAME scoped SQL and parameters that will be executed."""
        config = bigquery.QueryJobConfig(
            dry_run=True,
            use_query_cache=False,
            use_legacy_sql=False,
            query_parameters=self._brand_parameters(allowed_brands),
        )
        job = self.client.query(
            sql, job_config=config, timeout=api_timeout_seconds,
            retry=None, job_retry=None,
        )
        return job.total_bytes_processed

    def execute_scoped_query(
        self,
        sql: str,
        *,
        allowed_brands: tuple[str, ...],
        maximum_bytes_billed: int,
        max_rows: int,
        timeout_seconds: float,
        api_timeout_seconds: float,
        query_id: str,
    ) -> QueryExecution:
        """Internal QueryService adapter, not an independently safe SQL tool.

        No pandas conversion or unbounded result materialization. Job timeout
        and cancellation are best-effort server controls, not hard billing or
        wall-clock guarantees. No automatic query resubmission is enabled.
        """
        config = bigquery.QueryJobConfig(
            use_legacy_sql=False,
            use_query_cache=False,
            query_parameters=self._brand_parameters(allowed_brands),
            maximum_bytes_billed=maximum_bytes_billed,
            job_timeout_ms=max(1, int(timeout_seconds * 1000)),
            labels={"application": "retail-agent"},
        )
        # Known ID allows best-effort cancellation even if submission times out.
        job_id = f"retail_{query_id}"
        try:
            job = self.client.query(
                sql, job_config=config, job_id=job_id,
                timeout=api_timeout_seconds, retry=None, job_retry=None,
            )
            result = job.result(
                timeout=timeout_seconds,
                max_results=max_rows + 1,
                page_size=min(max_rows + 1, 100),
                retry=None, job_retry=None,
            )
            names = [column.name for column in result.schema]
            if len(names) != len(set(names)):
                raise ValueError("Duplicate output column names are unsupported")
            rows = tuple(dict(row.items()) for row in islice(result, max_rows + 1))
            return QueryExecution(
                rows=rows[:max_rows],
                truncated=len(rows) > max_rows,
                bytes_processed=job.total_bytes_processed,
                bytes_billed=job.total_bytes_billed,
            )
        except Exception:
            # Do not log raw SQL, data, or BigQuery exception text here.
            try:
                self.client.cancel_job(job_id, timeout=api_timeout_seconds, retry=None)
            except Exception:
                pass  # Cancellation failure must not hide the original error.
            raise

    def get_table_schema(self, table_name: str) -> List[Dict[str, Any]]:
        """Get schema information for a specific table.
        
        Args:
            table_name: Name of the table (orders, order_items, products, users).
            
        Returns:
            List of dictionaries containing column information.
        """
        try:
            table_ref = f"{self.dataset_id}.{table_name}"
            table = self.client.get_table(table_ref)
            schema_info = []
            for field in table.schema:
                schema_info.append({
                    "name": field.name,
                    "type": field.field_type,
                    "mode": field.mode,
                    "description": field.description or ""
                })
            logging.info(f"Retrieved schema for table {table_name}")
            return schema_info
        except Exception as e:
            logging.error(f"Failed to get schema for table {table_name}: {str(e)}")
            raise
