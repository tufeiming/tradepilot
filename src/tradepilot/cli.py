"""Command line entry point with delayed VeighNa imports."""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from tradepilot.config import ConfigError, load_config


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tradepilot", description="VeighNa A股信号盯盘")
    subparsers = parser.add_subparsers(dest="command", required=True)

    doctor = subparsers.add_parser("doctor", help="检查配置、行情、历史和飞书")
    doctor.add_argument("--config", type=Path, default=Path("config.toml"))
    doctor.add_argument("--send-test", action="store_true")

    monitor = subparsers.add_parser("monitor", help="启动无界面实时盯盘")
    monitor.add_argument("--config", type=Path, default=Path("config.toml"))
    return parser


def configure_logging(runtime_dir: Path) -> None:
    log_dir = runtime_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s",
        "%Y-%m-%d %H:%M:%S",
    )
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    file_handler = RotatingFileHandler(
        log_dir / "tradepilot.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    logging.basicConfig(level=logging.INFO, handlers=[console, file_handler], force=True)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(f"配置错误：{exc}", file=sys.stderr)
        return 2

    config.runtime_dir.mkdir(parents=True, exist_ok=True)
    os.chdir(config.config_path.parent)
    configure_logging(config.runtime_dir)

    from tradepilot.builtin_components import build_default_catalog

    catalog = build_default_catalog()
    try:
        catalog.validate(config)
    except ConfigError as exc:
        print(f"配置错误：{exc}", file=sys.stderr)
        return 2

    if args.command == "doctor":
        from tradepilot.doctor import run_doctor

        return run_doctor(config, send_test=args.send_test, catalog=catalog)

    from tradepilot.app import TradePilotApp

    application = TradePilotApp(config, catalog)

    def request_stop(signum=None, frame=None) -> None:
        application.request_stop()

    signal.signal(signal.SIGINT, request_stop)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, request_stop)
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, request_stop)

    try:
        application.start()
        application.run_forever()
    except KeyboardInterrupt:
        application.request_stop()
    except Exception:
        logging.getLogger(__name__).exception("TradePilot stopped unexpectedly")
        return 1
    finally:
        application.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
