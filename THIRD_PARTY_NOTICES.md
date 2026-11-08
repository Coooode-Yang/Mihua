# 第三方内容与许可证

项目根目录的 MIT 许可证适用于米花项目代码，不覆盖下面保留原许可的第三方代码、素材和运行依赖。发布二进制时还需保留打包目录 `licenses/` 中的依赖声明。

| 内容 | 来源与许可 | 仓库中的位置或使用方式 |
| --- | --- | --- |
| Kenney Cube Pets / animal-chick | Kenney，CC0 1.0；[素材网站](https://kenney.nl/assets) | 原模型、纹理与许可在 `assets/source/chick/`；渲染图、动作和图标来自该模型 |
| Nature Skills / nature-reader 2.1.1 | 上游贡献者，Apache-2.0；[上游仓库](https://github.com/Yuan1z0825/nature-skills)，固定提交 `c1a8e7bbcf86d82a268dc4a9980d36082b31fb2f` | `assets/skills/` 中保留原指导文档、许可、来源说明与哈希清单 |
| validate_reader_math | 同一 Nature Skills 提交，Apache-2.0 | `westie_agent/reader_math.py` 保留原来源声明与原实现 |
| PySide6 / Qt for Python 6.8.3 | Qt 的 LGPL / GPL / 商业许可及其第三方许可；[官方许可说明](https://doc.qt.io/qtforpython-6/licenses.html) | 开发环境安装，二进制发布动态链接并携带对应许可；不要把 Qt 本身重新标为 MIT |
| Pydantic AI slim 2.63.0 | MIT；[上游仓库](https://github.com/pydantic/pydantic-ai) | 用户环境安装的模型编排依赖 |
| OpenAI Python SDK | Apache-2.0；[上游仓库](https://github.com/openai/openai-python) | 用户环境安装的兼容 API 客户端；不附带服务商凭据 |
| Pillow 13.0.0 | MIT-CMU（该版本元数据；保留 Pillow / PIL 的原声明）；[上游仓库](https://github.com/python-pillow/Pillow) | 图片处理与素材生成 |
| pdfplumber / pdfminer.six | MIT；[pdfplumber](https://github.com/jsvine/pdfplumber)、[pdfminer.six](https://github.com/pdfminer/pdfminer.six) | 本地 PDF 文本与几何提取 |
| pypdfium2 / PDFium | Apache-2.0 / BSD 及其捆绑依赖许可；[上游仓库](https://github.com/pypdfium2-team/pypdfium2) | 本地 PDF 页面渲染；二进制发布保留其完整许可 |
| Filee 2.2.0 | MIT，© 2026 Myeonggu Kim 与贡献者；[上游版本](https://github.com/KnifeLemon/Filee/releases/tag/v2.2.0) | 源仓库不包含二进制；开发者运行准备脚本后安装到被忽略的 `runtime/`，发布时保留引擎声明 |
| PyInstaller | GPL 与其分发例外；[上游许可](https://pyinstaller.org/en/stable/license.html) | 仅为构建工具 |
| NSIS | NSIS 及附属许可；[官方仓库](https://github.com/kichik/nsis) | 安装包构建工具，下载固定版本并验证 SHA-256 |

`Tools` 是本项目沿用的本地工具子项目，原 `pyproject.toml` 已声明 MIT；其作者与历史边界保留在该目录。

测试图片、PDF、DOCX 与 PPTX 为合成样例，不包含用户真实文献。角色生成代码中的几何动作、放大镜和枕垫由本项目创建；原模型及其许可保持不变。

未在源仓库中分发 Python、Microsoft VC runtime、模型权重或 Filee 可执行文件。若自行制作应用发布包，应依照实际捆绑内容补全声明，保留安装依赖自带的 LICENSE、NOTICE 与 COPYING。依赖及上游许可可能有变化，发布时以对应版本中的原文件为准。
