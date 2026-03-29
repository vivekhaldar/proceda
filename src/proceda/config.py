"""ABOUTME: Configuration loading and models.
ABOUTME: Loads proceda.yaml and provides typed config dataclasses."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

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

    @property
    def api_key(self) -> str | None:
        return os.environ.get(self.api_key_env)


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
class CacheConfig:
    """Step-level execution cache settings."""

    enabled: bool = False
    min_traces: int = 3
    min_confidence: float = 0.8
    optimization_level: int = 1


@dataclass
class ProcedaConfig:
    """Root configuration model."""

    llm: LLMConfig = field(default_factory=LLMConfig)
    apps: list[AppConfig] = field(default_factory=list)
    dev: DevConfig = field(default_factory=DevConfig)
    security: SecurityConfig = field(default_factory=SecurityConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    cache: CacheConfig = field(default_factory=CacheConfig)

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

        if "cache" in data:
            c = data["cache"]
            config.cache = CacheConfig(
                enabled=c.get("enabled", False),
                min_traces=c.get("min_traces", 3),
                min_confidence=c.get("min_confidence", 0.8),
                optimization_level=c.get("optimization_level", 1),
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
