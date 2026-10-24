# 技能包使用说明

版本：0.3.0

## 安装与调用

解压后保持 `stable-sprite-animation/` 文件夹完整。支持 `SKILL.md` 的 agent 可把它放进自己的技能目录；Codex 的默认用户技能目录为 `~/.codex/skills/`。不要把 ZIP 当作已安装技能；若当前会话未发现新技能，开新会话再调用，或明确提供 `SKILL.md` 路径。

不支持自动发现技能的 agent，也可以直接读取本包 `SKILL.md`，然后按其流程调用 Python 脚本。

示例请求：

> 使用 stable-sprite-animation。我上传的角色走路，12 帧。保留原腿型，左右脚自然交替，输出透明 PNG 序列和 GIF。

用户只需图片、动作、帧数。agent 负责选择部件与轨迹，或生图区域与注册锚点，以及动作节奏和循环方式。

本版增加保留原腿型的原地小跳：整腿整数平移，用身体下沉表现蓄力和落地缓冲，腾空按播放时间采样抛物线。适用条件和认可的 12 帧示例见 `references/cutout-jump.md`。短步走路见 `references/cutout-walk.md`，张嘴、喷嚏等整组生图路径继续保留。用户可以请求“保持原腿型，原地跳跃 12 帧，起跳和落地自然，直接展示 GIF”。

输出 PNG 带透明通道；GIF 默认使用灰色底作播放预览。需要透明 GIF 时须另行处理其有限色透明格式，当前脚本不声称提供该功能。

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
