# 构建与发布

源码仓库和可执行发布包分开管理。源码提交不包含 EXE、`.venv`、`runtime`、个人数据、旧程序备份或签名私钥。

## 构建 Windows 应用

使用 Windows x64、Python 3.11 / 3.12 和独立虚拟环境。先按 README 安装运行、开发依赖，再准备文件转换引擎：

```powershell
.\.venv\Scripts\python.exe packaging/prepare_filee.py
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe packaging/audit_public.py
.\.venv\Scripts\python.exe packaging/build_release.py --no-archives
```

输出在 `Release/v版本号/MihuaAgent/`。构建器检查输出范围与是否已有个人数据，收集程序、素材、运行依赖和许可证，生成默认设置，并在独立目录运行编译版自检。不要把构建目标设为正在使用的米花程序目录。

需要原生“米花.exe”启动器及安装包时，另外安装 MinGW-w64，并确保 `gcc` / `windres` 在 PATH：

```powershell
.\.venv\Scripts\python.exe packaging/build_windows_exe.py
```

NSIS 编译器由脚本下载固定版本并检查 SHA-256。生成的安装包和免安装 ZIP 放入 `Release/`，不提交源仓库；发布前应在没有开发环境的 Windows 电脑上人工验收启动、托盘、多屏、缩放、退出、卸载与数据保留。

## 首次公开 Release

1. 更新 `westie_agent/__init__.py` 中的版本和 CHANGELOG。
2. 完成源码测试、公开文件检查及 Windows 打包验证。
3. 在 GitHub 创建与版本一致的 Tag，例如 `v0.5.27`，建立 Release 草稿。
4. 上传实际验证过的安装包、免安装 ZIP、SHA256SUMS.txt，填写更新说明与已知限制。
5. 检查附件不含个人数据与密钥，再发布草稿。

GitHub 自动提供的 “Source code (zip)” 是源码，不是可直接双击运行的应用。当前没有自动上传或自动发布工作流；Windows CI 只验证源码，不生成或公布 EXE。

## 未来自动更新

自动更新尚未接入当前版本。等仓库地址与正式 Release 约定确定后，再实现稳定通道、完整性验证、用户确认、独立安装助手、数据保留和失败回滚。不要把普通 ZIP 下载误写成已支持自动升级。
