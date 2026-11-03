"""Shared demo entitlements for tests (mirrors access.example.json)."""

from retail_agent.security.access import Entitlements

DEMO_ENTITLEMENTS = Entitlements({
    "manager_a": ["Brand A", "Brand B"],
    "manager_b": ["Brand C"],
})
