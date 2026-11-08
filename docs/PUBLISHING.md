# 首次上传 GitHub

此目录是一份独立、清理后的源码快照。不要上传个人运行目录，也不要复用包含旧素材的历史 `.git` 目录。

## 仓库信息

- 建议名称：`Mihua`
- 简介：`米花：Windows 小鸡桌宠与学习、研究、日常助手。`
- 建议 Topics：`desktop-pet`、`windows`、`pyside6`、`pomodoro`、`zotero`、`study-assistant`。
- 代码许可证：MIT；第三方资源保持原许可证。

## 上传

在 GitHub 创建一个空仓库。已有本地 README 和 LICENSE 时，无需再勾选初始化文件。在本源码目录运行：

```powershell
python packaging/audit_public.py
git status --short
git add .
git diff --cached --stat
git commit -m "Prepare Mihua open source release"
git remote add origin <你创建的GitHub仓库HTTPS地址>
git push -u origin main
```

如果本地目录尚未初始化 Git，先执行 `git init -b main`。提交作者使用你自己的 Git 配置；本准备步骤不替你填写姓名和邮箱。

提交前确认 LICENSE 中的贡献者标注符合你的署名偏好；MIT 适用于本项目代码，不能替代第三方的 Apache、CC0 或 Qt 许可。不要将本地公开检查报告误认为完整法律审查。

## 仓库设置

- 检查 Actions 首次 Windows CI 结果。
- 为 `main` 设置分支保护和合并检查，按需启用 Dependabot、Secret scanning 与私密漏洞报告。
- README 当前使用相对链接，无需填写仓库 URL 即可显示图片和文档。
- 准备二进制 Release 时按 [RELEASING.md](RELEASING.md) 操作，先验证再上传。

仓库已发布在 https://github.com/Coooode-Yang/Mihua。后续开发在此 Git 工作目录提交；正式 Release 与自动更新按 [UPDATES.md](UPDATES.md) 操作。

参考：[创建仓库](https://docs.github.com/en/repositories/creating-and-managing-repositories/creating-a-new-repository)、[GitHub Releases](https://docs.github.com/en/repositories/releasing-projects-on-github/about-releases)。
