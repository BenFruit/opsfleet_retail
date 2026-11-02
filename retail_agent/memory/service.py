import logging
import re
import unicodedata
from hashlib import sha256

from retail_agent.memory.models import RememberRequest, UserMemoryError
from retail_agent.safety.result_guard import check_text


logger = logging.getLogger(__name__)


def memory_key(text: str) -> str:
    normalized = " ".join(unicodedata.normalize("NFKC", text).casefold().split())
    return sha256(normalized.encode("utf-8")).hexdigest()


def explicit_remember(question: str) -> bool:
    # Conservative MVP intent gate in addition to LLM classification. One-off
    # formatting alone cannot trigger persistence; quoted/indirect intent still
    # needs semantic evaluation (documented, not a security proof).
    if re.search(r"\b(?:do not|don't|never)\s+remember\b|\bне\s+запом", question, re.I):
        return False
    return bool(re.search(r"\bremember\b|\bзапомни(?:те)?\b", question, re.I))


class UserMemory:
    def __init__(self, repository, embedder, *, manager_id):
        self.repository = repository
        self.embedder = embedder
        self.manager_id = manager_id

    def load(self):
        try:
            rows = self.repository.load(self.manager_id)
            if len(rows) > 20:
                raise UserMemoryError("Stored memory exceeds the MVP context limit.")
            result = []
            for row in rows:
                if not isinstance(row["content"], str) or not 1 <= len(row["content"]) <= 200:
                    raise UserMemoryError("Stored memory has an invalid format.")
                result.append({"memory_id": row["memory_id"], "text": check_text(row["content"])})
            return result
        except Exception:
            raise UserMemoryError("User memory is unavailable. Analysis can continue without saved preferences.") from None

    def remember(self, request: RememberRequest, *, question, turn_id):
        if not explicit_remember(question):
            raise UserMemoryError("Persistent memory requires an explicit 'remember' request; one-off formatting is not saved.")
        try:
            known = {memory_key(item["text"]) for item in self.load()}
            entries = {}
            for text in request.memories:
                check_text(text)
                key = memory_key(text)
                if key not in known:
                    entries[key] = text
            if len(known) + len(entries) > 20:
                raise UserMemoryError("Memory limit reached (20 entries). Forget an old entry first.")
            # Obtain all embeddings before the single atomic database write.
            prepared = [{"key": key, "text": text, "vector": self.embedder.embed(
                text, document=True, title="User memory", observation_name="memory_embedding",
            )} for key, text in entries.items()]
            count = self.repository.remember(self.manager_id, prepared, self.embedder.model, turn_id) if prepared else 0
            logger.info("user_memory_saved", extra={"row_count": count, "status": "ok"})
            return {"status": "memory", "saved": True, "count": count,
                    "message": f"Memory saved: {count} new entries; existing exact duplicates were not added."}
        except UserMemoryError:
            raise
        except Exception:
            raise UserMemoryError("Memory was not confirmed saved. Analysis can continue; inspect /memories before retrying.") from None

    def forget(self, memory_id=None):
        if memory_id is not None and not re.fullmatch(r"[0-9a-f]{32}", memory_id):
            raise UserMemoryError("Use /forget followed by a memory ID from /memories.")
        try:
            count = self.repository.forget(self.manager_id, memory_id)
            logger.info("user_memory_forgotten", extra={"row_count": count, "status": "ok"})
            return f"Forgot {count} memory entries."
        except Exception:
            raise UserMemoryError("Memory deletion was not confirmed. Inspect /memories before retrying.") from None
