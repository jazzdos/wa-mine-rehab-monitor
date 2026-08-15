"""Project configuration: pydantic models loaded from `config/base.yaml` (or
an override path passed to the CLI).

`ProjectConfig` allows arbitrary extra top-level keys (`extra="allow"`) so a
future credential-shaped key (e.g. a SLIP API token) can land in the resolved
config without a schema change; `wa_mine_monitor.secrets.redact_secrets`
strips anything credential-shaped before the config is ever echoed or
persisted -- see `cli.config_check` and `secrets.py`'s module docstring.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, field_validator


class RunConfig(BaseModel):
    """The `run:` section of the project config."""

    data_root: Path
    redistribute_public: bool

    @field_validator("data_root")
    @classmethod
    def _expand_data_root(cls, value: Path) -> Path:
        """Resolve a `~`-prefixed data root against the invoking user's home.

        Every filesystem path this project persists (run manifests, snapshot
        layouts) is derived from this field, so it is expanded once here
        rather than at each call site.
        """
        return value.expanduser()


class SourcesConfig(BaseModel):
    """The `sources:` section of the project config."""

    minedex_public_export_blocked: bool = True


class ProjectConfig(BaseModel):
    """The full resolved project configuration."""

    model_config = ConfigDict(extra="allow")

    run: RunConfig
    sources: SourcesConfig


def load_config(path: Path) -> ProjectConfig:
    """Load and validate a `ProjectConfig` from a YAML file at `path`."""
    raw = yaml.safe_load(Path(path).read_text())
    return ProjectConfig.model_validate(raw)
