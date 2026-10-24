# 整组生图提示词与验收

先查看种子图与模板，不用固定的鳄鱼坐标套其他角色。用户输入的动作和帧数是约束；下面的阶段只是提示词组织方法。

## 提示词组成

1. 说明各参考图的职责：批准角色决定身份，画布决定排列，额外动作图只说明运动结构。
2. 指定 N 帧、C 列 R 行、阅读顺序、每格尺寸和统一锚点。网格多出的格子留空。
3. 指定允许运动的部位，以及应保持不动的部位；所有帧共享朝向、比例、服装、颜色和像素颗粒。
4. 按 N 写完整动作节拍，覆盖准备、动作、恢复，避免只有中间高潮。动作不同，阶段分配也应不同。
5. 要求真实透明背景、清晰像素边缘、无文字/标签/格线/贴纸描边，特效留在本格范围内。
6. 要求关节有正确连接和遮挡。张嘴要表现下颌、口腔、牙齿，而不是在脸上画黑缝。只在动作需要时添加这些要求，不把鳄鱼解剖强加给其他角色。

可修改的骨架：

```text
Edit the supplied reference canvas into one coherent N-frame spritesheet.
Reference 1 is the approved character identity. Reference 2 defines the grid.
Layout: C columns, R rows, reading order; extra cells remain transparent.
Keep the same character scale, facing direction and root anchor in every cell.
Fixed parts: <agent identifies these from the requested action>.
Moving parts and effects: <agent identifies these, including swept regions>.
Action: <user's exact intent>.
Frames 1..N: <explicit chronological beats>.
Use a genuinely transparent background and crisp pixel clusters.
No captions, grid lines, scenery, unintended duplicates or clipped effects.
For a rest loop: recover to the same natural generated idle pose at the end.
```

## 示例：12 帧喷嚏

1 静止；2 鼻子发痒；3 半眯眼准备；4 吸气；5 闭眼张嘴；6 即将喷出；7 喷嚏峰值；8 跟随与云扩散；9 嘴开始闭合、云消散；10 恢复；11 自然静止；12 保持静止。

这个例子不要求所有动作都固定身体。甩手需要手臂运动，走路需要腿部运动，点头需要头部运动。只有用户希望固定的部分才应被硬锁定。

## 视觉验收与重试

- 看完整图集，再看循环。特别看最后几帧接第一、第二帧，不只比较首末两张。
- 确认动作语义完整：静止不动也能拿到低像素差，因此低差异本身不是动作合格证。
- 检查颜色/头部形状闪变、关节脱节、嘴部黑洞、特效像实物或能量弹、边缘碎点、越格、遮罩切掉部件等问题。
- 生成部分与固定部分出现接缝时，先检查注册和运动区域是否覆盖完整连接处；不要单纯扩大到全图来让错误消失。
- 一次只修最明确的问题。静态位置问题优先修注册/合成；结构或动作不对需要有针对性地编辑或重新生成该组。
- 使用外部批准图当作精确循环终点，需要足够的收尾帧逐步回到它并通过过渡检查。默认使用生成组自身的自然静止帧，避免硬切。
- 若无法验证跨帧质量，交付为待审样片，附具体限制；不要把重影叠化或轻微随机位移伪装成新画面。
