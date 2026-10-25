# Stable Sprite Animation

[![Tests](https://github.com/charolettebradford1361985svo-bit/stable-sprite-animation/actions/workflows/tests.yml/badge.svg)](https://github.com/charolettebradford1361985svo-bit/stable-sprite-animation/actions/workflows/tests.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

把角色参考图、动作提示词和帧数，变成透明 PNG 序列和 GIF 的 **Codex / AI agent 技能与 Python 工具**。重点是保留角色部件、控制动作节奏和检查循环衔接。

An agent skill and local Python tools for reference-preserving sprite animation: intact-limb walks, small jumps, compact run recipes, and alignment of generated sequences.

## 支持的流程

先读 [对象分类](references/object-routing.md)，再按需读取匹配的制作参考；本版暂不附图片示例。角色与物品／环境物分开选择制作方法；分类是参考入口，不是固定动画模板。

| 流程 | 做法 | 适用范围 |
|---|---|---|
| 原腿型走路 | 整条原裤腿和鞋小幅刚性运动，左右错开半周期 | 朝向不变、双腿可分离的小步走路 |
| 斜侧人形走路参考 | 固定近远腿、脚轨迹、受控膝弯、鞋视角与前后摆臂 | 按角色制作局部脚本；不是走路 CLI 的新模式 |
| 原腿型跳跃 | 整腿整数平移、浅蹲蓄力、抛物线腾空、落地缓冲 | 原地小跳，衣身与腿根有足够重叠 |
| 短腿跑步参考 | 原脚块平移、前后换腿、短暂腾空、屈肘和提包摆臂 | 按角色编写局部渲染脚本；不是现有走路 CLI 的模式 |
| 局部动作重画参考 | 生图提供关键姿势，在原生网格重画眼睑、下颌等局部 | 小尺寸角色张嘴、打哈欠；不是自动张嘴 CLI |
| 生图序列整理 | 参考图生图后，注册、锁定固定区域并检查循环 | 张嘴、喷嚏、新姿势或其他需要补画的内容 |

agent 负责判断方法、选择部件裁框与锚点、安排逐帧参数。前两条流程使用本地 Pillow、NumPy、OpenCV；需要补画时使用所在 agent 环境提供的生图能力。本仓库不含模型权重、API 凭据或付费服务。

## 安装与使用

从 [Releases](https://github.com/charolettebradford1361985svo-bit/stable-sprite-animation/releases) 下载技能 ZIP，把其中完整的 `stable-sprite-animation/` 文件夹放进 Codex 的用户技能目录：

- Windows：`%USERPROFILE%\.codex\skills\`
- macOS / Linux：`~/.codex/skills/`

也可以克隆本仓库到该目录下的 `stable-sprite-animation` 文件夹。已有同名技能时先保留原版本。其他支持 `SKILL.md` 的 agent 可以直接读取 [技能入口](SKILL.md)。

使用 Python 3.10+，在虚拟环境中安装依赖：

```text
python -m pip install -r requirements.txt
```

在新对话中上传角色图片，然后说：

> 使用 stable-sprite-animation。让这个角色原地跳跃，12 帧，保持原腿型，起跳和落地自然，给我透明 PNG 序列和 GIF。

agent 会选择配置并执行脚本。对于已经准备好的配置，可以直接运行：

```text
python scripts/cutout_jump.py --image character.png --config jump.json --out output/jump-01
python scripts/cutout_walk.py --image character.png --config walk.json --out output/walk-01
```

输出目录应为新目录或空目录。输出包括 `frames/`、`preview.gif`、图集、配置、运动记录、验证报告和 `result.zip`。PNG 保持透明，GIF 默认灰底。完整安装与使用说明见 [PACKAGE_GUIDE.md](PACKAGE_GUIDE.md)。

## 参数与质量边界

先读 [步态表现选择](references/gait-readability.md)：长腿通常需要清楚的前后换腿，短粗腿可以弱化交叉，靠短步和承重节奏表达；同时考虑视角、遮挡与实际显示尺寸，不改变原腿型来套统一动作。

- [走路配置](references/cutout-walk.md) · [跳跃配置](references/cutout-jump.md) · [短腿跑步参考](references/cutout-run.md) · [提包手臂](references/carrying-arm.md) · [生图序列流程](references/generated-sequence.md)。按需要读取对应文档即可。
- [斜侧角色走路](references/three-quarter-walk.md)：记录已认可的 12 帧设计、手脚共用节奏、近臂前景遮挡及鞋视角修正；保留“仍有浮动感”的质量边界，不附第三方角色图片。
- 示例中的鳄鱼裁框对应一张特定的 200×200 原图。仓库未包含该私人角色素材；换图后需要重新确定裁框，不能直接套坐标。
- 紧凑角色跑步参数对应另一张 32×42 原图，是制作参考而非 CLI 配置。样例坐标、脚宽和 12 帧参数不可作为所有角色的固定规则。
- 朝向改变、深蹲、明显屈膝、缺失肢体或新可见内容需要额外素材。当前部件脚本不会自动补出这些轮廓。
- 固定部位可以随身体整体移动，验证在身体局部坐标中进行。“原像素保留”不意味着整个角色永远停在画布的同一位置。
- 合成站姿仍需检查胯部、袖口和半透明边缘；部件保形、循环公式和首尾一致都不能单独证明动作自然。
- 跳跃样例有 12 张 PNG、11 个独特画面，首末站稳姿势相同。GIF 还可能合并相邻相同帧，报告会区分这些数量并保留时长。

身体露底、局部像素缺失或头发摆动，参阅 [身体补全与逐帧检查](references/integrity-and-secondary-motion.md)：覆盖掩码与封闭洞检测互补，检查最终 GIF，并保留已认可动作。不是自动毛发模拟或通用补全模型。

小尺寸面部动作参阅 [动作参考与原生网格重画](references/native-grid-redraw.md)：保持连贯下颌和原图像素尺度，避免缩放嘴部贴片。本案例动作结构基本认可，粗像素修正尚待实际验证。

动画默认采用 [独立审阅与逐版修订](references/independent-review.md)：主 agent 制作、子 agent 只读审阅，最多三轮修订（V01 初版至 V04），每版保留并一起交付。无委派能力时明确降级自审，未播放不能声称播放验收；这是 agent 工作流，不是新增 CLI 自动修复功能。

## 开发与维护

```text
python -X utf8 -m unittest discover -s tests -v
```

当前有 30 项测试，使用合成 RGBA 图，检查部件像素、左右脚支撑、腾空轨迹、身体固定区域、循环与导出时长。测试不依赖私人图片或联网生图。GitHub Actions 在提交和 PR 时执行测试。

发现问题请提交 [Issue](https://github.com/charolettebradford1361985svo-bit/stable-sprite-animation/issues/new/choose)，附可公开使用的最小复现素材、配置和问题帧。贡献流程见 [CONTRIBUTING.md](CONTRIBUTING.md)，版本记录见 [CHANGELOG.md](CHANGELOG.md)。

## 许可证

代码和仓库文档采用 [MIT](LICENSE)。输入图片、第三方素材及外部模型输出的权利由各自来源决定；软件许可证不会转让这些素材的权利。
