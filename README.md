# associative-learning-memory
ALM explores how AI can connect new papers and notes to problems you set aside, point to the evidence, and suggest a small test to see if an idea is worth revisiting.

ALM（联想学习记忆）把你搁置的问题记成"卡在哪个条件上"。每当有新论文或笔记进来，它判断这份资料是否改变了那个条件；如果是，就给出一条带原文依据和验证办法的提醒。

现在是 v0.1：一个 SQLite 文件、一个模型、一个命令行。没有小模型、推荐排序和微调，这些等基本闭环被证明有用后再加。

## 安装

需要 Python 3.10 以上，没有第三方依赖。

```bash
git clone https://github.com/seanz298/associative-learning-memory
cd associative-learning-memory
pip install -e .
```

然后按 `.env.example` 设置环境变量。默认走 OpenAI 兼容接口（DeepSeek、OpenAI 等），也支持 Anthropic。

## 用法

```bash
# 1. 录入卡点。写不出"卡在哪个条件上"的目标先不要录。
alm add-blocker --project pcb --goal "自动布线" \
  --blocker "生成的候选路径会穿过禁布区" \
  --tried "生成模型直接出路径；约 15% 的样例违规" \
  --evidence "tests/routing/failed_cases.json"

# 2. 录入资料。它会对照所有未解决的方案层卡点。
alm ingest paper.txt --title "Trajectory repair" --ref https://example.org/paper

# 3. 看提醒，试过之后记录结果。
alm matches --status new
alm mark 3 useful        # 或 tried / rejected

alm blockers             # 未解决的卡点
alm resolve 1            # 卡点已解决
alm stats                # 提醒数、各状态数量、累计花费
```

录入卡点时也会反过来对照已有资料（`--no-scan` 关闭）。

## 一条提醒要过的门槛

提醒必须同时有四项，缺一项就丢弃：

| 字段 | 内容 |
|---|---|
| 主张 | 方法 A 可能在条件 B 下解除障碍 C |
| 条件 | 何时适用，迁移后还要检查什么 |
| 依据 | 资料原文中的一段话 |
| 验证 | 一天内能做完、复用失败样例的测试 |

依据由代码核对：那段话必须真的出现在资料原文里，否则整条丢弃。提醒始终是待验证的假设，不是结论。

## 卡点分两层

- `method`（方案层）：方法本身不成立。只有这一层参与对照。
- `implementation`（实现层）：方法可行，代码或环境有问题。只存档。

## 怎么判断它有没有用

跑两周后看 `alm stats`：`useful` 和 `tried` 占全部提醒的比例，以及其中有多少是你自己没想到的。如果一条都没有，先检查卡点是否写得太泛，再考虑加组件。

## 和 Orkestr 的关系

提醒最终是给干活的模型看的。`alm matches --json` 按 [Orkestr](https://github.com/seanz298/Orkestr) 记忆接口里 `Hint` 的字段输出，适配器还没写，等 Orkestr 的流水线稳定后再接。

## 开发

```bash
pip install -e ".[dev]"
pytest
```

测试用假模型，不联网、不花钱。

## 目录

```
alm/store.py   四张表：卡点、资料、提醒、调用日志
alm/core.py    抽象卡点、提炼机制、判断是否相关，以及门槛
alm/llm.py     模型调用（标准库）
alm/cli.py     命令行
```
