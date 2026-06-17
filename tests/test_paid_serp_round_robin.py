from __future__ import annotations

from kindly_web_search_mcp_server.search import provider_config as pc


def _provider(name: str) -> pc.ProviderConfig:
    return pc.ProviderConfig(
        name=name,
        env_key="",
        search_fn=lambda *args, **kwargs: [],  # noqa: ARG005
        group=pc.ProviderGroup.paid_serp,
        requires_key=False,
    )


def test_select_paid_serp_configs_rotates_across_calls(monkeypatch) -> None:
    monkeypatch.setattr(pc, "_SERP_PAID_RR_CURSOR", 0)
    configs = [
        _provider("brave"),
        _provider("serpapi"),
        _provider("serper"),
    ]

    first = [config.name for config in pc.select_paid_serp_configs(configs, limit=2)]
    second = [config.name for config in pc.select_paid_serp_configs(configs, limit=2)]
    third = [config.name for config in pc.select_paid_serp_configs(configs, limit=2)]

    assert first == ["brave", "serpapi"]
    assert second == ["serper", "brave"]
    assert third == ["serpapi", "serper"]
