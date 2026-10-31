const FENCE = "```";

/** A-leggo's first note is an actual math-learning note, not a framework demo. */
export const CO_WRITER_SAMPLE_TEMPLATE = `# A-leggo 学习笔记

> 用一页纸记录题眼、关键步骤、错误证据与下一次复习动作。

## 题目

设函数 \\(f(x)=x^2\\)，讨论它在 \\(x=1\\) 处的导数。

## 我的过程

1. 写出差商：\\(\\frac{f(1+h)-f(1)}{h}\\)。
2. 化简并令 \\(h\\to0\\)，得到导数为 \\(2\\)。

## 证据与复习

- **题眼**：先回到导数定义，再化简差商。
- **第一处错误**：如果忘记除以 \\(h\\)，结果会失去导数含义。
- **最小检查题**：用定义求 \\(f(x)=x^3\\) 在 \\(x=2\\) 处的导数。
- **下次第一反应**：看到“由定义求导”，先写差商，不直接套公式。

## 可引用资料

在这里插入教材页码、讲义段落或自己的推导。A-leggo 会保留来源位置，方便之后复习。

${FENCE}text
knowledge_point: 导数定义
evidence: 主动回忆 + 基础题
confidence: 待再次作答确认
${FENCE}
`;
