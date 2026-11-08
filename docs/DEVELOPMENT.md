# 开发与验证

## 环境

Windows 10 / 11，Python 3.11 或 3.12。部分功能使用 Win32、DPAPI、系统托盘和全局快捷键，尚未验证 Linux / macOS 支持。使用独立 `.venv`，不要在系统 Python 中混装项目依赖。

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt -e ./Tools
.\.venv\Scripts\python.exe app.py
```

`requirements.txt` 声明直接运行依赖，`Tools/pyproject.toml` 声明本地工具层依赖。`requirements-lock.txt` 保留经验证的 Windows 开发环境快照，并不保证所有平台或未来软件源均可复现；常规安装使用前两个声明。

## 测试

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe app.py --chick-self-test
.\.venv\Scripts\python.exe app.py --unified-self-test
.\.venv\Scripts\python.exe app.py --api-self-test
.\.venv\Scripts\python.exe verify.py
.\.venv\Scripts\python.exe packaging/audit_public.py
```

普通 pytest 排除 `real_smoke` 标记，使用模拟网络/API 和临时数据。`--api-self-test` 使用本机模拟服务，不调用真实模型。Qt 离屏检查可以核验事件和布局行为，但不能替代真实桌面、多屏、缩放和长时间运行测试。

只在明确需要检查真实 Windows 主机时单独运行：

```powershell
.\.venv\Scripts\python.exe -m pytest Tools/tests -m real_smoke
```

不要把真实主机输出、真实 API Key 或个人数据库附在公开测试报告中。

## 可选转换组件

```powershell
.\.venv\Scripts\python.exe packaging/prepare_filee.py
```

下载地址与版本固定，并检查 SHA-256；也可以用 `--archive` 指定已下载的官方 ZIP。组件放在 `runtime/filee/`，不提交 Git。不同格式可能依赖引擎的额外组件；界面仅提供本机已就绪的转换能力。未配置外部引擎不影响桌宠、待办、计时或普通聊天。

## 素材

安装 `packaging/requirements-artwork.txt` 后可以从保留的 OBJ 重新渲染。素材说明见 [ARTWORK.md](ARTWORK.md)。

## 依赖与边界

界面在 Qt 主线程运行；模型、文献读取和转换工作在后台执行。`westie_agent/reader_math.py` 为带来源说明的上游 Apache-2.0 代码，不应删除其声明。`assets/skills` 中的固定上游资源由校验清单保护，变更时需更新对应来源、版本和校验信息。

`Tools` 是独立可安装子项目，包含只读观测和受控操作 API。个人版的普通聊天不向模型注册这些网络工具；保留它们用于兼容、独立开发和测试。
