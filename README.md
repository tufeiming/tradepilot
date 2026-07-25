# TradePilot

TradePilot 是基于 VeighNa 的 A股/ETF 信号监控和策略研究程序。当前版本使用腾讯公开接口作为
POC 行情源，以15分钟 SMA10/SMA60 交叉产生买入关注和卖出关注，并通过飞书自定义机器人通知。
策略研究直接复用 VeighNa 官方 CtaBacktester GUI；数据源和策略均可由配置或扩展包替换。

> 当前版本只能监控，不能交易。`TencentGateway.send_order()` 会直接抛出异常，腾讯公开接口
> 也不得用于未来的自动交易环境。

第一次使用时，从 [15分钟策略从零运行指南](docs/15m-quickstart.md) 开始，不需要预先了解 VeighNa。

## 环境

- Python 3.13
- `pandas==2.2.3`（兼容官方回测 GUI）
- `vnpy==4.4.0`
- `vnpy_ctabacktester==1.3.0`
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
bar_window_minutes = 15
fast_window = 10
slow_window = 60

[execution]
mode = "notify"

[feishu]
enabled = true
```

每个标的运行一个独立 CTA 策略实例。历史回放只预热指标，不发送旧信号；正式启动后仅在完整
15分钟K线上的严格金叉或死叉产生一次通知。缺分钟、午休跨接和未完成K线不会进入指标。

`execution.mode` 已固定为稳定接口：`notify`、`paper`、`live`。当前 `monitor` 只允许 `notify`；
`paper/live` 会在创建 VeighNa 引擎前被拒绝。历史模拟交易必须使用下面的回测命令，它不会连接
券商交易接口，也不会改变实时运行模式。

## 图形化回测

```powershell
uv run tradepilot backtest
```

命令会连接配置中的只读行情 Gateway，并直接打开 VeighNa 官方 CTA回测窗口。内置
`DoubleMaLongBacktestStrategy` 与实时策略共用严格 MA 交叉计算，但只在 `BACKTESTING` 引擎中
把 BUY/SELL 转为多头模拟成交；误放到实盘 CTA 引擎会立即拒绝启动。

在回测窗口选择 `15m` 后，可下载腾讯当前保留的约六个滚动月原生 OHLCV。数据进入项目独立历史
库，不会冒充 VeighNa `1m` 数据。第一次接触项目时，直接按照
[15分钟图形化回测](docs/backtesting.md) 完成下载、参数填写、回测和结果检查。

## 设计文档

- [15分钟策略从零运行指南](docs/15m-quickstart.md)
- [基线策略：15分钟双均线信号](docs/baseline-strategy.md)
- [数据源与策略扩展架构](docs/architecture.md)
- [图形化回测与运行模式](docs/backtesting.md)

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
