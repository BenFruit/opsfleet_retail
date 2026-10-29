import os

from dotenv import load_dotenv
from google import genai
from google.genai import types
from typing import TypeVar
from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)

class GeminiClient:
    def __init__(self, model: str | None = None):
        load_dotenv()

        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise ValueError("GEMINI_API_KEY is not configured")

        self.model = os.getenv("GEMINI_MODEL","gemini-3.5-flash-lite")
        self.client = genai.Client(api_key=api_key)

    def generate(self, prompt: str) -> str:
        response = self.client.models.generate_content(
            model=self.model,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.1,
                max_output_tokens=2048,
            ),
        )

        if not response.text:
            raise RuntimeError("Gemini returned an empty response")

        return response.text

    def generate_structured( self, prompt: str, response_schema: type[T]) -> T:
        response = self.client.models.generate_content(
            model=self.model,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.1,
                response_mime_type="application/json",
                response_schema=response_schema,
            ),
        )

        if not response.text:
            raise RuntimeError("Gemini returned an empty response")

        return response_schema.model_validate_json(response.text)

    def close(self) -> None:
        self.client.close()