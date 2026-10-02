from typing import Any

from griptape_nodes.exe_types.core_types import ControlParameterInput, Parameter, ParameterList
from griptape_nodes.exe_types.node_types import AsyncResult
from griptape_nodes.exe_types.param_types.parameter_float import ParameterFloat
from griptape_nodes.exe_types.param_types.parameter_json import ParameterJson
from griptape_nodes.exe_types.param_types.parameter_string import ParameterString

from jev_nodes.common import (
    ChoiceAnswer,
    advanced_group,
    ask_jev,
    context_parameter,
    make_question,
    missing_api_key_errors,
    to_state,
)
from jev_nodes.row_outputs import RowOutputsMixin, parse_row

MAX_OPTIONS = 255


class PickOne(RowOutputsMixin):
    # Each option's flow output is named after its row in Options, like option_38cf3387...
    ROWS_PARAM = "options"
    OUTPUT_PREFIX = "option_"

    def __init__(self, name: str, metadata: dict[Any, Any] | None = None) -> None:
        super().__init__(name, metadata)

        self.add_parameter(ControlParameterInput(tooltip="Run this node", name="exec_in"))
        self.add_parameter(context_parameter())
        self.add_parameter(
            ParameterString(
                name="question",
                display_name="Question",
                tooltip="Optional. What JEV should decide about the context, "
                "for example 'Which department should handle this note?'",
                default_value="",
                placeholder_text="Which option fits the text best?",
                allow_output=False,
            )
        )
        self.options = ParameterList(
            name="options",
            display_name="Options",
            tooltip="One option per row. Write a short label, or 'Label: description' to tell JEV what the "
            "option means. Each option gets its own flow output, and the flow follows the one JEV picks.",
            type="str",
            ui_options={"placeholder_text": "label: description"},
            max_items=MAX_OPTIONS,
        )
        self.add_parameter(self.options)

        self.add_parameter(
            ParameterString(
                name="choice",
                display_name="Choice",
                tooltip="The label of the option JEV picked.",
                allow_input=False,
                allow_property=False,
                placeholder_text="The label of the option JEV picked.",
            )
        )
        self.add_parameter(
            ParameterString(
                name="description",
                display_name="Description",
                tooltip="The description of the option JEV picked, the text after its colon. Empty if the "
                "option has no description.",
                allow_input=False,
                allow_property=False,
                placeholder_text="The description of the option JEV picked.",
            )
        )
        self.add_parameter(
            ParameterFloat(
                name="confidence",
                display_name="Confidence",
                tooltip="How sure JEV is of its pick, from 0 to 1. Low values mean the text could fit "
                "another option too.",
                allow_input=False,
                allow_property=False,
            )
        )
        self.add_parameter(
            ParameterJson(
                name="probabilities",
                display_name="Probabilities",
                tooltip="JEV's probability for every option, keyed by label. They add up to about 1.",
                allow_input=False,
                allow_property=False,
                placeholder_text="JEV's probability for every option, keyed by label.",
            )
        )

        self.add_node_element(advanced_group())

    def _row_output_label(self, index: int, text: str) -> str:
        return parse_row(text)[0]

    def _criteria(self) -> dict[str, str | None]:
        """Read Options into JEV criteria: labels mapped to descriptions, or None for undescribed labels."""
        criteria: dict[str, str | None] = {}
        for row in self.get_parameter_value("options") or []:
            label, description = parse_row(row)
            if not label:
                continue
            if label in criteria:
                raise ValueError(
                    f"{self.name}: '{label}' appears more than once in Options. Each label must be unique."
                )
            criteria[label] = description
        if len(criteria) < 2:  # noqa: PLR2004
            raise ValueError(f"{self.name}: Options needs at least two options for JEV to pick from.")
        return criteria

    def validate_before_node_run(self) -> list[Exception] | None:
        return missing_api_key_errors(self.name)

    def process(self) -> AsyncResult[None]:
        # Clear the last run's pick so a failed call can't route down a stale branch.
        self.parameter_output_values.pop("choice", None)
        self.parameter_output_values.pop("description", None)
        yield lambda: self._ask()

    def _ask(self) -> None:
        state = to_state(self.get_parameter_value("context"))
        if state is None:
            raise ValueError(f"{self.name}: Context is empty. Connect or type the text to ask about.")
        question = (self.get_parameter_value("question") or "").strip()
        criteria = self._criteria()
        choice = make_question("choice", instructions=question or None, criteria=criteria)

        result = ask_jev(self.name, self.get_parameter_value("model"), state, choice)
        if not isinstance(result, ChoiceAnswer):
            raise RuntimeError(f"{self.name}: expected a choice from JEV, got {type(result).__name__}.")

        self.parameter_output_values["confidence"] = result.confidence
        self.parameter_output_values["probabilities"] = result.probabilities
        self.parameter_output_values["description"] = criteria.get(result.choice) or ""
        self.parameter_output_values["choice"] = result.choice

    def get_next_control_output(self) -> Parameter | None:
        # Returning None ends the flow here. Don't set stop_flow: the engine never clears it, so every
        # later run would dead-end too.
        picked = self.parameter_output_values.get("choice")
        if picked is None:
            return None
        for param in self._row_output_params():
            if param.display_name == picked:
                return param
        return None
