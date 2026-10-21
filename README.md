# BioAdapt-Core

AI 辅助单细胞多组学分析平台的实现仓库。本文给出**安装、跑测试、起本地开发栈、调用 API** 的完整步骤。

先明确一件事：仓库里的是一套**参考实现（reference deployment）**——SQLite 关系索引 + 内存对象平面 + 单进程编排 + 无登录的前端，全部绑在 `127.0.0.1` 上。它把方案里的数据契约、策略即代码、状态机与审批闭环、证据卡与评测门禁都跑通了，但**不是可交付的生产件**：对象存储、真实计算执行器、导出 worker、身份提供方都需要部署方替换。第 10 节列出了这份单机部署做不到的事，请先读完再对外演示。

---

## 1. 文档地图

| 文档 | 用途 |
| --- | --- |
| [AI辅助单细胞多组学分析平台建设方案-V2.0修订稿.md](file:///Users/yifanwang/workspace/BioAdapt-Core/AI%E8%BE%85%E5%8A%A9%E5%8D%95%E7%BB%86%E8%83%9E%E5%A4%9A%E7%BB%84%E5%AD%A6%E5%88%86%E6%9E%90%E5%B9%B3%E5%8F%B0%E5%BB%BA%E8%AE%BE%E6%96%B9%E6%A1%88-V2.0%E4%BF%AE%E8%AE%A2%E7%A8%BF.md) | 设计基线。代码与测试里的章节引用（`§5.7`、`§9.2`、`§11.6` …）全部指向它的编号 |
| [建设方案V1.0-专家评审问题清单.md](file:///Users/yifanwang/workspace/BioAdapt-Core/%E5%BB%BA%E8%AE%BE%E6%96%B9%E6%A1%88V1.0-%E4%B8%93%E5%AE%B6%E8%AF%84%E5%AE%A1%E9%97%AE%E9%A2%98%E6%B8%85%E5%8D%95.md) | V1.0 评审遗留问题，V2.0 修订稿逐条回应 |
| [审查报告-2026-10-20.md](file:///Users/yifanwang/workspace/BioAdapt-Core/%E5%AE%A1%E6%9F%A5%E6%8A%A5%E5%91%8A-2026-10-20.md) | 安全 / 架构 / 性能 / 测试四条线的审查结论与**批次一～六修复记录**，含遗留说明 |

`scripts/serve_workbench.py`、`tests/integration/`、`services/api/tests/` 是三个最可靠的"这段代码怎么用"来源。

只想跑通一次分析的话，直接看 **§6.3**（从头做的完整顺序 + 示例数据的实测数字）和 **§6.4**（换成自己的数据会撞哪四面墙）。

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

当前基线：**1389 passed**（本机约 2.5 分钟）。`testpaths` 已配置为 `packages`、`services`、`workflows`、`tests`，所以在仓库根目录直接跑 `pytest` 就会覆盖四层。

### 5.2 分层 / 单包

```bash
.venv/bin/pytest packages/storage/tests -q      # 95 passed（存储索引）
.venv/bin/pytest services/api/tests -q          # 212 passed：单服务的路由×策略用例
.venv/bin/pytest services/orchestrator -q       # 68 passed：状态机与审批门
.venv/bin/pytest tests/integration -q           # 124 passed：真实 run → 证据卡 → 指标
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

### 6.3 从头做一次单细胞分析（顺序 + 示例数据实测数字）

这一节回答的是"我有一批细胞数据，怎么一路走到一个能交付的结论"。顺序就是 §5.1 的管线顺序，也就是工作台导航条的顺序：`阶段 0 判据 → 数据登记 → 写计划 → 人批计划 → run 与状态机 → 计算链 → 候选集 → 证据卡 → 专家队列 → 复核签字 → 导出`。

**先把一句硬结论放在最前面**：在这份参考部署里，**只有种子 run `run_scrnaseq_01` 这一条路能一路走到证据卡**；从 HTTP/工作台新建的项目走不到计算，四个断点逐条写在 6.4。§12 那句"生产件缺件：……计算执行器"就是这件事，这里只补它对使用者的实际影响。

本节全部数字是 2026-10-21 在本机把 [card_factories.py](file:///Users/yifanwang/workspace/BioAdapt-Core/tests/integration/card_factories.py) 的 `carded_run()`（11 节点全跑）完整执行一遍得到的。

**示例数据是什么**

仓库里**没有任何真实数据文件**（`.h5ad` `.h5` `.mtx` `.csv` `.tsv` `.npz` `.rds` 一个都没有，`.venv` 里也不装 `numpy`/`anndata`/`scanpy`）。唯一的示例是 [dataset.py:196](file:///Users/yifanwang/workspace/BioAdapt-Core/workflows/scrnaseq/tests/dataset.py#L196) 的 `synthetic_dataset(seed=20260501)`，纯 Python 生成：

- **128 cells × 105 genes**，GENCODE 44 / HGNC symbol；2 组 × 8 供者 × 4 群体 = `t_cell 48 / b_cell 32 / monocyte 24 / rare_state 24`，case 64 / control 64
- 预埋缺陷各成对跨组：shallow 8、doublet 8、ambient 8、高线粒体 2（目标占比 0.45）
- 分布刻意横跨全部 6 条 QC 界：library size **1099 / 9332 / 31063**（min/中位/max），detected genes **83 / 94 / 96**，mito fraction **0.034 / 0.0647 / 0.4634**
- 群体是"构造真值"，不是标注列——所以簇号 `c000…c004` 是确定性聚类的输出，`c002`、`c001` 这两个常量是种子硬编码了自己的聚类结果（[driver_factories.py:154](file:///Users/yifanwang/workspace/BioAdapt-Core/tests/integration/driver_factories.py#L154)）

**阶段 0：判据先于人**

24 个判据的默认集是 `version="v0-unconfirmed"`、`confirmed=False`（[thresholds.py:13](file:///Users/yifanwang/workspace/BioAdapt-Core/packages/schemas/src/bioadapt_schemas/thresholds.py#L13)）。确认它的是 `POST /v1/projects/{project_id}/thresholds/confirm`（[routes_data.py:94](file:///Users/yifanwang/workspace/BioAdapt-Core/services/api/src/bioadapt_api/routes_data.py#L94)）：仅 PI、需二次因子、请求体必须**重述全部 24 个字段**，`confirmed` 由平台置位（注册时自带 `confirmed` 直接被拒，[routes_data.py:71](file:///Users/yifanwang/workspace/BioAdapt-Core/services/api/src/bioadapt_api/routes_data.py#L71)）。未确认时计算、起草、排队照跑，只有**签字与导出**两个出口 403（§1.5）。工作台**没有这一页**，只能调 API。种子项目用的是 `ATTESTED_CUTS`（已确认），所以演示能一路签到底——你自己建的项目不行。

**顺序、屏幕与端点**

| # | 动作 | 屏 | 端点 | 关键点 |
| --- | --- | --- | --- | --- |
| 1 | 建项目 | `/` | `POST /v1/projects` | `privacy_tier` 故意无默认值；`created_by` 取自 token，不许手填 |
| 2 | 登记数据 | `/data` | `POST …/assets` → 预签 PUT → `POST …/assets/{id}/complete`（[:139](file:///Users/yifanwang/workspace/BioAdapt-Core/services/api/src/bioadapt_api/routes_data.py#L139)、[:164](file:///Users/yifanwang/workspace/BioAdapt-Core/services/api/src/bioadapt_api/routes_data.py#L164)） | 浏览器算 SHA-256 并预填 `asset_id`/`content_fingerprint`；PUT 不带 bearer；**行只在 complete 成功后存在** |
| 3 | 记录计划 | `/plans` | `POST …/plans` | 这一屏收**整份计划 JSON**，服务端校验器是唯一把关（不做静默规范化）；返回 `plan_sha256` |
| 4 | 起 run | `/projects/{id}` 的 Launch a run | `POST …/runs`（`Idempotency-Key` 头必填，缺即 invalid，[routes_runs.py:79](file:///Users/yifanwang/workspace/BioAdapt-Core/services/api/src/bioadapt_api/routes_runs.py#L79)） | 202 + `run_id = sha256(project\|key\|digest)[:12]`；同键不同体 409 |
| 5 | 批准与推进 | `/approvals` | `POST …/runs/{id}/approvals` → `…/approvals/{aid}/decision` → `POST …/runs/{id}/transitions` | transitions 是**唯一**状态机入口；请求门的人不能自己批；服务账号永远不能批 |
| 6 | 计算 | — | 进程内 driver | 见下表；**HTTP 侧没有执行器**（6.4 断点 1） |
| 7 | 候选集与卡 | `/queue` | `POST …/runs/{id}/cards/draft` | 一簇一个 `CandidateSet`，成员指纹由 `derive_candidate` 复现 |
| 8 | 排队 | `/queue` | `GET …/runs/{id}/cards/queue` | 分数 `impact(0-3) × uncertainty(0-3) + client ask(0/2)`（[queue/page.tsx:141](file:///Users/yifanwang/workspace/BioAdapt-Core/apps/web/app/projects/%5BprojectId%5D/queue/page.tsx#L141)），上限受 §13.4 的每周复核预算约束 |
| 9 | 复核与签字 | `/candidates/{id}` | `POST …/cards/{card_id}/reviews` → `…/sign_off` | 起草或复核过这张卡的人**不能再签它**（§13.1）；标签、ontology 术语、最终状态只能由人写入 |
| 10 | 导出 | `/exports` | `POST …/exports` → `GET …/exports/{id}` | 本仓无导出 worker，队列里的行永远 `QUEUED` |

**计算链：11 个节点与实测输出**（实现在 [steps.py:151](file:///Users/yifanwang/workspace/BioAdapt-Core/workflows/scrnaseq/src/bioadapt_scrnaseq/steps.py#L151) 起，工具版本一律 `1.0.0`）

| 节点 / 工具 | 参数 | 实测 |
| --- | --- | --- |
| `qc` `scrnaseq_qc` | 6 条规则全部 `mark`（不删细胞），`max_flagged_share=0.35` | flagged **18/128**：`shallow_library` 8、`few_genes` 8（这 8 个同时踩两条界）、`deep_library` 8、`mito_high` 2，dropped 0。**`doublet_like`/`ambient_like` 都是 0** ← 下面第一条坑 |
| `normalize` `scrnaseq_normalize` | `target_sum=10000` | log2(CPM+1)，`tolerance=1e-6` |
| `feature_selection` `scrnaseq_feature_selection` | `n_top=50, min_mean 0.5, max_mean 8.0, min_var 0.05, seed 7` | 50 个基因，前 12：CD3D CD3E S100A8 S100A9 MS4A1 CD79A LYZ IL7R CD14 HBB CD79B GATA1 |
| `embedding` `scrnaseq_pca` | `n_components=10, seed 11`（幂迭代，不是 randomized SVD） | evr 0.2236 / 0.1547 / 0.1358 / 0.1270 / 0.0992 / …。**链里没有 UMAP**，平台展示的 2D 图不属于这条管线 |
| `neighbors` `scrnaseq_neighbors` | `k=15, seed 13`，暴力 L2 | 1920 条边 |
| `clustering` `scrnaseq_cluster` | `resolution=1.0, seed 17`，greedy modularity（**不是 Leiden**） | 5 簇：c000 32、c001 24、c002 24、c003 26、c004 22；modularity 0.6856，singleton 0 |
| `markers` `scrnaseq_markers` | `top_n=4`，mean-difference 排名，`rank_only=True` | c002 = HBB 9.605、GATA1 9.003、PTPRC 6.788、MT-CO1 0.394 |
| `pseudobulk_de` `scrnaseq_pseudobulk_de` | 200 permutations，BH within contrast，`unit=donor` | 4 vs 4 供者、unpaired、0 共享供者；**最小 padj = 0.5721 → 0 个基因过 fdr 0.05** |
| `robustness` `scrnaseq_robustness` | `robustness_pbmc`：LOD、subsample 0.5、20 reps | 5 类扰动全 pass：seed cc_ari 1.000、resolution 0.961、leave-one-donor 0.950、subsampling 0.816、feature-subset 0.763；0 个 arm 消失 |
| `coverage` `scrnaseq_coverage` | `knn=20, ratio_min=2.0` | candidate 中位距离 6.625 vs 校准中位 3.918 → **ratio 1.69 < 2.0**：参考覆盖到了，novelty 不成立 |
| `state_axis` `scrnaseq_state_axis` | 轴 CD14 +1 / CD3D −1，`tail_quantile 0.95` | 对 c001：axis_explained 0.937、tail_share 1.000 → c001 是**单核细胞的轴尾状态**，不是一个新类型 |

每个节点的产物都落成一个可复算 sha256 的资产：`counts as_dcc47c37cb36` → `qc_flags as_75527a488d6c` → `expression as_b98a02312b50` → `features as_4d6f476ff569` → `embedding as_90c4e3e58dad` → `graph as_d2c48be73384` → `clusters as_17a40752d23e` → `markers as_af6649b88d49` → `de_table as_30dc4312b3c5` → `stability as_a70ffd9f7679` / `coverage as_0433c5ed9a4d` / `state_axis as_9663191ea68d`。

**最后这张卡长什么样**（焦点候选 c002，`card_eb0d741ac922`）：14 条证据（9 support / 1 contradict / 4 只够 trigger 一眼），`hypothesis_type=unknown`、`unknown_reason=quality_limited`、`composition_check="not run: the plan wires no composition test"`、`cross_modal_status=not_yet_validated`、`thresholds_version=v0-unconfirmed`、`final_status=unknown`、`sign_off=not_signed`。**这就是设计意图**：DE 一个基因都过不了、覆盖度又不支持 novelty，所以链有权拒绝给结论，而不是硬凑一个细胞名。

**三条最容易踩的**

1. **QC 分数不会自动生效。** 种子的计划只把 `counts` 接进 `qc` 节点（[driver_factories.py:168](file:///Users/yifanwang/workspace/BioAdapt-Core/tests/integration/driver_factories.py#L168)），而 `qc` 任务其实期望三个输入槽（`counts` 必连，`doublet_scores`、`ambient_scores` 可空，[implementation.py:444](file:///Users/yifanwang/workspace/BioAdapt-Core/workflows/scrnaseq/src/bioadapt_scrnaseq/implementation.py#L444)）。没连，那两条规则就**根本不评估**——预埋的 8 个 doublet、8 个 ambient 一个都没标出来。链自己会诚实地报 `warn: qc_rules_not_evaluated`（[steps.py:228](file:///Users/yifanwang/workspace/BioAdapt-Core/workflows/scrnaseq/src/bioadapt_scrnaseq/steps.py#L228)）。要真筛，就把 scrublet / decontam 的分数作为 `CellScoreProduct` 资产接成第二、第三个输入。
2. **计划的契约不是建议。** 三条停止条件必须全在（`input_schema_invalid`、`critical_quality_failure`、`unresolved_design_confounding`，[plan.py:12](file:///Users/yifanwang/workspace/BioAdapt-Core/packages/schemas/src/bioadapt_schemas/plan.py#L12)）；凡是输出结论类产物（`candidate_sets`/`evidence_cards`/`de_table_support`/`stability_summary`）的节点必须 `requires_approval=true`（[plan.py:108](file:///Users/yifanwang/workspace/BioAdapt-Core/packages/schemas/src/bioadapt_schemas/plan.py#L108)）；`DesignMatrix` 至少一条 `PASS/FAIL` 的混杂检查；discovery 与 confirmation 分区不许重叠。
3. **预算是三轴独立的**：算力分钟、模型成本 USD、迭代次数，取计划与项目配额的逐轴较小值，任一轴耗尽即 `BUDGET_EXCEEDED`（§9.7）。种子用的是 1 号种子：`max_iterations=1`、`max_compute_minutes=180`、`max_token_cost_usd=5.0`。

**自己复现上面这些数字**

```bash
cd /Users/yifanwang/workspace/BioAdapt-Core && .venv/bin/python - <<'PY'
import sys
sys.path[:0] = ["tests/integration", "workflows/scrnaseq/tests"]
import card_factories as CF, driver_factories as F
made = CF.carded_run()                      # 11 节点全跑，并把 5 张卡起草入库
qc = made.measured("qc_flags")
print("cells", len(qc.rows), "flagged", qc.marked_cells, [(r.name, r.flagged) for r in qc.rules])
print("clusters", {k: len(v) for k, v in made.measured("clusters").cells_by_cluster().items()})
print("min padj", min(r.padj for r in made.measured("de_table").rows))
card = made.draft(F.CANDIDATE_CLUSTER).card
print(card.card_id, card.final_status, card.unknown_reason)
PY
```

### 6.4 换成你自己的数据：四个断点（按硬度排）

想把手上的一管数据跑成结论，下面四条都会挡路。前两条 README 别处没写清，值得先读：

1. **HTTP 起的 run 没有人执行。** `InMemoryQueue` 的注释原话："Nothing here starts a run: the reference build has no worker, so an admitted run stays where its engine left it"（[deps.py:72](file:///Users/yifanwang/workspace/BioAdapt-Core/services/api/src/bioadapt_api/deps.py#L72)）。11 节点那条链只在进程内跑（种子的 driver、pytest），API 只负责受理、记账、状态机。
2. **`tool_registry_snapshot` 没地方查，工具也没有注册入口。** `POST …/runs` 要求这个字段（[routes_runs.py:40](file:///Users/yifanwang/workspace/BioAdapt-Core/services/api/src/bioadapt_api/routes_runs.py#L40)），但 `/openapi.json` 里唯一和工具相关的路由只有 ad-hoc 授予三条（[routes_tools.py:90](file:///Users/yifanwang/workspace/BioAdapt-Core/services/api/src/bioadapt_api/routes_tools.py#L90)）；`Action.TOOL_REGISTER` 在策略层存在，没有任何 HTTP 消费者。执行侧还会因快照漂移直接 `POLICY_BLOCKED`。
3. **对象平面到不了。** 预签 URL 的 host 写死 `https://object.internal/{key}?…`（[objectstore.py:108](file:///Users/yifanwang/workspace/BioAdapt-Core/packages/storage/src/bioadapt_storage/objectstore.py#L108)），浏览器 PUT 不进去，于是 `complete` 永远 409 `nothing has been uploaded`。要接 MinIO/S3。
4. **格式对不上——转换器已经补到落盘，注册那一步还在人身上。** 计算侧只认 `format == "json"`（平台自己的 product codec），h5ad 行会被拒（[runner.py:69](file:///Users/yifanwang/workspace/BioAdapt-Core/workflows/scrnaseq/src/bioadapt_scrnaseq/runner.py#L69)、[:744](file:///Users/yifanwang/workspace/BioAdapt-Core/workflows/scrnaseq/src/bioadapt_scrnaseq/runner.py#L744)）。入口现在是 [scripts/convert_h5ad.py:73](file:///Users/yifanwang/workspace/BioAdapt-Core/scripts/convert_h5ad.py#L73)：`read_h5ad()` → `ExpressionProduct` → [admit()/write_admission()](file:///Users/yifanwang/workspace/BioAdapt-Core/workflows/scrnaseq/src/bioadapt_scrnaseq/admission.py#L126)，吐 `as_xxxx.json`（canonical JSON 的 `expression_v1` 资产）加 `as_xxxx.manifest.json`，后者 `asset` 那个对象就是 `POST /v1/projects/{p}/assets` 的请求体。剩下两件：`anndata`/`numpy`/`scanpy` 仍不在 `.venv` 里（装它要另批）；CLI 只往目录里写文件，因为参考部署的 index 与对象平面活在同一个进程的内存里，第二个进程插不进正在跑的 8000，而断点 3 的 `object.internal` 还堵着那次 PUT。

这条接缝本身是有测试的，而且不靠科学栈——适配器是 duck-typed 的：[test_h5ad_to_run.py:54](file:///Users/yifanwang/workspace/BioAdapt-Core/tests/integration/test_h5ad_to_run.py#L54) 把一个 AnnData 形状的对象转成资产、按上传路由的顺序登记（先写对象、再记行），跑完 8 个计算节点（qc → pseudobulk_de），`qc_flags`/`expression`/`clusters` 三个资产的 `file_sha256` 与"同一批细胞直接以 product 进门"的那次 run **逐字节相同**。落盘那一半另有 [test_admission.py](file:///Users/yifanwang/workspace/BioAdapt-Core/workflows/scrnaseq/tests/test_admission.py)（11 条）。没装 anndata 的机器上 CLI 不崩：`--check-backend` 只报在不在，真转换时打印 `needs_input [optional_backend_unavailable] …` 并以 2 退出。obs 列名不一样就用 `--obs-specimen-id`/`--obs-donor-id`/`--obs-group` 指过去；`--transform` 与 `--counts-layer` 是声明而不是标签，读进来的状态会被要 counts 的节点拒掉。断点 1 与 2 不解决的话，这条路仍然只能到"资产入库"，跑不出产物。

换组织时别忘了：`qc_pbmc_reference` 那 6 条判据是**血液**的界，参考实现自己在 spec 里写了"高线粒体组织（心、肌肉、肿瘤活检）需要规则覆盖而不是沿用血液界"。

---

## 7. 直接调用 API

### 7.1 通用约定

- **认证**：`Authorization: Bearer <token>`。HS256 compact JWS，校验 `iss`/`aud`/`exp`；签名不对 401，`iss` 不受信 401，过期 401 `the bearer token has expired`。
- **角色**：`platform_admin`、`principal_investigator`、`analyst`、`domain_expert`、`knowledge_curator`、`evaluation_lead`、`compliance_officer`、`service_agent`。三条与身份相关的硬规则（[access.py:154](file:///Users/yifanwang/workspace/BioAdapt-Core/packages/policies/src/bioadapt_policies/access.py#L154) 起）：
  - **人类专属动作**（服务账号一律 403）：`card:sign_off`、`egress:approve`、`approval:decide`、`approval:delegate`、`tool:register`、`tool:grant_adhoc`、`tool:promote`、`evaluation:freeze_truth`、`iam:grant_role`、`policy:write`、`case_library:write`。这是 §11.6 控制 6——权限与审批状态只能由人的动作触达。
  - **需要第二因子**（claims 的 `amr` 必须含 MFA 方式）：`egress:approve`、`tool:grant_adhoc`、`tool:promote`、`asset:delete`、`iam:grant_role`、`policy:write`、`evaluation:freeze_truth`。`tool:grant_adhoc` 只有 `principal_investigator` 角色持有（§9.6 的一次性审批是 PI 的票，`platform_admin` 也没有它）。
  - **职责分离**：请求审批门的人不能自己批它；出境请求者也不能自己批；发放 ad-hoc 授予的人也不能是提交那个工具的人（`tool:grant_adhoc` 必须带 `requesting_subject_id`，缺它直接拒绝）。
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
  | 403 | `policy_blocked` / `authorization_denied` | 角色不授予该动作，或越出项目作用域；结论出口（签字、导出）也会因项目阈值未经确认而拒（§1.5，见 7.2） |
  | 403 | `policy_blocked` / `approval_refused` | 门已决、或不属于当前问责人 |
  | 404 | `not_found` | code 形如 `resource_not_found` / `trace_not_found`；资源不存在或不在本项目内 |
  | 409 | `conflict` | 幂等键复用、唯一约束、`illegal_transition` |
  | 409 | `conflict` / `concurrent_write` | 审批写输给并发，`retryable: true`，重读后重放 |
  | 422 | `gate_blocked` / `card_gate_blocked` | 证据卡门禁：数值溯源不合规，或 `final_status` 超过了天花板 |

- **分页**：列表统一 `{items, limit, offset, next_offset}`，`limit` 默认 50，`next_offset` 为 `null` 表示到底。
- **幂等**：`POST /v1/projects/{p}/runs` 与 `POST …/exports` 必须带 `Idempotency-Key`；同键同体重放返回 200 + `Idempotency-Replayed: true`，同键不同体返回 409。状态机转换按 `attempt`/`approval_id` 幂等，重放同样返回 200。审批门按 `(run, kind, subject digest, node)` 定位，重复申请同一个门同样返回 200 + `Idempotency-Replayed: true`（见 7.3）。ad-hoc 授予按 `(gate, tool, version, node)` 定位（`grant_id` 由这四者派生），同一扇门重放同一个 spec 返回 200 + 该头，重放不同 spec 返回 409（见 7.5）。
- **文档端点**：`/docs` 与 `/redoc` 被关闭（返回 404），`/openapi.json` 可用（`title: BioAdapt API, version: 1.0`；折叠后 44 个路径、50 个操作：49 条 `/v1` + 1 条 `/v0` 别名）。
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

### 7.5 写操作：ad-hoc 工具的一次性授予（申请 → 决定 → 发放）

§9.6 的 ad-hoc 通道是平台唯一让未注册代码进计算容器的入口，所以它被拆成三步、缺一不可，而 `tool_grant` 表**只有**第 3 步会写。以下 curl 与响应都是对本仓参考实现实测所得（示例里先把 run 指向一个含 `project_adhoc` 节点的 plan 版本）：

```bash
# 1) analyst 申请一扇 adhoc_tool 门，绑定 plan 里那个 ad-hoc 节点
curl -s -X POST -H "Authorization: Bearer $AN" -H "Content-Type: application/json" \
  -d '{"kind":"adhoc_tool","node_id":"adhoc_score","assigned_to":"user_pi_01"}' \
  $API/v1/projects/prj_scrnaseq/runs/run_scrnaseq_01/approvals        # → 201，approval_id=apr_9248b45bdc25

# 2) PI 答这扇门
curl -s -X POST -H "Authorization: Bearer $TOK" -H "Content-Type: application/json" \
  -d '{"approved":true,"reason":"sandboxed, one submission, results stay unregistered"}' \
  $API/v1/projects/prj_scrnaseq/approvals/apr_9248b45bdc25/decision   # → 200，state=approved，decided_by=user_pi_01

# 3) 答门的这位 PI（带 amr）发放授予：只给 approval_id + spec，其余由服务端构造
curl -s -X POST -H "Authorization: Bearer $TOK" -H "Content-Type: application/json" \
  -d '{"approval_id":"apr_9248b45bdc25","spec":{…完整 ToolSpec…},"valid_for_minutes":120}' \
  $API/v1/projects/prj_scrnaseq/runs/run_scrnaseq_01/nodes/adhoc_score/adhoc-grants   # → 201
```

```json
{"grant_id": "grn_1c6eed5f2c41", "project_id": "prj_scrnaseq", "run_id": "run_scrnaseq_01",
 "node_id": "adhoc_score", "plan_id": "plan_scrnaseq", "plan_version": "6",
 "approval_id": "apr_9248b45bdc25", "tool_name": "project_qc_probe", "tool_version": "0.1.0",
 "spec_sha256": "0635cce1f39385ce3bc8dcbf47e46a596df6de69b412f355c8d36f2c8c779ebf",
 "requested_by": "user_analyst_01", "approver_id": "user_pi_01",
 "reason": "sandboxed, one submission, results stay unregistered",
 "approved_at": "2026-06-01T09:00:00Z", "expires_at": "2026-06-01T11:00:00Z",
 "max_uses": 1, "spent_at": null, "live": true}
```

响应里的 `spec` 是那一行存的完整提交（`spec_sha256` 就是它的 digest，服务端自己算）。谁走不到第 3 步：

| 情况 | 实测响应 |
| --- | --- |
| 提交工具的 analyst 来发放 | 403 `authorization_denied`，文案同时给出 `no role of ['analyst'] grants tool:grant_adhoc` 与 `the subject that submitted an ad-hoc tool may not grant it` |
| PI，但 token 的 `amr` 为空 | 403 `tool:grant_adhoc requires a second factor` |
| 服务账号（`service_agent`） | 403 `must be performed by a human` |
| 答门的是别的 PI | 403 `this grant must be issued by user_pi_01, who decided apr_…` |
| 请求体自己写 `approver_id`/`requested_by`/`project_id`/`expires_at`/`spent_at` | 400 `invalid_request`，`field` 就是那个字段名（契约是 `extra="forbid"`）；这些值只能来自登录态、门本身和注入时钟 |
| 门还 pending | 409 `a grant follows a decision, it does not stand in for one` |
| 门是 `node`/`plan` 类、或绑定的是别的 run/节点 | 400，后者文案点名 `gate … is about run_scrnaseq_01/adhoc_score, not run_scrnaseq_01/cluster` |
| spec 装不进笼子 | 400 `field=spec`，例如 `cpu 64 exceeds ad-hoc cap 8`。§9.6 的笼子（`DEFAULT_ADHOC_LIMITS`：8 核、32 GiB、240 分钟、无 GPU、闭网、无出口、`retry_limit=0`、`failure_policy=halt`）在**发放时**重算一遍，部署可通过 `ApiState.adhoc_limits` 收紧 |
| 节点不是 `project_adhoc`，或工具名/版本与 plan 里那条不符 | 400，文案给出 plan 实际 planned as 的那个工具 |

同一扇门重放同一份 spec → 200 + `Idempotency-Replayed: true`，台账仍只有一行；重放不同 spec → 409（第二次提交需要第二次决定）。读接口是 `GET …/runs/{id}/adhoc-grants`（分页，`?live_only=true` 滤掉已花与过期）和 `GET …/adhoc-grants/{grant_id}`，两者都只要 `approval:read`，跨项目读 404。审计写两行同一个 `resource_id`：`enforce` 的判定行与发放行（后者 details 带 `spec_sha256`、`requested_by`、`expires_at`）。

一次性真正落在哪儿：runner 在 checkpoint 之后、执行之前问两道，**两道都得点头**（[runner.py:639](file:///Users/yifanwang/workspace/BioAdapt-Core/workflows/scrnaseq/src/bioadapt_scrnaseq/runner.py#L639)）。先是进程内的注册表：这份权限确实被 admit 进了本进程、还在窗口内、还没被花掉。再是台账：`find_tool_grant(run, node)` 必须**查得到这一行**，然后对它做 compare-and-set —— `UPDATE tool_grant SET spent_at = ? WHERE spent_at is null AND expires_at > ?`（[catalog.py:841](file:///Users/yifanwang/workspace/BioAdapt-Core/packages/storage/src/bioadapt_storage/catalog.py#L841)）。查不到行、或抢不到行，都是 `POLICY_BLOCKED`（前者文案点名 `the ledger has no grant row for …`），一行产物都不写、`compute_minutes` 仍是 0。所以进程内 `ToolRegistry.grant()` 那条带外通道**到不了执行**：注册表里凭空多出来的权限买不到一次计算，"部署只用 `hydrate()` 构造注册表"从文档要求变成了引擎强制的规则。引擎侧的注册表由 `list_tool_grants(live_only=True)` 水合（[driver_factories.py:425](file:///Users/yifanwang/workspace/BioAdapt-Core/tests/integration/driver_factories.py#L425) 是这条流程的可读实现）：已花或已过期的行不会被重新 admit，重启也就带不回一个已经用完的权限。

快照算的是什么：`ToolRegistry.snapshot_id()`（[registry.py:318](file:///Users/yifanwang/workspace/BioAdapt-Core/packages/tool_registry/src/bioadapt_tool_registry/registry.py#L318)）只覆盖**已注册**的 ToolSpec 集合与禁用列表，ad-hoc 授予与它的 spec 不进快照。原因是发放路由的路径参数就是 run 与 node，门也是 run 级的——授予必然发生在 run 的 manifest 写完**之后**，把授予算进快照等于让 §4.1 的 preflight 恰好拒掉它本该放行的那个 run。ad-hoc 那一格的可复现举证改由台账行承担：`grant_id` 由 `(approval_id, tool, version, node)` 派生，`spec_sha256` 钉住引擎从行里重新 admit 的字节，还原时还会重跑一遍笼子。方案 §721 说的"每次正式运行记录 ToolSpec 集合版本"指的也正是部署的那套已注册工具；晋升（`promote()`）会让工具进这套，所以晋升确实会改变快照。

### 7.6 各写端点必填字段

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
| `POST …/runs/{id}/nodes/{node}/adhoc-grants` | `approval_id`（一扇已答的 `adhoc_tool` 门）`spec`（完整 `ToolSpec`）；可选 `reason` `valid_for_minutes`（只能缩短）。需 PI + 人类 + `amr`，且必须与门上的 `decided_by` 是同一个人 |
| `GET …/runs/{id}/adhoc-grants`、`GET …/adhoc-grants/{grant_id}` | 需 `approval:read`；列表支持 `limit` `offset` `live_only` |
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
    adhoc_grant_ttl=timedelta(hours=24),  # §9.6：一份 ad-hoc 授予多久之内还可执行，路由只会缩短它
    adhoc_limits=DEFAULT_ADHOC_LIMITS,    # §9.6 的笼子；发放时按它逐项复核提交物
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
- **`tool_grant` 是 ad-hoc 通道唯一的持久权限记录**（§9.6）：一行 = 一份已答的 `adhoc_tool` 门 + 它买下的那一次执行。`grant_id` 由 `(approval_id, tool_name, tool_version, node_id)` 派生，`spec_sha256` 必须等于 `spec` 那串字节的 digest（写库时校验，见 [orchestration.py 的 `_one_time`](file:///Users/yifanwang/workspace/BioAdapt-Core/packages/schemas/src/bioadapt_schemas/orchestration.py)），`spent_at` 只由 [`spend_tool_grant`](file:///Users/yifanwang/workspace/BioAdapt-Core/packages/storage/src/bioadapt_storage/catalog.py#L841) 的 compare-and-set 写。跨项目写被 scope 守卫拒绝，读只认本项目作用域。
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
| 在工作台点了 Launch a run，run 一直停在原地 | API 侧没有计算执行器，`InMemoryQueue` 的注释原话是 "Nothing here starts a run"（[deps.py:72](file:///Users/yifanwang/workspace/BioAdapt-Core/services/api/src/bioadapt_api/deps.py#L72)）。11 节点那条链只在进程内由 driver 跑，见 §6.3 |
| 新项目填不出 `tool_registry_snapshot` | 没有任何路由能列出注册表，`Action.TOOL_REGISTER` 在策略层存在但没有 HTTP 消费者（§6.4 断点 2） |
| 每次读证据卡都重新取产物、重算 sha256、重解析 | 这是 §5.9 的完整性控制本身（审查报告 P4，**有意不缓存**） |
| `GET …/runs/{id}/events`（SSE）存在但没人用 | 批次二删掉了前端 `EventSource` 客户端，本仓已无消费者（P7 因此不修） |
| `GET …/traces/{id}` 常 404 | 只有真实模型调用才会写 trace（`inv_…`）。种子 run 的卡由确定性 driver 产出，不含模型调用 |
| 工作台身份面板说 token "past its exp by this clock" | 面板拿浏览器真实时钟比较 `exp`，而参考部署的 `state.clock()` 是冻结在 2026-06-01 的（[deps.py:221](file:///Users/yifanwang/workspace/BioAdapt-Core/services/api/src/bioadapt_api/deps.py#L221)）。API 仍然接受它，两边说法不同是时钟不统一，不是 token 失效 |
| 批次六的 ad-hoc 授予三条路由在**正在跑的**开发实例上是 404 | 那个 `serve_workbench.py` 起来于批次三之前，改完 API 需要重启它才会生效；重启会丢掉浏览器写进去的演示行，所以文档里的实测响应取自同一份装配的代码内 TestClient |
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
- **ad-hoc 工具**只允许在非特权容器、只读输入、无网络、资源与时长配额下运行，且需要一次性 PI 审批。闭环已落地（批次六）：`tool:grant_adhoc` 只有 PI 角色持有、要求二次因子、要求发放人 ≠ 提交人，且必须由答那扇 `adhoc_tool` 门的人发出；笼子检查在发放时重算，`tool_grant` 行是一次性台账，引擎执行前用 CAS 抢它（见 7.5）。注册表只能从库水合，已花/过期的行带不回来；执行前台账行**必须存在**，进程内自造的权限买不到一次计算（批次七把它从文档约束变成引擎强制）。
- **不允许大模型自由生成任意 Shell 并直接在生产计算节点执行。**
- **密钥不写入 Prompt、代码仓库、运行参数或日志。** `.workbench/` 与其中的 token 永不提交。
- **注入防御**在 `tests/redteam/` 有对应门禁用例集；改 egress、prompt 组装、工具绑定后必须重跑。

---

## 12. 已知未完成项

- **ad-hoc 这条线只剩一个缺口**（H1 由批次六闭合，批次七又关掉「授予与快照的先后矛盾」与「带外通道」两条）：
  1. **工作台没有 ad-hoc 授予页**：`POST …/adhoc-grants` 只有 API 入口，PI 在界面上看不到「哪扇门已答、还没有发放」，也发不出这一枪。与下面批次五遗留 2 的阈值确认页同类。

  批次七关掉的两条各自这样落地（见 7.5）：快照只覆盖已注册工具集，发放必然晚于 manifest，先后矛盾因此不复存在，测试里的 `pin_registry_snapshot()` 裸 SQL 重钉已删除；执行前台账行必须存在，进程内自造的权限买不到一次计算。`ToolRegistry.grant()` 仍然公开给单测与红队用例测准入逻辑，但它再也走不到一次真实的计算。
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

**Q：我想从头跑自己的一批单细胞数据，照哪节做？**
§6.3 给顺序与示例数据的实测数字，§6.4 给现在会撞的四面墙（执行器、快照、对象平面、h5ad 格式）。一句话结论：这份部署里只有种子 run 那一条路一路走到证据卡。

**Q：3100 上每个页面都 500，报 `Cannot find module './682.js'`？**
`.next` 里的编译产物与源码不同步（改过 `app/`、切过分支之后常见）。`rm -rf apps/web/.next` 再 `npm run dev -- -p 3100`。

**Q：8000 上的 API 少了几条端点？**
它是批次五/六之前起来的旧进程（§10 最后一行）。重启 `serve_workbench.py` 才会有 `thresholds/confirm` 与 ad-hoc 授予三条路由；重启会退回种子状态，丢掉内存里演示产生的行。
