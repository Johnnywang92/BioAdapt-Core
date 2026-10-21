# BioAdapt-Core

AI 辅助单细胞多组学分析平台的实现仓库。本文给出**安装、跑测试、起本地开发栈、调用 API** 的完整步骤。

先明确一件事：仓库里的是一套**参考实现（reference deployment）**——SQLite 关系索引 + 内存对象平面 + 单进程编排 + 无登录的前端，全部绑在 `127.0.0.1` 上。它把方案里的数据契约、策略即代码、状态机与审批闭环、证据卡与评测门禁都跑通了，但**不是可交付的生产件**：对象存储、真实计算执行器、导出 worker、身份提供方都需要部署方替换。第 10 节列出了这份单机部署做不到的事，请先读完再对外演示。

---

## 1. 文档地图

| 文档 | 用途 |
| --- | --- |
| [AI辅助单细胞多组学分析平台建设方案-V2.0修订稿.md](file:///Users/yifanwang/workspace/BioAdapt-Core/AI%E8%BE%85%E5%8A%A9%E5%8D%95%E7%BB%86%E8%83%9E%E5%A4%9A%E7%BB%84%E5%AD%A6%E5%88%86%E6%9E%90%E5%B9%B3%E5%8F%B0%E5%BB%BA%E8%AE%BE%E6%96%B9%E6%A1%88-V2.0%E4%BF%AE%E8%AE%A2%E7%A8%BF.md) | 设计基线。代码与测试里的章节引用（`§5.7`、`§9.2`、`§11.6` …）全部指向它的编号 |
| [建设方案V1.0-专家评审问题清单.md](file:///Users/yifanwang/workspace/BioAdapt-Core/%E5%BB%BA%E8%AE%BE%E6%96%B9%E6%A1%88V1.0-%E4%B8%93%E5%AE%B6%E8%AF%84%E5%AE%A1%E9%97%AE%E9%A2%98%E6%B8%85%E5%8D%95.md) | V1.0 评审遗留问题，V2.0 修订稿逐条回应 |
| [审查报告-2026-10-20.md](file:///Users/yifanwang/workspace/BioAdapt-Core/%E5%AE%A1%E6%9F%A5%E6%8A%A5%E5%91%8A-2026-10-20.md) | 安全 / 架构 / 性能 / 测试四条线的审查结论与**批次一～四修复记录**，含遗留说明 |

`scripts/serve_workbench.py`、`tests/integration/`、`services/api/tests/` 是三个最可靠的"这段代码怎么用"来源。

---

## 2. 仓库结构

uv workspace，成员为 `packages/*`、`services/*`、`workflows/*`（见根 [pyproject.toml](file:///Users/yifanwang/workspace/BioAdapt-Core/pyproject.toml)）：

| 成员 | 包名 | 内容 | 方案章节 |
| --- | --- | --- | --- |
| `packages/schemas` | `bioadapt-schemas` | 数据契约、标识符、指纹、质量门禁 | §1.5 §3.3 §5.x §7.x §13.4 |
| `packages/tool_registry` | `bioadapt-tool-registry` | 工具准入、晋级通道、结果契约 | §9.4 §9.6 |
| `packages/policies` | `bioadapt-policies` | 出境白名单、模型路由、访问控制、注入防御 | §4.5 §9.7 §11.2–11.6 |
| `packages/storage` | `bioadapt-storage` | 带项目作用域的关系索引、对象布局、留存与删除 | §5.7–5.9 §9.7 §11.7 |
| `services/api` | `bioadapt-api` | 业务 API 与非功能/安全约束 | §9.3 §9.7 |
| `services/orchestrator` | `bioadapt-orchestrator` | 状态机、审批门、循环预算 | §9.1 §9.2 §9.7 |
| `services/evidence` | `bioadapt-evidence` | 证据卡服务与模型决策接线 | §7.2–7.8 §4.5 §11.5–11.6 |
| `workflows/scrnaseq` | `bioadapt-scrnaseq` | scRNA-seq 工具包装、确定性参考实现、run driver | §7.1 §9.4 §9.6 |
| `packages/evaluation` | `bioadapt-evaluation` | 评测集、指标字典、盲评协议、发布门禁 | §10.3–10.8 |

其余目录：

- `apps/web` — Next.js 14 项目工作台（数据登记、计划与审批、run、专家队列、单屏证据卡、导出）。它不是工作区成员，独立用 npm 管理。
- `tests/` — 跨层测试。`tests/integration/` 跑真实 run 的闭环，`tests/redteam/` 是发布门禁用例集（§10.x）。
- `scripts/serve_workbench.py` — 一键起带种子数据的本地 API。

**分层约束（§9.5）**：`packages/*` 只能互相引用，`services/*` 可以引用 `packages/*`，反过来不行；根 `tests/` 是唯一允许同时装配两层的角色。新增代码前先确认这一条没被破坏。

---

## 3. 环境要求

实测通过的工具链：

| 依赖 | 版本 | 备注 |
| --- | --- | --- |
| Python | **3.12**（本机 3.12.11） | `requires-python = ">=3.12"`；`.venv` 必须是 3.12，系统 `python3` 是 3.14 时不要直接用它跑测试 |
| [uv](https://docs.astral.sh/uv/) | 0.11.16 | workspace 解析器 |
| Node.js | 20.x（本机 v20.20.2） | 仅前端需要 |
| npm | 10.8.2 | 仅前端需要 |

---

## 4. 安装

### 4.1 后端（Python 工作区）

```bash
cd /path/to/BioAdapt-Core
uv sync
```

`uv sync` 会建 `.venv`，并按 `[tool.uv.sources]` 把九个成员以**可编辑**方式装进去（这些包没有发布到 PyPI，全部来自本地 workspace），同时安装 `dev` 组的 pytest / httpx。

验证：

```bash
.venv/bin/python -c "import bioadapt_api, bioadapt_orchestrator, bioadapt_scrnaseq; print('ok')"
```

后续所有 Python 命令都用 `.venv/bin/` 里的可执行文件，不要依赖 `source activate`。

### 4.2 前端（项目工作台）

```bash
cd apps/web
npm install          # 或 npm ci，仓库里有 package-lock.json
```

---

## 5. 跑测试

### 5.1 全量

```bash
.venv/bin/pytest -q
```

当前基线：**1341 passed**（本机约 2 分钟）。`testpaths` 已配置为 `packages`、`services`、`workflows`、`tests`，所以在仓库根目录直接跑 `pytest` 就会覆盖四层。

### 5.2 分层 / 单包

```bash
.venv/bin/pytest packages/storage/tests -q      # 86 passed（存储索引）
.venv/bin/pytest services/api/tests -q          # 187 passed：单服务的路由×策略用例
.venv/bin/pytest services/orchestrator -q       # 68 passed：状态机与审批门
.venv/bin/pytest tests/integration -q           # 119 passed：真实 run → 证据卡 → 指标
.venv/bin/pytest tests/redteam -q               # 45 passed：发布门禁，注入、越权、出境绕过
.venv/bin/pytest packages/evaluation -q         # 评测集与质量门禁
```

> **注意**：一次 pytest 调用里同时点多个子项目会触发 conftest 收集冲突（各包有自己的 `conftest.py` 与 `tests/` 目录名）。要么在根目录跑全量，要么逐包跑，不要写 `pytest packages/a/tests packages/b/tests`。

只跑单条用例：

```bash
.venv/bin/pytest services/api/tests/test_orchestration_routes.py -q -k "accountable"
```

### 5.3 前端

```bash
cd apps/web
npm run typecheck      # tsc --noEmit
npm run build          # 生产构建，可选
```

### 5.4 迁移漂移

`packages/storage` 的 schema spec 与已提交的 `0001_core.sql` 由 `test_committed_migration_matches_the_schema_spec` 钉住。改了 `schema.py`/`ddl.py` 就必须重新生成迁移，否则这条测试会红：

```bash
.venv/bin/pytest packages/storage -q -k migration
```

---

## 6. 起本地开发栈

两个终端。

### 6.1 终端 A：API + 种子数据

```bash
.venv/bin/python scripts/serve_workbench.py
```

它会构造一套真实闭环的参考 run，打印种子标识与五个角色 token，然后在 `http://127.0.0.1:8000` 起 uvicorn：

```
project    prj_scrnaseq
run        run_scrnaseq_01
candidates 5 · cards filed 5
tokens (paste one into the workbench's 'Acting as' panel):
  pi       eyJ…
  analyst  eyJ…
  expert   eyJ…
  expert2  eyJ…
  engine   eyJ…
```

种子状态刻意留了一处未完：审批收件箱里有一个 **pending 的 escalation 门**（`kind=escalation`，assigned_to `user_pi_01`），这样"已请求"与"已决策"两个界面才真的有东西可展示。

> **`.workbench/tokens.json` 里是本机开发用的 HS256 bearer token。** 该目录已在 `.gitignore` 中；不要提交、不要贴进 issue / 日志 / Prompt / 截图。签名密钥 `SECRET` 同样只存在于这个绑到 `127.0.0.1` 的进程里（§11.2 禁止密钥进代码仓库，这个常量只是开发便利的妥协，不是部署方式）。

### 6.2 终端 B：工作台

```bash
cd apps/web
npm run dev -- -p 3100        # 不加 -p 时 Next 默认 3000
```

打开 http://localhost:3100 。前端没有任何数据存储，也不做鉴权：

- 所有请求走 `/core-api/:path*`，由 [next.config.mjs](file:///Users/yifanwang/workspace/BioAdapt-Core/apps/web/next.config.mjs) 重写到 `BIOADAPT_API_ORIGIN`（默认 `http://127.0.0.1:8000`）。这是仓库里**唯一**被读取的环境变量，且只在前端构建配置里。用重写而不是直连，是为了不在浏览器里出现第二个 origin，也就不需要 API 提供 CORS 策略。
- 右上角 **Acting as** 面板粘贴一个 token 即以该身份操作；token 只存 `sessionStorage`（key `bioadapt.workbench.identity`），关掉标签页就没了。
- 面板上解出的姓名/角色**只是显示**。每个请求的权限都由 API 从 claims 重新推导，改前端文案不会改变授权。

页面：

| 路由 | 屏 |
| --- | --- |
| `/` | 项目入口 |
| `/projects/{id}/data` | 数据登记（§5.9 完整性字段） |
| `/projects/{id}/plans` | 分析计划与批准 |
| `/projects/{id}/runs/{runId}` | run 状态机、journal、预算 |
| `/projects/{id}/queue` | 专家复核队列（按影响×不确定性排序） |
| `/projects/{id}/candidates/{candidateId}` | 单屏证据卡 |
| `/projects/{id}/candidates/{candidateId}/alignment` | 跨轮对齐历史（§5.6） |
| `/projects/{id}/approvals` | 审批收件箱 |
| `/projects/{id}/exports` | 导出请求 |

---

## 7. 直接调用 API

### 7.1 通用约定

- **认证**：`Authorization: Bearer <token>`。HS256 compact JWS，校验 `iss`/`aud`/`exp`；签名不对 401，`iss` 不受信 401，过期 401 `the bearer token has expired`。
- **角色**：`platform_admin`、`principal_investigator`、`analyst`、`domain_expert`、`knowledge_curator`、`evaluation_lead`、`compliance_officer`、`service_agent`。三条与身份相关的硬规则（[access.py:154](file:///Users/yifanwang/workspace/BioAdapt-Core/packages/policies/src/bioadapt_policies/access.py#L154) 起）：
  - **人类专属动作**（服务账号一律 403）：`card:sign_off`、`egress:approve`、`approval:decide`、`approval:delegate`、`tool:register`、`tool:grant_adhoc`、`tool:promote`、`evaluation:freeze_truth`、`iam:grant_role`、`policy:write`、`case_library:write`。这是 §11.6 控制 6——权限与审批状态只能由人的动作触达。
  - **需要第二因子**（claims 的 `amr` 必须含 MFA 方式）：`egress:approve`、`tool:promote`、`asset:delete`、`iam:grant_role`、`policy:write`、`evaluation:freeze_truth`。
  - **职责分离**：请求审批门的人不能自己批它；出境请求者也不能自己批。
  - 服务账号只能持有 `service_agent` 角色，且只被允许 `run:read`、`run:update`、`asset:read`、`asset:write`、`model:call`。
  - `evaluation_lead` 与 `analyst`/`platform_admin` 互斥（§13.1：冻结 ground truth 的人不能同时是被测系统的建造者）。
- **追踪**：每个请求返回 `X-Trace-Id`。入站头若合法（`^[A-Za-z0-9_.:-]{1,64}$`）则复用，否则替换成新生成的；畸形值绝不透传。每请求写一行脱敏访问日志，字段固定。
  注意 `GET /v1/projects/{p}/traces/{trace_id}` 要的是**模型调用 id**（`inv_…`），由 [decision.py:501](file:///Users/yifanwang/workspace/BioAdapt-Core/services/evidence/src/bioadapt_evidence/decision.py#L501) 写入，只有 `audit:read` 主体可读——它和访问日志的 `X-Trace-Id` 不是同一个东西。
- **错误体**统一：

  ```json
  {"code": "…", "class": "…", "field": null, "message": "…",
   "retryable": false, "retry_after": null, "trace_id": "tr_…"}
  ```

  | 状态 | class / code | 触发 |
  | --- | --- | --- |
  | 400 | `validation` / `invalid_request` | 契约校验失败，`field` 指出哪个字段 |
  | 401 | `policy_blocked` / `unauthenticated` | 缺 token、签名不合法、过期 |
  | 403 | `policy_blocked` / `authorization_denied` | 角色不授予该动作，或越出项目作用域 |
  | 403 | `policy_blocked` / `approval_refused` | 门已决、或不属于当前问责人 |
  | 404 | `not_found` | code 形如 `resource_not_found` / `trace_not_found`；资源不存在或不在本项目内 |
  | 409 | `conflict` | 幂等键复用、唯一约束、`illegal_transition` |
  | 409 | `conflict` / `concurrent_write` | 审批写输给并发，`retryable: true`，重读后重放 |
  | 422 | `gate_blocked` / `card_gate_blocked` | 证据卡门禁：数值溯源不合规，或 `final_status` 超过了天花板 |

- **分页**：列表统一 `{items, limit, offset, next_offset}`，`limit` 默认 50，`next_offset` 为 `null` 表示到底。
- **幂等**：`POST /v1/projects/{p}/runs` 与 `POST …/exports` 必须带 `Idempotency-Key`；同键同体重放返回 200 + `Idempotency-Replayed: true`，同键不同体返回 409。状态机转换按 `attempt`/`approval_id` 幂等，重放同样返回 200。审批门按 `(run, kind, subject digest, node)` 定位，重复申请同一个门同样返回 200 + `Idempotency-Replayed: true`（见 7.3）。
- **文档端点**：`/docs` 与 `/redoc` 被关闭（返回 404），`/openapi.json` 可用（`title: BioAdapt API, version: 1.0`；折叠后 41 个路径、47 个操作：46 条 `/v1` + 1 条 `/v0` 别名）。
- **弃用别名**：`GET /v0/projects/{p}/clusters/{cluster_id}/evidence?run_id=…` 仍可用，响应带 `Deprecation: true` 和 `Link: </v1/projects/{p}/evidence>; rel="successor-version"`。`cluster_id` 只在 run 内唯一，所以 `run_id` 必填。

### 7.2 读操作（以下返回值都是对本仓参考部署实测所得）

```bash
TOK=$(python3 -c "import json;print(json.load(open('.workbench/tokens.json'))['pi'])")
API=http://127.0.0.1:8000

curl -s -H "Authorization: Bearer $TOK" $API/v1/projects/prj_scrnaseq
```

```json
{
  "project_id": "prj_scrnaseq",
  "study_goal": "resolve an uncharacterized immune population",
  "species": "human",
  "privacy_tier": "restricted",
  "created_by": "user_pi_01",
  "regulatory_profile": [],
  "annotation_granularity_target": "cell_type_fine",
  "review_load_budget_hours_per_week": 6.0,
  "thresholds": {
    "version": "v0-unconfirmed", "confirmed": true,
    "rare_share_max": 0.01, "rare_min_cells": 50, "rare_min_donors": 2,
    "low_confidence_percentile": 10.0, "classifier_margin_min": 0.15,
    "coverage_knn": 20, "coverage_distance_ratio_min": 2.0,
    "stability_cc_ari_min": 0.6, "stability_jaccard_min": 0.5, "stability_pass_ratio": 0.6,
    "alignment_continuation_min": 0.6, "alignment_uncertain_min": 0.4,
    "min_n_per_group": 3, "de_fdr_max": 0.05, "de_min_abs_log2fc": 1.0,
    "detection_frequency_in_min": 0.6, "detection_frequency_excluded_max": 0.1,
    "donor_consistency_min": 0.8, "numeric_tolerance": 1e-06, "label_agreement_min": 0.99,
    "expert_queue_card_limit": 10, "summary_rare_share_max": 0.02, "summary_rare_min_cells": 50
  },
  "created_at": "2026-06-01T09:00:00Z"
}
```

`thresholds` 是**这个项目判数用的那一套**：起草、`alignment` 重分类、专家队列（含稀有判定与卡数上限）都读项目行的这份，不再读部署常量。上面这份是工作台播种的项目（**重启后**的样子；还在跑的那个实例是批次三起的旧种子，`confirmed` 仍为 `false`），`confirmed: true` 但 `version` 仍是 `"v0-unconfirmed"`——工作台演示的是走完阶段 0 之后的状态，为了让签字与导出可用，它把附录 C1 的**占位数值原样登记为已确认**；新建项目（`POST /v1/projects`）注册进来的就是 `confirmed: false`，自带 `confirmed: true` 的注册体直接被 400 拒掉——注册不能替项目自我确认，否则创建项目的那一次请求就顺手打开了自己的结论出口。`confirmed` 只影响两件事：

- **结论出口会关**：`POST …/cards/{id}/sign_off`（`sign_off` 非 `not_signed` 时）与 `POST …/exports` 在未确认时返回 403 `policy_blocked`，文案点名那套未经确认的 version，并各自落一条审计行。计算、起草、排队、撤签都不受它影响——跑阶段 0 基线正是替换这些数的方法。
- **只有人能开**：确认走 `POST /v1/projects/{p}/thresholds/confirm`（7.4），要求 PI、人类身份、二次因子，且请求体必须重述全部 24 个字段；`confirmed` 本身由服务端置位，调用方自填会被 400 拒掉。

要注意 `version` 一旦被换成新的一套，旧 run 已落库产物引用的还是老 version，重新起草会撞 [cards.py:1305](file:///Users/yifanwang/workspace/BioAdapt-Core/services/evidence/src/bioadapt_evidence/cards.py#L1305) 的 §7.3 漂移守卫（`ContractDrift`）——这是"结论必须标清楚用哪套数量出来的"的代价，需要重跑而不是改数。另外**计算侧尚未接上**：节点参数里的阈值仍由驱动直接取 `DEFAULT_THRESHOLDS`（见第 12 节遗留项）。

```bash
curl -s -H "Authorization: Bearer $TOK" "$API/v1/projects/prj_scrnaseq/runs/run_scrnaseq_01"
```

```json
{
  "run_id": "run_scrnaseq_01", "project_id": "prj_scrnaseq",
  "state": "ANALYSIS_RUNNING", "plan_id": "plan_scrnaseq", "plan_version": "1",
  "reproducibility_level_target": "byte",
  "tool_versions": {"scrnaseq_qc": "1.0.0", "scrnaseq_neighbors": "1.0.0", "…": "…"},
  "tool_registry_snapshot": "35a7bb0dae4dc6d58cca5f400790f29612ce3de860a7e14bd2661ce6f483a793",
  "input_hashes": {"as_91c579ff55db": "91c579ff55db790ef200ce0f10862d33a421963d0047fbd0639b55aa90cacb88"},
  "parameters": {}, "queue_position": null
}
```

run 的 `state` 取值：`CREATED`、`PREFLIGHT_RUNNING`、`PREFLIGHT_PASSED`、`PLAN_DRAFTED`、`PLAN_APPROVED`、`ANALYSIS_RUNNING`、`QC_REVIEW`、`CANDIDATE_DISCOVERY`、`EVIDENCE_BUILDING`、`TARGETED_ANALYSIS`、`VALIDATION`、`HUMAN_REVIEW`、`REPORT_READY`、`ARCHIVED`，以及失败侧 `BLOCKED`、`RETRYABLE_FAILURE`、`NEEDS_INPUT`、`POLICY_BLOCKED`、`RESOURCE_EXCEEDED`、`BUDGET_EXCEEDED`、`ESCALATED_TO_HUMAN`、`FAILED`。

专家队列把"待看"与"已看"分开，并对每个位置给出理由：

```bash
curl -s -H "Authorization: Bearer $TOK" \
  "$API/v1/projects/prj_scrnaseq/runs/run_scrnaseq_01/cards/queue?limit=1"
```

```json
{
  "run_id": "run_scrnaseq_01",
  "drafted": 5,
  "queue": ["card_21a8e85ab0cb"],
  "observed": ["card_eb0d741ac922", "card_6f211fb1d04a", "card_7ea2023cc627", "card_cf10fd743fc3"],
  "scores": [{
    "card_id": "card_21a8e85ab0cb", "candidate_id": "cand_pariqkeybnic_68ab2c37",
    "display_name": "c001", "impact": 1, "uncertainty": 2, "client_ask": false, "score": 2,
    "reasons": [
      "not rare by the composition rule: 24 of 128 cells across 8 donors",
      "no coverage measurement, so the reference's reach is unknown (§7.6)",
      "1 record(s) argue against the ones that support it"
    ]
  }],
  "limit": 1, "thresholds_version": "v0-unconfirmed", "rule_version": "expert_queue_rule_v1"
}
```

`reasons` 是这套队列的核心设计：排序必须能被复核者质疑，所以每个分数都带着它是怎么来的。

预算读的是三条独立上限与它们的用量（§9.7）：

```bash
curl -s -H "Authorization: Bearer $TOK" \
  "$API/v1/projects/prj_scrnaseq/runs/run_scrnaseq_01/budget"
```

```json
{
  "run_id": "run_scrnaseq_01",
  "usage": {"iterations": 0, "compute_minutes": 5.5, "model_cost_usd": 0.0},
  "ceiling": {"max_iterations": 1, "max_compute_minutes": 180, "max_model_cost_usd": 5.0},
  "exhausted": []
}
```

`ceiling` 是计划值与项目配额**逐轴取小**的结果，不是某一轴的主从关系。

其余常用读接口：`GET …/transitions`（journal，每次移动都带理由）、`GET …/approvals?state=pending`、`GET …/approvals?overdue=true`、`GET …/candidates`、`GET …/candidates/{id}/evidence`、`GET …/candidates/{id}/alignment`、`GET …/audit`、`GET …/assets`。

### 7.3 写操作：审批闭环

这四步最能体现平台的安全语义（§11.6 请求者不能自批，§9.2 委托即移交问责）：

```bash
AN=$(python3 -c "import json;print(json.load(open('.workbench/tokens.json'))['analyst'])")

# 1) analyst 请求一个门
curl -s -X POST -H "Authorization: Bearer $AN" -H "Content-Type: application/json" \
  -d '{"kind":"escalation","assigned_to":"user_pi_01"}' \
  $API/v1/projects/prj_scrnaseq/runs/run_scrnaseq_01/approvals     # → 201

# 2) 请求者试图自己批这个门
curl -s -X POST -H "Authorization: Bearer $AN" -H "Content-Type: application/json" \
  -d '{"approved":true,"reason":"I asked for it"}' \
  $API/v1/projects/prj_scrnaseq/approvals/apr_xxx/decision
```

```json
{"code": "authorization_denied", "class": "policy_blocked",
 "message": "apr_xxx: no role of ['analyst'] grants approval:decide; approval:decide: the subject that requested a gate may not open it",
 "retryable": false, "trace_id": "tr_…"}
```

```bash
# 3) 问责人把它委托出去
curl -s -X POST -H "Authorization: Bearer $TOK" -H "Content-Type: application/json" \
  -d '{"to_subject":"user_pi_02"}' \
  $API/v1/projects/prj_scrnaseq/approvals/apr_e4a1e3023eaf/delegation   # → 200, assigned_to=user_pi_02

# 4) 原问责人再也批不动了；服务账号也批不动
curl -s -X POST -H "Authorization: Bearer $TOK" -H "Content-Type: application/json" \
  -d '{"approved":true,"reason":"changed my mind"}' \
  $API/v1/projects/prj_scrnaseq/approvals/apr_e4a1e3023eaf/decision
```

```json
{"code": "approval_refused",
 "message": "approval apr_e4a1e3023eaf is accountable to user_pi_02; delegate it to move the accountability"}
```

```json
{"code": "authorization_denied",
 "message": "apr_e4a1e3023eaf: no role of ['service_agent'] grants approval:decide; approval:decide must be performed by a human, not a service account"}
```

请求体的 kind 与 node_id 是**配对**而非可选：`node`/`adhoc_tool` 必须给 `node_id`，`plan`/`escalation` 给了反而 400：

```json
{"code": "invalid_request", "class": "validation", "message": "a node approval names the node it gates"}
```

> **审批门是按 subject digest 定位的**：`approval_id` 由 `run_id + kind + digest + node_id` 确定性生成，所以重复请求同一个门不会新建行（`record_approval` 见到同 id 就跳过写入，见 [catalog.py:664](file:///Users/yifanwang/workspace/BioAdapt-Core/packages/storage/src/bioadapt_storage/catalog.py#L664)）。重复申请回的是**库里那一行读回来的样子**：门已被决定 → 200 + `Idempotency-Replayed: true` + `state: "approved"`（含 `decided_by`），不是第二个 pending 门；门仍挂着 → 200 + 同一个 `approval_id`，`assigned_to` 仍是第一次那位负责人（重复申请不会把门换人），`requested_by` 也不会被覆盖。要开出第二扇门得让 digest 真的变——例如 `plan_version` 换成新版本（旧版本的批准不能给新版本的计划放行）。审计 details 里带 `replayed`，能查出一个门被申请过几次。

### 7.4 写操作：复核、签字、导出

```bash
curl -s -X POST -H "Authorization: Bearer $EX" -H "Content-Type: application/json" \
  -d '{"scope":"card","decision":"approve","rationale":"numbers match the products"}' \
  $API/v1/projects/prj_scrnaseq/cards/card_21a8e85ab0cb/reviews     # → 200，回整张卡
```

`scope` ∈ `card` `field` `membership`；`decision` ∈ `approve` `revise` `return_for_analysis` `reject` `mark_conflict` `mark_unknown`。响应里 `final_status` 是复核后重算的结果（`supported` / `provisional` / `conflict` / `unknown` / `rejected`）——复核者的一条意见不会凭空把卡抬成 supported。

```bash
curl -s -X POST -H "Authorization: Bearer $EX" -H "Content-Type: application/json" \
  -d '{"sign_off":"full"}' \
  $API/v1/projects/prj_scrnaseq/cards/card_21a8e85ab0cb/sign_off
# → {"card_id": "card_21a8e85ab0cb", "sign_off": "full", "final_status": "unknown"}
```

签字（`full` / `conditional` / `not_signed`）与结论状态是两条独立记录：`sign_off=full` 不会把 `final_status` 从 `unknown` 改成 `supported`。签字还必须由非作者完成（§5.9 的 CARD_SIGN_OFF）。

```bash
curl -s -X POST -H "Authorization: Bearer $TOK" -H "Content-Type: application/json" \
  -H "Idempotency-Key: exp_demo_0001" \
  -d '{"kind":"tables","filters":{"run_id":"run_scrnaseq_01"}}' \
  $API/v1/projects/prj_scrnaseq/exports
```

```json
{"export_id": "exp_a25b446fe05c", "status": "QUEUED", "kind": "tables",
 "poll": "/v1/projects/prj_scrnaseq/exports/exp_a25b446fe05c"}
```

首次 202，同键重放 200 + `Idempotency-Replayed: true`。`kind` ∈ `tables` `figures` `code_bundle` `delivery_zip`。`strict_local` 项目无论角色都拒绝导出（§5.9/§11.2）。

> 本地这份部署**没有导出 worker**，所以 `status` 会一直停在 `QUEUED`。这是缺件，不是 bug，见第 10 节。

签字与导出还多一道 §1.5 的门：项目注册的那套阈值若 `confirmed` 为 false，两者都返回 403 `authorization_denied`，文案点名那个未经确认的 version（撤签 `not_signed` 不在此列，起草与排队也不）。打开它的唯一入口是：

```bash
curl -s -X POST -H "Authorization: Bearer $TOK" -H "Content-Type: application/json" \
  -d '{"thresholds":{"version":"acme-2026-10-baseline","rare_share_max":0.01,"…":"其余 23 个字段全部重述"},"rationale":"盘点三个历史项目的候选分布"}' \
  $API/v1/projects/prj_scrnaseq/thresholds/confirm
```

这是人类专属 + 二次因子 + 仅 PI 的动作（缺 `amr: ["totp"]` → 403「second factor」），并且必须**一次重述全部 24 个字段**：漏一个 400 并列出缺哪些，`confirmed` 由平台置位、调用方自填 400，version 为空白 400，与当前生效的同名 version 再确认一次 409。审计 `thresholds:confirm` 记 `from_version`/`to_version`/`rationale`。

### 7.5 各写端点必填字段

从 `/openapi.json` 摘出，写 curl 或前端表单时按这个来：

| 端点 | 必填 |
| --- | --- |
| `POST /v1/projects` | `project_id` `study_goal` `species` `privacy_tier` `created_by` `annotation_granularity_target` `review_load_budget_hours_per_week` |
| `POST …/thresholds/confirm` | `thresholds`（全部 24 个字段，含 `version`；`confirmed` 不许调用方填）`rationale`；需 PI + 人类 + `amr: ["totp"]` |
| `POST …/plans` | `plan_id` `plan_version` `project_id` `objective` `design_matrix` `data_partitions` `qc_policy_id` `loop_budget` `nodes` `stop_conditions` `provenance_policy` |
| `POST …/assets` | `asset_id` `project_id` `uri` `file_sha256` `content_fingerprint` `format` `schema_version` `size_bytes` `privacy_tier` `usage_scope` `retention_class` `filtering_state` `created_at` |
| `POST …/assets/{id}/complete` | 同上（`DataAsset`） |
| `POST …/runs` | `plan_id` `plan_version` `input_asset_ids` `tool_registry_snapshot` + `Idempotency-Key` 头 |
| `POST …/runs/{id}/transitions` | `to_state` `reason` |
| `POST …/runs/{id}/approvals` | `kind`（`plan`/`node`/`adhoc_tool`/`escalation`） |
| `POST …/approvals/{id}/decision` | `approved` `reason` |
| `POST …/approvals/{id}/delegation` | `to_subject` |
| `POST …/cards/{id}/reviews` | `scope` `decision` `rationale` |
| `POST …/cards/{id}/sign_off` | `sign_off` |
| `POST …/exports` | `kind` + `Idempotency-Key` 头 |
| `POST …/annotations`、`…/annotations/{v}/review` | 见 `routes_annotations.py` |
| `POST …/egress/requests`、`…/egress/approvals` | 见 `routes_compliance.py` |

---

## 8. 配置：这套代码怎么装配

**Python 侧不读任何环境变量**（全仓 `os.environ` / `getenv` 零命中）。部署形态由构造 `ApiState` 决定：

```python
from bioadapt_api.app import create_app
from bioadapt_api.deps import ApiState

app = create_app(ApiState(
    catalogs=...,        # 项目 → Catalog 的解析（参考实现：SingleNodeCatalogs）
    objects=...,         # ObjectStore 协议实现
    signer=...,          # 预签 URL 的签名器
    verifier=...,        # bearer token 校验器
    quotas=...,          # 项目配额（存储、活跃 run 数、算力分钟、token 成本）
    queue=...,           # 运行槽位（参考实现 InMemoryQueue）
    traces=...,          # 模型调用内容存取
    log_sink=...,        # 访问日志出口
    clock=...,           # 注入时钟，所有时间判定走它
    approval_policy=..., # 审批窗口（工作日数）；§9.2/附录 C1 要求它是部署输入而非常量
    local_hosts=...,     # 视为本地的模型端点
    cloud_allowlist=..., # 出境白名单
    provider_terms=...,  # 供应商条款有效期
    stream_max_events=50,
    stream_poll_seconds=0.05,
))
```

字段定义在 [deps.py:114](file:///Users/yifanwang/workspace/BioAdapt-Core/services/api/src/bioadapt_api/deps.py#L114)。

`scripts/serve_workbench.py` 的 `build()` 是一份可读的装配样例（本地工作台用的配额：10 MB 存储、4 个活跃 run、480 算力分钟、10.0 USD token 成本、1 个运行槽、3 个工作日审批窗口）。要换 Postgres、MinIO/S3、真实执行器或 OIDC 校验器，**改的是这里**，不是找 `.env`。

唯一的环境变量是前端的 `BIOADAPT_API_ORIGIN`（默认 `http://127.0.0.1:8000`）。

---

## 9. 存储与迁移

- 关系索引是 `Catalog(path, *, clock, check_same_thread=True)`。传 `":memory:"` 得到进程内实例，传文件路径得到持久实例；生产形态是 Postgres + RLS，接口层用同一套 spec 生成 DDL。
- 项目作用域靠 `catalog.session(project_id)` 返回的代理，每次调用重新绑定 scope；**线程没设 scope 就查询会抛 `ScopeError`（fail closed）**，不会返回上一个项目的行。
- **索引里没有外键**（`schema.py`、`ddl.py`、`migrations/0001_core.sql` 均无 FK 约束）。孤儿行因此是可写入的，完整性靠写入路径与测试约束。这一点在审查报告 §四⑨ 的遗留说明里有记录。
- 迁移文件 `packages/storage/migrations/0001_core.sql` 是**就地重生成**的。若已有部署库上线，需要另出增量迁移，而不是重跑 `0001`。
- 重新生成与漂移检查：

  ```bash
  .venv/bin/pytest packages/storage -q -k migration   # 钉住 spec 与已提交 SQL 一致
  ```

- 对象平面目前只有 `InMemoryObjectStore` 与 `ObjectStore` Protocol。`object_key_for()` 给出布局 `bioadapt/{project}/{retention_class}/{asset_id}/{sha256 前 16 位}.{format}`（例如 `bioadapt/prj_scrnaseq/run_linked/as_91c579ff55db/91c579ff55db790e.json`），预签 URL 由 `HmacSigner` 签，TTL 有上限。

---

## 10. 这份单机参考部署做不到的事

先读这节再对外演示，避免把缺件当成缺陷：

| 现象 | 原因 |
| --- | --- |
| `GET …/assets/{id}/download` 返回 `https://object.internal/…?sig=…` | 签名 URL 的 host 是占位。生产要接 MinIO/S3 并换签名器 |
| 对上传 URL 发真实 `PUT` 拿不到 2xx | 没有可路由的对象存储。`POST …/assets/{id}/complete` 在字节缺失时报 409 `nothing has been uploaded to {key}`（§5.9 的完整性检查正在工作，不是坏掉） |
| 导出永远 `QUEUED` | 没有导出 worker。`poll` 字段给出的就是最终状态 |
| 每次读证据卡都重新取产物、重算 sha256、重解析 | 这是 §5.9 的完整性控制本身（审查报告 P4，**有意不缓存**） |
| `GET …/runs/{id}/events`（SSE）存在但没人用 | 批次二删掉了前端 `EventSource` 客户端，本仓已无消费者（P7 因此不修） |
| `GET …/traces/{id}` 常 404 | 只有真实模型调用才会写 trace（`inv_…`）。种子 run 的卡由确定性 driver 产出，不含模型调用 |
| 工作台身份面板说 token "past its exp by this clock" | 面板拿浏览器真实时钟比较 `exp`，而参考部署的 `state.clock()` 是冻结在 2026-06-01 的（[deps.py:221](file:///Users/yifanwang/workspace/BioAdapt-Core/services/api/src/bioadapt_api/deps.py#L221)）。API 仍然接受它，两边说法不同是时钟不统一，不是 token 失效 |
| 状态全在内存 | `serve_workbench.py` 重启即回种子状态。要持久化，给 `Catalog` 传文件路径并接真对象存储 |
| 无登录、无 TLS | 服务只绑 `127.0.0.1`。身份由 `Hs256Verifier` 的参考签发器模拟 |
| 单连接线程池 | 所有 worker 线程共享一个 sqlite 连接，写读需串行；这是参考实现的容量上限，不是设计目标 |

---

## 11. 安全护栏速览（改代码前必读）

这些是本仓的硬约束，任何"顺手优化"都不能越过：

- **隐私分级**（`privacy_tier`）：`public` `restricted` `strict_local` `hgr_controlled`。`strict_local` 与 `hgr_controlled` 的原始数据与敏感派生结果一律留本地，默认禁止出境且禁止任何云端模型调用；`strict_local` 项目连导出都被拒绝。
- **出境是白名单驱动的模板构造**，不是"过滤掉坏字段"：字段级拒绝会被记录，任何细胞级数据出境直接阻断（[egress.py](file:///Users/yifanwang/workspace/BioAdapt-Core/packages/policies/src/bioadapt_policies/egress.py)，§11.3–11.5）。
- **权限与审批状态不可由模型侧写路径触达**，只能经人工动作改变（§11.6）。服务账号永远不能决策审批门。
- **未经确认的判据出不了结论**（§1.5/§14.4）：项目阈值 `confirmed` 为 false 时，签字与导出两个结论出口 403；打开它的 `thresholds:confirm` 是仅 PI 的人类动作且要求二次因子，请求体必须重述全部 24 个字段、`confirmed` 由平台置位。计算、起草、排队不受此门影响（附录 C1 要求阶段 0 先跑基线才能替换这些数）。
- **预算是三条独立上限**：算力分钟、模型成本 USD、迭代次数。取计划与项目配额的逐轴较小值；任一轴耗尽即 `BUDGET_EXCEEDED`，模型成本不是算力分钟的注脚（§9.7）。
- **ad-hoc 工具**只允许在非特权容器、只读输入、无网络、资源与时长配额下运行，且需要一次性 PI 审批。**注意**：这条闭环（H1）尚未实现，见下。
- **不允许大模型自由生成任意 Shell 并直接在生产计算节点执行。**
- **密钥不写入 Prompt、代码仓库、运行参数或日志。** `.workbench/` 与其中的 token 永不提交。
- **注入防御**在 `tests/redteam/` 有对应门禁用例集；改 egress、prompt 组装、工具绑定后必须重跑。

---

## 12. 已知未完成项

- **H1：ad-hoc 工具授权闭环**（PI 专属 + 强制路由）仍未实现，是这条工作线上唯一开放的待办。在此之前，`adhoc_tool` 门只能被记录和校验，`require_node_approval` 只认 `NODE` 类门。
- 生产件缺件：对象存储实现、计算执行器、导出 worker、真实 IdP、Postgres + RLS 部署、增量迁移。
- 批次五修掉了本轮写文档时发现的两处（审批门响应与存储不一致、`Thresholds.confirmed` 无人消费），但留下三条相关的未完成项：
  1. **计算侧仍读代码常量**：判数、起草、队列与两个结论出口都读项目行的阈值，而节点参数里的 `thresholds` 由驱动直接取 `DEFAULT_THRESHOLDS`。要让"算的时候"也用项目那一套，需要把项目阈值穿过 `POST …/runs` → 编排器 → 工作流节点参数，涉及所有已注册 plan 的模板，单独排期。
  2. **工作台没有阈值确认页**：`POST …/thresholds/confirm` 只有 API 入口，未确认项目在工作台上签字只会看到 403 文案。
  3. **门禁只管入口不管追溯**：把项目阈值改回未确认不会让已签的结论或已完成的导出失效，需要人撤签（撤签不受门禁限制，正是为此保留）。
- 详见 [审查报告-2026-10-20.md](file:///Users/yifanwang/workspace/BioAdapt-Core/%E5%AE%A1%E6%9F%A5%E6%8A%A5%E5%91%8A-2026-10-20.md) 各批次末尾的「遗留说明」。

---

## 13. 常见问题

**Q：`python3 -m pytest` 报 collection error？**
用 `.venv/bin/pytest`。系统 Python 可能是 3.14，而工作区只保证 3.12。

**Q：`/docs` 打不开？**
故意的：`docs_url=None, redoc_url=None`。用 `/openapi.json`。

**Q：一次跑多个包的测试全红？**
conftest 冲突。在根目录跑全量，或逐包跑（见 §5.2）。

**Q：8000 端口被占？**
`lsof -nP -iTCP:8000 -sTCP:LISTEN` 找到上一个 `serve_workbench.py` 再处理。它就是参考部署本身，改端口要同步改前端 `BIOADAPT_API_ORIGIN`。

**Q：`npm run dev` 起不来或 `npx tsc` 找不到？**
`apps/web/node_modules` 未安装，或 shell PATH 里没有 Node。先 `npm install`；必要时显式给 PATH：`PATH="/usr/local/bin:/usr/bin:/bin:$PATH" npx tsc --noEmit -p tsconfig.json`。

**Q：登记数据后 `complete` 一直 409？**
正常，见 §10 第二行。要在本地跑完整闭环，直接用 `serve_workbench.py` 的种子 run。
