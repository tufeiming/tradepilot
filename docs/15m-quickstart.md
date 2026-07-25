# 15分钟策略从零运行指南

本文面向第一次接触 TradePilot、VeighNa 和量化回测的人。按顺序完成后，可以做到两件事：

1. 不等 A股开盘，使用约半年历史数据在图形界面中回测 15 分钟 SMA10/SMA60 策略。
2. A股开盘时运行实时盯盘，在完整 15 分钟K线出现均线交叉后发送飞书通知。

当前系统不会操作真实账户。实时模式只产生关注信号，腾讯 Gateway 在基础设施层拒绝任何委托。

## 一、先了解当前策略

程序把实时 Tick 依次处理为：

```text
腾讯实时行情
  -> 完整1分钟K线
  -> 完整15分钟K线
  -> SMA10和SMA60
  -> 严格金叉/死叉
  -> 飞书通知
```

每天正常有 16 根 15 分钟K线，时间使用区间开始时间：

```text
上午：09:30 09:45 10:00 10:15 10:30 10:45 11:00 11:15
下午：13:00 13:15 13:30 13:45 14:00 14:15 14:30 14:45
```

规则如下：

- SMA10 从 SMA60 下方严格穿到上方，产生 `BUY`，表示“买入关注点”。
- SMA10 从 SMA60 上方严格穿到下方，产生 `SELL`，表示“卖出关注点”。
- 两条均线相等不算交叉，维持在同一侧不会重复通知。
- 只有完整 15 分钟K线参与计算，缺分钟、午休拼接和未完成K线会被排除。
- 启动时加载的历史数据只用于预热，不会把旧信号重新发送到飞书。

`BUY` 和 `SELL` 都不是实际成交。系统当前不读取资金、持仓或账户。

## 二、准备 Windows 环境

### 1. 打开项目目录

以下命令全部在 PowerShell 中执行。已有项目时直接进入：

```powershell
Set-Location D:\Codes\PycharmProjects\tradepilot
```

第一次从 GitHub 获取项目时使用：

```powershell
Set-Location D:\Codes\PycharmProjects
git clone https://github.com/tufeiming/tradepilot.git
Set-Location tradepilot
```

确认当前位置正确：

```powershell
Get-Location
Get-ChildItem pyproject.toml, config.example.toml
```

应该能看到 `pyproject.toml` 和 `config.example.toml`。

### 2. 安装并检查 uv

先检查：

```powershell
uv --version
```

如果提示找不到 `uv`，可以通过 Windows Package Manager 安装：

```powershell
winget install --id astral-sh.uv -e
```

安装后关闭并重新打开 PowerShell，再次执行 `uv --version`。

### 3. 创建 Python 3.13 环境

```powershell
uv sync --python 3.13 --all-groups
```

`uv` 会创建项目本地 `.venv` 并安装锁定依赖。首次运行需要联网。检查版本：

```powershell
uv run python --version
uv run tradepilot --help
```

Python 应为 `3.13.x`，帮助中应包含 `doctor`、`monitor` 和 `backtest`。

## 三、创建本地配置

### 1. 从示例复制

先检查本地配置是否已经存在：

```powershell
Test-Path config.toml
```

输出 `False` 时再复制：

```powershell
Copy-Item config.example.toml config.toml
```

输出 `True` 时不要覆盖，直接打开已有文件检查。该文件被 Git 忽略，可以保存本地 webhook。

### 2. 第一次只做回测的配置

打开 `config.toml`，确认内容至少为：

```toml
[data_source]
name = "tencent"
poll_interval_seconds = 3.0
stale_after_seconds = 30.0

[monitor]
symbols = ["515080.SSE"]
minimum_history_bars = 100

[strategy]
name = "double_ma_signal"
bar_window_minutes = 15
fast_window = 10
slow_window = 60

[execution]
mode = "notify"

[feishu]
enabled = false
```

字段含义：

| 字段 | 含义 |
|---|---|
| `symbols` | 需要监控的股票或 ETF；必须带 `.SSE` 或 `.SZSE` |
| `minimum_history_bars` | 启动前至少预热多少根策略周期K线 |
| `bar_window_minutes` | 实时K线聚合周期，当前必须是 15 |
| `fast_window` | 快均线周期，当前默认 10 |
| `slow_window` | 慢均线周期，当前默认 60 |
| `mode` | 当前只能是 `notify`，表示只通知 |
| `feishu.enabled` | 是否启用飞书；回测阶段可以关闭 |

常见代码示例：

```text
515080.SSE   上海 ETF
600519.SSE   上海股票
159915.SZSE  深圳 ETF
000001.SZSE  深圳股票
```

## 四、先用 GUI 回测验证流程

回测不要求 A股正在交易，也不要求配置飞书。

### 1. 打开回测界面

```powershell
uv run tradepilot backtest
```

等待出现 VeighNa “CTA回测”窗口。窗口左侧是参数，中央是统计和日志，右侧是结果图表。

### 2. 下载 15 分钟历史数据

第一次必须先下载。左侧填写：

| 界面字段 | 第一次建议值 | 说明 |
|---|---|---|
| 本地代码 | `515080.SSE` | 和配置中的标的一致 |
| K线周期 | `15m` | 不要选成 `1m` 或 `d` |
| 开始日期 | `2026/01/01` | 可以早于腾讯实际保留日期 |
| 结束日期 | 最近一个已收盘交易日 | 周末使用上一个周五 |

截至 2026-07-25，可使用结束日期 `2026/07/24`。点击“下载数据”，等待日志出现：

```text
515080.SSE-15m开始下载历史数据
515080.SSE-15m历史数据下载完成，数据量：1904，范围：2026-01-26 09:30 至 2026-07-24 14:45
```

实际数量会随时间变化，不要求永远是 1904。判断成功的标准是：

- 日志显示“下载完成”，不是“数据下载失败”。
- 数据量大于 100。
- 最后一根时间接近最近已收盘交易日的 `14:45`。

数据保存在：

```text
.vntrader/tradepilot_history.db
```

重复下载会更新同一根K线，不会不断创建重复数据。腾讯只保留约六个滚动月，应定期下载以保留本地
旧数据。

### 3. 设置回测参数

下载完成后，不需要关闭窗口。继续填写左侧：

| 界面字段 | 流程验证值 | 为什么这样填 |
|---|---:|---|
| 交易策略 | `DoubleMaLongBacktestStrategy` | 当前 A股/ETF 多头模拟策略 |
| 本地代码 | `515080.SSE` | 必须和下载代码相同 |
| K线周期 | `15m` | 必须和下载周期相同 |
| 开始日期 | `2026/02/09` | 给开始日前留下至少 100 根预热K线 |
| 结束日期 | `2026/07/24` | 不超过已下载数据末日 |
| 手续费率 | `0` | 第一次只验证流程 |
| 交易滑点 | `0` | 第一次只验证流程 |
| 合约乘数 | `100` | 一手 ETF 按 100 份模拟 |
| 价格跳动 | `0.001` | ETF 常用最小价格变化 |
| 回测资金 | `100000` | 示例研究本金 |

以后日期发生变化时，选择规则是：

1. 先在下载日志中确认最早和最晚日期。
2. 回测开始日期至少比最早日期晚 7 个完整交易日，建议留 10 个交易日。
3. 回测结束日期不能晚于下载日志中的最后交易日。

### 4. 确认策略参数

点击“开始回测”，随后会弹出策略参数窗口。确认：

```text
fast_window = 10
slow_window = 60
history_size = 100
bar_window_minutes = 15
fixed_size = 1
t_plus_one = true
```

参数含义：

| 参数 | 含义 |
|---|---|
| `fast_window` | 10 根15分钟K线的简单移动平均 |
| `slow_window` | 60 根15分钟K线的简单移动平均 |
| `history_size` | 指标内部保留和预热的K线数量 |
| `fixed_size` | 每次模拟买入一手 |
| `t_plus_one` | 当天买入后，不允许当天模拟卖出 |

点击参数窗口中的“确定”，等待日志停止滚动。

### 5. 判断回测是否成功

日志应依次出现类似内容：

```text
开始加载15分钟历史数据
15分钟历史数据加载完成，数据量：...
策略初始化完成
开始回放历史数据
历史数据回放结束
逐日盯市盈亏计算完成
策略统计指标计算完成
```

然后检查：

- 中央统计区域有回测日期、总交易日、成交笔数、收益和回撤。
- 右侧净值、回撤、每日盈亏和盈亏分布图不是空白。
- 点击“成交记录”能查看模拟开仓和平仓。
- 点击“K线图表”能查看K线和模拟买卖位置。

本项目实测使用 2026-02-09 至 2026-07-24 数据时，回放 1744 根K线并产生 31 笔模拟成交。数量会
随数据和参数变化。收益为正或负都不影响“流程已经打通”的判断，也不能说明策略有效。

### 6. 关闭回测界面

直接关闭窗口即可。历史数据不会被删除，下次可以直接运行：

```powershell
uv run tradepilot backtest
```

只有需要补充最新数据时才重新点击“下载数据”。

## 五、配置飞书通知

完成回测流程后，再配置实时通知。

### 1. 在飞书创建自定义机器人

在目标飞书群中添加“自定义机器人”，获取 webhook URL。URL 形式类似：

```text
https://open.feishu.cn/open-apis/bot/v2/hook/xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
```

不要把真实 URL 提交到 Git，也不要发到公开聊天或截图中。

### 2. 写入本地配置

编辑已被 Git 忽略的 `config.toml`：

```toml
[feishu]
enabled = true
webhook_url = "https://open.feishu.cn/open-apis/bot/v2/hook/你的机器人标识"
```

如果机器人启用了签名校验，密钥不要写进 TOML，在当前 PowerShell 设置：

```powershell
$env:TRADEPILOT_FEISHU_SECRET = "你的签名密钥"
```

也可以用 `TRADEPILOT_FEISHU_WEBHOOK_URL` 环境变量覆盖 TOML 中的 URL。

### 3. 发送测试消息

```powershell
uv run tradepilot doctor --send-test
```

成功时，飞书群会收到带 `[TEST]` 的消息。`doctor` 还会检查依赖版本、实时快照以及每个标的是否有
至少 100 根 15 分钟预热数据。

如果只想测试机器人而不改策略，可以一直使用这条命令，不需要启动 `monitor`。

## 六、开盘时实时盯盘

### 1. 启动

在项目根目录执行：

```powershell
uv run tradepilot monitor
```

程序启动时会：

1. 连接只读腾讯行情。
2. 下载或刷新最近约半年 15 分钟历史数据。
3. 为每个配置标的创建独立策略实例。
4. 加载 100 根历史K线预热 SMA10/SMA60。
5. 预热成功后启动实时策略，并发送“策略就绪”和“TradePilot启动”通知。

首次启动可能因下载历史数据等待几秒。不要关闭 PowerShell 窗口。

### 2. 开盘和非开盘时间的表现

在交易时间运行时，程序每 3 秒轮询行情。只有新的完整 15 分钟K线产生严格交叉时才发送 BUY 或
SELL，因此长时间没有买卖通知是正常现象。

在晚上、周末、节假日运行时：

- 程序可以正常启动和完成历史预热。
- 不会伪造实时K线或买卖点。
- 会等待下一个交易时段，无需反复重启。

### 3. 飞书信号内容

信号会包含：

- BUY 或 SELL 方向。
- 标的代码。
- 15分钟K线开始时间。
- K线收盘价。
- 当前 SMA10 和 SMA60。
- 数据源名称。

同一标的、方向和K线时间只成功通知一次。网络失败的消息保存在本地待发送队列，程序重启后继续
重试。

### 4. 正常停止

在运行窗口按：

```text
Ctrl+C
```

程序会停止策略、关闭行情线程、保存通知状态，并发送正常关闭通知。不要直接结束 Python 进程，除非
程序已经失去响应。

## 七、增加多个标的

修改 `config.toml`：

```toml
[monitor]
symbols = ["515080.SSE", "159915.SZSE", "600519.SSE"]
minimum_history_bars = 100
```

注意：

- 代码不能重复。
- 必须使用六位数字和正确交易所后缀。
- 每个标的拥有独立的 SMA、K线和通知状态。
- GUI 下载历史时需要逐个切换“本地代码”并点击“下载数据”。
- 实时启动时任一标的预热失败，不应阻止其他已就绪标的运行。

## 八、日常使用建议

### 每个交易日收盘后

1. 运行 `uv run tradepilot backtest`。
2. 选择标的和 `15m`。
3. 把结束日期改为当天。
4. 点击“下载数据”补充本地历史。
5. 下载完成后关闭窗口，或继续运行回测。

### 每次修改策略参数后

1. 修改 `config.toml`。
2. 重新运行完整回测。
3. 完全关闭旧的实时进程。
4. 执行 `uv run tradepilot doctor`。
5. 再执行 `uv run tradepilot monitor`。

### 查看日志和本地状态

```text
.vntrader/logs/tradepilot.log
.vntrader/tradepilot_history.db
.vntrader/tradepilot_notifications.json
```

整个 `.vntrader` 目录都是本地运行状态，不应提交到 Git。

## 九、常见问题

| 现象 | 原因 | 处理 |
|---|---|---|
| `config.toml` 不存在 | 还没有复制示例配置 | 执行 `Copy-Item config.example.toml config.toml` |
| 配置提示缺少 webhook | `feishu.enabled=true` 但没有 URL | 回测时设为 `false`，实时通知时填写本地 URL |
| GUI 数据量为 0 | 尚未下载或代码、周期不一致 | 选择同一代码和 `15m`，先点击“下载数据” |
| 只有约 5 个交易日 | 误选了 `1m` | 切换为 `15m` 后重新下载 |
| 策略未就绪 | 回测开始日期预热不足，或实时历史少于100根 | 向后调整回测开始日期，检查网络并重新下载 |
| 没有收到 BUY/SELL | 没出现严格交叉，或当前不在交易时段 | 先看启动/就绪通知，等待完整15分钟K线 |
| 飞书测试失败 | URL、签名、机器人权限或网络错误 | 检查本地配置和飞书机器人安全设置 |
| GUI 图表 `KeyError: 0` | Pandas 版本不兼容 | 执行 `uv sync --python 3.13 --all-groups` |
| 修改代码后界面没变化 | 旧 GUI 进程仍在 | 完全关闭窗口后重新运行命令 |
| `15m` 参数优化被拒绝 | 当前只支持 15m 单次回测 | 手工修改参数，分别运行并记录结果 |

## 十、当前限制

- 腾讯是非官方免费 POC 数据源，没有稳定性和交易级质量承诺。
- 腾讯 `m15` 约保留六个滚动月，不适合两三年以上研究。
- 当前分钟数据没有正式定义完整的复权处理流程。
- 回测没有完整模拟涨跌停、停牌、成交量限制和真实券商撮合。
- 实时 SELL 不检查是否持仓。
- 当前禁止自动下单，`paper` 和 `live` 模式尚未开放。

当前阶段应把系统用于验证数据、策略、回测和通知流程。进入仿真或实盘前，必须更换正式数据/券商
Gateway，并增加账户核对、T+1、整数手、资金、风控和紧急停止机制。
