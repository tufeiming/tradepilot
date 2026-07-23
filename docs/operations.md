# 运行与部署

## Windows 开发和首轮盯盘

先在项目目录完成环境和配置：

```powershell
uv sync --python 3.13 --all-groups
Copy-Item config.example.toml config.toml
$env:TRADEPILOT_FEISHU_WEBHOOK_URL = "https://open.feishu.cn/open-apis/bot/v2/hook/..."
$env:TRADEPILOT_FEISHU_SECRET = "可选签名密钥"
uv run tradepilot doctor --config config.toml --send-test
uv run tradepilot monitor --config config.toml
```

需要随登录启动时，可在 Windows 任务计划程序创建任务：

- 触发器：用户登录时，或工作日 09:00。
- 程序：`C:\Users\<用户名>\.local\bin\uv.exe`。
- 参数：`run tradepilot monitor --config D:\Codes\PycharmProjects\tradepilot\config.toml`。
- 起始于：`D:\Codes\PycharmProjects\tradepilot`。
- 失败后每 1 分钟重启，最多 3 次。
- 飞书环境变量应配置在运行任务的用户环境中，不要放进任务参数或仓库。

日志位于 `.vntrader/logs/tradepilot.log`，单文件 5MB，保留 5 份。

## Linux systemd 模板

代码不依赖 Windows API。迁移到 Linux 后，在服务器完成 `uv sync --python 3.13 --all-groups`，
并创建仅服务账号可读的 `/etc/tradepilot.env`：

```text
TRADEPILOT_FEISHU_WEBHOOK_URL=https://open.feishu.cn/open-apis/bot/v2/hook/...
TRADEPILOT_FEISHU_SECRET=optional-secret
```

`/etc/systemd/system/tradepilot.service` 示例：

```ini
[Unit]
Description=TradePilot VeighNa signal monitor
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=tradepilot
WorkingDirectory=/opt/tradepilot
EnvironmentFile=/etc/tradepilot.env
ExecStart=/opt/tradepilot/.venv/bin/tradepilot monitor --config /opt/tradepilot/config.toml
Restart=on-failure
RestartSec=10
TimeoutStopSec=15

[Install]
WantedBy=multi-user.target
```

```bash
sudo chmod 600 /etc/tradepilot.env
sudo systemctl daemon-reload
sudo systemctl enable --now tradepilot
sudo journalctl -u tradepilot -f
```

腾讯公开行情只能用于流程验证。接入任何自动交易模式前，必须替换为券商正式 Gateway。
