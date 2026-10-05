"""ABOUTME: Configuration loading and models.
ABOUTME: Loads proceda.yaml and provides typed config dataclasses."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml

from proceda.exceptions import ConfigError

_ENV_PATTERN = re.compile(r"\$\{(\w+)(?::-(.*?))?\}")

CONFIG_FILENAMES = [
    "proceda.yaml",
    "proceda.yml",
]

CONFIG_SEARCH_PATHS = [
    Path("."),
    Path.home() / ".config" / "proceda",
]


def _expand_env(value: str) -> str:
    """Expand ${VAR} and ${VAR:-default} patterns in a string."""

    def _replace(match: re.Match[str]) -> str:
        var_name = match.group(1)
        default = match.group(2)
        env_val = os.environ.get(var_name)
        if env_val is not None:
            return env_val
        if default is not None:
            return default
        return match.group(0)

    return _ENV_PATTERN.sub(_replace, value)


def _expand_env_recursive(obj: Any) -> Any:
    """Recursively expand environment variables in config values."""
    if isinstance(obj, str):
        return _expand_env(obj)
    if isinstance(obj, dict):
        return {k: _expand_env_recursive(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_expand_env_recursive(item) for item in obj]
    return obj


@dataclass
class LLMConfig:
    """LLM provider configuration."""

    model: str = "anthropic/claude-sonnet-4-20250514"
    api_key_env: str = "ANTHROPIC_API_KEY"
    temperature: float = 0.7
    max_tokens: int = 4096
    max_retries: int = 3
    thinking: str | None = None
    reasoning_effort: str | None = None
    api_base: str | None = None
    timeout: float = 60.0
    audit_dir: str | None = None

    @property
    def api_key(self) -> str | None:
        return os.environ.get(self.api_key_env)


@dataclass
class DecisionBackendConfig:
    """One bounded-decision provider; credentials are resolved only at call time."""

    name: str = "jev"
    kind: str = "typesafe"
    model: str = "jev-1.13.0"
    base_url: str = "https://api.typesafe.ai"
    api_key_env: str = "TYPESAFE_API_KEY"
    max_input_chars: int = 60000
    timeout: float = 20.0
    input_cost_per_million: float = 0.042
    threshold: float | None = None


@dataclass
class DecisionConfig:
    """Opt-in internal cascades; natural-language skills need no new annotations."""

    enabled: bool = False
    backends: list[DecisionBackendConfig] = field(default_factory=list)
    threshold: float = 0.97
    fast_completion: bool = True
    verify_proposals: bool = False
    max_repairs: int = 1
    fallback_llm: LLMConfig | None = None


@dataclass
class PlanningConfig:
    """Opt-in source-only compilation; case values never enter the compiler."""

    enabled: bool = False
    cache_dir: str = ".proceda/plans"
    cache_epoch: str = "v1"
    compiler_llm: LLMConfig | None = None
    mode: Literal["full", "business_decisions"] = "full"
    native_decisions: bool = True
    require_cached: bool = False


@dataclass
class AppConfig:
    """MCP app (tool server) configuration."""

    name: str = ""
    description: str = ""
    transport: str = "stdio"
    command: list[str] | None = None
    url: str | None = None
    env: dict[str, str] | None = None


@dataclass
class DevConfig:
    """Developer mode settings."""

    show_reasoning: bool = True
    log_mcp: bool = True


@dataclass
class SecurityConfig:
    """Security settings."""

    tool_denylist: list[str] = field(default_factory=list)


@dataclass
class LoggingConfig:
    """Logging settings."""

    run_dir: str = ".proceda/runs"
    redact_secrets: bool = True


@dataclass
class ProcedaConfig:
    """Root configuration model."""

    llm: LLMConfig = field(default_factory=LLMConfig)
    apps: list[AppConfig] = field(default_factory=list)
    dev: DevConfig = field(default_factory=DevConfig)
    security: SecurityConfig = field(default_factory=SecurityConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    decisions: DecisionConfig = field(default_factory=DecisionConfig)
    planning: PlanningConfig = field(default_factory=PlanningConfig)

    @staticmethod
    def from_dict(data: dict[str, Any]) -> ProcedaConfig:
        data = _expand_env_recursive(data)
        config = ProcedaConfig()

        if "llm" in data:
            llm = data["llm"]
            config.llm = LLMConfig(
                model=llm.get("model", config.llm.model),
                api_key_env=llm.get("api_key_env", config.llm.api_key_env),
                temperature=llm.get("temperature", config.llm.temperature),
                max_tokens=llm.get("max_tokens", config.llm.max_tokens),
                max_retries=llm.get("max_retries", config.llm.max_retries),
                thinking=llm.get("thinking", config.llm.thinking),
                reasoning_effort=llm.get("reasoning_effort"),
                api_base=llm.get("api_base"),
                timeout=llm.get("timeout", config.llm.timeout),
                audit_dir=llm.get("audit_dir"),
            )

        if "decisions" in data:
            dec = data["decisions"]
            if not isinstance(dec, dict):
                raise ConfigError("decisions must be a mapping")
            for flag in ("enabled", "fast_completion", "verify_proposals"):
                if flag in dec and not isinstance(dec[flag], bool):
                    raise ConfigError(f"decisions.{flag} must be a boolean")
            threshold = dec.get("threshold", 0.97)
            if type(threshold) not in (float, int) or not 0.5 < threshold <= 1:
                raise ConfigError("decisions.threshold must be in (0.5, 1]")
            repairs = dec.get("max_repairs", 1)
            if type(repairs) is not int or repairs < 0 or repairs > 5:
                raise ConfigError("decisions.max_repairs must be an integer from 0 to 5")
            backends = []
            for entry in dec.get("backends", []):
                try:
                    backend = DecisionBackendConfig(**entry)
                except TypeError as exc:
                    raise ConfigError(f"Invalid decision backend fields: {exc}") from exc
                if backend.kind not in {"typesafe", "ollaya"}:
                    raise ConfigError("Decision backend kind must be typesafe or ollaya")
                if backend.threshold is not None and (
                    type(backend.threshold) not in (int, float) or not 0.5 < backend.threshold <= 1
                ):
                    raise ConfigError("Backend threshold must be in (0.5, 1]")
                if (
                    type(backend.max_input_chars) is not int
                    or backend.max_input_chars < 1
                    or type(backend.timeout) not in (int, float)
                    or backend.timeout <= 0
                ):
                    raise ConfigError("Decision context guard and timeout must be positive")
                backends.append(backend)
            fallback = dec.get("fallback_llm")
            fallback_config = ProcedaConfig.from_dict({"llm": fallback}).llm if fallback else None
            config.decisions = DecisionConfig(
                enabled=dec.get("enabled", False),
                backends=backends,
                threshold=float(threshold),
                fast_completion=dec.get("fast_completion", True),
                verify_proposals=dec.get("verify_proposals", False),
                max_repairs=repairs,
                fallback_llm=fallback_config,
            )
            if config.decisions.enabled and not backends:
                raise ConfigError("Enabled decisions require at least one configured backend")

        if "planning" in data:
            planning = data["planning"]
            if not isinstance(planning, dict):
                raise ConfigError("planning must be a mapping")
            if type(planning.get("enabled", False)) is not bool:
                raise ConfigError("planning.enabled must be a boolean")
            mode = planning.get("mode", "full")
            if not isinstance(mode, str) or mode not in {"full", "business_decisions"}:
                raise ConfigError("planning.mode must be full or business_decisions")
            if type(planning.get("native_decisions", True)) is not bool:
                raise ConfigError("planning.native_decisions must be a boolean")
            if type(planning.get("require_cached", False)) is not bool:
                raise ConfigError("planning.require_cached must be a boolean")
            for name in ("cache_dir", "cache_epoch"):
                if name in planning and (
                    not isinstance(planning[name], str) or not planning[name].strip()
                ):
                    raise ConfigError(f"planning.{name} must be a nonempty string")
            compiler = planning.get("compiler_llm")
            if compiler is not None and not isinstance(compiler, dict):
                raise ConfigError("planning.compiler_llm must be a mapping")
            config.planning = PlanningConfig(
                enabled=planning.get("enabled", False),
                cache_dir=planning.get("cache_dir", ".proceda/plans"),
                cache_epoch=planning.get("cache_epoch", "v1"),
                compiler_llm=(
                    ProcedaConfig.from_dict({"llm": compiler}).llm if compiler is not None else None
                ),
                mode=mode,
                native_decisions=planning.get("native_decisions", True),
                require_cached=planning.get("require_cached", False),
            )

        if "apps" in data:
            config.apps = []
            for app_data in data["apps"]:
                config.apps.append(
                    AppConfig(
                        name=app_data.get("name", ""),
                        description=app_data.get("description", ""),
                        transport=app_data.get("transport", "stdio"),
                        command=app_data.get("command"),
                        url=app_data.get("url"),
                        env=app_data.get("env"),
                    )
                )

        if "dev" in data:
            dev = data["dev"]
            config.dev = DevConfig(
                show_reasoning=dev.get("show_reasoning", True),
                log_mcp=dev.get("log_mcp", True),
            )

        if "security" in data:
            sec = data["security"]
            config.security = SecurityConfig(
                tool_denylist=sec.get("tool_denylist", []),
            )

        if "logging" in data:
            log = data["logging"]
            config.logging = LoggingConfig(
                run_dir=log.get("run_dir", ".proceda/runs"),
                redact_secrets=log.get("redact_secrets", True),
            )

        return config

    @staticmethod
    def load(config_path: str | Path | None = None) -> ProcedaConfig:
        """Load config from file, searching standard locations."""
        if config_path:
            path = Path(config_path)
            if not path.exists():
                raise ConfigError(f"Config file not found: {path}")
            return ProcedaConfig._load_file(path)

        for search_dir in CONFIG_SEARCH_PATHS:
            for filename in CONFIG_FILENAMES:
                path = search_dir / filename
                if path.exists():
                    return ProcedaConfig._load_file(path)

        return ProcedaConfig()

    @staticmethod
    def _load_file(path: Path) -> ProcedaConfig:
        try:
            text = path.read_text()
            data = yaml.safe_load(text) or {}
            return ProcedaConfig.from_dict(data)
        except yaml.YAMLError as e:
            raise ConfigError(f"Invalid YAML in {path}: {e}") from e
