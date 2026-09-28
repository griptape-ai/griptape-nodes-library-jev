"""Shared parameters and JEV calls for the nodes in this library."""

import json

from griptape_nodes.exe_types.core_types import ParameterGroup
from griptape_nodes.exe_types.param_types.parameter_string import ParameterString
from griptape_nodes.retained_mode.griptape_nodes import GriptapeNodes
from griptape_nodes.traits.options import Options
from typesafe_sdk import Choice, Noul, Score, TypeSafeAuthenticationError, TypeSafeClient, TypeSafeError

API_KEY_NAME = "TYPESAFE_API_KEY"
MODELS = ["jev-latest", "jev-preview"]
QUESTION_ID = "answer"

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


def ask_jev(node_name: str, model: str, state: str | dict | list, question: Noul | Choice | Score) -> object:
    """Ask JEV one question about the state and return its answer, with errors a user can act on."""
    api_key = GriptapeNodes.SecretsManager().get_secret(API_KEY_NAME, should_error_on_not_found=False)
    try:
        with TypeSafeClient(api_key=api_key, model=model) as client:
            response = client.system_one(state=state, questions={QUESTION_ID: question})
    except TypeSafeAuthenticationError as e:
        msg = f"{node_name}: TypeSafe rejected the API key. Check {API_KEY_NAME} in Settings > API Keys & Secrets."
        raise RuntimeError(msg) from e
    except TypeSafeError as e:
        raise RuntimeError(f"{node_name}: JEV request failed: {e}") from e
    return response.answers[QUESTION_ID]
