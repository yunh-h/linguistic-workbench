"""Transport adapters returning only final structured text, never reasoning/tool calls."""

import json

import httpx

from workbench.llm_config import ConfigurationError, LLMConfig


class BadResponse(ValueError):
    """A completed response has repairable candidate-format errors."""
    reason = "format"


class ProviderUnavailable(ValueError):
    """Configuration, refusal or incomplete response: do not retry."""
    def __init__(self, message: str, reason: str = "provider"):
        super().__init__(message)
        self.reason = reason


def _responses_text(result: dict) -> str:
    if result.get("status") != "completed":
        details = result.get("incomplete_details")
        if isinstance(details, dict) and details.get("reason") == "max_output_tokens":
            raise ProviderUnavailable("truncated response", "truncated")
        if isinstance(details, dict) and details.get("reason") == "content_filter":
            raise ProviderUnavailable("filtered response", "refused")
        raise ProviderUnavailable("model response did not complete")
    outputs = result.get("output")
    if not isinstance(outputs, list):
        raise ProviderUnavailable("invalid provider output")
    pieces = []
    for output in outputs:
        if not isinstance(output, dict):
            raise ProviderUnavailable("invalid provider item")
        if output.get("type") != "message":
            continue
        content = output.get("content")
        if not isinstance(content, list):
            raise ProviderUnavailable("invalid message content")
        for part in content:
            if not isinstance(part, dict):
                raise ProviderUnavailable("invalid content item")
            if part.get("type") == "refusal":
                raise ProviderUnavailable("model refused enrichment", "refused")
            if part.get("type") == "output_text":
                if not isinstance(part.get("text"), str):
                    raise BadResponse("invalid structured text")
                pieces.append(part["text"])
    if len(pieces) != 1:
        raise BadResponse("expected one structured model response")
    return pieces[0]


def _chat_text(result: dict) -> str:
    choices = result.get("choices")
    if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
        raise ProviderUnavailable("expected one completion")
    choice = choices[0]
    # In particular, reject length-truncated output even if it happens to parse.
    if choice.get("finish_reason") != "stop":
        if choice.get("finish_reason") == "length":
            raise ProviderUnavailable("truncated completion", "truncated")
        if choice.get("finish_reason") == "content_filter":
            raise ProviderUnavailable("filtered completion", "refused")
        raise ProviderUnavailable("completion did not finish normally")
    message = choice.get("message")
    if not isinstance(message, dict) or message.get("role") != "assistant":
        raise ProviderUnavailable("invalid completion message")
    if message.get("refusal"):
        raise ProviderUnavailable("model refused completion", "refused")
    if message.get("tool_calls") or message.get("function_call"):
        raise ProviderUnavailable("refused or non-text completion")
    text = message.get("content")
    if not isinstance(text, str) or not text.strip():
        raise BadResponse("missing structured content")
    # reasoning_content is intentionally ignored. Mixed <think>/markdown content
    # is rejected by the downstream JSON validator rather than heuristically cut.
    return text


def request_structured(instructions: str, prompt: dict, schema: dict) -> tuple[str, dict]:
    try:
        config = LLMConfig.from_env()
    except ConfigurationError as exc:
        raise ProviderUnavailable("invalid provider configuration", "configuration") from exc
    if not config.available:
        raise ProviderUnavailable("provider not configured", "configuration")
    if config.provider == "lmstudio":
        endpoint = "/chat/completions"
        payload = {
            "model": config.model,
            "messages": [{"role": "system", "content": instructions},
                         {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)}],
            "response_format": {"type": "json_schema", "json_schema": {
                "name": "linguistic_enrichment_v01", "strict": True, "schema": schema}},
            "temperature": 0.7, "max_tokens": config.max_tokens, "stream": False,
        }
    else:
        endpoint = "/responses"
        payload = {
            "model": config.model, "store": False, "instructions": instructions,
            "input": json.dumps(prompt, ensure_ascii=False),
            "text": {"format": {"type": "json_schema", "name": "linguistic_enrichment_v01",
                                "strict": True, "schema": schema}},
        }
    headers = {"Authorization": f"Bearer {config.api_key}"} if config.api_key else {}
    with httpx.Client(timeout=httpx.Timeout(config.timeout_seconds, connect=5.0),
                      trust_env=config.provider != "lmstudio", follow_redirects=False) as client:
        response = client.post(config.base_url + endpoint, json=payload, headers=headers)
        response.raise_for_status()
        try:
            result = response.json()
        except ValueError as exc:
            raise ProviderUnavailable("invalid provider JSON envelope") from exc
    if not isinstance(result, dict):
        raise ProviderUnavailable("invalid provider envelope")
    text = _chat_text(result) if config.provider == "lmstudio" else _responses_text(result)
    # Only keep provenance. Reasoning, raw response and credentials are not stored.
    return text, {"id": result.get("id", "unavailable"), "model": result.get("model") or config.model,
                  "provider": config.provider}
