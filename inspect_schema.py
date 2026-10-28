from google.cloud import bigquery

client = bigquery.Client(project='opsfleet-assessment-yerassyl')
table = client.get_table("bigquery-public-data.thelook_ecommerce.orders")
for i in table.schema:
    print(i.name, i.field_type)