import json
from typing import Any

from griptape_nodes.exe_types.core_types import (
    ControlParameterInput,
    ControlParameterOutput,
    Parameter,
    ParameterGroup,
    ParameterMode,
    ParameterTypeBuiltin,
)
from griptape_nodes.exe_types.node_types import AsyncResult, BaseNode
from griptape_nodes.exe_types.param_types.parameter_bool import ParameterBool
from griptape_nodes.exe_types.param_types.parameter_float import ParameterFloat
from griptape_nodes.exe_types.param_types.parameter_string import ParameterString
from griptape_nodes.retained_mode.griptape_nodes import GriptapeNodes
from griptape_nodes.traits.options import Options
from typesafe_sdk import (
    Noul,
    NoulAnswer,
    NoulCriteria,
    TypeSafeAuthenticationError,
    TypeSafeClient,
    TypeSafeError,
)

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


class AskYesNo(BaseNode):
    def __init__(self, name: str, metadata: dict[Any, Any] | None = None) -> None:
        super().__init__(name, metadata)

        self.add_parameter(ControlParameterInput(tooltip="Run this node", name="exec_in"))
        self.add_parameter(
            ControlParameterOutput(
                name="yes",
                display_name="Yes",
                tooltip="Taken when the probability of yes is at or above the threshold.",
            )
        )
        self.add_parameter(
            ControlParameterOutput(
                name="no",
                display_name="No",
                tooltip="Taken when the probability of yes is below the threshold.",
            )
        )

        context = ParameterString(
            name="context",
            display_name="Context",
            tooltip="The text JEV should read to answer the question. JSON works too. JEV accepts text only. "
            "To ask about an image, describe it first with a node like Describe Image.",
            default_value="",
            multiline=True,
            placeholder_text="Text to ask about",
            allow_output=False,
        )
        # Keep ParameterString's converter but narrow its input types from "any".
        context.input_types = CONTEXT_INPUT_TYPES
        self.add_parameter(context)
        self.add_parameter(
            ParameterString(
                name="question",
                display_name="Question",
                tooltip="A yes/no question about the context. Ask one narrow thing, "
                "for example 'Does the customer ask for a refund?'",
                default_value="",
                multiline=True,
                placeholder_text="Does the text ...?",
                allow_output=False,
            )
        )

        with ParameterGroup(name="Define Yes and No (optional)") as criteria_group:
            criteria_group.ui_options = {"collapsed": True}
            ParameterString(
                name="yes_means",
                display_name="Yes means",
                tooltip="Optional. Describe what should count as yes. Most questions don't need this. "
                "Use it when the line between yes and no is subtle.",
                default_value="",
                multiline=True,
                placeholder_text="Mentions a prior attempt, ticket, or asking before",
                allow_output=False,
            )
            ParameterString(
                name="no_means",
                display_name="No means",
                tooltip="Optional. Describe what should count as no. Most questions don't need this. "
                "Use it when the line between yes and no is subtle.",
                default_value="",
                multiline=True,
                placeholder_text="No sign of any previous contact",
                allow_output=False,
            )
        self.add_node_element(criteria_group)

        self.add_parameter(
            ParameterFloat(
                name="threshold",
                display_name="Say Yes at or above",
                tooltip="Take the Yes branch when the probability of yes is at least this value. "
                "Raise it to say Yes only when JEV is very sure. Lower it to say Yes on weaker signals.",
                default_value=0.5,
                slider=True,
                min_val=0.0,
                max_val=1.0,
                step=0.01,
                allow_output=False,
            )
        )

        self.add_parameter(
            ParameterBool(
                name="answer",
                display_name="Answer",
                tooltip="True if the probability of yes is at or above the threshold.",
                allow_input=False,
                allow_property=False,
            )
        )
        self.add_parameter(
            ParameterFloat(
                name="probability",
                display_name="Probability",
                tooltip="JEV's probability that the answer is yes, from 0 to 1. "
                "Values near 0.5 mean yes and no are about equally likely.",
                allow_input=False,
                allow_property=False,
            )
        )

        with ParameterGroup(name="Data Outputs") as data_group:
            data_group.ui_options = {"collapsed": True}
            Parameter(
                name="data_if_yes",
                display_name="Data if Yes",
                tooltip="Passed to Output when the answer is Yes. Whatever feeds this runs before the question "
                "is asked, even if the answer is No, so put expensive work after the Yes branch instead.",
                input_types=["any"],
                type="any",
                default_value=None,
                allowed_modes={ParameterMode.INPUT},
            )
            Parameter(
                name="data_if_no",
                display_name="Data if No",
                tooltip="Passed to Output when the answer is No. Whatever feeds this runs before the question "
                "is asked, even if the answer is Yes, so put expensive work after the No branch instead.",
                input_types=["any"],
                type="any",
                default_value=None,
                allowed_modes={ParameterMode.INPUT},
            )
            Parameter(
                name="output",
                display_name="Output",
                tooltip="Data if Yes or Data if No, depending on the answer.",
                output_type=ParameterTypeBuiltin.ALL.value,
                type=ParameterTypeBuiltin.ALL.value,
                default_value=None,
                allowed_modes={ParameterMode.OUTPUT},
            )
        self.add_node_element(data_group)

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
        self.add_node_element(advanced_group)

    def validate_before_node_run(self) -> list[Exception] | None:
        if not GriptapeNodes.SecretsManager().get_secret(API_KEY_NAME, should_error_on_not_found=False):
            return [
                ValueError(
                    f"{self.name}: {API_KEY_NAME} is not set. "
                    "Add it in Settings > API Keys & Secrets. Get a key at https://console.typesafe.ai/keys"
                )
            ]
        return None

    def process(self) -> AsyncResult[None]:
        # Clear the last run's answer so a failed call can't route down a stale branch.
        self.parameter_output_values.pop("answer", None)
        yield lambda: self._ask()

    def _ask(self) -> None:
        state = to_state(self.get_parameter_value("context"))
        if state is None:
            raise ValueError(f"{self.name}: Context is empty. Connect or type the text to ask about.")
        question = (self.get_parameter_value("question") or "").strip()
        if not question:
            raise ValueError(f"{self.name}: Question is empty.")

        # Only send the sides the user filled in. JEV accepts either one alone.
        criteria: NoulCriteria = {}
        if yes_means := (self.get_parameter_value("yes_means") or "").strip():
            criteria["true"] = yes_means
        if no_means := (self.get_parameter_value("no_means") or "").strip():
            criteria["false"] = no_means
        noul = Noul(instructions=question, criteria=criteria or None)

        api_key = GriptapeNodes.SecretsManager().get_secret(API_KEY_NAME, should_error_on_not_found=False)
        try:
            with TypeSafeClient(api_key=api_key, model=self.get_parameter_value("model")) as client:
                response = client.system_one(state=state, questions={QUESTION_ID: noul})
        except TypeSafeAuthenticationError as e:
            msg = f"{self.name}: TypeSafe rejected the API key. Check {API_KEY_NAME} in Settings > API Keys & Secrets."
            raise RuntimeError(msg) from e
        except TypeSafeError as e:
            raise RuntimeError(f"{self.name}: JEV request failed: {e}") from e

        result = response.answers[QUESTION_ID]
        if not isinstance(result, NoulAnswer):
            raise RuntimeError(f"{self.name}: expected a yes/no answer from JEV, got {type(result).__name__}.")

        answer = result.noul >= self.get_parameter_value("threshold")
        self.parameter_output_values["probability"] = result.noul
        self.parameter_output_values["output"] = self.get_parameter_value("data_if_yes" if answer else "data_if_no")
        self.parameter_output_values["answer"] = answer

    def get_next_control_output(self) -> Parameter | None:
        if "answer" not in self.parameter_output_values:
            self.stop_flow = True
            return None
        return self.get_parameter_by_name("yes" if self.parameter_output_values["answer"] else "no")
