"""Shared parameters and JEV calls for the nodes in this library."""

import json
import random
import time
from dataclasses import dataclass
from typing import Any

# The engine depends on httpx directly, so the library can use it without installing anything.
import httpx
from griptape_nodes.exe_types.core_types import ParameterGroup
from griptape_nodes.exe_types.param_types.parameter_string import ParameterString
from griptape_nodes.retained_mode.griptape_nodes import GriptapeNodes
from griptape_nodes.traits.options import Options

API_KEY_NAME = "TYPESAFE_API_KEY"
MODELS = ["jev-latest", "jev-preview"]
QUESTION_ID = "answer"

# See https://docs.typesafe.ai/api. The retry settings match the TypeSafe Python SDK's defaults.
API_URL = "https://api.typesafe.ai/v1/systemone"
TIMEOUT_SECONDS = 10.0
MAX_RETRIES = 2
RETRY_STATUSES = {408, 429, *range(500, 600)}
BACKOFF_INITIAL_SECONDS = 0.5
BACKOFF_MAX_SECONDS = 5.0
# Wait no longer than this between retries, even if TypeSafe asks for more.
MAX_RETRY_DELAY_SECONDS = 10.0

# JEV reads text only, so image, audio, and video outputs can't connect to Context.
CONTEXT_INPUT_TYPES = ["str", "json", "dict", "list", "TextArtifact", "JsonArtifact"]


def to_state(text: str | None) -> str | dict | list | None:
    """Convert Context into JEV state, or None if it's empty.

    ParameterString serializes connected dicts and lists to JSON, so parse JSON objects and arrays back
    out. JEV reads structured state better than the same data as a string.
    """
    text = (text or "").strip()
    if not text:
        return None
    if text[0] in "{[":
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
    return text


def context_parameter() -> ParameterString:
    """The Context parameter. It has an output so the same text can pass on to the next node."""
    context = ParameterString(
        name="context",
        display_name="Context",
        tooltip="The text JEV should read to answer the question. JSON works too. JEV accepts text only. "
        "To ask about an image, describe it first with a node like Describe Image.",
        default_value="",
        multiline=True,
        placeholder_text="Text to ask about",
    )
    # Keep ParameterString's converter but narrow its input types from "any".
    context.input_types = CONTEXT_INPUT_TYPES
    return context


def advanced_group() -> ParameterGroup:
    """The collapsed Advanced group with the model choice."""
    with ParameterGroup(name="Advanced") as advanced_group:
        advanced_group.ui_options = {"collapsed": True}
        ParameterString(
            name="model",
            display_name="Model",
            tooltip="jev-latest is the newest stable model. jev-preview is the newest release, stable or not.",
            default_value=MODELS[0],
            allow_input=False,
            allow_output=False,
            traits={Options(choices=MODELS)},
        )
    return advanced_group


def missing_api_key_errors(node_name: str) -> list[Exception] | None:
    if not GriptapeNodes.SecretsManager().get_secret(API_KEY_NAME, should_error_on_not_found=False):
        return [
            ValueError(
                f"{node_name}: {API_KEY_NAME} is not set. "
                "Add it in Settings > API Keys & Secrets. Get a key at https://console.typesafe.ai/keys"
            )
        ]
    return None


@dataclass(frozen=True)
class NoulAnswer:
    noul: float


@dataclass(frozen=True)
class ChoiceAnswer:
    choice: str
    confidence: float
    probabilities: dict[str, float]


@dataclass(frozen=True)
class ScoreAnswer:
    score: float
    confidence: float
    # Keyed by level, counting from 0.
    probabilities: dict[int, float]


def make_question(question_type: str, *, instructions: str | None = None, criteria: Any = None) -> dict[str, Any]:
    """Build a JEV question, leaving out the optional fields that are None."""
    question: dict[str, Any] = {"type": question_type}
    if instructions is not None:
        question["instructions"] = instructions
    if criteria is not None:
        question["criteria"] = criteria
    return question


def ask_jev(
    node_name: str, model: str, state: str | dict | list, question: dict[str, Any]
) -> NoulAnswer | ChoiceAnswer | ScoreAnswer:
    """Ask JEV one question about the state and return its answer, with errors a user can act on."""
    api_key = GriptapeNodes.SecretsManager().get_secret(API_KEY_NAME, should_error_on_not_found=False)
    body = {"state": state, "model": model, "questions": {QUESTION_ID: question}}
    try:
        response = _post_with_retries(api_key, body)
    except httpx.TimeoutException as e:
        raise RuntimeError(f"{node_name}: JEV didn't answer within {TIMEOUT_SECONDS:g} seconds. Try again.") from e
    except httpx.HTTPError as e:
        raise RuntimeError(f"{node_name}: couldn't reach TypeSafe: {e}") from e

    if response.status_code == httpx.codes.UNAUTHORIZED:
        msg = f"{node_name}: TypeSafe rejected the API key. Check {API_KEY_NAME} in Settings > API Keys & Secrets."
        raise RuntimeError(msg)
    if response.is_error:
        raise RuntimeError(f"{node_name}: JEV request failed (HTTP {response.status_code}): {_error_message(response)}")
    try:
        return _parse_answer(response.json()["answers"][QUESTION_ID])
    except (ValueError, KeyError, TypeError, AttributeError) as e:
        raise RuntimeError(f"{node_name}: JEV sent back an answer this node can't read: {e!r}") from e


def _post_with_retries(api_key: str | None, body: dict[str, Any]) -> httpx.Response:
    """POST to JEV, retrying connection failures and busy or failing responses with backoff."""
    with httpx.Client(timeout=TIMEOUT_SECONDS, headers={"Authorization": f"Bearer {api_key}"}) as client:
        for attempt in range(MAX_RETRIES):
            try:
                response = client.post(API_URL, json=body)
            except httpx.TransportError:
                delay = _backoff(attempt)
            else:
                if response.status_code not in RETRY_STATUSES:
                    return response
                delay = _retry_after(response) or _backoff(attempt)
            time.sleep(delay)
        return client.post(API_URL, json=body)


def _backoff(attempt: int) -> float:
    """Double the delay each attempt up to the maximum, minus up to a quarter of it at random."""
    delay = min(BACKOFF_INITIAL_SECONDS * 2**attempt, BACKOFF_MAX_SECONDS)
    return delay * (1 - random.random() * 0.25)  # noqa: S311 - jitter, not cryptography


def _retry_after(response: httpx.Response) -> float | None:
    """The delay TypeSafe asked for in seconds, or None if it didn't ask for one we can read."""
    for header, seconds_per_unit in (("retry-after-ms", 0.001), ("retry-after", 1.0)):
        try:
            delay = float(response.headers[header]) * seconds_per_unit
        except (KeyError, ValueError):
            continue
        if delay >= 0:
            return min(delay, MAX_RETRY_DELAY_SECONDS)
    return None


def _error_message(response: httpx.Response) -> str:
    """Pull the readable message out of an error response, including which field a 422 is about."""
    try:
        body = response.json()
    except ValueError:
        return response.text[:500] or response.reason_phrase
    if isinstance(body, dict):
        for value in (body.get("error"), body.get("message"), body.get("detail")):
            if isinstance(value, str):
                return value
            if isinstance(value, dict) and isinstance(value.get("message"), str):
                return value["message"]
            if isinstance(value, list):
                parts = []
                for entry in value:
                    if isinstance(entry, dict) and isinstance(entry.get("msg"), str):
                        location = ".".join(str(part) for part in entry.get("loc", []) if part != "body")
                        parts.append(f"{location}: {entry['msg']}" if location else entry["msg"])
                if parts:
                    return "; ".join(parts)
    return response.text[:500] or response.reason_phrase


def _parse_answer(answer: dict[str, Any]) -> NoulAnswer | ChoiceAnswer | ScoreAnswer:
    match answer["type"]:
        case "noul":
            return NoulAnswer(noul=float(answer["noul"]))
        case "choice":
            return ChoiceAnswer(
                choice=str(answer["choice"]),
                confidence=float(answer["confidence"]),
                probabilities={str(k): float(v) for k, v in answer["probabilities"].items()},
            )
        case "score":
            # JSON object keys are strings, so turn the level keys back into numbers.
            return ScoreAnswer(
                score=float(answer["score"]),
                confidence=float(answer["confidence"]),
                probabilities={int(k): float(v) for k, v in answer["probabilities"].items()},
            )
    raise ValueError(f"unknown answer type {answer['type']!r}")
