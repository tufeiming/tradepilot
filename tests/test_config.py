from pathlib import Path

import pytest

from tradepilot.builtin_components import build_default_catalog
from tradepilot.config import ConfigError, load_config, parse_vt_symbol


def write_config(path: Path, body: str) -> Path:
    path.write_text(body, encoding="utf-8")
    return path


BASE = """
[data_source]
name = "tencent"
poll_interval_seconds = 3
stale_after_seconds = 30

[monitor]
symbols = ["515080.SSE", "159915.SZSE"]
minimum_history_bars = 100

[strategy]
name = "double_ma_signal"
fast_window = 10
slow_window = 20

[feishu]
enabled = false
"""


def test_load_valid_config(tmp_path):
    config = load_config(write_config(tmp_path / "config.toml", BASE), environ={})
    build_default_catalog(discover=False).validate(config)
    assert config.monitor.symbols == ("515080.SSE", "159915.SZSE")
    assert config.data_source.name == "tencent"
    assert config.strategy.name == "double_ma_signal"
    assert config.strategy.settings["fast_window"] == 10
    assert config.runtime_dir == tmp_path / ".vntrader"


@pytest.mark.parametrize(
    "value",
    ["515080", "515080.SH", "600519.SZSE", "000001.SSE", "900901.SSE", "abc.SSE"],
)
def test_rejects_invalid_or_mismatched_symbol(value):
    with pytest.raises(ConfigError):
        parse_vt_symbol(value)


def test_rejects_duplicate_symbols(tmp_path):
    body = BASE.replace(
        'symbols = ["515080.SSE", "159915.SZSE"]',
        'symbols = ["515080.SSE", "515080.SSE"]',
    )
    with pytest.raises(ConfigError, match="duplicates"):
        load_config(write_config(tmp_path / "config.toml", body), environ={})


def test_rejects_component_settings_left_in_monitor_section(tmp_path):
    body = BASE.replace(
        "minimum_history_bars = 100",
        "minimum_history_bars = 100\npoll_interval_seconds = 3",
    )
    with pytest.raises(ConfigError, match="unknown monitor setting"):
        load_config(write_config(tmp_path / "config.toml", body), environ={})


def test_rejects_invalid_strategy_windows(tmp_path):
    body = BASE.replace("fast_window = 10", "fast_window = 20")
    with pytest.raises(ConfigError, match="fast_window"):
        config = load_config(write_config(tmp_path / "config.toml", body), environ={})
        build_default_catalog(discover=False).validate(config)


def test_history_warmup_cannot_be_configured_below_100(tmp_path):
    body = BASE.replace("minimum_history_bars = 100", "minimum_history_bars = 99")
    with pytest.raises(ConfigError, match="minimum_history_bars"):
        config = load_config(write_config(tmp_path / "config.toml", body), environ={})
        build_default_catalog(discover=False).validate(config)


def test_requires_webhook_when_feishu_enabled(tmp_path):
    body = BASE.replace("enabled = false", "enabled = true")
    with pytest.raises(ConfigError, match="webhook_url"):
        load_config(write_config(tmp_path / "config.toml", body), environ={})


def test_reads_feishu_webhook_from_toml(tmp_path):
    body = BASE.replace(
        "enabled = false",
        'enabled = true\nwebhook_url = "https://open.feishu.cn/open-apis/bot/v2/hook/local"',
    )
    config = load_config(write_config(tmp_path / "config.toml", body), environ={})
    assert config.feishu.webhook_url.endswith("/local")


def test_environment_overrides_toml_webhook_and_provides_secret(tmp_path):
    body = BASE.replace(
        "enabled = false",
        'enabled = true\nwebhook_url = "https://open.feishu.cn/open-apis/bot/v2/hook/local"',
    )
    config = load_config(
        write_config(tmp_path / "config.toml", body),
        environ={
            "TRADEPILOT_FEISHU_WEBHOOK_URL": "https://open.feishu.cn/open-apis/bot/v2/hook/id",
            "TRADEPILOT_FEISHU_SECRET": "secret",
        },
    )
    assert config.feishu.webhook_url.endswith("/id")
    assert config.feishu.secret == "secret"


def test_rejects_non_string_toml_webhook(tmp_path):
    body = BASE.replace("enabled = false", "enabled = true\nwebhook_url = 123")
    with pytest.raises(ConfigError, match="must be a string"):
        load_config(write_config(tmp_path / "config.toml", body), environ={})
