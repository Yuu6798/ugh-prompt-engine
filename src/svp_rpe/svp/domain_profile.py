"""Domain profile support for SVP value vocabularies and templates."""
from __future__ import annotations

from importlib.resources import files
from pathlib import Path
from string import Formatter
from typing import Any, Dict, Iterable, List, Mapping, Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field


class ProfileModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LabelRule(ProfileModel):
    condition: Dict[str, Any] = Field(default_factory=dict)
    label: str


class LabelsRule(ProfileModel):
    condition: Dict[str, Any] = Field(default_factory=dict)
    labels: List[str]


class TemplateRule(ProfileModel):
    id: str
    condition: Dict[str, Any] = Field(default_factory=dict)
    template: str


class DiffMetricSpec(ProfileModel):
    name: str
    tolerance: Optional[float] = None
    unit: Optional[str] = None
    exact_match: bool = False


class DomainProfile(ProfileModel):
    """Externalized domain vocabulary for generic SVP fields."""

    schema_version: str = "1.0"
    domain: str
    source_artifact_type: str = "artifact"
    default_por_surface: List[str] = Field(default_factory=lambda: ["unspecified"])
    default_grv_primary: str = "neutral"
    prompt_template: str = "Core character: {por_core}"
    por_check_template: str = "Maintains core character: {por_core}"
    grv_check_template: str = "Preserves gravity anchor: {grv_primary}"
    delta_e_check_template: str = "Energy follows {delta_e_transition} pattern"
    style_tag_sources: List[str] = Field(default_factory=lambda: ["por_surface"])
    por_surface_rules: List[LabelsRule] = Field(default_factory=list)
    grv_primary_vocab: List[LabelRule] = Field(default_factory=list)
    section_label_vocab: List[str] = Field(default_factory=list)
    constraint_templates: List[TemplateRule] = Field(default_factory=list)
    physical_check_templates: List[TemplateRule] = Field(default_factory=list)
    minimal_constraint_templates: List[TemplateRule] = Field(default_factory=list)
    delta_e_vocab: List[LabelRule] = Field(default_factory=list)
    diff_metrics: List[DiffMetricSpec] = Field(default_factory=list)

    def select_por_surface(self, context: Mapping[str, Any]) -> List[str]:
        labels: List[str] = []
        for rule in self.por_surface_rules:
            if _matches(rule.condition, context):
                labels.extend(rule.labels)
        return _unique(labels) or list(self.default_por_surface)

    def select_grv_primary(self, context: Mapping[str, Any]) -> str:
        for rule in self.grv_primary_vocab:
            if _matches(rule.condition, context):
                return rule.label
        return self.default_grv_primary

    def render_prompt(self, context: Mapping[str, Any]) -> str:
        return _format_block(self.prompt_template, context) or self.prompt_template

    def render_por_check(self, context: Mapping[str, Any]) -> str:
        return _format_if_complete(self.por_check_template, context) or self.por_check_template

    def render_grv_check(self, context: Mapping[str, Any]) -> str:
        return _format_if_complete(self.grv_check_template, context) or self.grv_check_template

    def render_delta_e_check(self, context: Mapping[str, Any]) -> str:
        rendered = _format_if_complete(self.delta_e_check_template, context)
        return rendered or self.delta_e_check_template

    def render_constraints(self, context: Mapping[str, Any]) -> List[str]:
        return _render_templates(self.constraint_templates, context)

    def render_physical_checks(self, context: Mapping[str, Any]) -> List[str]:
        return _render_templates(self.physical_check_templates, context)

    def render_minimal_constraints(self, context: Mapping[str, Any]) -> List[str]:
        return _render_templates(self.minimal_constraint_templates, context)

    def build_style_tags(
        self,
        context: Mapping[str, Any],
        por_surface: Iterable[str],
    ) -> List[str]:
        tags: List[str] = []
        for source in self.style_tag_sources:
            if source == "por_surface":
                tags.extend(por_surface)
                continue
            value = _value_for(source, context)
            if isinstance(value, list):
                tags.extend(str(item) for item in value)
            elif value is not None:
                tags.append(str(value))
        return _unique(tags)

    def format_structure_summary(self, context: Mapping[str, Any]) -> str:
        existing = _value_for("structure_summary", context)
        if existing:
            return str(existing)

        sections = _value_for("sections", context) or _value_for("structure", context)
        labels: List[str] = []
        if isinstance(sections, list):
            for item in sections:
                if isinstance(item, Mapping):
                    label = item.get("label") or item.get("name") or item.get("type")
                else:
                    label = getattr(item, "label", item)
                if label:
                    labels.append(str(label))

        if not labels:
            section_count = _value_for("section_count", context)
            if isinstance(section_count, int) and section_count > 0:
                labels = self.section_label_vocab[:section_count]

        if labels:
            return f"{len(labels)} sections: {', '.join(labels)}"
        return "0 sections"

    def format_delta_e(self, context: Mapping[str, Any]) -> str:
        existing = _value_for("delta_e_profile", context)
        if existing:
            return str(existing)
        transition = _value_for("delta_e_transition", context)
        if transition:
            label = str(transition)
        else:
            for rule in self.delta_e_vocab:
                if _matches(rule.condition, context):
                    label = rule.label
                    break
            else:
                label = "stable"
        intensity = _value_for("delta_e_intensity", context)
        if isinstance(intensity, (int, float)):
            return f"{label} ({float(intensity):.2f})"
        return label

    @property
    def diff_metric_names(self) -> List[str]:
        return [metric.name for metric in self.diff_metrics]

    @property
    def diff_tolerances(self) -> Dict[str, float]:
        return {
            metric.name: metric.tolerance
            for metric in self.diff_metrics
            if metric.tolerance is not None
        }


def load_domain_profile(domain: str = "music", path: Optional[Path | str] = None) -> DomainProfile:
    if path is not None:
        data = _load_profile_file(Path(path))
    else:
        data = _load_local_profile(domain)
        if data is None:
            data = _load_packaged_profile(domain)
        if data is None:
            searched = ", ".join(str(p) for p in _local_profile_paths(domain))
            raise FileNotFoundError(
                f"domain profile not found for {domain!r}; searched {searched} "
                "and packaged svp_rpe.config.domain_profiles resources"
            )
    if not isinstance(data, dict):
        raise ValueError(f"domain profile must be a mapping: {domain}")
    return DomainProfile.model_validate(data)


def _load_profile_file(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError(f"domain profile must be a mapping: {path}")
    return data


def _load_local_profile(domain: str) -> Optional[dict[str, Any]]:
    for candidate in _local_profile_paths(domain):
        if candidate.is_file():
            return _load_profile_file(candidate)
    return None


def _load_packaged_profile(domain: str) -> Optional[dict[str, Any]]:
    try:
        resource = files("svp_rpe.config.domain_profiles").joinpath(f"{domain}.yaml")
    except ModuleNotFoundError:
        return None
    if not resource.is_file():
        return None
    data = yaml.safe_load(resource.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"domain profile must be a mapping: packaged {domain}.yaml")
    return data


def _local_profile_paths(domain: str) -> list[Path]:
    return [
        Path(__file__).resolve().parents[3] / "config" / "domain_profiles" / f"{domain}.yaml",
        Path.cwd() / "config" / "domain_profiles" / f"{domain}.yaml",
    ]


def _render_templates(rules: Iterable[TemplateRule], context: Mapping[str, Any]) -> List[str]:
    rendered: List[str] = []
    for rule in rules:
        if not _matches(rule.condition, context):
            continue
        value = _format_if_complete(rule.template, context)
        if value:
            rendered.append(value)
    return rendered


def _format_block(template: str, context: Mapping[str, Any]) -> Optional[str]:
    rendered_lines: List[str] = []
    for line in template.splitlines():
        rendered = _format_if_complete(line, context)
        if rendered is not None:
            rendered_lines.append(rendered)
    if not rendered_lines:
        return None
    return "\n".join(rendered_lines)


def _format_if_complete(template: str, context: Mapping[str, Any]) -> Optional[str]:
    field_names = [
        part[1].split(".", 1)[0]
        for part in Formatter().parse(template)
        if part[1] is not None and part[1] != ""
    ]
    for field_name in field_names:
        if _value_for(field_name, context) is None:
            return None
    try:
        return template.format_map(_FormatContext(context))
    except (KeyError, ValueError):
        return None


class _FormatContext(dict):
    def __init__(self, context: Mapping[str, Any]) -> None:
        super().__init__()
        self._context = context

    def __missing__(self, key: str) -> Any:
        value = _value_for(key, self._context)
        if value is None:
            raise KeyError(key)
        return value


def _matches(condition: Mapping[str, Any], context: Mapping[str, Any]) -> bool:
    for raw_key, expected in condition.items():
        if raw_key.endswith("_min"):
            key = raw_key[: -len("_min")]
            value = _value_for(key, context)
            if not isinstance(value, (int, float)) or float(value) < float(expected):
                return False
        elif raw_key.endswith("_max"):
            key = raw_key[: -len("_max")]
            value = _value_for(key, context)
            if not isinstance(value, (int, float)) or float(value) > float(expected):
                return False
        elif raw_key.endswith("_exists"):
            key = raw_key[: -len("_exists")]
            exists = _value_for(key, context) is not None
            if bool(expected) != exists:
                return False
        elif raw_key.endswith("_in"):
            key = raw_key[: -len("_in")]
            value = _value_for(key, context)
            if value not in expected:
                return False
        else:
            value = _value_for(raw_key, context)
            if isinstance(value, str) and isinstance(expected, str):
                if value.lower() != expected.lower():
                    return False
            elif value != expected:
                return False
    return True


def _value_for(path: str, context: Mapping[str, Any]) -> Any:
    current: Any = context
    for part in path.split("."):
        if isinstance(current, Mapping):
            current = current.get(part)
        else:
            current = getattr(current, part, None)
        if current is None:
            return None
    return current


def _unique(values: Iterable[str]) -> List[str]:
    seen = set()
    output: List[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        output.append(value)
    return output
