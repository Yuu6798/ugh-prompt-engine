"""svp/render_yaml.py — SVPBundle → YAML output."""
from __future__ import annotations

import yaml

from svp_rpe.svp.models import SVPBundle


def render_yaml(bundle: SVPBundle) -> str:
    """Render SVPBundle as YAML string."""
    data = bundle.model_dump(exclude_none=True)
    return yaml.safe_dump(data, allow_unicode=True, default_flow_style=False, sort_keys=False)
