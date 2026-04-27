"""Domain profile support for SVP value vocabularies and templates."""
from __future__ import annotations

from importlib.resources import files
from pathlib import Path
from string import Formatter
from typing import Any, Dict, Iterable, List, Literal, Mapping, Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from svp_rpe.rpe.models import SemanticLabel


class ProfileModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LabelRule(ProfileModel):
    condition: Dict[str, Any] = Field(default_factory=dict)
    label: str


class LabelSpec(ProfileModel):
    label: str
    confidence: float = 0.5


class LabelsRule(ProfileModel):
    id: Optional[str] = None
    condition: Dict[str, Any] = Field(default_factory=dict)
    layer: Literal["perceptual", "structural", "semantic_hypothesis"] = "perceptual"
    labels: List[LabelSpec]

    @field_validator("labels", mode="before")
    @classmethod
    def coerce_label_specs(cls, value: object) -> object:
        if isinstance(value, list):
            return [
                {"label": item}
                if isinstance(item, str)
                else item
                for item in value
            ]
        return value


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

    def select_por_surface(self, context: Mapping[str, Any]) -> List[SemanticLabel]:
        labels: List[SemanticLabel] = []
        for index, rule in enumerate(self.por_surface_rules):
            evidence = _condition_evidence(rule.condition, context)
            if evidence is None:
                continue
            source_rule = rule.id or f"profile.por_surface.{index}"
            labels.extend(
                SemanticLabel(
                    label=spec.label,
                    layer=rule.layer,
                    confidence=spec.confidence,
                    evidence=evidence,
                    source_rule=source_rule,
                )
                for spec in rule.labels
            )
        if labels:
            return _unique_semantic_labels(labels)
        return [
            SemanticLabel(
                label=label,
                layer="perceptual",
                confidence=0.5,
                evidence=[f"default_por_surface={label}"],
                source_rule="profile.default_por_surface",
            )
            for label in self.default_por_surface
        ]

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
        por_surface: Iterable[str | SemanticLabel],
    ) -> List[str]:
        tags: List[str] = []
        for source in self.style_tag_sources:
            if source == "por_surface":
                tags.extend(_label_text(item) for item in por_surface)
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
    return _condition_evidence(condition, context) is not None


def _condition_evidence(
    condition: Mapping[str, Any],
    context: Mapping[str, Any],
) -> Optional[List[str]]:
    evidence: List[str] = []
    for raw_key, expected in condition.items():
        if raw_key.endswith("_min"):
            key = raw_key[: -len("_min")]
            value = _value_for(key, context)
            if not isinstance(value, (int, float)) or float(value) < float(expected):
                return None
            evidence.append(f"{key}={_format_value(value)} >= {_format_value(expected)}")
        elif raw_key.endswith("_max"):
            key = raw_key[: -len("_max")]
            value = _value_for(key, context)
            if not isinstance(value, (int, float)) or float(value) > float(expected):
                return None
            evidence.append(f"{key}={_format_value(value)} <= {_format_value(expected)}")
        elif raw_key.endswith("_exists"):
            key = raw_key[: -len("_exists")]
            exists = _value_for(key, context) is not None
            if bool(expected) != exists:
                return None
            evidence.append(f"{key}_exists={exists}")
        elif raw_key.endswith("_in"):
            key = raw_key[: -len("_in")]
            value = _value_for(key, context)
            if value not in expected:
                return None
            evidence.append(f"{key}={_format_value(value)} in {expected}")
        else:
            value = _value_for(raw_key, context)
            if isinstance(value, str) and isinstance(expected, str):
                if value.lower() != expected.lower():
                    return None
            elif value != expected:
                return None
            evidence.append(f"{raw_key}={_format_value(value)} == {_format_value(expected)}")
    return evidence


def _format_value(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:.4g}"
    return str(value)


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


def _label_text(value: str | SemanticLabel) -> str:
    return value.label if isinstance(value, SemanticLabel) else str(value)


def _unique_semantic_labels(values: Iterable[SemanticLabel]) -> List[SemanticLabel]:
    seen = set()
    output: List[SemanticLabel] = []
    for value in values:
        key = (value.layer, value.label)
        if key in seen:
            continue
        seen.add(key)
        output.append(value)
    return output
