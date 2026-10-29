from schema.models import ColumnPolicy, SensitivityLevel


USER_COLUMN_POLICIES = {
    "id": ColumnPolicy(sensitivity=SensitivityLevel.QUASI_IDENTIFIER),
    "first_name": ColumnPolicy(sensitivity=SensitivityLevel.DIRECT_PII),
    "last_name": ColumnPolicy(sensitivity=SensitivityLevel.DIRECT_PII),
    "email": ColumnPolicy(sensitivity=SensitivityLevel.DIRECT_PII),
    "age": ColumnPolicy(sensitivity=SensitivityLevel.QUASI_IDENTIFIER),
    "gender": ColumnPolicy(sensitivity=SensitivityLevel.QUASI_IDENTIFIER),
    "state": ColumnPolicy(sensitivity=SensitivityLevel.SAFE),
    "street_address": ColumnPolicy(sensitivity=SensitivityLevel.DIRECT_PII),
    "postal_code": ColumnPolicy(sensitivity=SensitivityLevel.QUASI_IDENTIFIER),
    "city": ColumnPolicy(sensitivity=SensitivityLevel.QUASI_IDENTIFIER),
    "country": ColumnPolicy(sensitivity=SensitivityLevel.SAFE),
    "latitude": ColumnPolicy(sensitivity=SensitivityLevel.DIRECT_PII),
    "longitude": ColumnPolicy(sensitivity=SensitivityLevel.DIRECT_PII),
    "traffic_source": ColumnPolicy(sensitivity=SensitivityLevel.SAFE),
    "created_at": ColumnPolicy(sensitivity=SensitivityLevel.SAFE),
    "user_geom": ColumnPolicy(sensitivity=SensitivityLevel.DIRECT_PII),
}


ORDERS_COLUMN_POLICIES = {
    "order_id": ColumnPolicy(sensitivity=SensitivityLevel.SAFE),
    "user_id": ColumnPolicy(sensitivity=SensitivityLevel.QUASI_IDENTIFIER),
    "status": ColumnPolicy(sensitivity=SensitivityLevel.SAFE),
    "gender": ColumnPolicy(sensitivity=SensitivityLevel.QUASI_IDENTIFIER),
    "created_at": ColumnPolicy(sensitivity=SensitivityLevel.SAFE),
    "returned_at": ColumnPolicy(sensitivity=SensitivityLevel.SAFE),
    "shipped_at": ColumnPolicy(sensitivity=SensitivityLevel.SAFE),
    "delivered_at": ColumnPolicy(sensitivity=SensitivityLevel.SAFE),
    "num_of_item": ColumnPolicy(sensitivity=SensitivityLevel.SAFE),
}


ORDER_ITEMS_COLUMN_POLICIES = {
    "id": ColumnPolicy(sensitivity=SensitivityLevel.SAFE),
    "order_id": ColumnPolicy(sensitivity=SensitivityLevel.SAFE),
    "user_id": ColumnPolicy(sensitivity=SensitivityLevel.QUASI_IDENTIFIER),
    "product_id": ColumnPolicy(sensitivity=SensitivityLevel.SAFE),
    "inventory_item_id": ColumnPolicy(sensitivity=SensitivityLevel.SAFE),
    "status": ColumnPolicy(sensitivity=SensitivityLevel.SAFE),
    "created_at": ColumnPolicy(sensitivity=SensitivityLevel.SAFE),
    "shipped_at": ColumnPolicy(sensitivity=SensitivityLevel.SAFE),
    "delivered_at": ColumnPolicy(sensitivity=SensitivityLevel.SAFE),
    "returned_at": ColumnPolicy(sensitivity=SensitivityLevel.SAFE),
    "sale_price": ColumnPolicy(sensitivity=SensitivityLevel.SAFE),
}


PRODUCTS_COLUMN_POLICIES = {
    "id": ColumnPolicy(sensitivity=SensitivityLevel.SAFE),
    "cost": ColumnPolicy(sensitivity=SensitivityLevel.SAFE),
    "category": ColumnPolicy(sensitivity=SensitivityLevel.SAFE),
    "name": ColumnPolicy(sensitivity=SensitivityLevel.SAFE),
    "brand": ColumnPolicy(sensitivity=SensitivityLevel.SAFE),
    "retail_price": ColumnPolicy(sensitivity=SensitivityLevel.SAFE),
    "department": ColumnPolicy(sensitivity=SensitivityLevel.SAFE),
    "sku": ColumnPolicy(sensitivity=SensitivityLevel.SAFE),
    "distribution_center_id": ColumnPolicy(sensitivity=SensitivityLevel.SAFE),
}


TABLE_POLICIES = {
    "users": USER_COLUMN_POLICIES,
    "orders": ORDERS_COLUMN_POLICIES,
    "order_items": ORDER_ITEMS_COLUMN_POLICIES,
    "products": PRODUCTS_COLUMN_POLICIES,
}