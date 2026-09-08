# Lean Eval Toolkit

通过远程 API 评测语言模型解决 Lean 4 数学题的能力。工具负责读取题目、请求模型、提取完整
Lean 文件，并使用 [AXLE（Axiom Lean Engine）](https://github.com/AxiomMath/axiom-lean-engine)
逐个验证候选证明。启动或托管待测模型不属于本项目范围。

目前支持：

- [miniF2F Lean 4](https://github.com/google-deepmind/miniF2F)：自动拆分上游的
  `MiniF2F/Valid.lean` 和 `MiniF2F/Test.lean`；
- [PutnamBench](https://github.com/trishullab/PutnamBench)：自动读取 `lean4/src/*.lean`；
- 自定义 JSONL、单个 `.lean` 文件或包含 `.lean` 文件的目录。

模型接口采用 OpenAI-compatible `chat/completions` 协议，可连接 vLLM、DeepSeek、OpenAI、
OpenRouter 等兼容端点。原生 Anthropic Messages 等非兼容协议暂未直接支持，可在前方部署兼容网关。

## 安装

需要 Python 3.12 与 [uv](https://docs.astral.sh/uv/)：

```bash
uv sync --all-groups
cp .env.example .env
```

所有凭据和运行参数均由 `.env` 管理；`.env`、`data/` 和 `results/` 不会提交到 Git。

## 配置模型

本地 vLLM（模型服务需由使用者另行启动）：

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

提供商特有参数可以用 JSON 注入，而无需修改代码：

```dotenv
MODEL_EXTRA_BODY={"top_p":0.95}
MODEL_EXTRA_HEADERS={"X-Custom-Header":"value"}
```

AXLE 默认使用与当前 miniF2F 和 PutnamBench 上游一致的 Lean 4.27 环境：

```dotenv
AXLE_API_URL=https://axle.axiommath.ai
AXLE_API_KEY=
AXLE_ENVIRONMENT=lean-4.27.0
AXLE_TIMEOUT_SECONDS=900
```

AXLE 官方名称与环境变量拼写都是 `AXLE`；PyPI 发行包叫 `axiom-axle`，Python 导入名为
`axle`。API key 在允许匿名调用的服务上可以留空。

## 准备数据集

查看支持的上游布局：

```bash
uv run lean-eval datasets list
```

miniF2F：

```bash
git clone https://github.com/google-deepmind/miniF2F data/miniF2F
uv run lean-eval datasets import data/miniF2F \
  --name minif2f --output data/minif2f.jsonl
```

PutnamBench：

```bash
git clone https://github.com/trishullab/PutnamBench data/PutnamBench
uv run lean-eval datasets import data/PutnamBench \
  --name putnambench --output data/putnambench.jsonl
```

也可以不预先生成 JSONL，直接把仓库目录传给 `lean-eval run`。规范化 JSONL 每行格式如下：

```json
{"id":"demo","dataset":"manual","split":"dev","formal_statement":"import Mathlib\ntheorem demo : True := by\n  sorry\n","informal_statement":"证明 True。","metadata":{}}
```

必填字段是 `id` 与包含至少一个 `sorry` 的 `formal_statement`。加载器也接受常见别名，例如
`name`、`statement`、`code`、`nl_statement`；未识别字段会保存在 `metadata`。对于目录，每个含
`sorry` 的 `.lean` 文件会成为一个任务，因此可以直接手动添加题目：

```lean
import Mathlib

/-- A manually added problem. -/
theorem my_problem (n : ℕ) : n = n := by
  sorry
```

## 运行评测

先用一题确认模型和 AXLE 配置：

```bash
uv run lean-eval run data/minif2f.jsonl --name minif2f --split test --limit 1
```

再运行完整评测：

```bash
uv run lean-eval run data/minif2f.jsonl --name minif2f --split test
uv run lean-eval run data/putnambench.jsonl --name putnambench \
  --attempts 4 --concurrency 4
```

CLI 参数优先于 `.env` 中的 `EVAL_ATTEMPTS`、`EVAL_CONCURRENCY` 和 `EVAL_RESULTS_DIR`。
`--attempts k` 会为每题独立生成 k 个候选，并报告经验 pass@k（至少一个候选通过的题目比例）。

每次运行产生独立目录：

```text
results/<UTC时间>-<模型名>/
├── run.json       # 非敏感运行配置与数据来源
├── results.jsonl  # 每次尝试的原始回答、Lean 代码、用量、错误与 AXLE 详情
└── summary.json   # 总题数、成功尝试、已解题数和 pass@k
```

结果会在每次尝试完成时立即追加，因此中断后已经完成的记录仍会保留。API key 不会写入产物。

## 判分规则

模型被要求返回完整 Lean 源文件并替换所有 `sorry`。每个候选连同未经改动的原题分别作为
AXLE `verify_proof` 的 `content` 与 `formal_statement` 提交；项目显式设置
`permitted_sorries=[]`。只有 AXLE 同时返回 `okay = true` 且 `failed_declarations` 为空时才通过。
候选代码只发送给模型 API 与 AXLE，不会在本机执行。

## 开发

```bash
uv run ruff check .
uv run pytest
```
