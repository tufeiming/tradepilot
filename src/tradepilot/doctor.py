"""Read-only diagnostics for versions, Tencent data, and Feishu."""

from __future__ import annotations

from datetime import datetime
from importlib.metadata import PackageNotFoundError, version
from zoneinfo import ZoneInfo

from tradepilot.bootstrap import build_default_catalog
from tradepilot.core.components import ComponentCatalog
from tradepilot.core.config import AppConfig
from tradepilot.notifications.feishu import FeishuClient

EXPECTED_VERSIONS = {
    "vnpy": "4.4.0",
    "vnpy_ctastrategy": "1.4.1",
    "vnpy_sqlite": "1.1.3",
}


def run_doctor(
    config: AppConfig,
    *,
    send_test: bool = False,
    catalog: ComponentCatalog | None = None,
) -> int:
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

    selected_catalog = catalog or build_default_catalog()
    selected_catalog.validate(config)
    data_source = selected_catalog.data_sources.get(config.data_source.name)
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    print(f"[INFO] data_source: {config.data_source.name} ({data_source.display_name})")
    print(f"[INFO] strategy: {config.strategy.name}")

    for diagnostic in data_source.diagnose(config, now):
        vt_symbol = diagnostic.vt_symbol
        if diagnostic.quote_error or diagnostic.price is None or diagnostic.quote_time is None:
            error = diagnostic.quote_error or "provider returned an incomplete quote diagnostic"
            failures.append(f"quote failed for {vt_symbol}: {error}")
            print(f"[FAIL] {vt_symbol}: 实时报价检查失败：{error}")
        else:
            print(
                f"[{'FAIL' if diagnostic.quote_stale else 'OK'}] {vt_symbol}: "
                f"{diagnostic.price:.4f}, "
                f"行情时间 {diagnostic.quote_time:%Y-%m-%d %H:%M:%S}"
            )
            if diagnostic.quote_stale:
                failures.append(f"stale quote for {vt_symbol}")

        if diagnostic.history_error:
            failures.append(f"history failed for {vt_symbol}: {diagnostic.history_error}")
            print(f"[FAIL] {vt_symbol}: 分钟历史加载失败：{diagnostic.history_error}")
        else:
            history_bars = diagnostic.history_bars or 0
            enough = history_bars >= config.monitor.minimum_history_bars
            print(f"[{'OK' if enough else 'FAIL'}] {vt_symbol}: {history_bars} 根已完成分钟线")
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
