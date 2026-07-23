"""Read-only diagnostics for versions, Tencent data, and Feishu."""

from __future__ import annotations

from datetime import datetime
from importlib.metadata import PackageNotFoundError, version

from tradepilot.config import AppConfig
from tradepilot.notifier import FeishuClient
from tradepilot.tencent import SHANGHAI_TZ, TencentClient, is_trading_session

EXPECTED_VERSIONS = {
    "vnpy": "4.4.0",
    "vnpy_ctastrategy": "1.4.1",
    "vnpy_sqlite": "1.1.3",
}


def run_doctor(config: AppConfig, *, send_test: bool = False) -> int:
    failures: list[str] = []
    for package, expected in EXPECTED_VERSIONS.items():
        try:
            installed = version(package)
        except PackageNotFoundError:
            installed = "not installed"
        ok = installed == expected
        print(f"[{'OK' if ok else 'FAIL'}] {package}: {installed} (expected {expected})")
        if not ok:
            failures.append(f"{package} version mismatch")

    client = TencentClient()
    now = datetime.now(SHANGHAI_TZ)
    try:
        quotes = {item.vt_symbol: item for item in client.fetch_quotes(config.monitor.symbols)}
    except Exception as exc:
        quotes = {}
        failures.append(f"Tencent quote request failed: {exc}")
        print(f"[FAIL] 腾讯实时行情: {exc}")

    for vt_symbol in config.monitor.symbols:
        quote = quotes.get(vt_symbol)
        if quote is None:
            failures.append(f"missing quote for {vt_symbol}")
            print(f"[FAIL] {vt_symbol}: 无实时报价")
        else:
            age = (now - quote.timestamp).total_seconds()
            stale = is_trading_session(now) and (
                quote.timestamp.date() != now.date() or age > config.monitor.stale_after_seconds
            )
            print(
                f"[{'FAIL' if stale else 'OK'}] {vt_symbol}: "
                f"{quote.last_price:.4f}, 行情时间 {quote.timestamp:%Y-%m-%d %H:%M:%S}"
            )
            if stale:
                failures.append(f"stale quote for {vt_symbol}")

        try:
            history = client.fetch_history(vt_symbol, now=now)
        except Exception as exc:
            failures.append(f"history failed for {vt_symbol}: {exc}")
            print(f"[FAIL] {vt_symbol}: 分钟历史加载失败：{exc}")
        else:
            enough = len(history) >= config.monitor.minimum_history_bars
            print(f"[{'OK' if enough else 'FAIL'}] {vt_symbol}: {len(history)} 根已完成分钟线")
            if not enough:
                failures.append(f"insufficient history for {vt_symbol}")

    if send_test:
        if not config.feishu.enabled or not config.feishu.webhook_url:
            failures.append("Feishu is disabled")
            print("[FAIL] 飞书测试: feishu.enabled=false")
        else:
            try:
                FeishuClient(
                    config.feishu.webhook_url,
                    config.feishu.secret,
                ).send(f"[TEST] TradePilot 飞书通知测试\n{now:%Y-%m-%d %H:%M:%S}")
            except Exception as exc:
                failures.append(f"Feishu test failed: {exc}")
                print(f"[FAIL] 飞书测试: {exc}")
            else:
                print("[OK] 飞书测试消息已发送")

    if failures:
        print(f"Doctor finished with {len(failures)} failure(s).")
        return 1
    print("Doctor finished successfully.")
    return 0
