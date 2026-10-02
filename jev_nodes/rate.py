from typing import Any

from griptape_nodes.exe_types.core_types import ControlParameterInput, Parameter, ParameterList
from griptape_nodes.exe_types.node_types import AsyncResult
from griptape_nodes.exe_types.param_types.parameter_float import ParameterFloat
from griptape_nodes.exe_types.param_types.parameter_int import ParameterInt
from griptape_nodes.exe_types.param_types.parameter_json import ParameterJson
from griptape_nodes.exe_types.param_types.parameter_string import ParameterString

from jev_nodes.common import (
    ScoreAnswer,
    advanced_group,
    ask_jev,
    context_parameter,
    make_question,
    missing_api_key_errors,
    to_state,
)
from jev_nodes.row_outputs import RowOutputsMixin, parse_row

# The API accepts up to 10 levels.
MAX_LEVELS = 10


def level_description(text: str) -> str:
    """The part of a Levels row that JEV rates against: the text after the colon, or the whole row."""
    label, description = parse_row(text)
    return description or label


class Rate(RowOutputsMixin):
    # Each level's flow output is named after its row in Levels, like rate_level_38cf3387...
    ROWS_PARAM = "levels"
    OUTPUT_PREFIX = "rate_level_"

    def __init__(self, name: str, metadata: dict[Any, Any] | None = None) -> None:
        super().__init__(name, metadata)
        self._route: str | None = None

        self.add_parameter(ControlParameterInput(tooltip="Run this node", name="exec_in"))

        self.add_parameter(context_parameter())
        self.add_parameter(
            ParameterString(
                name="question",
                display_name="Question",
                tooltip="Optional. What JEV should rate about the context, for example 'How urgent is this message?'",
                default_value="",
                placeholder_text="How ... is the text?",
                allow_output=False,
            )
        )
        self.add_parameter(
            ParameterList(
                name="levels",
                display_name="Levels",
                tooltip="One level per row, lowest first, up to 10. Describe the situation at each level, like "
                "'Broken, but a workaround exists', not a degree like 'Moderate'. To name a level's flow output, "
                "put a label before a colon, like 'Minor: broken, but a workaround exists'.",
                type="str",
                ui_options={"placeholder_text": "Describe this level"},
                max_items=MAX_LEVELS,
            )
        )

        self.add_parameter(
            ParameterFloat(
                name="score",
                display_name="Score",
                tooltip="JEV's score, from 1 to the number of levels. It weighs every level by its probability, "
                "so it can fall between levels, like 1.7.",
                allow_input=False,
                allow_property=False,
            )
        )
        self.add_parameter(
            ParameterInt(
                name="level",
                display_name="Level",
                tooltip="The score rounded to the nearest level, as a whole number starting at 1. The flow takes "
                "this level's output.",
                allow_input=False,
                allow_property=False,
            )
        )
        self.add_parameter(
            ParameterString(
                name="level_description",
                display_name="Level Description",
                tooltip="The description of Level.",
                allow_input=False,
                allow_property=False,
                placeholder_text="The description of Level.",
            )
        )
        self.add_parameter(
            ParameterFloat(
                name="confidence",
                display_name="Confidence",
                tooltip="How sure JEV is of its score, from 0 to 1. Low values mean JEV spread its probability "
                "across levels. The levels may overlap, or the context may not say enough.",
                allow_input=False,
                allow_property=False,
            )
        )
        self.add_parameter(
            ParameterJson(
                name="probabilities",
                display_name="Probabilities",
                tooltip="JEV's probability for every level, keyed by level number. They add up to about 1.",
                allow_input=False,
                allow_property=False,
                placeholder_text="JEV's probability for every level, keyed by level number.",
            )
        )

        self.add_node_element(advanced_group())

    def _row_output_label(self, index: int, text: str) -> str:
        label, description = parse_row(text)
        # Count from 1 to match the row numbers in Levels.
        return label if description else f"Level {index + 1}"

    def _row_output_tooltip(self, label: str) -> str:
        return f"Taken when the score rounds to {label}."

    def validate_before_node_run(self) -> list[Exception] | None:
        return missing_api_key_errors(self.name)

    def process(self) -> AsyncResult[None]:
        # Clear the last run's result so a failed call can't route down a stale branch.
        self._route = None
        yield lambda: self._ask()

    def _ask(self) -> None:
        state = to_state(self.get_parameter_value("context"))
        if state is None:
            raise ValueError(f"{self.name}: Context is empty. Connect or type the text to rate.")
        rows = self._rows()
        if len(rows) < 2:  # noqa: PLR2004
            raise ValueError(f"{self.name}: Levels needs at least two levels for JEV to rate against.")
        if len(rows) > MAX_LEVELS:
            raise ValueError(f"{self.name}: Levels has {len(rows)} levels. JEV accepts up to {MAX_LEVELS}.")
        descriptions = [level_description(text) for _, text in rows]
        question = (self.get_parameter_value("question") or "").strip()
        score = make_question("score", instructions=question or None, criteria=descriptions)

        result = ask_jev(self.name, self.get_parameter_value("model"), state, score)
        if not isinstance(result, ScoreAnswer):
            raise RuntimeError(f"{self.name}: expected a score from JEV, got {type(result).__name__}.")

        # JEV numbers levels from 0. Report them from 1 to match the row numbers in Levels.
        # Round half up rather than to even, so 1.5 reads as the higher level.
        index = min(int(result.score + 0.5), len(rows) - 1)
        self.parameter_output_values["score"] = result.score + 1
        self.parameter_output_values["level"] = index + 1
        self.parameter_output_values["level_description"] = descriptions[index]
        self.parameter_output_values["confidence"] = result.confidence
        self.parameter_output_values["probabilities"] = {str(k + 1): v for k, v in sorted(result.probabilities.items())}
        self._route = rows[index][0]

    def get_next_control_output(self) -> Parameter | None:
        # Returning None ends the flow here. Don't set stop_flow: the engine never clears it, so every
        # later run would dead-end too.
        if self._route is None:
            return None
        return self.get_parameter_by_name(self._route)
