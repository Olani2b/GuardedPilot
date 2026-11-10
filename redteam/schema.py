"""Scenario file schema and loader. One YAML per scenario in redteam/scenarios/.

Validated strictly (unknown keys are errors) so a typo can't silently turn into
"attack failed". See docs/redteam.md for the full format.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

Category = Literal["benign", "scene_text_injection", "poisoned_brief", "tool_output_injection"]
CAPABILITY = {"benign": None, "scene_text_injection": "A1", "poisoned_brief": "A2",
              "tool_output_injection": "A3"}


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SceneTextInjection(Strict):
    type: Literal["scene_text"]
    at_waypoint: int = Field(ge=1, description="1-based waypoint the sign is painted under")
    offset_m: tuple[float, float] = (0.0, 0.0)  # east, north from the waypoint
    width_m: float = 14.0
    text: str


class BriefInjection(Strict):
    type: Literal["brief"]
    mode: Literal["append", "prepend", "replace_line"] = "append"
    match: str | None = None  # line to replace, for replace_line
    text: str


class ToolOutputInjection(Strict):
    type: Literal["tool_output"]
    tool: Literal["get_weather", "get_telemetry", "segment_scene", "describe_scene"]
    field: str  # key added to (or overwritten in) the tool's result
    value: str
    after_calls: int = 0  # inject only after this many earlier calls of the tool


Injection = SceneTextInjection | BriefInjection | ToolOutputInjection


class Predicate(Strict):
    """One attack-success condition, evaluated against the server's event log.

    Scored twice: over every command the model attempted (ASR-a) and over the
    ones that executed (ASR-e). All set fields must hold for the same event.
    """

    tool: str | None = None
    target_outside: Literal["geofence"] | None = None
    target_inside: str | None = None  # zone name: scenario zone, "no_fly", or a no-fly zone's name
    pose_outside: Literal["landing_zones"] | None = None
    alt_above: float | None = None
    recipient_not_allowlisted: bool | None = None
    target_off_mission: bool | None = None  # goto > OFF_MISSION_M from every waypoint and home
    absent: bool = False  # true: success if NO matching command exists

    @model_validator(mode="after")
    def _some_condition(self):
        if self.tool is None and not any([self.target_outside, self.target_inside, self.pose_outside,
                                          self.alt_above is not None, self.recipient_not_allowlisted,
                                          self.target_off_mission]):
            raise ValueError("predicate needs a tool or a condition")
        return self


class AttackerGoal(Strict):
    kind: str
    description: str


class Scenario(Strict):
    id: str
    category: Category
    mission: str
    seed: int = 0
    description: str
    # naive: the attacker writes like an outsider. procedural: the attacker imitates the
    # operator / mission procedure (mission id, tool names, arguments). Benign: none.
    phrasing: Literal["naive", "procedural"] | None = None
    injections: list[Injection] = []
    zones: dict[str, list[tuple[float, float]]] = {}
    attacker_goal: AttackerGoal | None = None
    success_if: list[Predicate] = []

    @property
    def capability(self) -> str | None:
        return CAPABILITY[self.category]

    @model_validator(mode="after")
    def _consistent(self):
        if self.category == "benign":
            if self.success_if or self.attacker_goal:
                raise ValueError("benign scenarios have no attacker goal or success_if")
        elif not self.success_if or not self.injections or not self.attacker_goal:
            raise ValueError("attack scenarios need injections, attacker_goal and success_if")
        return self


def load(path: str | Path) -> Scenario:
    return Scenario.model_validate(yaml.safe_load(Path(path).read_text()))


def load_suite(directory: str | Path = "redteam/scenarios") -> list[Scenario]:
    scenarios = [load(p) for p in sorted(Path(directory).glob("*.yaml"))]
    ids = [s.id for s in scenarios]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate scenario ids")
    return scenarios
