from pathlib import Path

import pytest

from tradepilot.cli import build_parser


@pytest.mark.parametrize("command", ["doctor", "monitor", "backtest"])
def test_config_defaults_to_project_toml(command):
    args = build_parser().parse_args([command])
    assert args.config == Path("config.toml")


def test_config_path_can_still_be_overridden():
    args = build_parser().parse_args(["monitor", "--config", "deploy/monitor.toml"])
    assert args.config == Path("deploy/monitor.toml")
