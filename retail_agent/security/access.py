import json
from pathlib import Path


MANAGER_BRANDS = {
    "manager_a": frozenset({"Brand A", "Brand B"}),
    "manager_b": frozenset({"Brand C"}),
    "manager_empty": frozenset(),
}


def load_brand_access(path: str) -> None:
    """Load trusted local demo config at startup, never from chat/model content."""
    data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict) or not data:
        raise ValueError("Access config must map manager IDs to lists of brands")
    mapping = {}
    for manager, brands in data.items():
        if not isinstance(manager, str) or not manager.strip() or not isinstance(brands, list):
            raise ValueError("Invalid manager access config")
        if any(not isinstance(brand, str) or not brand.strip() for brand in brands):
            raise ValueError("Brand names must be nonempty strings")
        mapping[manager] = frozenset(brands)
    MANAGER_BRANDS.clear()
    MANAGER_BRANDS.update(mapping)

def get_allowed_brands(manager_id: str) -> frozenset[str]:
    brands_allowed = MANAGER_BRANDS.get(manager_id)
    if brands_allowed:
        return brands_allowed
    else:
        raise PermissionError("No authorized brands for this manager")

if __name__ == "__main__":
    for manager_id in ["manager_a", "manager_b", "manager_empty", "unknown"]:
        try:
            print(manager_id, "->", get_allowed_brands(manager_id))
        except PermissionError as error:
            print(manager_id, "-> denied:", error)
