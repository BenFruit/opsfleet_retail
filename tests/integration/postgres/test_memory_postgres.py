import os
import unittest
from unittest.mock import Mock
from uuid import uuid4

from retail_agent.memory.repository import MemoryRepository
from retail_agent.memory.service import UserMemory, memory_key
from retail_agent.settings import Settings
from retail_agent.storage.postgres import connection_factory


VECTOR = [1.0] + [0.0] * 767


@unittest.skipUnless(os.getenv("RETAIL_TEST_POSTGRES") == "1", "Opt-in local PostgreSQL memory test")
class MemoryPostgresTests(unittest.TestCase):
    def test_embeddings_exact_duplicates_owner_isolation_and_forget(self):
        repo = MemoryRepository(connection_factory(Settings()))
        owner, other = "memory_test_" + uuid4().hex, "memory_test_" + uuid4().hex
        entry = {"text": "Prefers bullets.", "key": memory_key("Prefers bullets."), "vector": VECTOR}
        try:
            self.assertEqual(repo.remember(owner, [entry], "gemini-embedding-2", uuid4().hex), 1)
            self.assertEqual(repo.remember(owner, [entry], "gemini-embedding-2", uuid4().hex), 0)
            self.assertEqual(repo.remember(other, [entry], "gemini-embedding-2", uuid4().hex), 1)
            own = repo.load(owner)
            self.assertEqual(len(own), 1)
            restarted = UserMemory(MemoryRepository(connection_factory(Settings())), Mock(), manager_id=owner)
            self.assertEqual(restarted.load()[0]["text"], "Prefers bullets.")
            self.assertEqual(repo.forget(other, own[0]["memory_id"]), 0)
            with repo.connection() as conn:
                size = conn.execute("SELECT vector_dims(embedding) AS dimensions FROM user_memories WHERE owner_id=%s", (owner,)).fetchone()
                self.assertEqual(size["dimensions"], 768)
            self.assertEqual(repo.forget(owner, own[0]["memory_id"]), 1)
            self.assertEqual(len(repo.load(other)), 1)
        finally:
            repo.forget(owner)
            repo.forget(other)
