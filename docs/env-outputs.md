# Env Outputs 私有适配 / Private environment adapter

本模块只存在于环境适配分支。通用框架的主分支不携带内部环境、任务数据、
私有工具实现或服务凭证。适配器在运行时读取用户明确指定的 sandbox 环境产物，
转换为通用 `Case`，复用 Labeler、Base、四类质量检查及 subject → hop → task 聚合。

The adapter loads external task and harness artifacts at runtime. It does not bundle
private environment code or automatically train an agent.

## 已实现的接口

```python
from ree.env_outputs import load_task_context, normalize_trajectory, build_task_pack
```

- `load_task_context(task_path, environment_path) -> TaskContext`：读取单个任务的说明、
  goal、checklist、底线项与条件拆解；读取工具声明、system prompt、skill 资料和压缩配置。
  `current_datetime` 优先于 `current_date`，保留时刻。加分项只作为可选条件。
- `normalize_trajectory(raw, context, final_state=None, initial_state=None) -> Case`：支持
  OpenAI 累积快照和旧版 Anthropic content blocks；按相邻快照重叠拼接可观察证据，
  保留压缩前已观察到的调用；忽略 hidden thinking。每个工具调用保留当时的 harness。
- `build_task_pack(context, output, baseline=None, client=None) -> dict`：通过框架的受限
  适配接口冻结任务上下文，并追加 `environment.state_grounding`。可选 designer 最多再
  提议七个增量子项；固定状态检查占据第八个槽位。产物已有时拒绝覆盖。

Source `checklist_deps` is trusted reference data, not an automatic Subject assignment.
Optional dependencies remain optional. Generic request analysis still decides the
subjects; task and hop are derived by the framework.

## 从真实外部数据生成 Case 和 TaskPack

先确保外部 `env_outputs` checkout 是 `sandbox` 分支。在环境适配分支安装框架：

```bash
uv sync --extra dev
```

以下脚本不会调用 LLM、执行环境工具或修改外部仓库。路径须明确指定；例如当前
已检查的环境为 `outputs/barbershop_browse`，任务为 `sets/set_0/task_gen_001.json`，
对应旧轨迹为 `conversations/set_0/task_gen_001_raw.json`。

```bash
export ENV_OUTPUTS_ROOT=/absolute/path/to/env_outputs-sandbox
export REE_TASK_ENV=barbershop_browse
export REE_TASK_SET=set_0
export REE_OUTPUT_DIR=/absolute/path/to/new-evaluation-directory
uv run python - <<'PY'
import json
import os
from pathlib import Path
from ree.env_outputs import load_task_context, normalize_trajectory, build_task_pack

source = Path(os.environ["ENV_OUTPUTS_ROOT"])
env = source / "outputs" / os.environ["REE_TASK_ENV"]
set_name = os.environ["REE_TASK_SET"]
task_file = env / "sets" / set_name / "task_gen_001.json"
raw_file = env / "conversations" / set_name / "task_gen_001_raw.json"
output = Path(os.environ["REE_OUTPUT_DIR"])
output.mkdir(parents=True, exist_ok=False)
context = load_task_context(task_file, env)
raw = json.loads(raw_file.read_text(encoding="utf-8"))
# Old raw files often lack final_state. Do not invent it from a success claim.
case = normalize_trajectory(raw, context)
(output / "case.json").write_text(case.model_dump_json(indent=2), encoding="utf-8")
record = build_task_pack(context, output / "task-pack")
print(json.dumps({"case": str(output / "case.json"), "pack": record["path"],
                  "evidence_complete": case.evidence_complete}, indent=2))
PY
```

模型评分需要用户自己的兼容 API 配置。设置 `REE_MODEL`、`REE_BASE_URL`、`REE_API_KEY`
后执行；密钥只在环境变量中，不写入任务包：

```bash
uv run ree score "$REE_OUTPUT_DIR/case.json" \
  --pack "$REE_OUTPUT_DIR/task-pack" \
  --output "$REE_OUTPUT_DIR/evaluation.json" \
  --html "$REE_OUTPUT_DIR/inspector.html" --locale both
```

只生成 Case、TaskPack 或 HTML 不代表模型判定已校准。`--replay` 只能回放显式准备的
judge fixture，验证运行链路；它不会自动评价新轨迹，也不应将预设分数当作真实评分。
不得为让真实轨迹的 demo 跑通而自动生成全部通过的 replay 标签。

## 接到本地 RL rollout 后

本仓库的训练接入是评分 API。环境 rollout、模拟用户和 GRPO/GSPO 优化器仍由训练侧
提供。每条新 rollout 应保留完整消息快照、对应工具声明，以及需要验收时的真实初始/
最终状态。推荐训练侧在结束会话前调用 sandbox 的 `/session/call_list` 和 `/session/info`；
后者提供 `final_state`。仅任务文件的 `init_config` 是声明的初始化输入，不应冒充运行时
初始化后的精确状态；做严格 diff 时采集真实 before/after 状态。

```python
from pathlib import Path
from ree.env_outputs import normalize_trajectory
from ree.evaluator import evaluate, training_reward
from ree.judge import OpenAIJudge
from ree.pack import load_pack

# Values below are supplied by the training-side rollout collector.
case = normalize_trajectory(
    collected_rollout,
    task_context,
    initial_state=collected_initial_state,
    final_state=collected_final_state,
)
client = OpenAIJudge.from_env(max_calls=200, deadline=600)
try:
    evaluation = evaluate(case, client, load_pack(Path(frozen_pack_path)))
    # Raises for invalid/missing-evidence records; do not substitute a zero reward.
    reward = training_reward(evaluation)
finally:
    client.close()
```

Optional exact state assertions can use the public verifier, without another LLM:

```python
from ree.verification import ArtifactCheck, evaluate_artifact, verification_event

check = ArtifactCheck(
    artifact_ref="final_state",
    pointer="/records/selected/status",  # Actual declared state path, not a guessed one.
    operation="equals",
    expected="cancelled",
)
judgement = evaluate_artifact(case, check)
case.events.append(verification_event("verifier/cancellation-status", judgement))
case = type(case).model_validate(case.model_dump())
```

Verifier evidence does not by itself assign the final reward. The Base/result judge and
registered plugin policy decide how it affects evaluation. Missing artifact, malformed
state, or an unresolved JSON pointer remains `missing_evidence`.

## 轨迹与边界处理

- 累积快照不能全部平铺，否则旧调用被重复计算。适配器按相邻快照对齐，不用全局文本
  去重；用户真实重复一句话会保留。
- 某些旧轨迹单独保存 assistant output，却不把该回复放进下次模型输入。适配器保留
  这段已观察到的回复，并继续从下一快照增量读取。
- 压缩后的非 meta 前缀可能是新用户输入。保留这些文本并对无法确定的重叠顺序标记
  `evidence_complete=false`；不静默删掉用户要求。
- tool_call/tool_result 通过真实调用标识关联。无对应调用、冲突调用标识或冲突工具名
  明确拒绝；不伪造调用来“修好”证据。
- JSONSchema 中的 `password`/`api_key` 属性是 schema 描述，不是凭证值。声明保留；
  结构化传输凭证从配置中移除，真实调用中相应值遮盖但保留字段名。状态快照中发现
  凭证字段则明确拒绝；不删除字段或填假值，以免存在性/状态验证误判。
- 工具声明可能逐快照变化。每次调用存储首次观察时的 `Event.harness`，避免用最后一次
  声明错误校验全部历史调用；顶层真实 system 在调用快照冻结前应用。缺 schema 与
  显式空 schema 必须区分。压缩只允许文本内容变化，`is_error` 等结构化状态冲突不会
  被当作普通压缩吞掉。
- 带 tool_calls 的 assistant 前言不算最终回复；没有终止回复的轨迹保留空 response 并
  标记证据不完整。
- `final_state` 只在真实提供时生成 Artifact。状态改变的完成声明缺少必要状态证据时，
  专属状态检查返回 `missing_evidence`；纯查询不强行要求状态 diff。

## 验证范围

源快照：sandbox commit `1a0f569024f17c530bfd6a415ad6b8e41705002a`（2026-11-13）。
检查当前 checkout 的所有 `outputs/*/sets/*/task*.json` 与已有 raw conversation：

| 只读验证 | 结果 |
| --- | --- |
| 任务 JSON 读取 | 622 个中 618 个通过 |
| 任务读取失败 | 4 个任务引用未声明的 `find_nearby_events`；真实 schema/实现为 `search_events`，旧别名又指向不存在的方法；明确拒绝，不猜别名 |
| raw 轨迹读取与规范化 | 597 个中 539 个通过 |
| raw 无法匹配任务 | 54 个旧轨迹对应任务文件在当前源 checkout 不存在 |
| raw 对应任务 schema 不一致 | 与上述同一问题有关的 4 个轨迹 |
| 规范化证据完整性 | 521 个标记完整，18 个保守标记不完整，保留观察证据 |
| 合成回归测试 | 22 个通过；覆盖累积/压缩快照、工具关联、重复用户输入、schema 变化、凭证遮盖、缺状态与增量槽位限制 |

这些是输入协议和证据接线验证，不是模型 judge 成功率或任务成功率。没有通过静默删除
坏数据或编造状态提高成功数字。源仓库 README 的 16 环境及部分示例路径已过时。

另以 `barbershop_browse` 的真实叶子工具做了本地无 LLM smoke：实例化任务初始数据、
运行 `fetch_user_profile` 和 `update_user_profile`，采集实际 before/after，使用通用
artifact verifier 检查状态变化及目标值。整个 smoke 禁止网络，未修改外部源文件；
可选语义搜索依赖被替换为一旦调用就拒绝的 guard。这证明这些确定性叶子操作和
状态证据接线工作，不代表完整 HTTP 服务、搜索/embedding、LLM rollout 或训练已跑通。

机器可读验证记录：

- [只读源协议扫描](validation/env-outputs-sweep.json)
- [实际叶子工具执行](validation/env-outputs-local-smoke.json)
- [私有分支整合验证](validation/env-outputs-integration.json)

记录只保留路径、计数和判断结果，不携带原始任务、轨迹、用户状态或服务凭证。
