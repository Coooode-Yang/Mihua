# `netpet_tools` v1 接口与宿主交接规范 (`TOOL_API.md`)

本文档定义 `netpet_tools` 首版（`schema_version = "1.0"`）全部公开接口、返回结构、错误码、超时预算、副作用边界与 Codex 宿主接入约定。

---

## 一、宿主接入快速概览

```python
from netpet_tools import ActionApprovalError, ErrorCode, ToolConfig, ToolService

# 1. 创建实例级配置（默认 mode="mock", allow_writes=False；无全局可变状态）
config = ToolConfig(mode="mock", scenario_id="case_02_portal", allow_writes=False)
service = ToolService(config)

# 2. 获取暴露给大模型 / PydanticAI 的只读与计划工具注册表（恰好 7 项）
model_tools = service.available_tools
# list(model_tools.keys()) == [
#   "get_network_snapshot",
#   "query_dns",
#   "probe_connectivity",
#   "measure_latency_and_loss",
#   "get_proxy_state",
#   "check_target_routing_rule",
#   "plan_action",
# ]

# 3. 宿主确认后执行（仅宿主 UI/控制器调用，绝不暴露给模型）
plan_result = service.plan_action(
    action_name="open_browser_url",
    params={"url": "https://auth.campus.edu.test/portal/login"},
    verification_target="http://connect.rom.miui.com/generate_204",
)
if plan_result["success"]:
    plan_id = plan_result["data"]["plan_id"]
    # 用户在桌宠确认面板点击“允许执行”后：
    token = service.approve_action(plan_id)  # 单次有效，切勿送入 LLM 上下文或普通日志
    exec_result = service.execute_action(plan_id, approval_token=token)
```

### 线程与并发约定
- 全部公开方法均为**同步方法**，无内部 Qt 事件循环或弹窗阻塞；Codex 可直接在 `QThread` / `QRunnable` 工作线程中调用。
- 不同 `ToolService` 实例之间完全隔离（包括 `mock` 场景状态与操作计划），可安全并行运行评测。
- 同一 `ToolService` 实例内部通过可重入锁保护计划与模拟状态，并通过独立互斥写锁将 `execute_action` 严格串行化。

---

## 二、统一返回信封 v1 (`ToolResult`)

除宿主专用的 `approve_action(plan_id) -> str` 外，所有工具方法统一返回纯 Python `dict`（经 Pydantic v2 `model_dump(mode="json")` 与 `json.dumps` 校验，不泄漏 `datetime`、`Path`、`bytes` 或异常对象）：

```json
{
  "schema_version": "1.0",
  "tool": "query_dns",
  "success": true,
  "status": "ok",
  "timestamp": "2026-10-30T12:00:00+00:00",
  "duration_ms": 14.2,
  "mode": "mock",
  "scenario_id": "case_01_normal",
  "data": {},
  "summary": "域名 'www.campus.edu.test' (A) 主查询状态为 NOERROR，返回 1 条记录",
  "warnings": [],
  "error": null
}
```

### 状态与错误码语义
- `status`：
  - `"ok"`：完整获取观测结果或完成本次受控动作（`success=True, error=None`）。**注意**：`NXDOMAIN`、HTTP `403`、DNS `TIMEOUT`、ICMP 100% 丢包均属于有效的负面网络观测，`status="ok"` 且 `success=True`。
  - `"partial"`：核心观测已返回，但部分字段因权限/预算受限缺失（如 Windows 位置权限阻止读取 SSID，但保留了 IP 与路由信息；`success=True, error=None`，原因写入 `warnings`）。
  - `"error"`：输入非法、场景缺失、权限拒绝、不支持该代理类型或动作本身执行失败（`success=False`，`error={code, message, retryable}`）。
- `ErrorCode` 稳定枚举：
  - `INVALID_INPUT`：参数类型、范围、域名/URL 格式、过期或未知 `plan_id` 错误。
  - `TIMEOUT`：底层子进程超出总预算超时。
  - `PERMISSION_DENIED`：未授权目标私网 URL、未在允许列表中的浏览器主机、缺失/错误/重放 `approval_token`、或 `real` 模式下 `allow_writes=False`。
  - `LOCATION_PERMISSION_REQUIRED`：依赖 Windows 位置权限的操作（如位置受限时校验 Wi-Fi 配置文件）无法进行。
  - `UNSUPPORTED`：不在白名单内的动作、PAC 代理下请求 `configured_proxy` 或手动改写系统代理、首次 Wi-Fi 配网等。
  - `DEPENDENCY_UNAVAILABLE`：运行环境缺失必要系统组件。
  - `INVALID_SCENARIO`：`mock` 模式下 `scenario_id` 为空、非法、文件不存在或结构损坏（绝不自动退回 `real`）。
  - `INTERNAL_ERROR`：底层命令执行失败或未预期异常。

### 审计日志与脱敏
- `service.get_audit_records()` 返回当前实例内存中的脱敏调用记录；若 `ToolConfig.audit_log_path` 非空，每次调用追加一行 JSONL。
- 自动脱敏规则：所有包含 `token` / `secret` / `password` / `api_key` / `auth` 的参数值替换为 `"<redacted>"`；URL 查询参数值全部脱敏为 `key=<redacted>`；代理字符串中的凭据自动脱敏。

---

## 三、公开函数逐项规范

### 1. `get_network_snapshot`
- **签名**：`get_network_snapshot(interface: str = "auto", include_recent_events: bool = False) -> dict[str, Any]`
- **暴露给模型**：是（位于 `available_tools`）
- **输入约束**：
  - `interface`：非空字符串，长度 `<= 128`，不含控制字符；`"auto"` 按连接状态、默认网关存在性、物理/虚拟类型与路由跃点数排序选择；指定名称时匹配适配器名或描述，不存在返回 `INVALID_INPUT`（附 `available_interfaces`）。
  - `include_recent_events`：严格 `bool`。
- **Mode 行为**：
  - `mock`：从当前实例隔离的场景快照构造并归一化。
  - `real`：优先执行固定只读 PowerShell 脚本获取 UTF-8 JSON（含 `Win32_NetworkAdapterConfiguration` 真实 DHCP 租约时间戳），失败时回退解析中英文 `ipconfig /all`；无线字段补充自 `netsh wlan show interfaces`；`include_recent_events=True` 时读取 `Microsoft-Windows-WLAN-AutoConfig/Operational` 最近 8 条事件。
- **超时预算**：PowerShell 快照 `6.0s`（回退 `ipconfig` `5.0s`），`netsh wlan` `4.0s`，WLAN 事件 `4.0s`。
- **副作用**：无（纯只读；遇到位置权限受限时返回 `status="partial"` 并保留 IP/路由数据，不修改系统隐私设置）。
- **关键返回字段 (`data`)**：
  - `selected_interface`：含 `adapter_type` (`wifi|ethernet|virtual|other`)、`ipv4_addresses`（每项标记 `is_apipa`、`is_private`）、`ipv6_addresses`（每项标记 `is_link_local`、`is_global_or_ula`）、`has_apipa_ipv4`、`has_usable_ipv6`、`default_gateways_ipv4/ipv6`、`dns_servers`。
  - DHCP 独立字段：`dhcp_enabled`、`dhcp_lease_present`、`dhcp_lease_obtained`、`dhcp_lease_expires`、`dhcp_lease_valid`（无租约时间戳时恒为 `null`，绝不根据私网 IP 推断有效）。
  - `wlan`：`signal_quality_pct` 保留原始百分比；仅原生读取到 dBm 时填入 `rssi_dbm`；仅百分比且在 `1..99` 时填入 `rssi_estimated_dbm = quality/2 - 100`（`rssi_source="estimated_from_quality"`）；`0%` 与 `100%` 边界质量下 `rssi_estimated_dbm=null` 且 `rssi_source="boundary_clamped"`。

---

### 2. `query_dns`
- **签名**：`query_dns(domain: str, dns_server: str | None = None, record_type: str = "A", compare_public: bool = False) -> dict[str, Any]`
- **暴露给模型**：是
- **输入约束**：
  - `domain`：裸域名（支持中文 IDNA 域名），长度 `<= 253` ASCII 字符，拒绝 URL、端口、单标签主机名或纯 IP 字面量（返回 `INVALID_INPUT`）。
  - `dns_server`：`None` 或合法 IPv4/IPv6 地址字面量。
  - `record_type`：仅支持 `"A"` 或 `"AAAA"`。
  - `compare_public`：严格 `bool`；为 `True` 时额外查询 `config.dns.public_resolvers`。
- **Mode 行为**：
  - `mock`：按场景 `dns.responses` 返回对应解析器状态与记录，经过相同的对比分析函数。
  - `real`：通过 `dnspython` 执行查询，解析 `NoNameservers.kwargs["errors"]` 精确区分 `SERVFAIL`、`REFUSED`、`TIMEOUT`、`UNREACHABLE`。
- **超时预算**：单解析器默认 `2.5s`，整次调用总预算默认 `6.0s`（超出剩余预算时跳过剩余公共解析器并标记 `status="partial"`）。
- **副作用**：无（仅发送 DNS 查询报文）。
- **返回 JSON 示例 (`data`)**：
  ```json
  {
    "data_origin": "synthetic",
    "domain": "www.campus.edu.test",
    "ascii_domain": "www.campus.edu.test",
    "record_type": "A",
    "compare_public": true,
    "primary_result": {
      "resolver_label": "system",
      "nameservers": ["10.10.0.21", "10.10.0.22"],
      "status": "NOERROR",
      "records": ["10.10.8.80"],
      "ttl": 300,
      "elapsed_ms": 6.4,
      "transport": "udp_53_with_tcp_fallback",
      "detail": null
    },
    "queries": ["..."],
    "comparison": {
      "all_noerror": false,
      "status_differ": true,
      "records_differ": null,
      "observation_notes": [
        "Domain resolved on primary resolver but returned NXDOMAIN on public resolvers; this is normal for campus intranet-only domains under split-horizon DNS."
      ]
    },
    "routing_limitation": "DNS queries use OS UDP/TCP port 53 sockets and remain subject to system routing tables, active VPN/TUN adapters, or transparent gateway DNS interception."
  }
  ```

---

### 3. `probe_connectivity`
- **签名**：`probe_connectivity(target_url: str | None = None, route: str = "direct", timeout_s: float = 3.0) -> dict[str, Any]`
- **暴露给模型**：是
- **输入约束**：
  - `target_url`：`None`（使用 `config.probe.probe_endpoints` 已知探测端点）或 `http`/`https` URL。拒绝内嵌凭据、控制字符、`localhost`/回环地址、链路本地/云元数据（`169.254.0.0/16`）；RFC1918 私网目标仅在匹配 `allowed_private_targets` 或 `trusted_portal_hosts` 时放行，否则返回 `PERMISSION_DENIED`（包括真实模式下 DNS 解析结果的二次校验）。
  - `route`：`"direct"` 或 `"configured_proxy"`（代理地址仅从 `config.proxy.configured_proxy_url` 读取；未配置或仅存在 PAC 时返回 `UNSUPPORTED`，绝不偷偷退回直连）。
  - `timeout_s`：`0.2 <= timeout_s <= config.probe.http_total_budget_s`（默认上限 `8.0s`）。
- **Mode 行为**：
  - `mock`：从场景 `http_probes[route]` 读取响应状态码、头、响应体片段，执行真实同款的 `evaluate_portal_evidence` 证据分级。
  - `real`：`httpx.Client(trust_env=False, verify=True, follow_redirects=False)`，流式读取响应体上限 `8192` 字节，通过 `httpcore` trace 分离采集 `tcp_connect_time_ms`、`tls_handshake_time_ms` 与 `http_response_time_ms`。
- **Portal 证据等级 (`portal_state`)**：
  - `"confirmed"`：已知探测端点偏离期望签名，且重定向/响应主机命中已配置 `trusted_portal_hosts`（或返回 511 且命中可信主机）。
  - `"suspected"`：已知探测端点发生异常重定向/内容替换但目标主机未在 `trusted_portal_hosts` 中，或自定义目标跨站重定向至 `trusted_portal_hosts`。
  - `"not_detected"`：已知探测端点状态码与内容前缀完全符合期望签名。
  - `"unknown"`：自定义 URL 无期望签名基线（普通 200、403、普通 `/login` 302 仅作线索），或探测超时/连接失败。
- **副作用**：无（不打开浏览器、不执行脚本、不提交凭据）。

---

### 4. `measure_latency_and_loss`
- **签名**：`measure_latency_and_loss(host: str, count: int = 4) -> dict[str, Any]`
- **暴露给模型**：是
- **输入约束**：
  - `host`：非回环、非链路本地的 IPv4/IPv6 地址或域名；拒绝以 `-` 或 `/` 开头的参数与 URL。
  - `count`：严格整数 `1 <= count <= 10`（拒绝 `bool` 与越界值）。
- **Mode 行为**：
  - `mock`：截取场景确定性样本序列前 `count` 项计算真实比例（例如 `case_03_loss` 中 `count=10` 为 `60.0%`，`count=4` 为 `50.0%`）。
  - `real`：无窗口调用 `ping.exe -n <count> -w <timeout_ms> <host>`，兼容中英文输出，过滤“无法访问目标主机 / Destination host unreachable”伪接收行。
- **超时预算**：单包默认 `1000ms`，总预算上限 `12.0s`。
- **副作用**：无（仅发送少量 ICMP Echo 请求）。
- **关键返回字段 (`data`)**：`sent`、`received`、`lost`、`loss_rate_pct`、`icmp_rtt_samples_ms`、`icmp_rtt_min_ms`、`icmp_rtt_avg_ms`、`icmp_rtt_max_ms`、`interpretation_limits`。

---

### 5. `get_proxy_state`
- **签名**：`get_proxy_state() -> dict[str, Any]`
- **暴露给模型**：是
- **输入约束**：无参数。
- **Mode 行为**：
  - `mock`：读取场景隔离的代理与端口状态。
  - `real`：读取 `HKCU\...\Internet Settings`、连接自动检测标志位、代理环境变量、`netsh winhttp show proxy`、仅探测 `candidate_local_ports` 中的 `127.0.0.1` 端口；仅当 `mihomo_controller.enabled=True` 时以短超时只读请求回环控制接口（密钥取自环境变量 `NETPET_MIHOMO_SECRET`），否则 `client_type`、`mode`、`tun_state` 均诚实返回 `"unknown"`。
- **超时预算**：WinHTTP `3.0s`，每个本地候选端口 `0.15s`，Mihomo 控制器 `1.5s`。
- **副作用**：无（纯只读）。

---

### 6. `check_target_routing_rule`
- **签名**：`check_target_routing_rule(url_or_domain: str) -> dict[str, Any]`
- **暴露给模型**：是
- **输入约束**：合法域名或 `http`/`https` URL。
- **匹配与冲突判定规则**：
  - 仅匹配 `config.proxy.routing_rules` 显式配置（不内置 `.edu.cn`/`.gov.cn`/`github.com` 臆断）。
  - 后缀匹配强制点号边界（`school.example.test.evil.test` 与 `myschool.example.test` 绝不命中 `school.example.test`）。
  - 仅在证据充分时返回 `conflict=True`（如规则已确认需 `direct`，且客户端控制器确认处于 `global` 模式或系统代理指向已关闭的本地端口）；若仅观察到 `ProxyEnable=True` 而客户端分流模式未知，返回 `conflict=None`、`potential_conflict=True`，并在 `missing_evidence` 中提示需补充双路径 `probe_connectivity` 对比证据。
- **超时预算**：同 `get_proxy_state`。
- **副作用**：无。

---

### 7. `plan_action`
- **签名**：`plan_action(action_name: str, params: dict[str, Any] | None = None, verification_target: str | None = None) -> dict[str, Any]`
- **暴露给模型**：是（模型唯一可调用的操作规划入口）
- **输入约束**：
  - `action_name` 白名单：
    1. `"flush_dns"`：`params={}`
    2. `"open_browser_url"`：`params={"url": "<http/https>"}`（URL 主机必须命中 `trusted_portal_hosts` 或 `allowed_browser_hosts`）
    3. `"reconnect_wifi"`：`params={"interface": str, "profile_name": str, "ssid": Optional[str]}`（目标配置文件必须已保存在该接口上）
    4. `"set_system_proxy"`：`params={"enable": bool}`（仅支持 HKCU 手动代理；PAC 激活时拒绝并返回 `UNSUPPORTED`；`enable=True` 时要求已有或预配置有效代理地址）
  - `verification_target`：`None`、原始故障 URL 或原始故障域名。
- **超时预算**：`real` 模式下 `reconnect_wifi` 预检读取已有配置文件最多 `4.0s`；其余均为毫秒级本地校验。
- **副作用**：**无系统副作用**（仅在 `ToolService` 实例内存中保存待确认计划，默认有效期 `300s`）。

---

### 8. `approve_action`（仅宿主调用）
- **签名**：`approve_action(plan_id: str) -> str`
- **暴露给模型**：**严禁暴露**（不在 `available_tools` 中）
- **行为**：校验 `plan_id` 存在、未过期、未被批准或执行过，生成并返回密码学随机单次 `approval_token`（内部仅存储 SHA-256 摘要）。若校验失败抛出 `ActionApprovalError(code, message)`。
- **副作用**：更新实例内计划状态为 `"approved"`。

---

### 9. `execute_action`（仅宿主调用）
- **签名**：`execute_action(plan_id: str, *, approval_token: str) -> dict[str, Any]`
- **暴露给模型**：**严禁暴露**（不在 `available_tools` 中）
- **输入约束**：仅接受已保存的 `plan_id` 与关键字参数 `approval_token`；不接受重新传入 `action_name` 或 `params`。
- **安全门控**：
  - 缺失/不匹配令牌、重放已消费令牌、计划过期、或 `real` 模式下 `allow_writes=False` 时，一律拒绝执行，返回 `status="error"` 且 `data.execution_status="not_executed"`。
- **执行后复检与回退语义 (`data`)**：
  ```json
  {
    "data_origin": "synthetic",
    "plan_id": "plan_0123456789abcdef",
    "action_name": "set_system_proxy",
    "execution_status": "succeeded",
    "recovery_status": "recovered",
    "before": {},
    "after": {},
    "rollback_status": "not_needed",
    "action_details": {}
  }
  ```
  - `execution_status`：`succeeded | failed | not_executed`
  - `recovery_status`：`recovered | not_recovered | unknown | not_checked`（未提供 `verification_target` 时恒为 `not_checked`；`open_browser_url` 仅启动浏览器未登录时复检仍为 `not_recovered`）
  - `rollback_status`：`not_needed | succeeded | failed | unsupported`（`set_system_proxy` 回退前核对当前注册表值仍等于本次写入值，检测到外部并发修改时中止回退并返回 `failed`，防止覆盖用户新设置）
- **副作用**：`mock` 模式仅修改当前 `ToolService` 实例内存状态；`real` 模式且 `allow_writes=True` 时执行对应受控系统操作并广播 WinINet 刷新。

---

### 10. `set_campus_domain` 与 `get_campus_profile`（宿主动态配置接口）
- **签名**：
  - `set_campus_domain(domain: str, alias_name: str | None = None, *, auto_verify_dns: bool = False, enforce_resolution: bool = False) -> dict[str, Any]`
  - `get_campus_profile() -> dict[str, Any]`
- **暴露给模型**：否（由宿主控制器根据 LLM 对话提取的学校域名调用）
- **功能**：
  - 动态将学校主域名（如 `bnuz.edu.cn`）注入到 `trusted_portal_hosts`、`allowed_browser_hosts`、`allowed_private_targets`，并生成确认的直连路由规则（`policy="direct", user_confirmed=True`）。
  - `auto_verify_dns=True` 时主动调用 DNS 解析该域名；若 `enforce_resolution=True` 且域名解析返回 `NXDOMAIN` 或超时，则拒绝注入并返回 `success=False`，防止大模型幻觉导致规则配错。
- **返回值示例**：
  ```json
  {
    "success": true,
    "domain": "bnuz.edu.cn",
    "ascii_domain": "bnuz.edu.cn",
    "alias_name": "北京师范大学珠海校区",
    "dns_verified": true,
    "dns_primary_status": "NOERROR",
    "dns_records": ["172.16.200.11"],
    "summary": "已成功设置校园主域名 'bnuz.edu.cn'（北京师范大学珠海校区），DNS解析核实有效；认证白名单与直连策略已动态生效。"
  }
  ```

---

## 四、Codex 接入清单与待用户补充配置项

### Codex 侧接入要点
1. **工具注册**：直接遍历 `service.available_tools.items()` 注册至 PydanticAI Agent；保留函数签名与 docstring。
2. **“用户输入校名 → 大模型推断域名 → 工具验证生效”闭环**：
   - 用户在桌宠输入：“我是北京师范大学珠海校区的”。
   - 大模型提取对应顶级域名为 `bnuz.edu.cn`。
   - 宿主直接调用 `service.set_campus_domain("bnuz.edu.cn", alias_name="北京师范大学珠海校区", auto_verify_dns=True, enforce_resolution=True)`。
   - 工具自动进行 DNS 核验与动态规则注入，完全免去普通学生手动编辑 JSON 的门槛。
3. **确认弹窗绑定**：当模型调用 `plan_action` 返回 `success=True` 时，桌宠界面展示 `data["action_name"]`、`data["target"]`、`data["impact"]`、`data["expires_at"]`；用户点击确认后在宿主侧顺序调用 `tok = service.approve_action(plan_id)` 与 `service.execute_action(plan_id, approval_token=tok)`，再将返回的 `exec_result`（不含 `tok`）回传给对话流解释复检结果。
4. **证据驱动提示词配合**：利用 `portal_state`、`conflict`、`missing_evidence` 与 `recovery_status` 引导模型考虑多候选假设并在证据不足时诚实说明。

### 待用户补充的真实环境配置（不阻塞通用模块与离线评测）
在接入真实校园网与用户电脑运行 `mode="real"` 前，需由用户在本地配置中补充：
1. **学校校园网认证主机 (`probe.trusted_portal_hosts` & `probe.allowed_browser_hosts`)**：例如学校 Portal 登录页域名或认证 AC 地址。
2. **学校校内测试/常用站点与私网允许范围 (`probe.allowed_private_targets` & `proxy.routing_rules`)**：例如教务系统、校内文献库域名及确认的 `direct`/`proxy` 策略（将 `user_confirmed` 设为 `true`）。
3. **本机代理客户端信息 (`proxy.configured_proxy_url` & `proxy.mihomo_controller`)**：用户实际使用的代理软件本地 HTTP 监听端口，以及是否开启 Mihomo/Clash 兼容控制接口与对应环境变量密钥。
