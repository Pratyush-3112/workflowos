"""Thin, resilient LLM client with JSON enforcement, single-retry repair, and safe fallback."""

import json
import os
from typing import Any, Callable, Dict, Optional, Tuple, Type, TypeVar
from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class LLMClient:
    """Thin wrapper around OpenAI API enforcing structured output, retry-once, and safe fallback."""

    def __init__(self, api_key: Optional[str] = None, model: str = "gpt-4o-mini"):
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        self.model = os.getenv("WORKFLOWOS_LLM_MODEL", model)

    @property
    def is_configured(self) -> bool:
        """Check if a real OpenAI API key is available."""
        return bool(self.api_key and self.api_key.strip())

    def call_structured(
        self,
        system_prompt: str,
        user_prompt: str,
        response_schema: Type[T],
        fallback_fn: Callable[[], T],
    ) -> Tuple[T, bool, Optional[str]]:
        """Call LLM requesting JSON output conforming to response_schema.
        
        Returns:
            (result_instance, was_fallback, optional_audit_note)
        """
        if not self.is_configured:
            # Safe immediate fallback when API key is not provided
            return fallback_fn(), True, "No OpenAI API key provided. Engaged deterministic safe fallback."

        try:
            import openai
            client = openai.OpenAI(api_key=self.api_key)
        except Exception as exc:
            return fallback_fn(), True, f"Failed to initialize OpenAI client: {exc}. Engaged safe fallback."

        # Attempt 1: Initial call
        messages = [
            {"role": "system", "content": system_prompt + "\nYou must respond strictly with valid JSON conforming to the requested schema."},
            {"role": "user", "content": user_prompt},
        ]

        try:
            raw_response = client.chat.completions.create(
                model=self.model,
                messages=messages,
                response_format={"type": "json_object"},
                temperature=0.2,
            )
            content = raw_response.choices[0].message.content or "{}"
            parsed_json = json.loads(content)
            validated = response_schema.model_validate(parsed_json)
            return validated, False, None
        except Exception as first_error:
            # Retry Once: Pass the error back to the LLM to fix its own JSON
            repair_messages = list(messages)
            repair_messages.append({"role": "assistant", "content": content if "content" in locals() else "{}"})
            repair_messages.append({
                "role": "user",
                "content": f"Your previous output failed validation: {first_error}. Please correct the JSON output to strictly match the schema.",
            })

            try:
                retry_response = client.chat.completions.create(
                    model=self.model,
                    messages=repair_messages,
                    response_format={"type": "json_object"},
                    temperature=0.1,
                )
                retry_content = retry_response.choices[0].message.content or "{}"
                retry_json = json.loads(retry_content)
                validated = response_schema.model_validate(retry_json)
                return validated, False, "Recovered via single-retry schema correction."
            except Exception as second_error:
                # Safe Fallback: Never crash the pipeline
                return (
                    fallback_fn(),
                    True,
                    f"LLM call failed after retry ({second_error}). Engaged deterministic safe fallback.",
                )
