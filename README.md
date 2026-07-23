# TradePilot

TradePilot 是基于 VeighNa 的 A股/ETF 无界面信号监控程序。当前版本使用腾讯公开接口作为
POC 行情源，以一分钟 MA10/MA20 交叉产生买入关注和卖出关注，并通过飞书自定义机器人通知。
数据源和策略通过组件注册表选择，可由配置切换或通过独立 Python 扩展包替换。

> 当前版本只能监控，不能交易。`TencentGateway.send_order()` 会直接抛出异常，腾讯公开接口
> 也不得用于未来的自动交易环境。

## 环境

- Python 3.13
- `vnpy==4.4.0`
- `vnpy_ctastrategy==1.4.1`
- `vnpy_sqlite==1.1.3`
- Windows 或 Linux

```powershell
uv sync --python 3.13 --all-groups
Copy-Item config.example.toml config.toml
$env:TRADEPILOT_FEISHU_WEBHOOK_URL = "https://open.feishu.cn/open-apis/bot/v2/hook/..."
$env:TRADEPILOT_FEISHU_SECRET = "可选的签名密钥"
```

也可以把 webhook 直接写入已被 Git 忽略的本地 `config.toml`：

```toml
[feishu]
enabled = true
webhook_url = "https://open.feishu.cn/open-apis/bot/v2/hook/..."
```

环境变量中的 webhook 优先于 TOML。不使用飞书时，可设置 `feishu.enabled = false`。签名密钥、
真实配置、日志和 VeighNa 运行状态均不会进入 Git。

## 自检

```powershell
uv run tradepilot doctor --send-test
```

自检会确认依赖版本、每个标的的实时快照、至少 100 根已完成分钟线以及飞书 webhook。

## 实时监控

```powershell
uv run tradepilot monitor
```

按 `Ctrl+C` 正常退出。程序会在配置文件旁创建 `.vntrader`，保存 CTA 配置、日志、信号去重状态
和尚未成功发送的飞书消息。

自选列表采用 VeighNa 标准代码：

```toml
[data_source]
name = "tencent"
poll_interval_seconds = 3.0
stale_after_seconds = 30.0

[monitor]
symbols = ["515080.SSE", "159915.SZSE"]
minimum_history_bars = 100

[strategy]
name = "double_ma_signal"
fast_window = 10
slow_window = 20

[feishu]
enabled = true
```

每个标的运行一个独立 CTA 策略实例。历史回放只预热指标，不发送旧信号；正式启动后仅在完整
一分钟K线上的严格金叉或死叉产生一次通知。

## 设计文档

- [基线策略：一分钟双均线信号](docs/baseline-strategy.md)
- [数据源与策略扩展架构](docs/architecture.md)

## 测试

```powershell
uv run pytest
uv run ruff check .
```

默认测试完全脱网。真实腾讯和飞书检查只能通过 `doctor` 或显式标记的集成测试执行。

## 开发流程

本仓库是独立应用，VeighNa 仅作为锁定版本的依赖，不修改或复制其源码。`master` 只保存已经通过
测试和验收的版本；后续改动从 `feature/<name>` 分支开发，检查通过后再合并回 `master`。首版验收后
创建 Git 标签，确认新的远端地址后再配置并推送。

## 数据与风险边界

- 仅支持上海和深圳交易所的股票、ETF，不支持北交所。
- 腾讯接口没有稳定性或交易级质量承诺，仅用于走通盯盘流程。
- 当前信号不读取真实持仓；`SELL` 表示卖出关注点，不代表一定持有该标的。
- 自动交易前必须换用券商正式 Gateway，并完成回测、仿真、风控和人工小额验收。
