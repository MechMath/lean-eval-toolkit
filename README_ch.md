# Lean Eval Toolkit

[English](README.md)

Lean Eval Toolkit 用于通过远程 API 评测语言模型解决 Lean 4 数学题的能力。工具将题目发送到
OpenAI-compatible chat-completions 端点，提取模型返回的完整 Lean 文件，再使用
[AXLE（Axiom Lean Engine）](https://github.com/AxiomMath/axiom-lean-engine)验证候选证明。

启动或托管待测模型不属于本项目范围。模型可以是由 vLLM 启动的本地模型，也可以是
DeepSeek、OpenAI 或 OpenRouter 等兼容商业 API。

## 功能

- 仓库内包含固定版本的 miniF2F 与 PutnamBench JSONL 快照；
- Lean 环境随每条题目保存，不依赖 `.env` 中唯一的全局版本；
- 支持导入上游数据仓库、JSONL、单个 `.lean` 文件或 Lean 文件目录；
- 支持并发、多次尝试、split/题目 ID 筛选和经验 pass@k；
- 使用 AXLE `verify_proof` 严格判分，不允许任何 `sorry`；
- 流式保存模型输出、token 用量、错误信息与验证详情。

## 内置数据集

| 数据集 | 题数 | 划分 | Lean 环境 |
| --- | ---: | --- | --- |
| miniF2F | 498 | validation 256（未验证），test 242 | test：`lean-4.30.0`；validation：`lean-4.27.0` |
| PutnamBench | 672 | test | `lean-4.27.0` |

上游 commit、原作者、许可证、提取规则和 SHA-256 记录在
[`data/README.md`](data/README.md)及各数据集的 `PROVENANCE.md` 中。快照只包含带 `sorry`
占位符的公开题面，不包含模型答案或参考证明。

## 安装

项目需要 Python 3.12 和 [uv](https://docs.astral.sh/uv/)：

```bash
uv sync --all-groups
cp .env.example .env
```

## 配置

本地 vLLM-compatible 端点：

```dotenv
MODEL_BASE_URL=http://localhost:8000/v1
MODEL_API_KEY=
MODEL_NAME=Qwen/Qwen3-8B
```

DeepSeek：

```dotenv
MODEL_BASE_URL=https://api.deepseek.com/v1
MODEL_API_KEY=replace-me
MODEL_NAME=deepseek-chat
```

提供商特有参数和请求头可通过 `MODEL_EXTRA_BODY`、`MODEL_EXTRA_HEADERS` 以 JSON 提供。
AXLE 单独配置：

```dotenv
AXLE_API_URL=https://axle.axiommath.ai
AXLE_API_KEY=
AXLE_ENVIRONMENT=
AXLE_TIMEOUT_SECONDS=900
```

`AXLE_ENVIRONMENT` 仅用于没有版本信息的自定义题目。内置记录均有独立 `environment`；从仓库
导入时则读取 `lean-toolchain`。`.env`、`results/` 和临时上游 checkout 均被 Git 忽略。

## 运行评测

先试跑一道 miniF2F：

```bash
uv run lean-eval run data/minif2f/problems.jsonl \
  --name minif2f --split test --limit 1
```

运行 miniF2F 时请使用 `--split test`。其中 242 道 test 题已在 AXLE `lean-4.30.0` 环境下
使用 `import Mathlib` 验证；validation split 仍明确标记为未验证。

以每题四次尝试运行 PutnamBench：

```bash
uv run lean-eval run data/putnambench/problems.jsonl \
  --name putnambench --attempts 4 --concurrency 4
```

可以重复使用 `--id` 精确选择题目：

```bash
uv run lean-eval run data/putnambench/problems.jsonl \
  --name putnambench --id putnam_1968_a1
```

兼容性实验中可以强制覆盖 AXLE Lean 环境：

```bash
uv run lean-eval run data/minif2f/problems.jsonl \
  --name minif2f --environment lean-4.28.0
```

优先级为 `--environment` > 数据记录中的 `environment` > `AXLE_ENVIRONMENT` 回退值。覆盖仅对
本次运行生效，不修改固定版本的 JSONL，并会记录在 `run.json` 中。

模型请求与 SFT 数据使用相同的对话格式：仅包含一条 `user` 消息，以
`Complete the following Lean 4 code:` 开头，随后是 `lean4` 代码块和详细证明计划要求。模型
数据若带有 informal 表述（例如 miniF2F），会以 Lean `--` 行注释的形式插入该代码块开头。
模型回复可以包含所要求的推理过程，但必须以显式标记为 `lean4` 的代码块结尾；工具会提取
最后一个这样的代码块并将其发送给 AXLE 验证。

源文件开头的 copyright、release、license 和 author 声明会作为来源信息保存在
`metadata.source_header`，并从 `formal_statement` 中移除，不会发送给待测模型。

每次运行都会生成：

```text
results/<UTC 时间>-<模型名>/
├── run.json
├── results.jsonl
└── summary.json
```

每次尝试完成后会立即追加结果，因此中断不会丢失已经完成的记录。API key 不会写入产物。

## 添加或更新数据集

查看支持的布局并导入自定义数据：

```bash
uv run lean-eval datasets list
uv run lean-eval datasets import path/to/tasks \
  --name custom --output data/custom/problems.jsonl
```

规范化 JSONL 必须包含 `id`，以及至少含一个 `sorry` 的 `formal_statement`。建议明确指定 AXLE
环境：

```json
{"id":"demo","dataset":"custom","split":"dev","environment":"lean-4.27.0","formal_statement":"import Mathlib\ntheorem demo : True := by\n  sorry\n","metadata":{}}
```

更新内置快照时，还必须在对应 `PROVENANCE.md` 中更新上游 commit、题数、提取日期、转换说明
和 SHA-256。

## 验证规则

未经修改的原题作为 AXLE `verify_proof` 的 `formal_statement`，模型候选作为 `content`，并设置
`permitted_sorries=[]`。只有 AXLE 返回 `okay = true` 且 `failed_declarations` 为空时才通过。
候选 Lean 代码只发送给模型 API 与 AXLE，不会在本机执行。

## 开发与验证记录

```bash
uv run ruff check .
uv run pytest
```

当前测试涵盖单元逻辑、CLI、API 契约、数据完整性和快照摘要。真实冒烟测试中，已配置的
DeepSeek 端点成功生成一道 miniF2F 证明，并在数据集固定的 `lean-4.27.0` 环境下通过 AXLE。
PutnamBench 的环境选择也已通过 AXLE 单独确认，未填充的 `sorry` 会被严格拒绝。
