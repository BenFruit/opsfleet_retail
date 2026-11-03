"""Editable presentation only. Google Doc loading is optional and read-only."""

import logging
import re
from dataclasses import dataclass
from hashlib import sha256
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


logger = logging.getLogger(__name__)
DEFAULT_STYLE = """Write for nontechnical retail executives in a clear, professional tone.
Be concise: lead with findings, then limitations and useful next steps.
Use the user's language unless they explicitly request another language.
Avoid greetings, filler and repeating every row of the app-rendered tables."""


@dataclass(frozen=True)
class Persona:
    text: str = DEFAULT_STYLE
    source: str = "default"

    @property
    def version(self) -> str:
        return sha256(self.text.encode("utf-8")).hexdigest()[:12]


class PersonaProvider:
    def __init__(self, doc_id: str = ""):
        self.doc_id = doc_id
        self.current = Persona()

    @staticmethod
    def document_id(value: str) -> str:
        value = value.strip()
        if re.fullmatch(r"[A-Za-z0-9_-]{20,160}", value):
            return value
        url = urlsplit(value)
        if url.scheme == "https" and url.netloc == "docs.google.com":
            match = re.fullmatch(r"/document/(?:u/\d+/)?d/([A-Za-z0-9_-]{20,160})(?:/.*)?", url.path)
            if match:
                return match.group(1)
        raise ValueError("Expected a document ID or a standard HTTPS Google Docs URL")

    def load(self) -> Persona:
        if not self.doc_id:
            return self.current
        failure_code = "persona_invalid_config"
        try:
            # Extract only the ID from trusted Google Docs URLs; reconstruct the
            # export endpoint instead of fetching arbitrary configured URLs.
            doc_id = self.document_id(self.doc_id)
            request = Request(
                f"https://docs.google.com/document/d/{doc_id}/export?format=txt",
                headers={"Accept": "text/plain"},
            )
            failure_code = "persona_fetch_failed"
            with urlopen(request, timeout=3) as response:
                failure_code = "persona_invalid_content"
                if response.headers.get_content_type() != "text/plain":
                    raise ValueError("Expected plain text, not a login/error page")
                content = response.read(16_001)
            if len(content) > 16_000:
                raise ValueError("Document too large")
            text = content.decode("utf-8-sig").strip()
            if not text or len(text) > 4000:
                raise ValueError("Empty or oversized persona")
            self.current = Persona(text, "google_doc")
            logger.info("persona_loaded", extra={"persona_version": self.current.version, "status": "ok"})
        except Exception as error:
            # No URLs, contents or raw HTTP exceptions in local logs.
            logger.warning("persona_load_failed", extra={
                "error_code": failure_code, "persona_version": self.current.version,
                "error_type": type(error).__name__,
                **({"http_status": error.code} if isinstance(error, HTTPError) else {}),
            })
        return self.current  # Last successful in-process version, or built-in default.
