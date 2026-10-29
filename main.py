from retail_agent.llm.client import GeminiClient
from retail_agent.llm.sql_generator import SQLGenerator
from retail_agent.data.bigquery_runner import BigQueryRunner
from retail_agent.schema.service import SchemaService

def main():
    runner = BigQueryRunner(project_id='opsfleet-assessment-yerassyl')
    schema_service = SchemaService(runner)
    schema = {
        "orders" : schema_service.get_table_schema("orders"),
        "order_items" : schema_service.get_table_schema("order_items"),
        "products" : schema_service.get_table_schema("products"),
        "users" : schema_service.get_table_schema("users")
    }

    llm = GeminiClient()

    try:
        sql_generator = SQLGenerator(llm)
        result = sql_generator.generate(question="What are the top 5 products by completed order-revenue?", schema=schema)
        print("\nGenerated SQL:\n")
        print(result.sql)

        print("\nExplanation:\n")
        print(result.explanation)

    finally:
        llm.close()

if __name__ == "__main__":
    main()