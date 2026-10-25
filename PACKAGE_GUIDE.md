# 技能包使用说明

版本：0.3.4

## 安装与调用

解压后保持 `stable-sprite-animation/` 文件夹完整。支持 `SKILL.md` 的 agent 可把它放进自己的技能目录；Codex 的默认用户技能目录为 `~/.codex/skills/`。不要把 ZIP 当作已安装技能；若当前会话未发现新技能，开新会话再调用，或明确提供 `SKILL.md` 路径。

不支持自动发现技能的 agent，也可以直接读取本包 `SKILL.md`，然后按其流程调用 Python 脚本。

示例请求：

> 使用 stable-sprite-animation。我上传的角色走路，12 帧。保留原腿型，左右脚自然交替，输出透明 PNG 序列和 GIF。

用户只需图片、动作、帧数。agent 负责选择部件与轨迹，或生图区域与注册锚点，以及动作节奏和循环方式。

本版补充短腿跑步与提包手臂的制作参考：前后换腿、短暂腾空、屈肘摆臂、手与包同步移动，以及局部修改时冻结已认可序列。见 `references/cutout-run.md` 和 `references/carrying-arm.md`。跑步由 agent 按角色制作局部渲染脚本，参考 JSON 不适用于现有走路 CLI。原地小跳见 `references/cutout-jump.md`，短步走路见 `references/cutout-walk.md`，整组生图路径继续保留。

0.3.2 增加 `references/three-quarter-walk.md`：适用于斜侧露膝人形角色，包含固定近远腿、受控膝弯、固定鞋视角素材和可见摆臂的制作经验及 12 帧参数。需要按角色写局部脚本；未新增 CLI 模式。案例已获用户认可，但浮动感仍是待改进项。

输出 PNG 带透明通道；GIF 默认使用灰色底作播放预览。需要透明 GIF 时须另行处理其有限色透明格式，当前脚本不声称提供该功能。

0.3.3 新增 `references/integrity-and-secondary-motion.md`：拆层后的完整底层、开放轮廓缺口与封闭洞检查、GIF 解码核查，以及固定发根的整束摆动。每次改图后需重新审阅最终全部帧；这些是按角色实施的制作与验收方法，未新增 CLI 自动修复模式。

0.3.4 新增 `references/native-grid-redraw.md`：关键姿势生图参考、原生网格逐帧重画、连贯下颌与像素尺度检查。Aseprite 是可选制作工具；未新增自动张嘴命令。案例动作结构基本认可，粗像素修正尚待验证。

## 环境

- Python 3.10 或更新版本。
- 安装 `requirements.txt` 的依赖；建议独立虚拟环境：

  ```text
  python -m venv .venv
  <venv-python> -m pip install -r requirements.txt
  ```

- 完整原图部件的短步走路和原地小跳仅需上述 Python 环境。需要新造型或缺失部件时，agent 还需参考图生图/编辑工具。本包不自带模型或付费凭据，生成依照所在环境的授权与费用约定。

## 验证与反馈

```text
python -m unittest discover -s tests -v
```

报告问题时保留本地运行目录。部件动画提供配置、`motion.json`、原图、`validation.json` 和问题帧/GIF；生图路径另提供提示词、生成图集与 `run.json`。无需提供任何 API 密钥。

固定像素一致取决于正确的部件/区域配置；有意的身体起伏须在局部坐标验证。动作质量还取决于角色结构、素材和视觉检查；本包不会把“公式循环”或“首尾相同”自动等同于“自然”。
