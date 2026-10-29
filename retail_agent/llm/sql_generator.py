import json

from pydantic import BaseModel, Field

from .client import GeminiClient


class SQLGeneration(BaseModel):
    sql: str = Field(description="BigQuery Standard SQL query")
    explanation: str = Field(description="Brief explanation of the query")


class SQLGenerator:
    def __init__(self, llm: GeminiClient):
        self.llm = llm

    def generate(
            self,
            question: str,
            schema: dict[str, list[dict]],
        ) -> SQLGeneration:

        prompt = f"""
        You are a SQL analyst for a retail company.

        Generate one BigQuery Standard SQL query to answer the question.

        Allowed dataset:
        bigquery-public-data.thelook_ecommerce

        Available schema:
        {json.dumps(schema, indent=2)}

        Rules:
        - Use only the provided tables and columns.
        - Generate a read-only SELECT query.
        - Never select or expose blocked_pii columns.
        - Aggregate-only attributes may be used only in aggregate analysis.
        - Never reveal individual customer attributes from aggregate-only fields.
        - Do not invent database columns or business definitions.
        - Do not use SELECT *.
        - Add LIMIT 200 unless the query returns a single aggregate.
        - If a business metric is ambiguous, state your assumption
        in the explanation.

        User question:
        {question}
        """

        return self.llm.generate_structured(
            prompt=prompt,
            response_schema=SQLGeneration,
        )