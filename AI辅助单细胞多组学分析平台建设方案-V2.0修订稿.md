# AI 辅助单细胞多组学分析平台建设方案 V2.0（评审修订稿）

**修订依据**：`AI辅助单细胞多组学分析平台建设方案.docx`（V1.0，2026-10-19）与《建设方案V1.0-专家评审问题清单》（97 条问题，编号 `Q-x-nn`）。
**本稿性质**：立项评审与实施准备用修订稿。V1.0 的科学原则与架构边界保持不变，本稿补入可操作定义、规模假设、量化验收与合规矩阵。
**标注约定**：
- 【新增】本稿新增章节；【修订】对 V1.0 内容的实质改动；括号内 `→ Q-xx` 表示该改动对应的评审问题。
- **【待确认】**：本稿给出的具体数值均为可讨论的默认建议，需在阶段 0 由团队实测或决策后替换。所有待确认项集中在附录 C，不要在确认前把数值写进代码常量。

版本：V2.0-draft ｜ 编制日期：2026-10-19 ｜ 用途：内部立项、产品设计、工程实施与技术评审

---

## 目录

1. 执行摘要、目标与术语定义
2. 产品定位、用户与范围边界
3. 总体架构与关键设计原则
4. 技术选型、部署拓扑、**规模假设与模型策略**
5. 数据模型与接口契约（**标识符、血缘、存储真值**）
6. 专业知识库与证据管理（**吞吐测算、许可、评测隔离**）
7. 特殊细胞发现与注释工作流（**候选分型、稳健性协议、判定规则**）
8. 多组学模态适配策略（**上游边界、模态 QC 指标、对齐规范**）
9. Agent 编排与 API 设计（**工具晋升通道、接口非功能约束**）
10. 验证体系与质量门禁（**真值与泄漏隔离、指标字典**）
11. 混合部署、安全与合规（**法规与合同合规矩阵**）
12. 分阶段开发路线与验收（**go/no-go 决策门**）
13. 团队分工、运维与风险管理（**审核产能负载模型、ADR**）
14. 首期 MVP 清单与**量化完成定义**
附录 A. 契约示例（更新）｜ 附录 B. 参考资料 ｜ 附录 C. 待确认默认值与待决架构决策

---

## 0. 本稿相对 V1.0 的修订对照表

| V1.0 章节 | 修订 | 解决的问题 |
|---|---|---|
| §1.2 目标 | 新增 §1.5 术语与判定定义表；"特殊细胞"改为五类候选分型 | Q-C-01, Q-A-05, Q-B-05 |
| §2.2 范围 | 明确首期输入边界为"已定量矩阵"，并定义上游交接契约 | Q-A-01 |
| §2.2/§6 | 新增 `usage_scope`：客户数据/案例能否跨项目复用成为契约字段 | Q-H-04 |
| §3.3 边界 | 新增状态机与 Nextflow 的职责真值划分 | Q-G-03, Q-D-12 |
| §4.1/§4.3 | 新增 §4.4 规模与非功能假设、§4.5 模型选型与推理策略、§4.6 离线分发 | Q-G-01/02, Q-D-01, Q-H-05 |
| §5.1 | `Sample` 拆为 Specimen/Library；`Cell` 拆出配对实体；`DataAsset.parent_asset_id` 改为血缘表 | Q-A-12/14, Q-F-02 |
| §5.1/§5.4 | `EvidenceCard` 主键从 `cluster_id` 改为 `candidate_id`（细胞集合指纹）；字段增加 `field_provenance`、发现/确认分离 | Q-A-06, Q-B-01, Q-D-10 |
| §5.2 | 新增 §5.6 标识符与两级指纹、§5.7 标签存储真值、§5.8 数据分级下沉到资产级 | Q-F-01/03, Q-A-13 |
| §6 | 新增 §6.6 吞吐与人力测算、§6.7 许可过滤、§6.8 评测隔离与共源证据标注、§6.9 分歧与更新传播 | Q-E-01/02/05/06/08/09/10 |
| §7 | 新增 §7.5 稳健性最小协议、§7.6 新颖性与覆盖度评估、§7.7 QC 策略表、§7.8 Marker 与支持判据 | Q-A-02/04/05/07/08, Q-B-01/05 |
| §8 | 新增 §8.0 各模态上游边界、§8.3 模态 QC 指标定义、§8.4 对齐规范 | Q-A-14, Q-C-03 |
| §9.4 | 新增 §9.6 工具晋升通道（临时代码沙箱） | Q-D-04, Q-J-02 |
| §9.3 | 新增 §9.7 接口非功能与安全约束（认证、幂等、异步、错误模型、分页、导出） | Q-F-05/06/07/09 |
| §10 | 新增 §10.5 真值与泄漏隔离、§10.6 指标字典、§10.7 盲评协议、§10.8 分级故障注入验收 | Q-I-01…10, Q-B-07/08, Q-D-08 |
| §11 | 新增 §11.4 法规与合同合规矩阵、§11.5 字段级出境 allowlist、§11.6 注入防护控制清单、§11.7 删除与数据地图 | Q-H-01…12, Q-D-07, Q-F-10/11 |
| §12 | 新增 §12.3 go/no-go 决策门；修正阶段 2 与阶段 4 的依赖矛盾（MVP 降级证据源） | Q-J-04/05 |
| §13 | 新增 §13.4 专家审核产能负载模型与候选卡队列上限；§13.5 ADR 登记 | Q-C-04/05, Q-J-06, Q-G-03 |
| §14 | 新增 §14.4 MVP 量化完成定义（按候选类型分别设定） | Q-C-01, Q-J-09 |

---

## 1. 执行摘要、建设目标与术语定义

### 1.1 方案摘要（承接 V1.0）

建设一套以"证据驱动的特殊细胞发现"为核心能力的分析平台：AI 负责理解项目、检索证据、提出方案并组织工具；确定性生物信息学工具负责计算；独立验证层负责质量门禁；领域专家对高风险科学结论进行审核。本地数据面 + 云端受控控制面的混合部署。

V2.0 的增量不是加功能，而是**把 V1.0 的原则变成可判定、可测、可验收的规则**：五类候选的可操作定义、最小稳健性协议、发现/确认分离、规模假设、审核产能模型、指标字典与合规矩阵。

### 1.2 总体目标（修订）

V1.0 的六项目标保留，其中三条在本稿被具体化：

1. 以**五类候选**（§1.5 与 §7.2）为核心能力，而非笼统的"特殊细胞"。→ Q-C-01
2. 每项关键注释具备证据卡、来源、方法版本、反对证据、**独立确认状态**与审核状态。→ Q-B-01
3. 混合部署的合规判断落到**法规 × 场景 × 控制点**矩阵与字段级出境清单（§11.4/§11.5）。→ Q-H-01/02

### 1.3 成功原则（保留 V1.0 六条，补充两条）

| 新增原则 | 设计要求 |
|---|---|
| 发现与确认分离 | 从数据中搜索出的候选，不得仅用同一数据同一集合的统计作为支持证据；无独立确认时结论上限为 `provisional`。→ Q-B-01 |
| 产能即约束 | 人工审核吞吐是系统吞吐上限；候选数量、优先级与审核负载是设计输入，不是运行期意外。→ Q-C-04 |

### 1.4 产品定位

面向生物信息学服务团队的 AI 分析决策与执行工作台：把项目背景、成熟工具、公开参考与经专家审核的历史案例连接起来，重点提升特殊细胞问题的可解释性、可追溯性与分析效率。

**定位的边界条件（新增，回答 build-vs-buy）**：平台的价值不在"能跑流程"（分析师用通用助手 + 现成脚本也能跑），而在三件通用助手给不了的事：① 强制的发现/确认分离与技术伪影排查闭环；② 不可覆盖的 provenance 与可审计交付；③ 经审核、带适用限制的知识与案例复用。若阶段 2 试点无法证明这三点带来可测收益，方案应退回"通用助手 + 规范模板"的轻量路线（见 §12.3 阶段 2 no-go 条件）。→ Q-J-01

### 1.5 【新增】术语与判定定义表

**规则**：本表是候选发现、质量门禁和验收标准的唯一定义来源；任何模块不得使用未在此表定义的判定词。阈值默认值均标【待确认】。

| 术语 | 定义（可判定形式） | 默认阈值【待确认】 | 用途 |
|---|---|---|---|
| **候选集合（CandidateSet）** | 一组有序、规范化的细胞标识集合（`sample_id:barcode`），绑定产生它的 `run_id`；不等于聚类标签名 | — | 全局主键，见 §5.6 |
| **稀有群** | 占该 run 有效细胞的比例低于 `θ_rare`，**且** 绝对细胞数 ≥ `n_min`，**且** 覆盖 ≥ `d_min` 个供体 | θ_rare = 1%（或按项目预设）；n_min = 50；d_min = 2 | 触发 C3；低于 n_min/d_min 只进"待观察"清单，不进候选 |
| **稳定（robust）** | 在 §7.5 最小必测扰动集上，与原始集合的 chance-corrected ARI ≥ `ari_min` **且** Jaccard ≥ `jac_min` 的扰动比例 ≥ `pass_ratio` | ari_min=0.6；jac_min=0.5；pass_ratio=0.6（5 类扰动中 ≥3 类通过） | 触发信号与证据卡 `stability_summary` |
| **低置信** | 参考映射最佳分数低于该工具在校准集上的第 `q` 分位，或 top1–top2 margin 低于 `m_min`；**不使用模型分数的绝对值作为唯一判据** | q=10%；m_min=0.15 | 触发 C1/C2 |
| **参考未覆盖** | 候选集合到参考的 kNN 距离分布显著右移（相对校准集 `p < 0.05` 或距离中位数比 > `r_min`）；这是"参考局限"，**不等于新颖** | k=20；r_min=2.0 | §7.6，与新颖性分开记录 |
| **新颖候选** | 同时满足：参考未覆盖 **且** 通过 §7.5 稳定 **且** 排除技术伪影后仍存在 **且** 不与已知连续状态轴重合（§7.6） | 四项全满足 | 允许进入专家队列，结论仍受 §1.5 `supported` 规则约束 |
| **伪影排除** | 双细胞、环境 RNA、低质量、应激/周期、批次特异性五类风险均有独立工具输出且未触发拦截 | 见 §7.7 各工具阈值 | C1–C5 通用前置 |
| **独立确认（confirmation）** | 支持证据来自：留出供体 / 独立重复样本 / 独立模态 / 独立数据集之一，且不是发现该候选所用的同一细胞集合 | 至少 1 条 | `supported` 的必要条件 |
| **证据充分（supported）** | §7.8 支持判据全部满足 **且** 存在独立确认 **且** 无未解决反证 **且** 专家已签署 | 见 §7.8 | `final_status` 上限 |
| **未知（unknown）** | 系统明确不给出身份假设；`hypothesis_type=unknown` 且必填 `unknown_reason`（参考未覆盖/证据不足/冲突未解/质量受限） | — | 允许的终态之一，非失败 |
| **可复现** | 三级：`byte`（文件 hash 一致）/ `numeric`（关键数值相对误差 ≤ `tol`，标签一致率 ≥ `lab_min`）/ `conclusion`（候选集合与 final_status 一致） | tol=1e-6；lab_min=0.99 | L2 指标，见 §10.6 |
| **返工** | `同 run 内重跑`（输入或参数错误）与 `结论修订`（标签/状态改变）分别计数，不合并 | — | §10.6 指标口径 |
| **分析耗时** | 墙钟时间，拆为 `platform_compute` / `queue_wait` / `human_review_wait` 三段，验收不得只看总时长 | — | §10.6 指标口径 |

---

## 2. 产品定位、用户与范围边界

### 2.1 目标用户（保留 V1.0 角色表，新增两处）

- 新增角色属性 **审核负载（review_load）**：领域专家的每周可投入小时数进入系统配置，是排期与候选优先级的输入（§13.4）。→ Q-C-04
- 客户/委托方：新增"其可见内容受 §11.5 字段级出境清单与 `usage_scope` 双重约束"（图表与摘要也可能构成可识别信息）。→ Q-H-03

### 2.2 首期纳入范围（修订）

| 项 | V1.0 | V2.0 修订 |
|---|---|---|
| 输入数据 | 标准表达矩阵、AnnData/h5ad、样本与实验设计元数据、配对 RNA+ATAC 标准化输入 | **明确首期只接已定量矩阵**（含 empty droplet 全集或说明过滤状态），上游 FASTQ→矩阵定量由现有实验室/生信交付流程完成，但其**软件名+版本+参考版本+barcode 规范**必须作为 DataAsset 元数据登记，否则 Preflight 阻止 | 
| 上游定量 | 未提及 | 列为阶段 3 之后的可选扩展，本期不做（写入 ADR-05） |
| 单核 RNA-seq | 未区分 | 首期**不承诺** snRNA-seq；如需，须单独 QC 策略与评测集（§7.7） |
| 规模 | 未声明 | 见 §4.4 单项目规模上限，超限明确拒绝并要求分片 |

新增输入契约字段（`DataAsset` 层面）：`upstream_pipeline{name,version,reference_build,reference_annotation_source}`、`filtering_state{none|barcode_filtered|gene_filtered|unknown}`、`usage_scope`。→ Q-A-01, Q-H-04

### 2.3 首期不做或延后（保留，新增三条）

- 不做视觉模型读图（UMAP/切片图像判断）；图像判断交给人，模型仅接收表格化统计摘要。→ Q-D-01
- 不做无审批的自动知识写回（V1.0 已列），并新增：**模型生成的 claim 未经人工复核不得进入检索命中集**。→ Q-E-01, Q-E-10
- 不做"一个潜在空间统一所有模态"的方案（V1.0 已列），并新增：**整合得分不得作为候选触发信号的唯一来源**。→ Q-G-05, Q-A-11

---

## 3. 总体架构与关键设计原则

### 3.1–3.2 分层架构（保留 V1.0 五层 A–E）

### 3.3 核心边界（保留四条，新增两条）

- 新增：**编排状态真值唯一**。业务状态机（审批、暂停、失败分类、恢复）是唯一状态真值；Nextflow 作为执行后端，其 `work dir`/cache/重试不得被解释为业务状态。回调必须幂等并以 `run_id + node_id + attempt` 关联，状态机与执行后端的差异由对账任务修复而非人工猜测。→ Q-G-03（ADR-01）
- 新增：**数值字段不得由模型产出**。证据卡中所有数值（效应量、比例、稳健性、细胞数）只允许来自工具输出或人工修订，模型只能引用与组织文本；每个字段带 `field_provenance`（`tool|retrieval|model|human`）与来源对象 ID，L5 校验数值一致性。→ Q-D-10

### 3.4 【新增】未决架构决策（ADR 摘要）

见附录 C：ADR-01 编排真值、ADR-02 标签存储位置（对象存储 vs Postgres 为真值）、ADR-03 单体拆分时点、ADR-04 上游定量是否在平台内、ADR-05 项目级临时工具是否默认开启、ADR-06 本地模型选型与硬件。

---

## 4. 技术选型、部署拓扑、规模假设与模型策略

### 4.1 推荐技术栈（保留 V1.0 表，修订四项）

| 模块 | V2.0 修订 |
|---|---|
| 工作流编排 | 框架不锁定：状态机与 checkpoint 存储格式自研（JSON/Postgres 表），模型层用适配接口封装；LangGraph 作为首选实现而非依赖前提。→ Q-D-12 |
| 科学计算 | R 生态通过显式工具接口（容器 + JSON 契约 + h5ad/lorenz 交换格式）兼容，禁止跨语言隐式对象假设；h5ad↔Seurat 往返会丢失 `reductions`/`graphs`，转换需登记为有损并保留双份派生资产。→ Q-G-04 |
| 知识检索 | pgvector 之外必须叠加**实体规范化层**（基因符号/本体 ID 精确匹配 + 术语字典），向量检索不承担基因符号匹配。→ Q-E-07 |
| 对象存储 | 若对外分发或托管到客户内网，MinIO 的 AGPLv3 义务需法务确认（§11.4、附录 C 核实清单）；否则优先选择许可更宽松的 S3 兼容实现。→ Q-H-06 |

技术选型原则保留：**在真实项目验证前，不采用尚无法解释其结果的专有模型作为唯一分析依据。**

### 4.2 混合部署拓扑（保留三域，修订规则）

- 云端控制面的功能范围按 §11.4 合规矩阵裁剪：涉及人类遗传资源或 GDPR/HIPAA 适用项目时，控制面不得持有供体级元数据，登录鉴权与审计可保留但需目的地区评估。→ Q-H-02
- 新增第四域：**客户内网部署面**（阶段 6），需支持完全无公网：私有 registry、离线依赖包、升级验证与远程运维权限边界（§4.6）。→ Q-H-11

### 4.3 最小化部署建议（保留三条）

### 4.4 【新增】规模与非功能假设

**所有数值为默认建议，需阶段 0 用真实历史项目盘点后替换（附录 C）。** 超限时系统必须显式拒绝，禁止静默降采样（除非人工批准并在报告中标注）。→ Q-G-01

| 类别 | MVP（阶段 2）上限 | 目标（阶段 6） | 超限行为 |
|---|---|---|---|
| 单项目样本数 | 20【待确认】 | 200 | Preflight 拒绝并要求拆分项目 |
| 单项目细胞数 | 50 万【待确认】 | 500 万 | 拒绝 / 要求分片 + 只跑支持 backed-mode 的工具子集 |
| 单资产大小 | 8 GB | 100 GB | 分片上传（§9.7） |
| 单项目资产总量 | 500 GB | 5 TB | 配额告警 + 保留政策（§5.9） |
| 并发运行 flow | 3 | 20 | 队列排队，状态显式返回 |
| 单 run 计算时间 | 4 h | 24 h（含 HPC 排队） | 超时熔断 + 失败分类 `RETRYABLE_FAILURE` |
| 端到端项目周期（含审批） | 10 个工作日 | 5 个工作日 | 触发审核负载复盘（§13.4） |
| 元数据 API P95 延迟 | 500 ms | 300 ms | 大表强制异步导出 |
| 平台可用性 | 工作时间 99% | 99.5% | — |
| RPO / RTO | 24 h / 8 h | 4 h / 4 h | — |
| 内部用户 | 分析师 30、专家 5 | 分析师 80、专家 15 | 审核产能随之扩容（§13.4） |
| 内存模型 | 工具需全矩阵入内存者，节点 ≥128 GB【待确认】 | 支持 backed-mode 优先 | 超内存 → `RESOURCE_EXCEEDED` 明确错误 |

**规模假设的可复现侧影响**：GPU 并行归约带来非确定性，故 §1.5 定义三级可复现；跨硬件比较一律采用 `conclusion` 级。→ Q-G-05

### 4.5 【新增】模型选型与推理策略 → Q-D-01, Q-D-05, Q-H-05

**数据分级 × 模型矩阵**

| privacy_tier | 允许的模型 | 上下文构造规则 | 能力门槛 |
|---|---|---|---|
| 公共 | 云旗舰模型（须有零保留/不入训协议）、本地模型 | 允许较完整摘要 + 检索片段 | 基线 |
| 受限 | 云模型（经审批、字段过滤后）或本地大模型 | 仅 ProjectProfile 摘要 + 指定证据块；字段级 allowlist（§11.5） | 抽取任务需在人工标注子集上 agreement κ ≥ 0.7 才可用 |
| 严格本地 | 仅本地模型，或无模型路径 | 全部本地；出站默认拒绝 | 不达标则该任务改为"人工 + 模板"，**禁止用低质量输出填证据卡** |

**本地推理（默认建议，待硬件盘点确认）**：抽取型任务（文献 claim 抽取、结构化）用中等规模量化模型（如 8B–14B INT4，单卡 24 GB）；规划/综合型任务用 30B+ 级（48 GB 或多卡）；无 GPU 环境下阶段 2 只在 `公共` 级项目启用云端模型，严格本地项目走非模型路径（确定性模板 + 人工）。

**任务分模型**：claim 抽取、检索重排、计划生成、文本组织分别评估，不假设一个模型全包。

**上下文压缩契约**：`ProjectProfile` 摘要为版本化 Schema，并含稀有信号保留规则——任何占比 < 2% 且 ≥ 50 细胞的集合必须原样进入摘要（含其 marker 与样本分布），不得被 top-K 截断；摘要构造本身纳入评测（"信号被摘要丢掉"作为用例类别）。→ Q-D-05

**模型侧运行清单（写入 `RunManifest`）**：`model_invocations[]` 含 `model_id`、供应商快照/版本、采样参数、prompt 全文 hash、输出 hash、token 与成本、策略判定结果；prompt 原文入受访问控制的可观测性存储（不入通用日志，见 §11.2）。→ Q-D-02, Q-D-11

**变更治理**：模型/ Prompt 变更按三级回归：冒烟集（分钟级）→ 分层子集 → 全量基准；未通过分层子集不得用于真实项目。→ Q-I-09

**预算与熔断**：单项目 token 预算上限【待确认，建议 MVP 每项目 ≤ 20 美元等值】、单 Agent 循环预算与最大迭代（§9.2），超限熔断并归类 `BUDGET_EXCEEDED`。→ Q-D-06, Q-G-08

### 4.6 【新增】离线与客户内网分发 → Q-G-04, Q-H-11

- 镜像：内部 registry + digest 校验；Docker→Apptainer/Singularity 转换在构建期完成并验证。
- 依赖锁：Python 用 `conda-lock`/`pixi`；R 用 `renv` lockfile；Bioconda 通道按日期快照固定。
- 离线升级包：版本包 + 校验清单 + 回滚包；客户环境补丁延迟的责任与风险分担写入服务协议（§11.4 合同行）。
- 远程运维：默认只读诊断包上传（脱敏），操作留痕；无回传通道时提供本地诊断脚本。

---

## 5. 数据模型与接口契约

### 5.1 核心实体（修订）

| 实体 | V2.0 变更 | 来源问题 |
|---|---|---|
| `Project` | + `annotation_granularity_target`（交付注释粒度目标）、+ `review_load_budget`（每周专家小时数）、+ `regulatory_profile`（HGR/GDPR/HIPAA/纯商业，驱动 §11.4） | Q-C-02, Q-C-04, Q-H-01 |
| `Sample` → **拆为 `Specimen` + `Library`** | `Specimen{specimen_id, donor_id, tissue, site, timepoint, collection_event}`；`Library{library_id, specimen_id, batch_id, sequencing_run_id, replicate_type(biological\|technical)}`；`Assay` 绑定 `Library` | Q-A-12 |
| `Cell` | `cell_key = specimen_id + ':' + feature_barcode`；配对关系移出到 `CellPairing` | Q-A-14 |
| `CellPairing`（新增） | `cell_pairing_id, modality_a, modality_b, method(experimental_wet_pairing\|barcode_inference\|st_registration\|unpaired), confidence, failure_reason` | Q-A-14, §8.3 |
| `DataAsset` | + `file_sha256` + `content_fingerprint`（§5.6）+ `privacy_tier`（资产级覆盖，§5.8）+ `usage_scope` + `upstream_pipeline` + `filtering_state` + `genome{build, annotation_source, version, id_system}`；`parent_asset_id` 降级为便捷字段，真值为 `AssetLineage` | Q-A-13, Q-F-02/03, Q-H-04 |
| `AssetLineage`（新增） | `run_id, node_id, asset_id, role(input\|output), ordinal`（多父 DAG） | Q-F-02 |
| `RunManifest` | + `model_invocations[]`（§4.5）、+ `reproducibility_level(target)`、+ `tool_registry_snapshot` | Q-D-02 |
| `CandidateSet`（新增，替代以 cluster 为实体） | `candidate_id, run_id, member_fingerprint, size, donor_count, display_name, cluster_origin{resolution, tool_version}, successors/predecessors, alignment_history[]` | Q-A-06 |
| `EvidenceCard` | 主键关联 `candidate_id`（不再是 `cluster_id`）；新增 §5.4 字段 | Q-A-06, Q-B-01, Q-D-10 |
| `ReviewEvent` | + `scope(card\|field\|membership)`、+ `supersedes_review_id`（版本链）、+ `retraction_reason` | Q-F-08 |

### 5.2 数据契约要求（保留 V1.0 五条，新增三条）

- 新增：**标识与命名规范**统一见 §5.6；报告与 API 一律引用 `candidate_id`，聚类名仅作展示。
- 新增：**删除可行性契约**——每个资产登记其全部下游副本与共享面（缓存、索引片段、导出物），支撑 §11.7 删除清单。→ Q-F-10
- 新增：**基因标识**以 Ensembl ID + 注释版本为主键，符号仅显示；跨物种映射必须携带 `mapping_method` 与 `confidence`。→ Q-A-13

### 5.3 分析计划契约（修订，补字段）

```
AnalysisPlan
  plan_id / plan_version / project_id / objective
  design_matrix: {formula, group_variable, paired_by, covariates[], confounding_checks[]}   # 新增 → Q-B-03
  data_partitions: {discovery_specimens[], confirmation_specimens[], rule}                  # 新增 → Q-B-01
  nodes[]:
    node_id / tool_name / tool_version
    inputs[] / outputs[] / parameters
    dependency_node_ids[]
    resources / privacy_policy / requires_approval
    quality_checks[] / retry_policy / fallback_node_id
    input_asset_lineage_constraints[]                                                        # 新增（多输入校验）
  qc_policy_id / reference_binding {dataset_id, version, label_provenance}                   # 新增 → Q-A-02, Q-E-05
  loop_budget {max_iterations, max_compute_minutes, max_token_cost}                           # 新增 → Q-D-06
  stop_conditions[] / expected_deliverables[] / provenance_policy
```

`confounding_checks` 为确定性工具输出：设计矩阵秩、条件与批次/供体的嵌套与混杂、每组最小重复数（`min_n_per_group`【待确认，建议 ≥3】）；不可识别时进入 `NEEDS_INPUT`，组间结论被限制为描述性。→ Q-B-02/03

### 5.4 结果契约与标签模型（修订）

```
EvidenceCard
  card_id / candidate_id                         # 主键改候选集合 → Q-A-06
  hypothesis_type: cell_type | cell_state | continuous_state | technical_artifact | unknown   # 新增连续态 → Q-A-09
  candidate_label / ontology{term_id, ontology_version, mapping_source} / label_level
  identity_vs_state: {identity_claim, state_claim, combined_expression}                        # → Q-C-03
  supporting_evidence_ids[]                      # 每条证据含 discovery|confirmation 标记 → Q-B-01
  contradicting_evidence_ids[] / unresolved_conflict(bool)
  artifact_screening {doublet, ambient, low_quality, batch, stress_cycle, over_split}           # → §7.7
  novelty_vs_coverage {reference_coverage_score, novelty_signal}                                # → Q-A-07
  stability_summary {protocol_version, passed_perturbations[], metric_values{}}                 # → §7.5
  cross_modal_status / composition_check {method, result}                                       # → Q-B-04
  field_provenance {field_name: tool|retrieval|model|human, source_object_id}                   # → Q-D-10
  support_rule_version / final_status: supported | provisional | conflict | unknown | rejected
  reviewer_id / review_reason / reviewed_at / sign_off
```

`final_status` 上限规则（写入门禁）：无独立确认 → 不得 `supported`；`unresolved_conflict=true` → 不得 `supported`（应为 `conflict`）；模型来源的数值字段 → 校验失败即拒收卡片。→ Q-B-01, Q-C-05, Q-D-10

### 5.5 知识实体（见 §6）

### 5.6 【新增】标识符与指纹规范 → Q-A-06, Q-F-03

- `candidate_id = "cand_" + base32(sha256(member_fingerprint))[:12] + "_" + run_short_id`
- `member_fingerprint = sha256(sorted(canonical_cell_key) 逐行 + '\n' 连接)`，`canonical_cell_key = specimen_id + ':' + feature_barcode`（小写统一、去除平台前后缀规则版本化）。
- `content_fingerprint`（资产级）= sha256(维度 ‖ var 索引集合 hash ‖ obs 索引集合 hash ‖ 非零元素计数与分块和的规范化序列化)。重序列化不改内容 → 指纹稳定；内容变更 → 指纹变更。文件级 `file_sha256` 并存。
- 跨运行候选对齐：`Jaccard(a,b)`；≥ 0.6【待确认】判定为延续，0.4–0.6 记录 `uncertain_alignment`，< 0.4 视为不同候选；分裂/合并显式写入 `alignment_history`。审核记录继承规则：延续关系成立时历史审核对当前卡片**可见但不自动生效**（必须显式 `carry_over` 决策并留痕）。
- 本体引用一律 `(ontology_name, term_id, ontology_version)` 三元组，禁止裸存 `ontology_id`。→ Q-E-03

### 5.7 【新增】标签与注释的存储真值 → Q-F-01, Q-G-02

- **真值 = 对象存储中不可变的注释资产版本**（AnnData/MuData 的派生资产，绑定 `run_id` 与 `content_fingerprint`）。
- Postgres 存索引与工作流：`AnnotationVersion(version_id, run_id, asset_id, label_column, level, created_by, status)` + 审核事件 + 统计缓存（候选规模、供体分布）；**不把百万级细胞标签逐行写入关系表作为真值**。
- 一致性：任何标签修订产生新的注释资产版本 + 新 `AnnotationVersion`；报告引用 `annotation_version_id`；读侧"当前有效"由版本链与 `ReviewEvent.scope` 解析，禁止原地覆盖。
- 性能：细胞级查询走派生（按 `AnnotationVersion` 分区的 Parquet 或数组文件），关系库只承担聚合与检索。

### 5.8 【新增】数据分级下沉到资产级 → Q-H-04, Q-A-14

`privacy_tier` 与 `usage_scope` 均为**资产级**字段，`project.privacy_tier` 只是默认值：
- `privacy_tier ∈ {public, restricted, strict_local}`（V1.0 三级保留）
- `usage_scope ∈ {this_project_only, internal_improvement, cross_project_allowed, deliverable_visible}`
- 模型网关与检索层按 `(asset.privacy_tier, asset.usage_scope, project.regulatory_profile)` 三元组做过滤；跨项目案例复用要求 `cross_project_allowed`，否则检索层直接不命中。→ Q-H-04, Q-E-10

### 5.9 【新增】保留与配额 → Q-F-12

派生资产按 `retention_class{ephemeral, run_linked, deliverable, archive}` 登记；`ephemeral` 有 TTL（默认 30 天【待确认】）；`run_linked` 随项目归档进入冷层；GC 作业不得删除被 `deliverable` 引用的资产（依赖 `AssetLineage`）。

---

## 6. 专业知识库与证据管理

### 6.1–6.4（保留 V1.0：以可追溯证据声明为单位、八类实体表、混合检索五步、本体与参考数据原则）

对 §6.4 补一句：**公共数据可访问 ≠ 无使用限制**；参考图谱标签的生成方式（人工策展 / 预测 / 混合）必须登记为 `label_provenance`，且 `label_provenance ≠ curated_expert` 的数据集不得用于 L3 真值。→ Q-E-05

### 6.5 知识入库与治理（保留四条，新增两条）

- 新增：模型抽取的 claim 在进入检索命中集之前需要状态 `reviewed`；`unreviewed` 只允许在"提示性线索"通道返回，且界面与证据卡明确区分。→ Q-E-01, Q-E-10
- 新增：每条 claim 登记 `license_class`（可引用片段/不可再分发/仅内部）与 `tm_rights`；许可不满足时检索层不得把原文片段送入出站请求。→ Q-E-08

### 6.6 【新增】吞吐与人力测算（阶段 0 必须实测替换） → Q-E-01

默认估算【全部待确认】：

| 参数 | 建议默认 | 说明 |
|---|---|---|
| 每篇全文可抽取 claim | 8–20 | 论文型；图谱/资源类另计 |
| MVP 期人工复核比例 | 100% → 目标 30% | 达到目标前不得扩大自动决策范围 |
| 复核人效 | 30 claim / 人日 | 含定位原文、判断适用限制 |
| 首期覆盖范围 | **不做全库**：2 物种 × 3 组织 × 1–2 疾病背景，约 150 篇 | 与阶段 2 试点选例（§12.2）对齐 |
| 由此推导人力 | 150 篇 × 14 claim ≈ 2100 claim ≈ **70 人日复核** | 分两批，先覆盖试点项目所用组织 |

结论：知识库首期价值来自**窄而深**的 marker/参考适用范围，而非文献量。检索质量评测集（查询-相关证据对）需同步建立 ≥ 50 条【待确认】。→ Q-I-08

### 6.7 【新增】许可与用途过滤 → Q-E-08, Q-H-04

检索层执行三阶过滤：`license_class` → `usage_scope` → `privacy_tier`；出站请求内容构造时逐块判定可否出境；不满足即从上下文剔除并在证据卡记录"因许可/用途限制未纳入该来源"。

### 6.8 【新增】评测隔离与共源证据 → Q-E-02, Q-E-05, Q-A-11

- 评测集数据集在知识库中标记 `used_in_benchmark=true`（含其派生 claim、marker_set、参考数据集与模型权重来源）；评测运行时检索、参考映射与分类器命中全部屏蔽该来源，并在报告生成时校验屏蔽生效（可审计的"隔离证明"）。
- `evidence_claim` 与 `reference_dataset` 记录 `upstream_data_sources[]`；证据卡构建时计算**证据独立性分组**（同一 atlas/同一训练集派生的证据归为一组），同组多条证据只计一条支持证据。→ Q-A-11
- 案例库标注 `produced_by_platform(bool)`；自产案例默认降权且不得作为 L3 真值来源。→ Q-E-10

### 6.9 【新增】冲突、更新与撤回 → Q-E-06, Q-E-09

- 相反 claim 共存时必须显式 `disagreement_state ∈ {open, resolved_by_experts, superseded}`，`resolved` 需 `resolution_review_id`；检索层不得把未解决分歧折叠为单一结论。
- claim 撤回/重训/许可变更触发**影响分析**：列出引用它的证据卡、报告与案例，标记为 `stale_ref`（不回改已归档报告的原文，改为附加更正条目）。

---

## 7. 特殊细胞发现与注释工作流

### 7.1 端到端流程（保留十步，修订三处）

| 步骤 | 修订 |
|---|---|
| 3 基础 QC | 从"跑一遍阈值"改为"选择 `QcPolicy` 并输出**过滤敏感性对照**"（不过滤基线并行，成本计入 §4.4）→ Q-A-04 |
| 5 候选发现 | 改为按**五类候选**分别生成（§7.2a），并强制写入触发信号的具体数值与工具版本 → Q-C-01 |
| 6 伪影排查 | 明确注册工具与假设字段（§7.7），输出结构化为 `artifact_screening` → Q-A-03 |

### 7.2 【新增】五类候选分型（取代笼统的"特殊细胞"） → Q-C-01

| 代号 | 名称 | 触发判定（§1.5 阈值） | 最小证据集 | 允许的最高 `final_status` |
|---|---|---|---|---|
| **C1** | 未分配/低置信群 | 参考分数低于校准分位且已排除低质量 | 伪影筛查 + 覆盖度 + 稳健性 + marker | `provisional`（无独立确认时） |
| **C2** | 参考冲突群 | ≥2 个**独立**参考源分支不一致（同组证据不算） | 双方参考适用范围 + 排除性 marker + 独立确认 | `supported` 可达（须确认） |
| **C3** | 稀有稳定群 | 占比 < θ 且 ≥ n_min 且 ≥ d_min 供体且稳定 | 稳健性 + 跨供体一致 + 伪影排除 + 连续态检查 | `provisional`（仅发现集时） |
| **C4** | 跨模态不一致群 | 模态间身份假设矛盾（定量：模态特异 marker 检出频率差 > 阈值） | 各模态独立证据 + 配对质量 + 冲突裁定 | `conflict` 或 `supported` |
| **C5** | 条件分布异常群 | 组成检验显著 **且** 通过混杂检查 | 组成分析方法 + 设计矩阵 + 混杂判定 | 描述性；受 `min_n_per_group` 约束 |

**优先级与队列上限**：每项目进入专家队列的卡片上限 `≤ 10`【待确认】，其余进入 `observed`（自动归档、不阻塞交付）；排序因子为"结论影响面 × 不确定性 × 客户诉求"（权重见 §13.4）。→ Q-C-04

### 7.2b 触发信号的用途限制（保留 V1.0 末段）

信号只用于触发深入调查，不能单独证明新细胞类型——本稿进一步规定：任何单一信号不得写入 `supporting_evidence_ids`，只能写入 `trigger_signals[]`（独立字段，L5 校验）。→ Q-A-07, Q-D-10

### 7.3 证据卡必备内容（保留 V1.0 九组字段，按 §5.4 更新）

### 7.4 关键分析注意事项（保留四条，新增三条）

- 组成型混杂："某群分布异常"可能只是整体组成位移；C5 必须附组成检验方法与多重检验策略。→ Q-B-04
- 循环推断禁止：发现与确认分离（§1.5、§5.3 `data_partitions`）。→ Q-B-01
- 检验力下限：每组生物学重复 < `min_n_per_group` 时，禁止给出组间显著性结论，仅描述性并标注限制。→ Q-B-02

### 7.5 【新增】稳健性最小协议 → Q-A-05, Q-B-06

必测扰动集（默认 5 类，可按项目增加，不可减少除非人工批准并记录）：

| 扰动 | 具体做法【待确认】 | 记录指标 |
|---|---|---|
| 分辨率 | 当前 resolution × {0.5, 0.75, 1.5, 2.0} | Jaccard、chance-corrected ARI |
| 随机种子 | 5 个种子（聚类+降维） | 同上 |
| 特征子集 | HVG 数量 × {0.5, 1.0, 1.5} 或随机 50% 基因 | 同上 |
| 供体留一 | 逐个去掉 ≥1 供体后重跑 | 集合是否仍存在、marker 一致性 |
| 下采样 | 50% 细胞重采样 | 集合规模与重合度 |

判据：`ari_min=0.6`、`jac_min=0.5`、`pass_ratio=0.6`（≥3/5 类通过）【待确认】。小群必须与置换基线比较（避免随机一致性被当作稳定）。所有指标值写入 `stability_summary.metric_values`，由工具产出，模型不得填写。

### 7.6 【新增】新颖性与参考覆盖度分离评估 → Q-A-07

- `reference_coverage_score`：候选细胞到参考的 kNN 距离分布 vs 校准集（正常参考细胞集）的经验分布；报告分位数与比值。
- `novelty_signal`：覆盖度异常 + 稳定 + 伪影排除 + 非连续态尾部 四条件联合。
- 连续态检查：MVP 用扩散伪时间/流形主轴或 marker 梯度，判断候选是否只是既有连续轴的极端；**不引入 RNA velocity**（阶段评估）。→ Q-A-09
- 输出：`novelty` 与 `coverage_gap` 永远作为两个独立字段呈现，禁止合并成"新细胞类型"表述。

### 7.7 【新增】QC 与伪影策略表 → Q-A-02, Q-A-03, Q-A-04

`QcPolicy` 为版本化配置对象：`{tissue, species, platform, dissociation, expected_artifacts, thresholds[], threshold_source, default_action}`。

- 阈值来源三类：知识库 claim（须 `reviewed`）、样本分布诊断（经验分位数 + 断点）、人工设定（必须记录理由，进入 `analysis_decision`）。
- **默认动作是"标记"而非"删除"**：MVP 期 QC 只产生风险标签与对照基线，细胞删除必须由项目负责人批准并在报告标注（防止稀有群被静默删除）。→ Q-A-04
- 双细胞/环境工具（示例，最终以工具注册为准）：`scrublet` / `doubletfinder` / `pycroscopy`；`soupx` / `decontx` / `cellbender`。每个 ToolSpec 必须填 `assumptions`（如模拟双细胞依赖假定发生率与形成模型；背景估计依赖空液滴/参考谱）与 `invalid_when`（核转录组、极低细胞数、无空液滴数据）。工具结论冲突时以"冲突"呈现，不做加权平均。
- 高 MT 的生物学解释（心肌、肾小管、肝、肿瘤）必须在 `QcPolicy` 中显式覆盖，否则 C1/C3 触发会被质量噪声污染。

### 7.8 【新增】Marker 与"支持"判据 → Q-B-05, Q-A-08

身份类支持证据的最小条件（工具产出，缺一即降级 `provisional`）：

1. pseudobulk（edgeR/limma 类）或适用混合模型，FDR ≤ 0.05 且 |log2FC| ≥ 1【待确认】；
2. 群内检出频率 ≥ 60% 且在指定排除群中 ≤ 10%（排除性 marker）；
3. 跨供体一致性：≥ 80% 供体方向一致【待确认】；
4. 多重检验策略显式记录（分层 BH / 置换）；
5. 秩和检验（Wilcoxon）只能作为排序辅助，不得单独作为身份证据。

---

## 8. 多组学模态适配策略

### 8.0 【新增】各模态上游边界 → Q-A-01

| 模态 | 首期是否支持 | 平台内起点 | 平台外交付物 |
|---|---|---|---|
| scRNA-seq | 是 | 定量矩阵（counts + barcode 全集说明） | 上游定量（10x 等） |
| RNA+ATAC（配对） | 首期核心扩展 | 已过滤矩阵 + fragment 文件（或已建峰对象） | basecalling→ barcode/fragment 处理 |
| CITE-seq | 后续优先 | RNA+抗体计数矩阵 + panel 定义 | 同上 |
| 空间 | 后续 | 平台标准输出 + 坐标变换 | 图像分割等（首期不含图像 AI） |
| VDJ | 后续 | contig/克隆型表 | 组装与配对流程 |

ATAC/空间/VDJ 的**参考版本与峰集合一致性检查**在 Preflight 阻断（见 §8.4）。

### 8.1–8.2（保留 V1.0 分阶段支持矩阵与整合原则）

对 §8.2 补充：**整合得分不作为候选触发信号的唯一来源**，且整合评估不得单以批次混合程度为目标（V1.0 已写，本稿落实为 L4 门禁：`批次混合指标改善` 必须与 `标签/状态保留指标不劣化` 成对通过，二者由 scib-metrics 类工具产出并写入证据卡）。

### 8.3 【新增】模态 QC 指标定义 → Q-A-14

| 模态 | 必测 QC（命名到指标，不写"检查质量"） |
|---|---|
| scRNA | 每样本 UMI/基因数分布、MT 比例、假基因比例、双细胞估计、背景 RNA 估计、空液滴保留率 |
| scATAC | fragment 数、TSS enrichment、FRiP、blacklist 重叠率、核小体峰、重复率 |
| Multiome 配对 | 实验配对标记率、barcode 推断配对结果与置信度、unimodal 比例、`CellPairing.failure_reason` 分布 |
| CITE-seq | 同型对照信噪、抗体未折叠率、通道缺失、每细胞抗体 UMI 分布 |
| 空间 | spot/像素混合细胞比例估计、组织区域分割质量、探针面板与基因 ID 对齐率、坐标变换登记 |
| VDJ | contig 质量、productive 率、双 V(D)J 异常、配对链比例、克隆型定义阈值 |

### 8.4 【新增】跨模态对齐规范 → Q-A-14

`ModalityAlignmentSpec{spec_version, coordinate_system, gene_id_system, peak_set_source, panel_definition_version, mapping_method, match_rate, unmatched_policy}`；未匹配项策略（丢弃 / 保留并标记 / 回退到单模态解释）必须显式，且联合嵌入输入组装时校验该 spec 版本一致。

---

## 9. Agent 编排与 API 设计

### 9.1 首期 Agent 划分（保留四角色 + "确定性工具不必封装为 Agent"）

新增第五角色约束：**Agent 无写权限**。所有 Agent 输出落库为草稿对象（`draft`），进入审批流后才成为有效版本；Agent 不得直接改状态机终态。→ Q-D-02, Q-D-10

### 9.2 工作流状态（保留，补充循环与升级语义）

```
CREATED -> PREFLIGHT_RUNNING -> PREFLIGHT_PASSED / BLOCKED
  -> PLAN_DRAFTED -> PLAN_APPROVED
  -> ANALYSIS_RUNNING -> QC_REVIEW
  -> CANDIDATE_DISCOVERY -> EVIDENCE_BUILDING
  -> TARGETED_ANALYSIS (bounded loop: loop_budget.max_iterations / max_compute_minutes / max_token_cost)
       -> 超限 -> ESCALATED_TO_HUMAN   # 新增，不允许无限循环
  -> VALIDATION -> HUMAN_REVIEW
  -> REPORT_READY -> ARCHIVED
异常分支：RETRYABLE_FAILURE / NEEDS_INPUT / POLICY_BLOCKED / RESOURCE_EXCEEDED / BUDGET_EXCEEDED / FAILED
```

- 重试权归属：状态机拥有业务重试（次数与退避策略在 `retry_policy`），Nextflow 仅做节点级容错；两侧重试事件都必须回调登记。→ Q-G-03
- 审批 SLA 与缺席处理：`requires_approval` 节点审批超时（默认 3 个工作日【待确认】）触发升级提醒与代理人委派；代理委派必须记录被授权人与时间；**审批不得由 Agent 或提交者自身完成**。→ Q-C-04, Q-F-05
- 幂等：每次转移保存 `actor/timestamp/reason/input_hash/versions`；重复回调按 `attempt` 去重。→ Q-G-03

### 9.3 主要业务 API（保留 V1.0 表，修订/新增资源）

| 接口 | 变更 |
|---|---|
| `/v1/clusters/{id}/evidence` | **改为 `/v1/candidates/{candidate_id}/evidence`**，`cluster_id` 只作查询别名并返回其归属 run（解决跨运行歧义）。→ Q-A-06 |
| `/v1/candidates/{id}/alignment` | 新增：返回跨运行对齐历史与重合度。 |
| `/v1/runs` 全部 POST | 新增 `Idempotency-Key` 要求；重放返回原资源。→ Q-F-06 |
| `/v1/annotations/{id}/review` | 新增 `scope`（卡片/字段/成员归属）、`supersedes`、`carry_over` 语义。→ Q-F-08 |
| `/v1/projects/{id}/assets` | 支持分片与客户端直传（临时凭证 ≤ 10 min）。→ Q-F-04 |
| `/v1/exports/{id}` | 新增异步导出（大表、图表包、可复现代码包、交付物 ZIP）。→ Q-F-09 |

### 9.4 工具注册规范（ToolSpec 扩展）

```
ToolSpec
  tool_name / tool_version / container_image_digest
  input_schema / output_schema
  supported_modalities / supported_species / supported_tissue_notes
  assumptions[] / invalid_when[]                    # 新增 → Q-A-03
  reference_compatibility{requires_counts, id_system, needs_log, embedding_source}   # 新增 → Q-A-10
  required_resources / measured_peak_memory / gpu_required      # 新增实测 → Q-G-02
  data_access_policy / network_policy / egress_allowlist[]
  quality_checks / failure_policy / retry_limit
  provenance_fields / reproducibility_level_target
  software_license / license_review_status / license_review_date   # 新增 → Q-H-06
  tool_class {registered | project_adhoc}            # 新增 → Q-D-04
  upstream_reference_sources[]                        # 新增（共源判定）→ Q-A-11
```

`ToolResult` 标准格式保留，新增：`validation_results` 必须含 §7.5/§7.8 的判据数值；`ad-hoc` 工具结果必须带 `unregistered: true` 并在报告中标注。

### 9.5 建议代码仓库结构（保留 V1.0）

新增目录：`packages/tool_registry/`（准入与许可登记）、`packages/policies/`（QcPolicy/合规矩阵/出境 allowlist 的策略即代码）、`tests/redteam/`（注入与安全用例库）。→ Q-D-07, Q-H-09

### 9.6 【新增】工具晋升通道（临时代码如何进入平台） → Q-D-04, Q-J-02

| 层级 | 说明 | 约束 | 上线时延目标 |
|---|---|---|---|
| `registered` | 正式注册工具，全员可用 | 完整 ToolSpec + 单测 + 基准用例 + 许可审查 | — |
| `project_adhoc` | 项目级临时工具：分析师或 Agent 生成的代码在**非特权容器、只读输入、无网络、资源与时长配额**内运行 | 单次提交需项目负责人的**一次性**批准；结果资产强制标 `unregistered`；不得用于交付结论的**唯一**支持证据（需 registered 工具复现或人工签署） | 申请→可用 ≤ 1 个工作日【待确认】 |
| `forbidden` | 直接在生产节点执行未审查 shell | 不允许（V1.0 已定） | — |

晋升流程：`ad-hoc` 被复用 ≥ 2 次 或 被写入交付结论 → 触发登记评审（清单：假设、失效场景、资源、许可、测试用例、输出契约）→ 进入 `registered`，评审 SLA ≤ 3 个工作日【待确认】。

理由：注册表封闭 + 无出路是平台被绕过的首要原因；本通道在保持"不跑任意 shell"的前提下给出可控出口。

### 9.7 【新增】接口非功能与安全约束 → Q-F-05/06/07/09

- 认证与授权：OIDC（可选接入客户 IdP）；授权为 **RBAC + 属性策略（ABAC）**，策略即代码存 `packages/policies/`，与 §11.3 验收用例一一对应；Postgres 行级安全（RLS）按项目作用域；对象存储前缀隔离 + 短时效签名 URL（≤ 10 min）；服务账号与人类操作者在审计中区分。→ Q-F-05, Q-F-11
- 异步与订阅：长任务 `202 + run_id`；状态获取支持轮询与 SSE（webhook 阶段 6）；进度含队列位置。→ Q-F-06
- 错误模型：`{code, class, field, message, retryable, retry_after, trace_id}`；错误分类与 §9.2 异常分支一致。
- 分页与过滤：所有列表强制分页（默认 50，上限 500）；大表只允许异步导出。→ Q-F-09
- 版本与兼容：`/v1` + `Deprecation` 头 + 契约测试；Schema 迁移采用"读旧写新 + 显式迁移作业"，历史项目在新版本下可解释（迁移脚本纳入回归）。→ Q-F-07
- 可观测性：`run_id` 贯穿 API→状态机→Nextflow→容器→模型调用（trace 传播），日志字段规范 + 敏感字段脱敏；模型 prompt/响应只进受访问控制的 trace 存储，不进通用日志。→ Q-D-11, Q-G-07
- 公平性与熔断：队列优先级政策、单项目资源配额、失控任务（含 Agent 循环）熔断。→ Q-G-08

---

## 10. 验证体系与质量门禁

### 10.1–10.3（保留 V1.0 五层验证、评测集设计、门禁规则示例）

补充到 §10.1 的检查项：L1 增加基因 ID 体系/基因组版本/模态对齐校验；L2 增加"Agent 决策可复现"（与计算可复现分列）；L3 增加弃权质量；L5 增加"数值字段与工具输出一致"（`field_provenance` 校验）与检索泄漏隔离证明。→ Q-A-13, Q-D-02, Q-I-01, Q-D-10, Q-E-02

§10.3 门禁新增行：

| 触发条件 | 动作 | 可自动恢复 |
|---|---|---|
| 候选的支持证据全部来自"发现所用同一细胞集合" | 结论上限降为 `provisional`，要求补充确认 | 否 |
| 数值字段来源为 `model` | 拒收证据卡并报错 | 否 |
| 命中证据 `used_in_benchmark=true`（评测运行中） | 屏蔽该来源并记录隔离证明 | 是（自动剔除） |
| 每项目进入专家队列卡片 > 上限 | 按优先级排队，其余归档为 `observed` | 是（排队） |
| `ad-hoc` 工具结果作为交付结论唯一支持证据 | 阻止交付，要求复现或人工签署 | 否 |

### 10.4 验收指标设定方法（保留，加一条硬约束）

新增：**未建立基线前，禁止用单一数字门槛做发布门禁**；但安全类门禁（越权、出境、注入）例外——它们必须从阶段 1 起就是硬阻断（用例见 §11.3、§10.8）。→ Q-I-04

### 10.5 【新增】评测集构建、真值与泄漏隔离 → Q-I-02/03, Q-E-02/05, Q-B-08

- **真值来源规则**：L3 真值仅限"独立标注"——≥2 名领域专家盲标 + 分歧裁定 + 冻结为只读制品（版本 + 内容指纹）；atlas 标签（`label_provenance ≠ curated_expert`）不得作为真值，可作参考。
- **规模下限**【待确认】：常见类 ≥3 数据集；每个稀有类 ≥200 细胞且 ≥3 供体；反例集 ≥20 例（模拟双细胞/背景 RNA + 已知批次伪影历史案例）。不足时以 bootstrap 置信区间报告，**不得用于阈值判定**，只作趋势。
- **合成数据**用于反例与压力测试（避免真实个人数据进入长期基准），来源与许可登记。→ Q-I-06
- **泄漏隔离**：评测数据集与其派生知识标记 `used_in_benchmark`，评测运行自动屏蔽（§6.8）；每次评测输出"隔离证明"（被屏蔽的来源清单）。
- **检索评测集**：≥50 条查询-应命中证据标注对；分别报告 `locatable_rate` 与 `support_validity_rate`。→ Q-I-08, Q-D-08

### 10.6 【新增】指标字典 → Q-B-10, Q-I-10

| 指标 | 分子 / 分母 | 采集点 | 目标【待确认】 | 不可接受值 |
|---|---|---|---|---|
| 稀有类召回（按 C1–C5 分型） | 命中专家预标候选数 / 专家预标候选总数（分供体分层，报 bootstrap CI） | 阶段 5 评测 | 高于传统基线且 CI 不重叠 | CI 覆盖基线却宣称改进 |
| 弃权质量 | 风险-覆盖率曲线；覆盖率 80% 时残余错误率 | 评测 | 残余错误率 ≤ 基线的 50% | 靠"一律粗标签"取得高覆盖 |
| 校准 | ECE（按群规模分层）、Brier | 评测 | ≤ 0.1 | 未测即设阈值 |
| 引用可追溯率 | 可定位到来源的 claim / 全部 claim | L5 | ≥ 0.95 | < 0.9 |
| 支持有效率 | 经 NLI/人工判定"确实支持"的引用 / 全部引用 | L5 | ≥ 0.85 | 与追溯率混为一谈 |
| 无依据结论率 | 无任何支持证据的结论条数 / 结论总数 | L5 + 抽审 | ≤ 0.02 | 出现出境或越权相关即为 0 容忍 |
| 专家修订率 | 被修订卡片 / 已审卡片（分"标签变更""状态变更"） | ReviewEvent | 阶段 2 ≤ 0.5，阶段 5 ≤ 0.25 | > 0.6（说明候选质量差） |
| 返工率 | `同 run 重跑`、`结论修订` 分别计数 / 项目数 | RunManifest | 重跑 ≤ 1 次/项目 | — |
| 端到端耗时 | 墙钟，分 platform_compute / queue_wait / review_wait | 监控 | 三段分别设目标 | 只报总时长 |
| 计算可复现率 | 按 `byte/numeric/conclusion` 三级分别统计 | 回归作业 | conclusion 级 100% | 用 byte 级掩盖 GPU 非确定性 |
| 安全门禁 | 越权访问次数、出境拦截率、注入用例通过率 | CI/红队 | 高危用例全通过 | 任一高危用例失败即阻断发布 |
| 绕过率 | 未走平台完成分析的项目占比 / 全部项目 | 项目登记 | ≤ 10% | > 30%（触发 §12.3 复盘） |
| 知识库复核吞吐 | 已复核 claim / 人日 | 知识服务 | 达到 §6.6 估算 | — |
| 未解决分歧率 | `disagreement_state=open` 且被引用的 claim / 被引用 claim | 检索层 | ≤ 0.05 | — |

### 10.7 【新增】盲评协议 → Q-I-05, Q-B-09

统一交付模板（固定字段顺序与图表清单）以遮蔽来源标识；≥3 名评审者且 ≥1 名未参与开发；一致性用 Krippendorff α ≥ 0.6 方可聚合；打分维度：结论可信度（1–5）、需修改结论条数、阅读耗时；基线为 2–3 条传统流程（不同分析师）而非单一分析师一次结果。

### 10.8 【新增】安全与门禁的分级故障注入 → Q-I-04

| 危害级别 | 用例来源 | 通过要求【待确认】 |
|---|---|---|
| 高危（越权访问、严格本地项目出境、注入改变工具/审批、跨项目数据混用） | §11.3 五条 + 属性测试 | ≥ 50 用例全通过；任一失败阻断发布 |
| 中危（配额逃逸、日志泄露敏感字段、删除残留） | 红队清单 | ≥ 20 用例，允许已知问题带修复期限 |
| 低危 | 常规渗透/依赖扫描 | SLA 内处置 |

---

## 11. 混合部署、安全与合规

### 11.1 数据分级（保留三级，新增第四级）

`public / restricted / strict_local`（V1.0）+ **【新增】`hgr_controlled`**：涉及中国人类遗传资源信息的项目，默认禁止出境且禁止任何云端模型调用，控制面仅存不携带遗传信息的流程元数据。分级下沉为资产级（§5.8）。→ Q-H-01

### 11.2 必备安全控制（保留八条，具体化三条）

- 密钥治理：KMS/Vault、按项目最小权限角色、轮转周期、泄漏响应剧本；"不写入 prompt/参数/日志"由技术强制（模板变量注入校验、出站内容扫描、CI 秘密扫描）。→ Q-H-08
- 审计：append-only / WORM 存储、完整性证明、管理员行为由第二角色监督、保留期与监管响应窗口、trace 存储按敏感级访问控制。→ Q-H-07, Q-D-11
- 供应链：镜像签名验证、SBOM（含 R/Conda 混合依赖生成方案）、按严重性的漏洞处置 SLA。→ Q-H-10
- 模型供应商：条款核查（保留期、是否入训、子处理商、区域、专属端点）作为 `restricted` 以上级使用云模型的前置条件。→ Q-H-05

### 11.3 安全验收测试（保留五条，改为可自动化）

每条对应 `packages/policies/` 中的策略表达式与 `tests/redteam/` 用例，纳入 CI，按 §10.8 分级门禁执行。→ Q-I-04, Q-F-05

### 11.4 【新增】法规与合同合规矩阵

**性质说明**：下表是"必须核查并留证据"的控制点清单，非法律意见；结论需由法务/合规出具，且法规与条款会变化，每次法规或供应商条款更新触发矩阵复审（`regulatory_profile` 变更即触发受影响项目的重新评估）。→ Q-H-01/02/04/06

| 适用域 | 触发场景 | 平台控制点 | 硬门禁 |
|---|---|---|---|
| 人类遗传资源（中国） | 采集、保藏、利用、对外提供（含向境外主体/境外模型服务提供遗传信息）、信息公开 | `hgr_controlled` 分级 + 出站全禁；许可/事先报告与证据登记在项目档案；派生统计是否构成"信息对外提供"由法务判定 | 无判定结论则禁止任何出境 |
| 个人信息（PIPL） | 供体健康类个人信息（年龄、性别、疾病、部位、地区、日期） | 单独同意证据、字段级出境 allowlist（§11.5）、最小化模板、目的限定 | 未在白名单的字段不得进入任何出站请求 |
| 跨境传输路径 | 控制面在云端、数据在本地（本方案的默认形态） | 按 `privacy_tier × 目的地区 × 字段` 的出境评估矩阵；鉴权数据所在域声明 | 评估未通过 → 该项目降级为全本地部署 |
| GDPR | 欧盟数据主体/客户 | DPA、处理者义务、被遗忘与可携带、访问日志 | 同 PIPL 矩阵 |
| HIPAA（如适用） | 美国健康数据 | 去标识化标准适用性、BAA | 未签 BAA 不得出境 |
| 客户合同 / DPA | 客户数据能否进入案例库并被后续项目复用 | `usage_scope`（§5.8）默认 `this_project_only`；检索层按 scope 过滤；删除请求可执行到副本清单（§11.7） | 违约风险项：跨客户复用需合同明确允许 |
| AI 参与度披露 | 交付报告 | 报告强制段落：AI 辅助范围、模型与知识库版本、限制条件、签署人 | 缺失则不能进入 `REPORT_READY` |
| 第三方许可 | MinIO(AGPLv3)、ArchR、10x 软件、CELLxGENE/图谱数据、CellTypist 模型、文献 TDM | ToolSpec `license_review_status`；注册表准入前审查；文献片段按 `license_class` 过滤 | 许可不合规工具不得注册 |
| 再识别风险 | 表达谱可推断基因型，摘要/图表可能构成可识别信息 | 见 §11.5；细胞级/供体级统计默认不外发 | 任何细胞级出境即阻断 |

（核实清单与法规名称/年份见附录 C 第 5 节，需以官方现行文本为准。）

### 11.5 【新增】字段级出境 allowlist → Q-H-02/03, Q-D-05

| 类别 | 默认允许出境（`restricted` 级 + 审批后） | 默认禁止 |
|---|---|---|
| 任务语义 | 目标描述、组织/物种/平台名、样本数（不带身份） | — |
| 汇总统计 | 按条件聚合的高层统计（组规模、QC 中位数）、经过阈值化的 marker 列表 | 供体级/细胞级任何原始统计、坐标、barcode、UMI |
| 派生表征 | 无 | 嵌入向量（可能可反演）、图谱对齐坐标 |
| 元数据 | 组织、疾病名（经评估）、时间点类型 | donor_id、姓名/编号、就诊机构、精确日期、地区、基因型信息 |
| 图像 | 无（且平台不使用视觉模型，§2.3） | 空间图像、UMAP（含细胞位置信息） |
| 知识 | 公开文献摘要（受 `license_class` 限制） | 客户专有数据、未公开研究结论 |

出站组装逻辑：白名单驱动的模板构造（非"过滤掉坏字段"），字段级拒绝记录写入审计；`hgr_controlled/strict_local` 下整表按禁止处理。

### 11.6 【新增】提示注入防护控制清单 → Q-D-07

1. 文献/网页/文件/元数据一律进"不可信内容区"，与系统指令物理分隔（结构化字段而非拼接字符串）；
2. 工具参数值不得来源于检索文本（参数只允许来自 ProjectProfile、注册表枚举、已批准计划）；
3. 输出严格 schema + 枚举校验，超范围字段丢弃并记录（不重试到通过）；
4. 检索片段预处理剥离链接、指令式句式、超长段；
5. 出站目标 allowlist（无默认开放网络）；
6. 权限/审批状态不可由模型侧写路径触达（只经人工动作）；
7. 红队语料库：含指令的伪造文献、被污染元数据、恶意 Supplementary，随版本更新，作为 §10.8 高危用例。

### 11.7 【新增】删除与数据地图 → Q-F-10/11

数据地图登记每类资产的副本面：对象存储、Postgres 索引、容器 `work dir`、Nextflow cache、pgvector 索引片段、检索结果缓存、导出包、trace 存储、备份副本。删除/归档作业产出**删除证明**（清单 + 时间 + 操作者），并校验跨项目共享面为零。

---

## 12. 分阶段开发路线与验收

### 12.0 阶段划分（保留 V1.0 阶段 0–6 与各阶段交付物/验收条件）

三处修订：

- **阶段 0 扩充**：除历史案例与风险清单外，必须产出 §1.5 术语表定稿、§4.4 规模假设实测、§6.6 吞吐测算、§11.4 合规矩阵初步结论（含法务意见）、§10.5 评测集与真值协议、附录 C 待确认清单的处置结果。未完成者不得进入阶段 1 编码。→ Q-G-01, Q-C-01
- **阶段 2 与阶段 4 的依赖矛盾修正**：MVP 阶段的知识来源限定为**窄范围、人工复核过的** marker/参考（§6.6）+ "提示性线索"通道（未复核 claim 不进证据卡）。因此 MVP 的验收不要求完整知识库，但要求：进入 `supporting_evidence_ids` 的每条证据都可定位且已复核。→ Q-J-04
- **阶段 1 增加两项硬产出**：`packages/policies/` 策略即代码（安全门禁从阶段 1 起硬阻断）与 §9.6 工具晋升通道（先有出路再谈规模）。→ Q-I-04, Q-D-04

### 12.1 首期 MVP 交付定义（保留，收紧两处）

- "可复现的 scRNA 基础工作流"须达到 §1.5 `conclusion` 级可复现；
- "候选发现"按 C1–C3 三类实现（C4/C5 属阶段 3 与评测阶段），避免首期摊开五类。→ Q-C-01, Q-J-09

### 12.2 建议的内部试点选例（保留四条）

新增要求：每个试点项目在启动前由领域专家**预先封存**"期望候选清单 + 关键限制"（盲于系统输出），用于 §10.6 稀有类召回与修订率的计算；封存件带版本与时间戳。→ Q-B-09, Q-I-05

### 12.3 【新增】go/no-go 决策门 → Q-J-05

| 阶段出口 | go 条件 | no-go / 回炉条件 |
|---|---|---|
| 0 → 1 | 术语表与规模假设定稿；评测集与真值协议就绪；合规有法务结论 | 无法确定规模或合规边界不清 → 暂停建设，改为"通用助手 + 规范模板"过渡方案 |
| 1 → 2 | 契约与血缘闭环可用；安全门禁在 CI 阻断；工具晋升通道可用 | 契约仍需为功能妥协 → 只做数据平台底座，不做 AI 层 |
| 2 → 3 | §14.4 MVP DoD 达标；绕过率 ≤ 10%；专家修订率 ≤ 0.5 | 修订率 > 0.6 或绕过率 > 30% → 回到候选定义与 QC 策略，不扩模态 |
| 3 → 4 | 配对关系正确且冲突可见；跨模态证据独立可追 | 冲突被整合得分掩盖 → 停止扩模态数量 |
| 4 → 5 | 检索质量与复核吞吐达标；许可过滤生效 | 复核吞吐不足 → 收窄知识范围而非放宽审核 |
| 5 → 6 | 盲评显示可量化改进且无高危安全失败；ROI 模型成立（§13.3 补充成本行） | 成本超过项目单价且无采用率证据 → 冻结生产化 |

裁定人：项目负责人 + 单细胞领域负责人 + 平台负责人三方共同签署；不通过时记录止损决策与保留资产（避免"沉没成本继续投"）。

---

## 13. 团队分工、运维与风险管理

### 13.1 角色配置（保留 V1.0 表，新增两条）

- **评测/基准负责人**（可与领域负责人分设）：拥有真值冻结与评测运行权限，独立于开发，避免既做又评。→ Q-J-06, Q-I-05
- **知识治理负责人**：claim 复核、许可登记、分歧裁定。→ Q-E-01/06, Q-H-06

### 13.2 运行治理（保留四条，新增三条）

- 工具版本快照：每次正式运行记录 ToolSpec 集合版本，升级采用影子运行（同数据新旧并行 + 差异报告）后再上线。→ Q-G-09
- 模型/知识库/Prompt 变更：三级回归（§4.5）。→ Q-I-09
- 指标 owner：§10.6 每个指标指定 owner 与采集点；无 owner 的指标不进报告。→ Q-J-10

### 13.3 主要风险与缓解（保留 V1.0 七条，新增四条）

| 风险 | 影响 | 缓解 |
|---|---|---|
| 分析师绕过平台 | 平台无数据、无收益，投入沉没 | §9.6 工具晋升通道；§10.6 绕过率指标；阶段 2 no-go 条件 |
| 专家产能瓶颈 | 审批排队、项目延期 | §13.4 负载模型与候选卡上限 |
| 规模假设错误 | 架构返工、成本失控 | §4.4 实测替换；阶段 1 只做单闭环不做微服务 |
| 模型/供应商条款变化或本地能力不足 | `restricted`/严格本地项目降级为无模型路径 | §4.5 降级路径；§11.4 合规矩阵复审 |

### 13.4 【新增】专家审核产能与负载模型 → Q-C-04

```
每周审核产能(卡片) = 专家数 × 每周小时 × 60 ÷ 单卡平均分钟数
项目周吞吐 = 每周审核产能 ÷ 每项目卡片数
```

示例（全部【待确认】，阶段 2 试点实测替换）：2 名专家 × 4 h/周 ÷ 25 min ≈ **19 卡片/周**；若每项目 6 卡片 → **≈ 3 个项目/周**的审核上限。

配套规则：
1. 每项目进入专家队列卡片上限 10，其余归档 `observed`；
2. 优先级评分 = 结论影响面(0–3) × 不确定性(0–3) + 客户诉求(0/2)，低分卡片只进报告附录；
3. 审核界面目标：单卡核心信息一屏内（卡片正文 + 2 条关键证据 + 反证 1 条），文献按需展开——把"25 分钟/卡"作为设计目标而非结果；→ Q-J-07
4. 分歧裁定协议：不一致时先二次独立评审 → 分歧保留为 `conflict`（可交付），由领域负责人裁定或明示"未解决分歧"，裁定记录进 `ReviewEvent` 并写入知识库（不得静默覆盖）。→ Q-C-05

---

## 14. 首期 MVP 清单与量化完成定义

### 14.1 首期必须交付（保留八条）

### 14.2 首期可以暂缓（保留五条）

### 14.3 决策建议（保留）

优先把"候选 → 正反证据 → 技术伪影排查 → 针对性分析 → 专家审核 → 案例归档"做成可靠闭环，再扩模态与部署规模。V2.0 补充：**先可靠，再快**——可靠性由 §1.5 判定定义 + §7.5/§7.8 规则 + §10 门禁保证，速度只在 §4.4 规模假设内优化。

### 14.4 【新增】MVP 量化完成定义（DoD）→ Q-C-01, Q-I-02, Q-J-09

在 3 个历史项目（含 1 个争议亚群项目、1 个多批次项目）上，且这些项目的数据集未出现在知识库检索命中集（§6.8 隔离）：

| # | 条件 | 阈值【待确认】 |
|---|---|---|
| 1 | 专家事先封存的关键候选中，至少 **80%** 被系统以任一类候选（C1–C3）提出 | 召回 ≥ 0.8 |
| 2 | 系统提出的候选中，至少 **50%** 被专家判定为"值得调查" | precision ≥ 0.5 |
| 3 | 假候选中，被判为"实际是双细胞/背景/低质量"的比例 ≤ **25%** | 伪影误报率 |
| 4 | 每张卡片的 100% 数值字段来源为工具或人工（`field_provenance` 校验通过） | 0 例模型产出数值 |
| 5 | 进入 `supporting_evidence_ids` 的证据 100% 可定位且已复核；无独立确认的卡片无一被标 `supported` | 门禁零违例 |
| 6 | 端到端计算达到 `conclusion` 级可复现（重跑候选与状态一致） | 100% |
| 7 | 允许 `unknown/provisional` 且专家认可该保守结论为正确处置的比例 ≥ **60%** | 弃权质量 |
| 8 | 单项目专家审核时长 ≤ **4 小时**；分析师绕过率 ≤ **10%** | 采用率 |
| 9 | 高危安全用例（越权、严格本地出境、注入）全通过 | §10.8 |
| 10 | 交付包内容完整（分析计划 + 运行清单 + 关键图表 + 证据卡 + 限制说明 + 已签署报告） | 模板零缺项 |

未达标处理：按 §12.3 回炉（不进入阶段 3 的多组学扩展）。

---

## 附录 A. 核心 JSON 契约示例（V2.0 更新）

### A1. 分析计划节点（补设计矩阵、发现/确认分区与循环预算）

```json
{
  "plan_id": "plan_001", "plan_version": "2.0", "project_id": "project_001",
  "objective": "investigate_uncharacterized_cell_population",
  "design_matrix": {
    "formula": "~condition + batch + donor",
    "group_variable": "condition", "paired_by": "donor_id",
    "covariates": ["age_band", "mt_pct"], "min_n_per_group": 3,
    "confounding_checks": [
      {"name": "condition_batch_aliasing", "tool": "design_identifiability", "status": "PASS"}
    ]
  },
  "data_partitions": {
    "rule": "holdout_by_donor",
    "discovery_specimens": ["spc_01", "spc_02", "spc_03"],
    "confirmation_specimens": ["spc_04", "spc_05"]
  },
  "qc_policy_id": "qc_tissue_kidney_human_10x_v3",
  "reference_binding": {"dataset_id": "ref_x", "version": "2026-05", "label_provenance": "curated_expert"},
  "loop_budget": {"max_iterations": 2, "max_compute_minutes": 180, "max_token_cost_usd": 5},
  "nodes": [
    {"node_id": "qc_001", "tool": "sc_qc", "tool_version": "PINNED", "tool_class": "registered",
     "inputs": ["asset_expression"], "outputs": ["qc_report", "qc_dataset"],
     "reproducibility_level_target": "conclusion", "requires_approval": false},
    {"node_id": "candidate_001", "tool": "cluster_candidate_discovery", "tool_version": "PINNED",
     "inputs": ["qc_dataset"],
     "parameters": {"candidate_types": ["C1", "C2", "C3"], "allow_unknown_label": true,
                     "rare_theta": 0.01, "min_cells": 50, "min_donors": 2,
                     "stability_protocol_id": "stab_min5_v1"},
     "outputs": ["candidate_sets", "marker_table", "stability_summary"], "requires_approval": true},
    {"node_id": "artifact_001", "tool": "artifact_screening", "tool_version": "PINNED",
     "inputs": ["candidate_sets"], "outputs": ["artifact_screening_result"],
     "assumptions_acknowledged": ["simulated_doublets_require_assumed_rate"]},
    {"node_id": "confirm_001", "tool": "pseudobulk_de", "tool_version": "PINNED",
     "inputs": ["candidate_sets", "qc_dataset"],
     "parameters": {"partition": "confirmation_specimens", "fdr": 0.05, "min_abs_log2fc": 1.0,
                     "detection_frequency_in": 0.6, "detection_frequency_excluded": 0.1},
     "outputs": ["de_table_support"]}
  ],
  "stop_conditions": ["input_schema_invalid", "critical_quality_failure",
                       "unresolved_design_confounding", "below_min_n_per_group"]
}
```

### A2. 证据卡（候选集合指纹 + 发现/确认分离 + 字段来源）

```json
{
  "card_id": "card_042",
  "candidate_id": "cand_k7f3q2_9ab41c_project001_r02",
  "display_name": "cluster_7 (run_02, resolution=1.0)",
  "member_fingerprint": "9ab41c...", "size": 1240, "donor_count": 3,
  "hypothesis_type": "cell_type",
  "candidate_label": "candidate_myeloid_subtype",
  "ontology": {"term_id": null, "ontology_name": null, "ontology_version": null, "mapping_source": "provisional_project_label"},
  "label_level": "provisional",
  "identity_vs_state": {"identity_claim": "myeloid_subtype?", "state_claim": null,
                         "continuous_state_check": {"method": "diffusion_pseudotime_axis", "result": "not_tail_of_known_axis"}},
  "trigger_signals": [{"type": "reference_coverage_gap", "value": 0.02}],
  "supporting_evidence_ids": ["ev_pbulk_001"],
  "confirmation_evidence_ids": ["ev_holdout_donors_004_005"],
  "contradicting_evidence_ids": ["ev_marker_absence_011"],
  "unresolved_conflict": false,
  "artifact_screening": {"doublet": "pass", "ambient": "warn", "low_quality": "pass",
                          "batch_specific": "pass", "stress_cycle": "pass", "over_split": "pass"},
  "novelty_vs_coverage": {"reference_coverage_score": 0.02, "novelty_signal": false,
                           "note": "coverage gap alone is not novelty"},
  "stability_summary": {"protocol_version": "stab_min5_v1",
                         "passed_perturbations": ["resolution", "seed", "feature_subset"],
                         "failed": ["leave_one_donor"], "metric_values": {"cc_ari": 0.63, "jaccard": 0.55}},
  "composition_check": {"method": null, "result": null},
  "cross_modal_status": "not_yet_validated",
  "field_provenance": {"size": {"source": "tool", "object_id": "asset_candidate_sets"},
                        "cc_ari": {"source": "tool", "object_id": "asset_stability_summary"},
                        "candidate_label": {"source": "model", "object_id": "draft_label_991"}},
  "support_rule_version": "supp_rule_v1",
  "final_status": "provisional",
  "status_reason": "leave_one_donor 扰动未通过且无独立模态确认，禁止 supported",
  "reviewer_id": "user_expert_01", "sign_off": "conditional", "reviewed_at": "2026-10-19T10:00:00Z"
}
```

---

## 附录 B. 技术参考资料

V1.0 的 11 条参考资料全部保留（Scanpy、scvi-tools 多模态教程、Nextflow、MuData、SpatialData、scib-metrics、Cell Ontology、CELLxGENE Census、LangGraph、2026 年单细胞 LLM Agent 基准论文、Seurat）。

V2.0 使用要求：

1. 每条在实施时重新验证版本、兼容性、维护状态与**许可**（附录 C 第 4 节）。
2. 第 10 条（单细胞 Agent 基准研究）仅用于借鉴**多维评价思路**；其任务划分与阈值不得直接当作本团队验收门槛——本团队门槛见 §14.4 且需基线实测。
3. 新增需登记的参考类别：双细胞/背景 RNA 估计方法的假设、ATAC QC 指标定义、组成检验方法、本体组合规范与单细胞数据报告规范；均纳入 ToolSpec `assumptions` 与 `quality_checks` 的来源。

---

## 附录 C. 待确认默认值与待决架构决策

### C1. 本稿所有默认数值（阶段 0 必须替换，禁止先写进代码常量）

| 位置 | 数值 | 确认方式 | 建议 owner |
|---|---|---|---|
| §1.5 | θ_rare=1%, n_min=50, d_min=2 | 历史项目候选分布盘点 | 领域负责人 |
| §1.5/§7.5 | ari_min=0.6, jac_min=0.5, pass_ratio=0.6 | 在反例集上标定假阳率 | 评测负责人 |
| §1.5 | tol=1e-6, lab_min=0.99 | GPU/CPU 差异实测 | 平台工程 |
| §5.6 | 对齐 Jaccard 阈值 0.6 | 历史重复运行对比 | 生信工程 |
| §5.3 | min_n_per_group=3 | 统计功效测算 | 统计顾问 |
| §7.8 | FDR≤0.05, lfc≥1, 60%/10% 检出频率, 80% 供体一致 | 专家共识 + 反例标定 | 领域负责人 |
| §4.4 | 全部规模/延迟/可用性数值 | 真实资产盘点 | 基础设施 |
| §4.5 | κ≥0.7 能力门槛、token 预算 | 小规模试点测算 | AI 工程 |
| §6.6 | 每篇 claim 数、复核人效、150 篇范围 | 试抽取 10 篇 | 知识治理 |
| §7.2 / §13.4 | 每项目卡片上限 10、25 min/卡、专家 4 h/周 | 试点实测 | 项目负责人 |
| §9.6 | 临时工具 1 工作日、晋升评审 3 工作日 | 试运行 | 平台负责人 |
| §10.5 | 稀有类 ≥200 细胞/≥3 供体、反例 ≥20、检索对 ≥50 | 功效测算 | 评测负责人 |
| §10.6 | 全部目标值与不可接受值 | 基线测量后定 | 项目 + 评测 |
| §10.8 | 高危 ≥50 / 中危 ≥20 用例 | 安全评审 | 安全工程 |
| §14.4 | DoD 十项阈值 | 阶段 0 定基线后锁定 | 三方裁定 |

### C2. 待决架构决策（ADR，需在阶段 1 前定稿）

- ADR-01 编排状态真值归属（状态机 vs Nextflow）
- ADR-02 注释标签存储真值（对象存储为真值 vs Postgres 逐行）
- ADR-03 单体拆分时点与判据
- ADR-04 上游定量是否纳入平台（当前：不纳入，§2.2）
- ADR-05 `project_adhoc` 临时工具是否默认开启
- ADR-06 本地模型选型与 GPU 预算
- ADR-07 客户内网部署的支持边界

### C3. 需团队自查、本稿仍未展开的部分

前端信息架构与证据卡可视化、报告模板与导出格式、客户门户的数据呈现范围、商务模型与定价。→ 见评审清单第六节"尚未覆盖部分"

### C4. 第三方许可登记（需在注册工具前逐项确认）

对象存储实现（AGPL 类）、ATAC 工具链、测序平台厂商软件、公共图谱数据、预训练注释模型、文献全文与片段、以及各 Python/R 包许可证矩阵。结论写入 ToolSpec `license_review_status`，由知识治理/法务共同确认。

### C5. 法规与条款核实清单（以官方现行文本为准，本稿不构成法律意见）

1. 人类遗传资源行政法规与实施细则的现行适用范围、对外提供/信息公开义务、国际协作场景与最新修订动向（2023 年实施细则已将对外提供方式调整为"事先报告 + 信息备份"，主管部门职责随机构改革调整，2026 年存在新一轮修订征求意见稿）。
2. 个人信息保护相关法律中健康医疗信息的处理与跨境路径；GDPR / HIPAA 的适用性判定。
3. 模型供应商协议：数据保留、训练使用、子处理商、区域与端点隔离。
4. 客户合同/DPA：数据用途、AI 参与度披露、删除义务、结果 IP 归属。

---

*本修订稿把 V1.0 的原则层保留，补齐了判定层、假设层与验收层。落地时的实际风险不在方案写得够不够细，而在附录 C1 的数值能否被真实项目数据替换——阶段 0 的资产盘点、专家封存与法务结论是整条路线能否成立的前提。*
