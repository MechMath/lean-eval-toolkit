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
| PutnamBench | 672 | test | `lean-4.30.0` |

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

非敏感默认值保存在 [`conf/default.yaml`](src/lean_eval_toolkit/conf/default.yaml)，其中包含
`model`、`axle`、`retry` 和 `evaluation` 配置，并通过下面的方式引入独立数据集注册表：

```yaml
includes:
  - datasets.yaml
```

内置数据集的说明、布局、各 split 环境和 import 模块策略保存在
[`conf/datasets.yaml`](src/lean_eval_toolkit/conf/datasets.yaml)。OmegaConf 会先合并所有引入的
YAML，再由 Pydantic 校验完整配置结构。

测试模板与模型/tokenizer 的 chat template 是两个独立层次。测试模板负责把题目字段渲染成
OpenAI-compatible `messages` 并从回复中读取 Lean；模型 chat template 则由推理服务/tokenizer
应用，负责 BOS/EOS 和角色 special token。toolkit 可以把显式配置的 `model.chat_template` 传给
模型端点，但不会用它拼接测试文本。

默认测试模板是内置的
[`lean-cot-v1.yaml`](src/lean_eval_toolkit/conf/test_templates/lean-cot-v1.yaml)：

```yaml
model:
  chat_template: null       # 可选的模型/tokenizer 模板

evaluation:
  test_template: lean-cot-v1  # 内置 ID 或 YAML 文件路径
```

v3 Plan-and-Repair 模型应选用 `evaluation.test_template: lean-plan-repair-v3`（附带的 WuProver
配置已选用）。初始用户消息要求先给证明计划，再给完整定理声明；题目置于 `lean4` 代码块中，
回复不重复 imports。严格回复格式依次为 `### Proof Plan` 与 `### Lean Proof`，后者包含
闭合且位于结尾的 `lean4` 代码块。宽松解析仍可作为降级路径，结果会记录解析策略及是否降级。
旧模型可继续使用 `lean-cot-v1`。

评测编译器反馈修复时，将 `evaluation.max_repair_rounds`（或 `--max-repair-rounds`）设为正数。
`evaluation.attempts` 仍表示每题独立轨迹数，每条轨迹最多生成 `1 + max_repair_rounds` 次。
普通 Lean 证明失败后，工具会把助手原始回复和包含
`Lean compiler feedback:\n\n<诊断信息>` 的 `tool` 消息追加到完整对话，再请求下一轮。
若服务端不接受没有结构化工具调用的 `tool` 消息，可设置
`evaluation.repair_feedback_role: user`（或 `--repair-feedback-role user`）。v3 模板在修复轮
要求 `### Revised Proof Plan`。证明通过立即停止；格式或服务错误终止当前轨迹，传输重试与
修复轮数相互独立。默认修复预算为零，保持单次生成行为。

WuProver 配置现以 `model.max_tokens: 8192` 起步。Stage 3 样本中最长的通过回复使用了
4477 个 completion tokens，最长的可提取回复使用了 6635 个；无法提取的回复均达到先前
30000 token 的上限。若其他配置或环境变量覆盖此值，可显式设置 `MODEL_MAX_TOKENS`。
输出反复耗尽上限时，单纯增大上限不能解决循环生成，还需核对服务端 chat template 与
thinking 模式。

WuProver 配置还通过 `model.extra_body.chat_template_kwargs.enable_thinking: false` 向每次
vLLM 聊天请求传入 no-think 开关。这是请求级 tokenizer 模板参数，不是替换整个模板的
`model.chat_template`，也不是评测测试模板。只有服务端实际加载的 chat template 支持
`enable_thinking` 时此开关才会改变渲染；固定 non-thinking 模板本身没有可切换的模式。
Qwen3-Coder-Next-Base 本来就是 non-thinking，传入此参数不会改变它的模板。
若要让其他客户端也默认 no-think，需要在启动 vLLM 时传入
`--default-chat-template-kwargs '{"enable_thinking": false}'`。

自定义测试模板通过 `fields` 将规范化 JSONL 字段映射到占位符：

```yaml
schema_version: 1
id: my-lean-format
fields:
  statement:
    source: formal_statement
    transforms: [rstrip]
  hint:
    source: metadata.hint
    required: false
    default: ""
messages:
  - role: system
    content: "Return a complete Lean proof."
  - role: user
    content: "${hint}\n${statement}"
output:
  primary:
    type: last_fenced_code
    languages: [lean4]
  fallbacks:
    - type: last_fenced_code
      languages: [lean, ""]
    - type: raw_lean
```

`formal_statement`、`informal_statement`、`id`、`environment` 等规范字段可以直接映射。
导入 JSONL 时无法识别的顶层字段会保存在 `metadata`，因此原始的 `hint` 应写成
`metadata.hint`。目前支持 `strip`、`rstrip`、`lean_comment` 和 `json` 转换。只有主解析器
失败时才会按声明顺序执行 fallback；实际采用的策略及是否发生降级会写入每条评测结果。

`.env` 只保存密钥和部署身份。使用默认本地 vLLM-compatible 端点时：

```dotenv
MODEL_NAME=Qwen/Qwen3-8B
MODEL_API_KEY=
AXLE_API_KEY=
```

部署相关的环境变量仍可覆盖 YAML。使用 DeepSeek 时：

```dotenv
MODEL_BASE_URL=https://api.deepseek.com/v1
MODEL_API_KEY=replace-me
MODEL_NAME=deepseek-chat
```

通过 `LEAN_EVAL_CONFIG=/path/to/config.yaml` 可以选择其他根 YAML；其中 `includes` 的相对路径
以该文件所在目录为基准。配置优先级依次为显式 CLI 覆盖、进程环境变量、`.env`、合并后的
YAML 默认值。

原有的 `MODEL_*`、`AXLE_*`、`RETRY_BACKOFF_SECONDS` 和 `EVAL_*` 环境变量继续作为覆盖项。
提供商特有参数和请求头通过 `MODEL_EXTRA_BODY`、`MODEL_EXTRA_HEADERS` 以 JSON 提供。
`MODEL_CHAT_TEMPLATE` 和 `EVAL_TEST_TEMPLATE` 分别选择上述两个独立的模板层。
`AXLE_ENVIRONMENT` 仅用于没有版本信息的自定义题目。`.env`、`results/` 和临时上游
checkout 均被 Git 忽略。

`MODEL_MAX_RETRIES` 和 `AXLE_MAX_RETRIES` 表示首次请求失败后最多重试的次数。模型网络错误、
HTTP 429/5xx，以及可重试的 AXLE 服务端错误会从 `RETRY_BACKOFF_SECONDS` 开始进行指数退避；
无效请求和普通 Lean 验证失败不会重试。

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

默认测试模板与 SFT 数据使用相同的对话格式：仅包含一条 `user` 消息，以
`Complete the following Lean 4 code:` 开头，随后是 `lean4` 代码块和详细证明计划要求。模型
回复首先按训练格式读取精确的 `### Complete Lean 4 Proof` section；严格解析失败后，再依次
尝试宽松 section、最后一个 `lean4`/`lean` 或无标签 fence，以及可识别的纯 Lean 文本。
fallback 只提高读取容错性，最终证明仍必须通过 AXLE。

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

`results.jsonl` 每行是一条独立尝试/轨迹。按顺序排列的 `rounds` 包含从 0 开始的 `round`、
`raw_response`、`finish_reason`、提取后的 `candidate`、`extraction_strategy`、`used_extraction_fallback`、
含 Lean 诊断的 `verification`、传给下一轮的 `feedback`、`usage`、`generation_ms`、
`verification_ms` 及错误字段。
尝试顶层仍保留候选、通过状态、用量和耗时等兼容字段；顶层耗时为各轮总和，顶层用量为最后一次
成功生成的回复。轨迹在首个通过轮停止；格式或服务错误会记录并终止轨迹。`run.json` 记录
测试模板 ID、修复预算、反馈角色、模型服务配置、chat template 和 AXLE 设置，不记录 API key。

`summary.json` 中的 `pass_at_k` 是 `final_pass_at_k` 的兼容别名。`direct_pass_at_1` 和
`direct_pass_at_k` 只看第 0 轮；`final_pass_at_1` 和 `final_pass_at_k` 看任意成功轮。
`@1` 取每题第 1 次独立尝试，`@k` 取配置的任意一次独立尝试；分母都是题目数。
汇总还包括修复前解出的题数、修复后新增解出的题数、每条轨迹进入的修复轮数均值和最大值，以及
`success_by_round`。每轮记录到达轨迹数、首次成功数；成功率、严格格式率和降级解析率均以
到达轨迹数为分母，生成失败也计入分母。逐轮记录生成/验证耗时和 prompt/completion token
总量；汇总提供所有轨迹总量和平均每轨迹耗时。`max_repair_rounds=0` 时直接与最终指标相同，
原有 pass@k 含义不变。

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

未经修改的原题作为 AXLE `verify_proof` 的 `formal_statement`，提取的候选按需补齐前导代码后
作为 `content`，并设置
`permitted_sorries=[]`。只有 AXLE 返回 `okay = true` 且 `failed_declarations` 为空时才通过。
候选 Lean 代码只发送给模型 API 与 AXLE，不会在本机执行。

候选只需包含完成后的目标声明。基准题的 `formal_statement` 提供 imports、options、命名空间和
证明空位；模型回复无需重复这些前导代码。对仅包含声明的候选，验证器会在发送给 AXLE 的
`content` 前自动补齐原题前导代码，使 `open scoped Nat` 等记法能正常解析；结果中的原始候选
保持不变。`verification.applied_preamble` 记录所补文本。若可识别的命名定理声明被改写，
`verification.candidate_statement_changed` 会标记；AXLE 拒绝后，修复反馈会提示恢复原题。
是否通过仍由 AXLE 判定，包括与原题定义等价的声明。设置 `AXLE_API_KEY`（或在 `.env` 中配置），运行
`AXLE_INTEGRATION=1 uv run pytest tests/test_axle_integration.py` 可用真实 AXLE 检查此约定。
仓库中的固定样例覆盖仅提交声明时通过，以及修改签名、保留 `sorry`、缺少 tactic 的 import、仅提交
tactic 主体时被拒绝。

## 开发与验证记录

```bash
uv run ruff check .
uv run pytest
```

当前测试涵盖单元逻辑、CLI、API 契约、数据完整性和快照摘要。真实冒烟测试中，已配置的
DeepSeek 端点成功生成一道 miniF2F 证明，并在数据集固定的 `lean-4.27.0` 环境下通过 AXLE。
PutnamBench 的环境选择也已通过 AXLE 单独确认，未填充的 `sorry` 会被严格拒绝。
