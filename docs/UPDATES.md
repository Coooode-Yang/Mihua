# 米花自动更新

米花 0.5.28 起支持 Windows x64 打包版在线更新，固定更新源为 [Coooode-Yang/Mihua](https://github.com/Coooode-Yang/Mihua/releases)。源码运行只检查版本，不替换 Git 工作目录。

## 使用

每次启动约 8 秒后在后台检查正式版，继续运行时每 6 小时检查一次。默认自动下载新版；下载和校验完成后，空闲时询问是否退出安装，确认后自动重启。可以选择稍后安装，并从小鸡右键菜单的“软件更新”继续。

没有新版或 GitHub 暂时无法连接时，米花照常使用，不弹后台错误窗口。“软件更新”也提供手动检查、取消下载和两个偏好选项：启动自动检查、自动下载。关闭自动检查后仍可手动检查。更新与 AI 模型设置独立，不需要 API Key 或 GitHub Token。

米花正在聊天、生成解读、整理日志、转换文件或进行专注计时时，不会自动弹出安装确认。手动安装也需要等待任务完成并暂停专注。更新会保存整个 `data/`，包括聊天、待办、专注、笔记、API Key 的 DPAPI 密文及个人模型文件。

旧版 0.5.27 及更早没有更新模块，需要先手动安装一次 0.5.28；此后可以通过米花获取新版。本机开发安装可由维护者迁移到新构建，无需清除数据。

## 下载与校验

程序请求 GitHub 的 `/releases/latest/download/latest.json`，使用随程序分发的 Ed25519 公钥验证签名，再根据已签名的版本、大小和 SHA-256 下载对应版本的 ZIP。只接受稳定三段版本号；只安装比当前更新的版本。GitHub 的源码 ZIP、普通 Tag、草稿和预发布版不作为应用更新。

ZIP 仅接受 `MihuaAgent/` 根目录；拒绝路径穿越、Windows 特殊文件名、链接、大小超限和重复路径。发布者的 `data/` 不会覆盖使用者数据。独立 `MihuaUpdater.exe` 会重新验证签名与压缩包，等待原进程退出后切换程序目录，再运行离屏启动检查，成功后启动米花。普通安装或检查失败会恢复旧程序与数据。

安装中断或断电时，可在程序同级的 `.MihuaAgent-update-*` 目录找到 `install-status.json`、`update-error.txt` 和旧程序 `previous/`。成功升级会留下旧程序备份，可确认新版本正常、退出米花后再按需清理；不要在更新运行时移动这些目录。遇到恢复未完成时，先备份更新目录与程序目录，按照记录确认 `data/` 所在位置，再恢复 `previous/`，不要直接删除含数据的目录。

## 维护者发布

代码中的 `update-config.json` 只包含仓库、通道和公开验证公钥，可以提交。私钥必须放在仓库之外；本机首次配置的私钥位于开源目录外的 `ReleaseKeys`。备份私钥，后续每次发布继续使用同一把，避免已安装客户端无法验证后续更新。开发者克隆本仓库不会取得官方发布权限。

1. 修改 `westie_agent/__init__.py` 的版本与 CHANGELOG，运行测试。
2. 按 [构建与发布](RELEASING.md) 构建并验证应用、独立助手及安装器。
3. 使用 `build_release.py` 的 `zip_tree` 为最终程序目录生成固定根目录的 `MihuaAgent-api-win64.zip`。不要把中文免安装 ZIP 改名冒充更新包，两者根目录不同。
4. 生成已签名的 `latest.json`：

```powershell
.\.venv\Scripts\python.exe packaging/release_updates.py prepare `
  --package Release/v0.5.28/MihuaAgent-api-win64.zip `
  --private-key <仓库外的私钥路径> `
  --output Release/v0.5.28/publish `
  --notes-file Release/v0.5.28/release-notes.txt
```

5. 在 GitHub 创建与清单一致的 `v0.5.28` Release 草稿，上传已验收安装器、免安装 ZIP、更新 ZIP、`latest.json` 和 SHA256SUMS.txt。检查附件完整后发布为正式版并标为 Latest。更新 ZIP 与清单必须来自同一次构建，不可发布后随意覆盖。

发布工具只生成本地文件，不上传。它拒绝包内的个人数据、缺失助手、版本格式错误和公私钥不匹配。签名私钥可选使用 `MIHUA_UPDATE_KEY_PASSWORD` 加密；禁止把密码或私钥提交到源码、Actions 日志或 Release。若自己 Fork 发行，生成自己的密钥，再使用 `release_updates.py configure --repository 用户/仓库 --public-key <公钥文件>` 并重新构建。官方程序不会接受改变更新源或公钥的 ZIP。

参考：[GitHub Releases API](https://docs.github.com/en/rest/releases/releases)。

## 连接故障

0.5.29 起，对短暂超时、连接中断和 GitHub 服务端错误最多尝试 3 次。DNS、证书、代理和 HTTP 限制会显示各自的具体提示。客户端沿用系统及环境配置中的代理和证书验证，不使用第三方更新镜像。

连接失败的本机记录保存在 `data/logs/update-connection.json`，仅包含时间、检查或下载阶段、错误类别和 HTTP 状态；不保存代理账号、下载临时地址或 API Key，也不上传。持续失败时可根据具体提示检查网络与系统代理。
