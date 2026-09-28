"""
OpenRouter Integration
"""
from typing import Optional, Dict, Any, List
from dataclasses import dataclass
import asyncio
import random

import httpx

from src.core.config import settings
from src.core.logging import get_logger
from src.core.metrics import EXTERNAL_CALL_RETRIES, LLM_REQUESTS, LLM_TOKENS

logger = get_logger(__name__)


@dataclass
class ModelConfig:
    model_id: str
    name: str
    max_tokens: int
    cost_per_1k_input: float
    cost_per_1k_output: float
    supports_persian: bool
    supports_json: bool


# Model configurations for different environments
MODEL_CONFIGS = {
    "development": [
        ModelConfig(
            model_id="openrouter/auto",
            name="Auto (Free Models)",
            max_tokens=4096,
            cost_per_1k_input=0.0,
            cost_per_1k_output=0.0,
            supports_persian=True,
            supports_json=True,
        ),
        ModelConfig(
            model_id="nvidia/nemotron-3-ultra",
            name="Nemotron 3 Ultra",
            max_tokens=4096,
            cost_per_1k_input=0.0,
            cost_per_1k_output=0.0,
            supports_persian=True,
            supports_json=True,
        ),
    ],
    "production": [
        ModelConfig(
            model_id="openai/gpt-4o",
            name="GPT-4o",
            max_tokens=4096,
            cost_per_1k_input=5.0,
            cost_per_1k_output=15.0,
            supports_persian=True,
            supports_json=True,
        ),
        ModelConfig(
            model_id="anthropic/claude-3.5-sonnet",
            name="Claude 3.5 Sonnet",
            max_tokens=4096,
            cost_per_1k_input=3.0,
            cost_per_1k_output=15.0,
            supports_persian=True,
            supports_json=True,
        ),
    ],
    "local": [
        ModelConfig(
            model_id="local/llama-3.1-70b",
            name="Llama 3.1 70B (Local)",
            max_tokens=4096,
            cost_per_1k_input=0.0,
            cost_per_1k_output=0.0,
            supports_persian=False,
            supports_json=True,
        ),
    ],
}


class OpenRouterIntegration:
    """OpenRouter access with model selection per environment.

    Talks to OpenRouter's OpenAI-compatible ``/chat/completions`` endpoint over
    ``httpx`` directly rather than through an SDK. ``httpx`` is already a
    dependency; the ``openrouter`` SDK on PyPI does not expose the
    ``client.chat.completions.create`` shape this code assumed, so the import
    raised ``ImportError`` and every call silently fell through to the
    keyword-based fallback. An API integration that cannot run is worse than no
    integration, because the fallback looks like a working answer.
    """

    #: Seconds before a request is abandoned. Generous, because a long wait is
    #: visible in the logs and a premature timeout is not recoverable.
    TIMEOUT_SECONDS = 60.0

    def __init__(self):
        self.logger = logger
        self.current_model = settings.DEFAULT_LLM_MODEL
        self.client = httpx.AsyncClient(
            base_url=settings.OPENROUTER_BASE_URL.rstrip("/"),
            timeout=self.TIMEOUT_SECONDS,
            headers=self._headers(),
        )

    def _headers(self) -> Dict[str, str]:
        """Auth and attribution headers for a single request."""
        return {
            "Authorization": f"Bearer {settings.OPENROUTER_API_KEY}",
            "Content-Type": "application/json",
            # OpenRouter uses these to attribute traffic to a project. Without
            # them a provider account shows anonymous requests, which is
            # invisible in their dashboard and impossible to reconcile against
            # the token usage this service records.
            "HTTP-Referer": settings.OPENROUTER_SITE_URL,
            "X-Title": settings.OPENROUTER_APP_NAME,
        }

    @property
    def is_configured(self) -> bool:
        """Whether a real request is possible.

        An absent key is a deployment state, not an error, so callers check
        this and take the documented fallback path deliberately instead of
        discovering it as a failed request.
        """
        return bool(settings.OPENROUTER_API_KEY)

    def get_available_models(self, environment: str = None) -> List[ModelConfig]:
        """Get available models for environment"""
        env = environment or settings.ENVIRONMENT
        return MODEL_CONFIGS.get(env, MODEL_CONFIGS["development"])

    def set_model(self, model_id: str) -> bool:
        """Set the current model"""
        # Validate model exists in any environment
        all_models = []
        for configs in MODEL_CONFIGS.values():
            all_models.extend([m.model_id for m in configs])

        if model_id in all_models or model_id.startswith("openrouter/"):
            self.current_model = model_id
            self.logger.info("model_changed", model=model_id)
            return True
        return False

    def get_current_model(self) -> str:
        """Get current model ID"""
        return self.current_model

    async def chat_completion(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.1,
        max_tokens: int = 4096,
        response_format: Dict[str, str] = None,
        model: str = None,
    ) -> Dict[str, Any]:
        """
        Make a chat completion request with exponential backoff retry.

        Returns:
            ``content`` plus the token counts. The counts are not optional
            extras: they are what the weight-attribution audit trail records
            per item, so a caller that loses them cannot account for what an
            analysis run cost.

        Raises:
            RuntimeError: no API key configured, or the provider returned
                something without a usable completion. Both are raised rather
                than returned as a sentinel, so the caller's fallback is a
                deliberate branch.
        """
        if not self.is_configured:
            raise RuntimeError(
                "OPENROUTER_API_KEY is not set, so no LLM request can be made"
            )

        model_id = model or self.current_model
        payload: Dict[str, Any] = {
            "model": model_id,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if response_format:
            payload["response_format"] = response_format

        # Exponential backoff with jitter
        base = settings.LLM_RETRY_BASE_SECONDS
        max_wait = settings.LLM_RETRY_MAX_SECONDS
        attempt = 0

        while True:
            try:
                response = await self.client.post("/chat/completions", json=payload)
                response.raise_for_status()
                body = response.json()
                break
            except httpx.HTTPError as e:
                attempt += 1
                if attempt >= 3 or isinstance(e, httpx.HTTPStatusError) and e.response.status_code < 500:
                    # Don't retry 4xx errors (except 429 which is handled below) or after 3 attempts
                    if isinstance(e, httpx.HTTPStatusError) and e.response.status_code == 429:
                        # Rate limited - wait and retry once more
                        if attempt >= 3:
                            self.logger.error(
                                "openrouter_rate_limited_exhausted",
                                model=model_id,
                                error=str(e),
                            )
                            LLM_REQUESTS.labels(model=model_id, outcome="rate_limited").inc()
                            raise
                        wait = min(base * (2 ** (attempt - 1)), max_wait)
                        wait += random.uniform(0, wait * 0.1)  # jitter
                        EXTERNAL_CALL_RETRIES.labels(target="openrouter", reason="rate_limited").inc()
                        self.logger.warning(
                            "openrouter_rate_limited_retry",
                            model=model_id,
                            attempt=attempt,
                            wait_seconds=round(wait, 2),
                        )
                        await asyncio.sleep(wait)
                        continue
                    self.logger.error(
                        "openrouter_request_failed", model=model_id, error=str(e)
                    )
                    LLM_REQUESTS.labels(model=model_id, outcome="error").inc()
                    raise
                # Transient error - retry with backoff
                wait = min(base * (2 ** (attempt - 1)), max_wait)
                wait += random.uniform(0, wait * 0.1)  # jitter
                EXTERNAL_CALL_RETRIES.labels(target="openrouter", reason="transient_error").inc()
                self.logger.warning(
                    "openrouter_transient_error_retry",
                    model=model_id,
                    attempt=attempt,
                    wait_seconds=round(wait, 2),
                    error=str(e),
                )
                await asyncio.sleep(wait)
            except ValueError as e:
                # A 200 with a non-JSON body: a proxy's error page, usually.
                self.logger.error(
                    "openrouter_response_not_json", model=model_id, error=str(e)
                )
                LLM_REQUESTS.labels(model=model_id, outcome="error").inc()
                raise RuntimeError(
                    f"OpenRouter returned a non-JSON body for {model_id}"
                ) from e

        try:
            content = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as e:
            self.logger.error(
                "openrouter_response_malformed", model=model_id, keys=sorted(body)
            )
            LLM_REQUESTS.labels(model=model_id, outcome="error").inc()
            raise RuntimeError(
                f"OpenRouter response for {model_id} had no usable completion"
            ) from e

        usage = body.get("usage") or {}
        LLM_REQUESTS.labels(model=model_id, outcome="success").inc()
        prompt_tokens = usage.get("prompt_tokens", 0) or 0
        completion_tokens = usage.get("completion_tokens", 0) or 0
        if prompt_tokens:
            LLM_TOKENS.labels(model=model_id, direction="input").inc(prompt_tokens)
        if completion_tokens:
            LLM_TOKENS.labels(model=model_id, direction="output").inc(completion_tokens)
        return {
            "content": content,
            "model": body.get("model", model_id),
            "tokens_used": usage.get("total_tokens", 0) or 0,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
        }

    def estimate_cost(self, prompt_tokens: int, completion_tokens: int, model: str = None) -> float:
        """Estimate cost for a request"""
        model_id = model or self.current_model

        # Find model config
        for configs in MODEL_CONFIGS.values():
            for config in configs:
                if config.model_id == model_id:
                    input_cost = (prompt_tokens / 1000) * config.cost_per_1k_input
                    output_cost = (completion_tokens / 1000) * config.cost_per_1k_output
                    return input_cost + output_cost

        return 0.0


# Singleton instance
openrouter_integration = OpenRouterIntegration()