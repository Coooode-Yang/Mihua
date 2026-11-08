# `netpet_tools` — 桌宠网络助手独立工具包 (v1)

`netpet_tools` 是保留的独立网络诊断与受控操作工具层。米花个人版普通聊天不注册网络工具；此子项目供独立开发、兼容代码与模拟测试使用。原工具作者为 AI-for-Net Project Team，项目声明 MIT。

## 功能范围与设计边界

1. **只读网络观测**：
   - `get_network_snapshot`：本机多网卡区分（Wi-Fi / 以太网 / 虚拟网卡）、出口接口选择、IPv4/IPv6（含 APIPA 识别）、默认路由、DNS、DHCP 配置与租约有效性、Wi-Fi 信号质量与保守 RSSI 估算、Windows 位置权限受限降级。
   - `query_dns`：基于 `dnspython` 的 `A`/`AAAA` 解析、指定解析器与可选公共解析器对比，严格区分 `NOERROR`、`NOERROR_NODATA`、`NXDOMAIN`、`SERVFAIL`、`REFUSED`、`TIMEOUT`、`UNREACHABLE`。
   - `probe_connectivity`：基于 `httpx`（显式 `trust_env=False`）的 `direct` / `configured_proxy` 双路径探测、不自动跟随重定向的校园网认证（Captive Portal）证据分级（`confirmed | suspected | not_detected | unknown`），分离记录 TCP 连接耗时、TLS 握手耗时与 HTTP 响应耗时。
   - `measure_latency_and_loss`：1–10 包受控 ICMP Echo 测量，按实际计数精确计算丢包率与 ICMP RTT。
   - `get_proxy_state`：分离读取 HKCU 用户代理、PAC/自动检测、环境变量代理、WinHTTP、配置候选本地端口监听状态及可选 Mihomo 只读控制接口。
   - `check_target_routing_rule`：按显式配置规则进行域名精确/有边界后缀匹配，仅在证据充分时判定 `conflict=True`，否则返回 `conflict=None` 并列出缺失证据。
2. **模型计划、宿主授权执行闭环**：
   - 模型仅可调用只读规划 `plan_action`（白名单：`flush_dns`、`open_browser_url`、`reconnect_wifi`、`set_system_proxy`）。
   - 仅宿主确认界面可调用 `approve_action` 获取单次令牌并调用 `execute_action`，自动复检原始故障目标并在支持且安全的前提下按条件回退。
3. **安全与隔离默认项**：
   - 默认 `mode="mock"`，默认 `allow_writes=False`。
   - `import netpet_tools` 零副作用（不访问网络、不读写注册表、不启动浏览器、不创建文件）。
   - 不依赖 PySide6、PydanticAI、CrewAI、Ollama 或任何模型 SDK。

---

## 环境要求与安装

- **操作系统**：Windows 10 / 11
- **Python**：3.11 或 3.12（已锁定核心依赖：`pydantic>=2.6,<3.0`、`httpx>=0.28,<1.0`、`dnspython>=2.6,<3.0`）

```powershell
cd Tools
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

安装后支持从任意工作目录直接导入 `from netpet_tools import ToolConfig, ToolService`，无需手动修改 `sys.path`。

---

## 运行与演示命令

### 1. 运行默认离线测试套件（完全离线、零系统写入）

```powershell
cd Tools
.\.venv\Scripts\python.exe -m pytest
```

### 2. CLI 只读诊断演示（默认禁止任何写操作）

```powershell
# 查看内置可公开调用工具与内置离线场景
.\.venv\Scripts\python.exe -m netpet_tools.cli --list-tools
.\.venv\Scripts\python.exe -m netpet_tools.cli --list-scenarios

# 运行 case_02_portal 场景下的网络快照与连通性探测
.\.venv\Scripts\python.exe -m netpet_tools.cli --mode mock --scenario case_02_portal --tool get_network_snapshot
.\.venv\Scripts\python.exe -m netpet_tools.cli --mode mock --scenario case_02_portal --tool probe_connectivity

# 传入 JSON 参数运行 DNS 对比或 ICMP 丢包测量
.\.venv\Scripts\python.exe -m netpet_tools.cli --mode mock --scenario case_03_loss --tool measure_latency_and_loss --params "{\"host\": \"10.24.18.1\", \"count\": 10}"
```

### 3. 宿主集成示例（只读注册 + Plan → Approve → Execute 闭环）

```powershell
.\.venv\Scripts\python.exe examples\host_integration.py
```

### 4. 手动开启真实本机只读 Smoke 测试（可选）

```powershell
.\.venv\Scripts\python.exe -m pytest -m real_smoke -v
```

---

## 配置文件与环境变量说明

参考 [`configs/config.example.json`](configs/config.example.json) 创建本地配置（例如 `configs/config.local.json`，已被 `.gitignore` 忽略）：

- `probe.trusted_portal_hosts`：填入本校真实校园网 Portal 认证域名或认证服务器 IP。
- `probe.allowed_private_targets`：填入允许 HTTP 探测的校内私网网段或校内域名后缀。
- `probe.allowed_browser_hosts`：填入允许 `open_browser_url` 打开的认证页或网络帮助页主机名。
- `proxy.configured_proxy_url`：填入本机显式 HTTP 代理地址（如 `"http://127.0.0.1:7890"`），供 `route="configured_proxy"` 对比探测使用。
- `proxy.mihomo_controller`：若使用 Mihomo/Clash 兼容内核并开启本地控制接口，可将 `enabled` 设为 `true`，并通过环境变量提供密钥（切勿写入配置文件）：
  ```powershell
  $env:NETPET_MIHOMO_SECRET = "your-local-controller-secret"
  ```
- `proxy.routing_rules`：填入经用户确认的站点路由策略（`user_confirmed: true`）。`config.example.json` 中的示例规则默认标记为 `user_confirmed: false`，不会冒充真实策略断言冲突。

---

## 已知限制

1. **应用层直连与系统级虚拟路由边界**：`probe_connectivity(route="direct")` 与 `query_dns` 仅保证工具自身未显式走应用层 HTTP 代理（`trust_env=False`），无法绕过操作系统层已生效的全局 VPN、TUN 虚拟网卡或透明网关策略。
2. **PAC 与非 HTTP 代理**：当系统仅配置 PAC (`AutoConfigURL`) 或未配置显式 HTTP/HTTPS 代理地址时，`route="configured_proxy"` 与 `set_system_proxy` 会明确返回 `UNSUPPORTED`，不会偷偷退回直连或覆盖 PAC 配置。
3. **首版不包含扩展功能**：蓝牙故障排查、苹果手机待办事项同步、久坐/喝水提醒不在首版 `netpet_tools` 范围内。
4. **详细接口文档与当前测试**：参见 [`docs/TOOL_API.md`](docs/TOOL_API.md)；当前开源副本验证见上级目录 [`docs/VALIDATION.md`](../docs/VALIDATION.md)。
