from typing import Any

from griptape_nodes.exe_types.core_types import (
    ControlParameterInput,
    ControlParameterOutput,
    Parameter,
    ParameterList,
)
from griptape_nodes.exe_types.node_types import AsyncResult, BaseNode, NodeResolutionState
from griptape_nodes.exe_types.param_types.parameter_float import ParameterFloat
from griptape_nodes.exe_types.param_types.parameter_json import ParameterJson
from griptape_nodes.exe_types.param_types.parameter_string import ParameterString
from griptape_nodes.retained_mode.events.connection_events import (
    DeleteConnectionRequest,
    ListConnectionsForNodeRequest,
    ListConnectionsForNodeResultSuccess,
)
from typesafe_sdk import Choice, ChoiceAnswer

from jev_nodes.common import advanced_group, ask_jev, context_parameter, missing_api_key_errors, to_state

# Each option's flow output is named after its row in Options, like option_38cf3387...
OPTION_PREFIX = "option_"
MAX_OPTIONS = 255


def parse_option(text: str | None) -> tuple[str, str | None]:
    """Split 'Label: description' at the first colon. A row without a colon is just a label."""
    label, _, description = (text or "").partition(":")
    return label.strip(), description.strip() or None


class PickOne(BaseNode):
    def __init__(self, name: str, metadata: dict[Any, Any] | None = None) -> None:
        super().__init__(name, metadata)
        self._syncing = False

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

    # Adding, deleting, or reordering a row in Options fires no value hook, but each one marks the node
    # unresolved. Catch that here to keep the flow outputs in step with the list.
    @property
    def state(self) -> NodeResolutionState:
        return self._state

    @state.setter
    def state(self, new_state: NodeResolutionState) -> None:
        self._state = new_state
        if new_state == NodeResolutionState.UNRESOLVED:
            self._sync_option_outputs()

    def after_value_set(self, parameter: Parameter, value: Any) -> None:
        if parameter.name == "options":
            self._sync_option_outputs()
        return super().after_value_set(parameter, value)

    def add_parameter(self, param: Parameter) -> None:
        # Loading a saved workflow recreates added parameters as plain Parameters, which loses the
        # flow output class. Rebuild option outputs as real flow outputs, keeping the saved label.
        if param.name.startswith(OPTION_PREFIX) and not isinstance(param, ControlParameterOutput):
            param = ControlParameterOutput(name=param.name, display_name=param.display_name, tooltip=param.tooltip)
        super().add_parameter(param)

    def _option_outputs(self) -> list[Parameter]:
        return [p for p in self.parameters if p.name.startswith(OPTION_PREFIX)]

    def _sync_option_outputs(self) -> None:
        """Give each non-empty row a flow output, labeled and ordered to match the list.

        Outputs are named after their row's unique ID rather than its position, so reordering, renaming,
        and deleting rows keep every wire on the option it was connected to.
        """
        options = getattr(self, "options", None)
        if self._syncing or not isinstance(options, ParameterList):
            return
        self._syncing = True
        try:
            wanted: list[tuple[str, str]] = []
            for row in options.get_child_parameters():
                output_name = OPTION_PREFIX + row.name.rsplit("_", 1)[-1]
                if row.name not in self.parameter_values:
                    # The row's value hasn't arrived yet, as while a saved workflow loads. Keep its output.
                    existing = self.get_parameter_by_name(output_name)
                    if existing is not None:
                        wanted.append((output_name, existing.display_name or ""))
                    continue
                label, _ = parse_option(self.parameter_values[row.name])
                if label:
                    wanted.append((output_name, label))

            wanted_names = [name for name, _ in wanted]
            for param in self._option_outputs():
                if param.name not in wanted_names:
                    self._delete_output_connections(param.name)
                    self.remove_parameter_element(param)

            for name, label in wanted:
                param = self.get_parameter_by_name(name)
                if param is None:
                    self.add_parameter(
                        ControlParameterOutput(name=name, display_name=label, tooltip=f"Taken when JEV picks {label}.")
                    )
                elif param.display_name != label:
                    param.display_name = label
                    param.tooltip = f"Taken when JEV picks {label}."

            # Re-adding an element moves it to the end, so re-add them all in list order. They're the same
            # objects, so their wires stay attached.
            if [p.name for p in self._option_outputs()] != wanted_names:
                for name in wanted_names:
                    param = self.get_parameter_by_name(name)
                    if param is not None:
                        self.root_ui_element.remove_child(param)
                        self.root_ui_element.add_child(param)
        finally:
            self._syncing = False

    def _delete_output_connections(self, parameter_name: str) -> None:
        """Delete the wires leaving an output. Removing the output alone leaves them behind in the engine."""
        result = self.engine.handle_request(ListConnectionsForNodeRequest(node_name=self.name, broadcast_result=False))
        if not isinstance(result, ListConnectionsForNodeResultSuccess):
            return
        for connection in result.outgoing_connections:
            if connection.source_parameter_name == parameter_name:
                self.engine.handle_request(
                    DeleteConnectionRequest(
                        source_node_name=self.name,
                        source_parameter_name=parameter_name,
                        target_node_name=connection.target_node_name,
                        target_parameter_name=connection.target_parameter_name,
                    )
                )

    def _criteria(self) -> dict[str, str | None]:
        """Read Options into JEV criteria: labels mapped to descriptions, or None for undescribed labels."""
        criteria: dict[str, str | None] = {}
        for row in self.get_parameter_value("options") or []:
            label, description = parse_option(row)
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
        choice = Choice(criteria=criteria, instructions=question or None)

        result = ask_jev(self.name, self.get_parameter_value("model"), state, choice)
        if not isinstance(result, ChoiceAnswer):
            raise RuntimeError(f"{self.name}: expected a choice from JEV, got {type(result).__name__}.")

        self.parameter_output_values["confidence"] = result.confidence
        self.parameter_output_values["probabilities"] = dict(result.probabilities)
        self.parameter_output_values["description"] = criteria.get(result.choice) or ""
        self.parameter_output_values["choice"] = result.choice

    def get_next_control_output(self) -> Parameter | None:
        # Returning None ends the flow here. Don't set stop_flow: the engine never clears it, so every
        # later run would dead-end too.
        picked = self.parameter_output_values.get("choice")
        if picked is None:
            return None
        for param in self._option_outputs():
            if param.display_name == picked:
                return param
        return None
