# SPDX-License-Identifier: Apache-2.0
"""Tests for agent template management."""

from __future__ import annotations

from pathlib import Path

import pytest
from nest_core.scenario import ScenarioConfig
from nest_shell.llm import MockLLMBackend
from nest_shell.templates import AgentTemplate, TemplateRegistry


class TestAgentTemplate:
    def test_from_yaml(self) -> None:
        """Load a built-in template from YAML."""
        tpl_dir = _builtin_dir()
        buyer = AgentTemplate.from_yaml(tpl_dir / "marketplace-buyer.yaml")
        assert buyer.name == "marketplace-buyer"
        assert buyer.provider == "openai"
        assert buyer.model == "gpt-4o-mini"
        assert "buyer" in buyer.system_prompt.lower()

    def test_round_trip(self, tmp_path: Path) -> None:
        """Write a template to YAML and read it back."""
        original = AgentTemplate(
            name="test-round-trip",
            description="A test template.",
            provider="anthropic",
            model="claude-sonnet-4-20250514",
            system_prompt="You are a test agent.\nFollow instructions.",
            temperature=0.5,
            max_tokens=128,
        )
        saved = original.to_yaml(tmp_path / "test-round-trip.yaml")
        assert saved.exists()

        loaded = AgentTemplate.from_yaml(saved)
        assert loaded.name == original.name
        assert loaded.description == original.description
        assert loaded.provider == original.provider
        assert loaded.model == original.model
        assert loaded.system_prompt == original.system_prompt
        assert loaded.temperature == original.temperature
        assert loaded.max_tokens == original.max_tokens

    def test_defaults(self) -> None:
        tpl = AgentTemplate(name="minimal", system_prompt="Hello.")
        assert tpl.provider == "openai"
        assert tpl.model == "gpt-4o-mini"
        assert tpl.temperature == 0.7
        assert tpl.max_tokens == 256


class TestTemplateRegistry:
    def test_list_templates_discovers_builtins(self) -> None:
        """Registry finds the built-in templates."""
        reg = TemplateRegistry()
        templates = reg.list_templates()
        names = [t.name for t in templates]
        assert "marketplace-buyer" in names
        assert "auction-auctioneer" in names
        assert "voting-voter" in names
        assert "consensus-leader" in names
        assert "supply-chain-supplier" in names
        assert "reputation-honest" in names
        assert len(templates) >= 16

    def test_get_template(self) -> None:
        reg = TemplateRegistry()
        tpl = reg.get_template("marketplace-seller")
        assert tpl.name == "marketplace-seller"
        assert "seller" in tpl.system_prompt.lower()

    def test_get_template_not_found(self) -> None:
        reg = TemplateRegistry()
        with pytest.raises(KeyError, match="nonexistent"):
            reg.get_template("nonexistent")

    def test_save_template(self, tmp_path: Path) -> None:
        reg = TemplateRegistry(user_dir=tmp_path)
        tpl = AgentTemplate(
            name="custom-agent",
            system_prompt="Be helpful.",
        )
        path = reg.save_template(tpl)
        assert path.exists()
        assert path.name == "custom-agent.yaml"
        loaded = AgentTemplate.from_yaml(path)
        assert loaded.name == "custom-agent"

    def test_duplicate(self, tmp_path: Path) -> None:
        reg = TemplateRegistry(user_dir=tmp_path)
        new_tpl = reg.duplicate_template("marketplace-buyer", "my-buyer")
        assert new_tpl.name == "my-buyer"
        assert new_tpl.system_prompt != ""

        saved_path = tmp_path / "my-buyer.yaml"
        assert saved_path.exists()

        loaded = AgentTemplate.from_yaml(saved_path)
        assert loaded.name == "my-buyer"

    def test_user_dir_overrides_builtin(self, tmp_path: Path) -> None:
        """User templates take precedence over built-in ones."""
        custom = AgentTemplate(
            name="marketplace-buyer",
            system_prompt="Custom buyer prompt.",
        )
        custom.to_yaml(tmp_path / "marketplace-buyer.yaml")

        reg = TemplateRegistry(user_dir=tmp_path)
        tpl = reg.get_template("marketplace-buyer")
        assert "Custom buyer prompt." in tpl.system_prompt


class TestFactoryTemplateIntegration:
    @pytest.mark.asyncio
    async def test_factory_uses_template_when_configured(self) -> None:
        """Factory loads template when config.agents.template is set."""
        from nest_shell.agent import shell_marketplace_factory

        config = ScenarioConfig.from_dict(
            {
                "name": "tpl-test",
                "seed": 42,
                "agents": {
                    "count": 4,
                    "brain": "llm",
                    "llm_model": "mock",
                    "template": "auto",
                    "roles": [
                        {"name": "buyer", "count": 2},
                        {"name": "seller", "count": 2},
                    ],
                },
                "task": {"type": "marketplace", "config": {"rounds": 2}},
            }
        )

        backend = MockLLMBackend()
        agents = shell_marketplace_factory(config, {}, backend=backend)
        assert len(agents) == 4

    @pytest.mark.asyncio
    async def test_factory_works_without_template(self) -> None:
        """Factory still works when no template is specified."""
        from nest_shell.agent import shell_marketplace_factory

        config = ScenarioConfig.from_dict(
            {
                "name": "no-tpl-test",
                "seed": 42,
                "agents": {
                    "count": 4,
                    "roles": [
                        {"name": "buyer", "count": 2},
                        {"name": "seller", "count": 2},
                    ],
                },
                "task": {"type": "marketplace", "config": {"rounds": 2}},
            }
        )

        backend = MockLLMBackend()
        agents = shell_marketplace_factory(config, {}, backend=backend)
        assert len(agents) == 4


def _builtin_dir() -> Path:
    """Locate the built-in templates/agents directory."""
    pkg = Path(__file__).resolve().parent
    for ancestor in [pkg.parent, pkg.parent.parent, pkg.parent.parent.parent]:
        candidate = ancestor / "templates" / "agents"
        if candidate.is_dir():
            return candidate
    # Fallback: relative to CWD
    return Path.cwd() / "templates" / "agents"
