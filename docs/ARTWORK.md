# 米花的形象与图标

角色使用 Kenney Cube Pets 的 `animal-chick`，采用 CC0。原始 OBJ、GLB、纹理和许可在 `assets/source/chick/`，另保留 `licenses/Kenney-Cube-Pets-CC0.txt`。

桌宠动作朝左；基础表六格，睡觉、思考、搜索分别有 12 帧：前四帧入场一次、后八帧循环。每格 384×384，固定画布和落点。三档显示大小为 140 / 190 / 240。

应用图标使用原模型的正面脸部，保留方块棱角、小圆眼、三角喙与头顶绒毛，配米白色圆角方形底；ICO 有 16–256 像素尺寸。桌宠朝向与应用图标朝向分别处理。

安装开发绘图依赖后，可在仓库根目录执行：

```powershell
python -m pip install -r packaging/requirements-artwork.txt
python packaging/render_chick_assets.py --source assets/source/chick/animal-chick.obj --output assets
python packaging/render_mihua_icon.py --source assets/source/chick/animal-chick.obj --output assets/ui
```

生成器直接读取模型与纹理；闭眼、翅膀、呼吸、枕垫和放大镜由几何或脚本生成，没有依赖旧小狗图像。生成预览可保留在本地，公开文档只收录已选择的图片。
