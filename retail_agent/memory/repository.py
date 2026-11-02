import json
from uuid import uuid4

from retail_agent.memory.models import UserMemoryError


class MemoryRepository:
    """Injected transaction factory; no LLM, user/session or transport dependency."""

    def __init__(self, connection_factory):
        self.connection = connection_factory

    def load(self, owner):
        with self.connection() as conn:
            return conn.execute(
                "SELECT memory_id, content, normalized_key FROM user_memories "
                "WHERE owner_id=%s ORDER BY created_at, memory_id LIMIT 21", (owner,),
            ).fetchall()

    def remember(self, owner, entries, model, turn_id):
        with self.connection() as conn:
            # Serialize changes per owner to enforce the small-library cap under
            # concurrent requests. A hash collision only serializes extra users.
            conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (owner,))
            existing = conn.execute("SELECT normalized_key FROM user_memories WHERE owner_id=%s", (owner,)).fetchall()
            keys = {row["normalized_key"] for row in existing}
            new = [item for item in entries if item["key"] not in keys]
            if len(keys) + len(new) > 20:
                raise UserMemoryError("Memory limit reached (20 entries). Forget an old entry first; nothing new was saved.")
            for item in new:
                conn.execute(
                    "INSERT INTO user_memories (memory_id, owner_id, content, normalized_key, embedding, embedding_model, source_turn_id) "
                    "VALUES (%s,%s,%s,%s,%s::vector,%s,%s)",
                    (uuid4().hex, owner, item["text"], item["key"], json.dumps(item["vector"], allow_nan=False), model, turn_id),
                )
            return len(new)

    def forget(self, owner, memory_id=None):
        with self.connection() as conn:
            # Same lock as insertion keeps capacity/accounting deterministic.
            conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (owner,))
            if memory_id is None:
                cursor = conn.execute("DELETE FROM user_memories WHERE owner_id=%s", (owner,))
            else:
                cursor = conn.execute("DELETE FROM user_memories WHERE owner_id=%s AND memory_id=%s", (owner, memory_id))
            return cursor.rowcount
