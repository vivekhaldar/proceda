"""ABOUTME: Tests for configuration loading, env var expansion, and config parsing.
ABOUTME: Covers _expand_env, _expand_env_recursive, and ProcedaConfig.from_dict."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from proceda.config import ProcedaConfig, _expand_env, _expand_env_recursive
from proceda.exceptions import ConfigError


class TestExpandEnv:
    def test_expand_set_variable(self) -> None:
        os.environ["TEST_VAR_XYZ"] = "hello"
        try:
            assert _expand_env("${TEST_VAR_XYZ}") == "hello"
        finally:
            del os.environ["TEST_VAR_XYZ"]

    def test_expand_with_default(self) -> None:
        result = _expand_env("${UNSET_VAR_12345:-fallback}")
        assert result == "fallback"

    def test_expand_unset_no_default(self) -> None:
        result = _expand_env("${UNSET_VAR_99999}")
        assert result == "${UNSET_VAR_99999}"

    def test_no_expansion_needed(self) -> None:
        assert _expand_env("plain text") == "plain text"


class TestExpandEnvAdvanced:
    def test_multiple_vars_in_one_string(self, monkeypatch) -> None:
        monkeypatch.setenv("PROCEDA_HOST", "localhost")
        monkeypatch.setenv("PROCEDA_PORT", "8080")
        assert _expand_env("${PROCEDA_HOST}:${PROCEDA_PORT}") == "localhost:8080"

    def test_mixed_set_and_unset(self, monkeypatch) -> None:
        monkeypatch.setenv("PROCEDA_SET_VAR", "hello")
        monkeypatch.delenv("PROCEDA_UNSET_VAR", raising=False)
        result = _expand_env("${PROCEDA_SET_VAR} and ${PROCEDA_UNSET_VAR:-fallback}")
        assert result == "hello and fallback"

    def test_empty_default(self, monkeypatch) -> None:
        monkeypatch.delenv("PROCEDA_MISSING", raising=False)
        assert _expand_env("${PROCEDA_MISSING:-}") == ""

    def test_env_var_set_to_empty(self, monkeypatch) -> None:
        monkeypatch.setenv("PROCEDA_EMPTY", "")
        # Empty string is a valid value; should not fall through to default
        assert _expand_env("${PROCEDA_EMPTY:-fallback}") == ""

    def test_recursive_expansion_in_dict(self, monkeypatch) -> None:
        monkeypatch.setenv("PROCEDA_DB_HOST", "db.example.com")
        monkeypatch.setenv("PROCEDA_DB_PORT", "5432")
        obj = {
            "connection": {
                "host": "${PROCEDA_DB_HOST}",
                "port": "${PROCEDA_DB_PORT}",
            }
        }
        result = _expand_env_recursive(obj)
        assert result == {"connection": {"host": "db.example.com", "port": "5432"}}

    def test_recursive_expansion_in_list(self, monkeypatch) -> None:
        monkeypatch.setenv("PROCEDA_ITEM_A", "alpha")
        monkeypatch.setenv("PROCEDA_ITEM_B", "beta")
        result = _expand_env_recursive(["${PROCEDA_ITEM_A}", "${PROCEDA_ITEM_B}", "literal"])
        assert result == ["alpha", "beta", "literal"]

    def test_non_string_values_pass_through(self) -> None:
        assert _expand_env_recursive(42) == 42
        assert _expand_env_recursive(True) is True
        assert _expand_env_recursive(None) is None
        assert _expand_env_recursive(3.14) == 3.14

    def test_from_dict_expands_app_env(self, monkeypatch) -> None:
        monkeypatch.setenv("PROCEDA_APP_TOKEN", "secret123")
        monkeypatch.setenv("PROCEDA_APP_CMD", "my-server")
        data = {
            "apps": [
                {
                    "name": "test",
                    "description": "Test app",
                    "transport": "stdio",
                    "command": ["${PROCEDA_APP_CMD}", "--verbose"],
                    "env": {"API_TOKEN": "${PROCEDA_APP_TOKEN}"},
                }
            ]
        }
        config = ProcedaConfig.from_dict(data)
        assert config.apps[0].command == ["my-server", "--verbose"]
        assert config.apps[0].env == {"API_TOKEN": "secret123"}


class TestProcedaConfig:
    def test_default_config(self) -> None:
        config = ProcedaConfig()
        assert config.llm.model == "anthropic/claude-sonnet-4-20250514"
        assert config.llm.temperature == 0.7
        assert config.apps == []
        assert config.security.tool_denylist == []

    def test_from_dict(self) -> None:
        data = {
            "llm": {"model": "gpt-4", "temperature": 0.5},
            "security": {"tool_denylist": ["bad__*"]},
        }
        config = ProcedaConfig.from_dict(data)
        assert config.llm.model == "gpt-4"
        assert config.llm.temperature == 0.5
        assert config.security.tool_denylist == ["bad__*"]

    def test_from_dict_with_apps(self) -> None:
        data = {
            "apps": [
                {
                    "name": "test",
                    "description": "Test app",
                    "transport": "stdio",
                    "command": ["python", "-m", "test"],
                },
                {
                    "name": "http_app",
                    "description": "HTTP app",
                    "transport": "http",
                    "url": "http://localhost:8000",
                },
            ]
        }
        config = ProcedaConfig.from_dict(data)
        assert len(config.apps) == 2
        assert config.apps[0].name == "test"
        assert config.apps[0].transport == "stdio"
        assert config.apps[1].transport == "http"

    def test_load_from_file(self, tmp_path: Path) -> None:
        config_file = tmp_path / "proceda.yaml"
        config_file.write_text("llm:\n  model: test-model\n  temperature: 0.3\n")
        config = ProcedaConfig.load(str(config_file))
        assert config.llm.model == "test-model"
        assert config.llm.temperature == 0.3

    def test_load_missing_file(self) -> None:
        with pytest.raises(ConfigError, match="not found"):
            ProcedaConfig.load("/nonexistent/path.yaml")

    def test_load_defaults_when_no_file(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        config = ProcedaConfig.load()
        assert config.llm.model == "anthropic/claude-sonnet-4-20250514"

    def test_load_invalid_yaml(self, tmp_path: Path) -> None:
        config_file = tmp_path / "proceda.yaml"
        config_file.write_text("{{{{invalid yaml")
        with pytest.raises(ConfigError, match="Invalid YAML"):
            ProcedaConfig.load(str(config_file))

    def test_dev_config(self) -> None:
        data = {"dev": {"show_reasoning": False, "log_mcp": False}}
        config = ProcedaConfig.from_dict(data)
        assert config.dev.show_reasoning is False
        assert config.dev.log_mcp is False

    def test_logging_config(self) -> None:
        data = {"logging": {"run_dir": "/tmp/runs", "redact_secrets": False}}
        config = ProcedaConfig.from_dict(data)
        assert config.logging.run_dir == "/tmp/runs"
        assert config.logging.redact_secrets is False

    def test_decision_stage_threshold_override(self) -> None:
        config = ProcedaConfig.from_dict({"decisions": {"backends": [{"threshold": 0.99}, {}]}})
        assert config.decisions.backends[0].threshold == 0.99
        assert config.decisions.backends[1].threshold is None
        for invalid in (True, "0.99", 0.5, 1.1):
            with pytest.raises(ConfigError, match="Backend threshold"):
                ProcedaConfig.from_dict({"decisions": {"backends": [{"threshold": invalid}]}})
