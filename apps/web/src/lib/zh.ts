/**
 * The Chinese side of the workbench.
 *
 * A key is the English source string exactly as it appears in the screen, so `t("Open project")` needs
 * no id registry and an untranslated screen still reads. `{name}` placeholders are part of the key and
 * are filled by the caller, so a sentence can move its numbers without depending on English word order.
 *
 * Keys are grouped by the screen that owns them and kept in that group's order. What the API answers is
 * deliberately absent: a refusal is displayed in the server's own words (ui.tsx, ProblemNote), and an
 * enum value keeps its stored spelling on the chip itself (title attribute) while this file supplies the
 * label a person reads.
 */

export const zh: Record<string, string> = {
  // ---------------------------------------------------------------- 导航与顶栏
  overview: "总览",
  data: "数据",
  plans: "方案",
  approvals: "审批",
  runs: "运行",
  "expert queue": "专家队列",
  exports: "导出",
  "no project open": "尚未打开项目",
  "sign in below": "请在下方粘贴 token",

  // ---------------------------------------------------------------- 通用组件
  "reading…": "读取中…",
  field: "字段",
  retryable: "可重试",
  after: "等待",
  trace: "trace",
  // final_status，原值仍留在 chip 的 title 上
  supported: "支持",
  provisional: "暂定",
  conflict: "冲突",
  unknown: "未知",
  rejected: "已被否定",

  // ---------------------------------------------------------------- 身份面板
  "Acting as": "当前身份",
  "The token decides the role; this panel only says which token a person is holding. A service account can draft, and can never sign off or decide an approval (§11.4).":
    "角色由 token 决定；这一面板只说明此刻拿的是谁的 token。服务账号可以起草，但永远不能签字，也不能决定审批门（§11.4）。",
  "paste a platform token first — the workbench stores no credentials of its own":
    "先粘贴一个平台 token——工作台自己不留任何凭据",
  "give the project id the screens should scope to": "给出这些屏幕要锁定到的项目 id",
  "the token could not be read": "这个 token 读不出来",
  "a compact JWS has three parts": "紧凑 JWS 应该有三段",
  "the token carries no subject": "token 里没有 subject",
  "service account": "服务账号",
  human: "人类",
  "mfa verified": "已二次因子",
  "no mfa": "未二次因子",
  "forget token": "忘记 token",
  "expires in {n} min": "还有 {n} 分钟过期",
  "past its exp by this clock — whether the API still honours it is its own call":
    "按本机时钟已经过了 exp——API 是否还认它，只有 API 能说了算",
  "no expiry claim to show": "没有 exp 声明可显示",
  "This token does not list project": "这个 token 的项目列表里没有项目",
  "The API scopes every read to the token, so the screens will report a denial rather than borrow another project's rows (§11.2).":
    "API 按 token 限定每次读取的范围，所以这些屏幕会报一条拒绝，而不是去借别的项目的行（§11.2）。",
  "Compact JWS": "紧凑 JWS",
  "held in this tab only: sessionStorage, never localStorage, never a URL":
    "只存在于本标签页：sessionStorage，不进 localStorage，也不进 URL",
  "Use this token": "用这个 token",
  Project: "项目",
  "every route below is project scoped on the server": "下面每一条路由在服务端都按项目限定范围",
  "Open project": "打开项目",
  current: "当前",

  // ---------------------------------------------------------------- 项目作用域
  "paste a platform token in the panel below; every read is made as someone.":
    "请先在下方面板里粘贴平台 token；每一次读取都得是某个人的身份。",
  "this screen is project scoped and needs a project in the path.": "这一屏按项目限定范围，路径里得带项目 id。",
  "the tab remembers project {remembered}, but the address names {address}. Reading {address}.":
    "本标签页记住的项目是 {remembered}，但地址栏写的是 {address}。按 {address} 读取。",

  // ---------------------------------------------------------------- 新建项目
  "Start or open a project": "开始或打开一个项目",
  "A project is the scope of every read: the API resolves the caller from the token and the project from the path.":
    "项目是每一次读取的范围：API 从 token 里解析出是谁在调用，从路径里解析出是哪个项目。",
  Open: "打开",
  "to register data, approve a plan, read the expert queue and export the report.":
    "即可登记数据、批准方案、阅读专家队列并导出交付报告。",
  "No project open. Create one below, or set a project id in the panel at the bottom.":
    "尚未打开项目。在下方新建一个，或在底部面板里直接设置项目 id。",
  "New project": "新建项目",
  "created_by is taken from the token you are holding, never typed in: an audit row that names a person has to name the person acting.":
    "created_by 取自你此刻拿着的 token，不允许手填：一条点名了人的审计记录，点的必须是正在操作的那个人。",
  Created: "已创建",
  "open it": "打开它",
  "what the delivery has to answer; it scopes which candidates matter": "交付要回答的问题；它划定了哪些候选项算数",
  "§13.4: this number is what the queue size has to fit inside": "§13.4：队列的规模必须能装进这个数字里",
  choose: "请选择",
  "creating…": "创建中…",
  "Create project": "创建项目",
  "paste a token first": "先粘贴 token",
  "This token reaches no registered project yet. Create one below, or ask for a token scoped to the project you work on.":
    "这个 token 还到达不了任何已登记的项目。可以在下方新建一个，或索取一个 scoped 到你正在做的项目的 token。",
  "project id": "项目 id",
  "only the ids this token is scoped to can be opened here; anything else is refused before a row is written (§11.2)":
    "这里只列出这个 token 被授权的项目 id；换成别的项目 id，会在写入任何记录之前就被拒绝（§11.2）",
  "this token carries every project, so the id is yours to name; the suggestion comes from the study goal and you can change it":
    "这个 token 覆盖全部项目，所以 id 由你来定；下面的建议是从研究目标里取的，你可以改",
  "prj_…": "prj_…",
  "This token is scoped only to projects that already exist, so no new project can be written with it. Every screen below is still reachable from the list above.":
    "这个 token 只授权到已存在的项目，因此没法用它新建项目。下方每一步仍然可以从上面的清单里进入。",
  "A project cannot attest its own cut set: until a principal investigator confirms the thresholds, the analysis still runs but signing a conclusion and exporting stay closed (§1.5).":
    "项目不能自己为自己的判据集合背书：在主要研究者确认阈值之前，分析照常运行，但结论签字与导出保持关闭（§1.5）。",

  // ---------------------------------------------------------------- token 里的角色
  principal_investigator: "主要研究者（PI）",
  analyst: "分析员",
  domain_expert: "领域专家",
  platform_admin: "平台管理员",
  compliance_officer: "合规官",
  knowledge_curator: "知识库管理员",
  service_agent: "服务账号",

  // ---------------------------------------------------------------- 公共短语
  "nothing is read until a token is set": "粘贴 token 之前不读取任何数据",
  "re-read": "重新读取",
  state: "状态",
  any: "全部",
  reason: "理由",
  decision: "决定",
  "no reason recorded": "未记录理由",
  yes: "是",
  no: "否",
  none: "无",
  is: "状态为",
  status: "状态",
  node: "节点",
  at: "位于",
  until: "直到",
  version: "版本",
  optional: "可选",
  currently: "当前",
  was: "原值",
  now: "新值",
  scope: "作用域",
  source: "来源",
  object: "对象",
  provenance: "溯源",
  thresholds: "阈值",
  Recorded: "已记录",
  "use the link": "点这个链接",
  " · group {group}": " · 独立组 {group}",

  // ---------------------------------------------------------------- 审批收件箱
  Approvals: "审批",
  "Approval inbox": "审批收件箱",
  "a lapsed window removes permission; it never lets a run advance (§9.2). Only a person who did not ask for the gate can open it (§11.6).":
    "窗口过期只是收回权限，绝不会让 run 往前走（§9.2）。只有没有申请这道门的人才可以打开它（§11.6）。",
  "overdue only": "只看逾期",
  "the overdue view of the same inbox": "同一个收件箱的逾期视图",
  "No gate is waiting in this inbox.": "这个收件箱里没有等待处理的门。",
  overdue: "已逾期",
  "asked for by": "申请人",
  "assigned to": "指派给",
  " · delegated from {who}": " · 由 {who} 转派而来",
  "decide by": "决策截止",
  "required: an approval without a reason is a missing audit row": "必填：没有理由的审批等于少一条审计记录",
  approve: "批准",
  refuse: "拒绝",
  "This gate was asked for by the subject you are acting as, so deciding it is refused here and at the API: the requester cannot open their own gate (§11.6). Delegate it instead.":
    "这道门正是你当前这个 subject 申请的，所以这里和 API 都会拒绝由你决策：申请人不能给自己开门（§11.6）。请改为转派。",
  "delegate to": "转派给",
  "moves the gate and records the move (§9.2)": "把门移给另一个人，并记录这次移动（§9.2）",
  delegate: "转派",

  // ---------------------------------------------------------------- 标签对齐
  "Label alignment": "标签对齐",
  "the same label in another run is a comparison, not a verdict: only a review can carry a conclusion across (§5.6)":
    "同一个标签出现在另一个 run 里只是比较，不是结论：只有评审才能把结论带过去（§5.6）。",
  "member fingerprint": "成员指纹",
  "back to the card": "返回这张卡片",
  "Alignment history": "对齐历史",
  "no alignment has been recorded for this label.": "这个标签还没有记录过对齐。",
  "OTHER CANDIDATE": "另一个候选",
  "OTHER RUN": "另一个 RUN",
  JACCARD: "JACCARD",
  KIND: "当时判定",
  "KIND TODAY": "今天算作",
  "CARRY OVER": "是否承接",
  "DECIDED BY": "决策人",
  "Same label in other runs": "其他 run 里的同名标签",
  "no other candidate carries this label.": "没有别的候选带着这个标签。",
  CANDIDATE: "候选",
  RUN: "RUN",
  SIZE: "规模",
  DONORS: "供者数",
  ALIGNED: "已对齐",

  // ---------------------------------------------------------------- 证据卡片
  "Evidence card": "证据卡片",
  "the card is read through the candidate that produced it, never through a cluster label":
    "卡片只能经由产生它的候选来读取，不能经由一个聚类标签",
  "run {run} · candidate {candidate} · support rule {rule} · thresholds {thresholds}":
    "run {run} · 候选 {candidate} · 支持规则 {rule} · 阈值 {thresholds}",
  claimed: "宣称的内容",
  "no label — hypothesis {type}": "没有标签——假设类型 {type}",
  ", reason {reason}": "，原因 {reason}",
  "label level": "标签层级",
  "candidate types": "候选类别",
  "status reason": "状态原因",
  "artifact screening": "技术假象筛查",
  "not screened on this card": "这张卡片没有做过该项筛查",
  composition: "组成",
  stability: "稳定性",
  "no perturbation summary": "没有扰动摘要",
  "novelty vs coverage": "新颖度对覆盖度",
  "cross modal": "跨模态",
  "AI Review Memo": "AI 生信审查备忘录",
  Confidence: "置信度",
  "Statistical Power Alert": "统计检验力受限警示",
  "Confounding Risks": "潜在混杂与伪影风险",
  "Stability (5-Perturbation)": "稳健性 (5 类扰动协议)",
  "Recommended Action for Expert": "建议专家审查操作",
  "Two records that carry it": "撑起它的两条记录",
  "nothing is listed as supporting evidence": "没有任何记录被列为支持证据",
  "The record against it": "反对它的那条记录",
  "no counter-record is filed on this card": "这张卡片上没有归档反对记录",
  "the rest, and the literature ({n} items)": "其余证据与文献（{n} 条）",
  "Trigger signals are what made the system look here; §7.5 keeps them out of the support set, and they are shown apart for the same reason.":
    "触发信号是系统为什么会看到这里的原因；§7.5 把它们排除在支持集之外，这里也因同样的理由单独列出。",
  Review: "评审",
  "the card now reads": "卡片现在的状态是",
  "Sign-off": "签字",
  "the person who drafted or reviewed this conclusion cannot sign it (§13.1)":
    "起草或评审这条结论的人不能为它签字（§13.1）",
  "on a card in state": "签在一张状态为",
  "Who wrote each number": "每个数字由谁写出",
  "§10.4: a value produced by a model is a defect, not a draft": "§10.4：模型产出的值是缺陷，不是草稿",
  "this card carries no field provenance rows": "这张卡片没有任何字段溯源记录",
  Trail: "轨迹",
  "every revision supersedes a review id; nothing is silently overwritten":
    "每次修订都会取代一条评审 id；没有任何东西被静默覆盖",
  "supersedes {id}": "取代 {id}",
  "carried over from {id}": "由 {id} 承接而来",
  "No person has acted on this card yet.": "还没有人对这张卡片做过动作。",
  "back to the queue": "返回队列",
  "the run this card is about": "这张卡片所属的 run",
  "what happened to this label in other runs": "这个标签在其他 run 里的情况",
  "independent confirmation": "独立确证",
  rationale: "理由说明",
  "required; it is the sentence a reader of the delivery will see": "必填；交付的读者看到的就是这句话",
  "a re-read that keeps the earlier verdict still gets its own row": "即使重读后结论不变，也要单独记一行",
  "recording…": "记录中…",
  "Record review": "记录评审",
  "this decision moves the status, and the gate decides whether it may": "这个决定会改变状态，而门会裁定它能不能改",
  "an approval adopts the card's conclusion, so it moves the status up to the ceiling; a card that states no hypothesis has nothing to adopt":
    "采纳是对卡片结论的认领，所以它会把状态抬到这张卡的天花板；没有写出假设的卡片没有结论可采纳",
  "this card can reach at most": "这张卡片最多只能到",
  sign_off: "sign_off",
  "required when withdrawing a sign-off": "撤销签字时必填",
  "Sign off": "签字",
  " · signed by {who}": " · 由 {who} 签署",
  " · not yet signed by a named person": " · 还没有具名的人签署",

  // ---------------------------------------------------------------- 数据登记
  Data: "数据",
  "Register data": "登记数据",
  "client-side direct upload is the only write path for object bytes: the API hands out a short-lived PUT signature for a key it derived itself":
    "浏览器直传是对象字节唯一的写入路径：API 为它自己推导出的 key 发一个短时效的 PUT 签名",
  "object to upload": "要上传的对象",
  "the digest is computed from these bytes; a mismatch at completion is a conflict, not a warning":
    "摘要由这些字节算出；完成阶段对不上是一个冲突，不是一个警告",
  bytes: "字节",
  "cross_project_allowed is what lets another project read this row at all":
    "只有 cross_project_allowed 才允许别的项目读到这一行",
  "unknown requires upstream pipeline metadata, so it is deliberately not the default guess here":
    "unknown 需要上游流程的元数据，所以这里故意不把它当成默认猜测",
  "This object is declared unfilterable-as-known, so the pipeline that produced it becomes part of the record. All four fields go to the API verbatim.":
    "这个对象被声明为“无法确证是否过滤过”，于是产出它的流程就成了记录的一部分。四个字段都会原样发给 API。",
  "upstream pipeline name": "上游流程名",
  "defaults to the file digest; a dataset-level fingerprint may differ": "默认取文件摘要；数据集级别的指纹可能不同",
  "declaring, uploading, completing…": "申报、上传、完成中…",
  Register: "登记",
  "Registered assets": "已登记的对象",
  "a download link is a short-lived signature, handed out per request and never stored on the row":
    "下载链接是一个短时效签名，按每次请求发放，从不写进这一行",
  "Signed for": "已为",
  asset: "对象",
  format: "格式",
  retention: "保留",
  filtering: "过滤",
  "produced by": "产出者",
  "registered by hand": "人工登记",
  download: "下载",
  "Nothing is registered in this project yet.": "这个项目还没有登记任何对象。",

  // ---------------------------------------------------------------- 数据向导
  "declare every field myself": "我自己逐字段申报",
  "pick a file and this screen reads the rest from its bytes; the digest is computed here, so a conflict at completion means the object plane really holds something else":
    "选一个文件，其余内容由这个界面从字节里读出来；摘要在这里算出，所以完成阶段对不上，意味着对象存储里确实放着别的东西",
  "file to register": "要登记的文件",
  "a counts document the workflow can read, or an h5ad this deployment reports on instead":
    "工作流能读取的 counts 文档；或 h5ad——本部署对它只做报告，不做解析",
  "This is an h5ad, and a browser cannot read HDF5. The reference converter runs as a command and refuses to guess the annotation: it maps specimen_id, donor_id and group onto the design row, keeps both sides of a lossy conversion, and drops every obs column nobody declared.":
    "这是一个 h5ad，而浏览器读不了 HDF5。参考转换器以命令行运行，并且拒绝猜测注释：它把 specimen_id、donor_id、group 映射到设计行上，为有损转换同时保留两侧，并丢掉每一列无人声明过的 obs 字段。",
  "Its output is a JSON document plus a manifest in .workbench/admissions; registering that document is this same screen, with the file picked again.":
    "它的输出是一个 JSON 文档，外加 .workbench/admissions 里的一份清单；登记那个文档，就是回到这个界面把文件再选一次。",
  "what the plan expects": "方案期望什么输入",
  "This file is too large to preview in a browser, so nothing has been checked and nothing is registered. The digest above is real; the contents are not read.":
    "这个文件太大，浏览器无法预览，所以既没有检查过内容，也没有登记。上面的摘要是真实的；内容没有被读过。",
  "This file is not a document the workflow reads, so it is not registered. The reference input is the JSON product the converter writes, whose top level names the slot it answers for.":
    "这个文件不是工作流会读取的文档，所以没有登记它。参考输入是转换器写出的那个 JSON 产物，它的顶层写明自己应答哪个 slot。",
  slot: "slot",
  missing: "缺失",
  "a run reads its root input from the counts slot": "run 从 counts slot 读取自己的根输入",
  contract: "契约版本",
  cells: "细胞数",
  genes: "基因数",
  donors: "供体",
  "{donors} donors, {specimens} specimens, {groups} groups": "{donors} 个供体、{specimens} 个样本、{groups} 个分组",
  "identifier space": "标识符空间",
  "the document names none": "文档没有写明",
  "The slot on this document is not counts, so no run can start from it.": "这个文档上的 slot 不是 counts，所以没有 run 能从它启动。",
  "These design columns are empty on at least one cell, and every rarity and robustness judgement counts donors:":
    "至少一个细胞在这些设计列上是空的，而每一次稀有度与稳健性判断都要数供体：",
  "{n} cell keys repeat; a matrix has to name each cell once.": "{n} 个 cell key 是重复的；矩阵必须给每个细胞唯一的名字。",
  "The reference pseudobulk step sums counts and refuses a matrix declared as":
    "参考流程的 pseudobulk 步骤对计数求和，它会拒绝一个声明为",
  "Declaration fields": "声明字段",
  "An unknown filtering state is a statement that nobody can show the filtering was done, so the pipeline that produced the object becomes part of the record. All four fields go to the API verbatim.":
    "把 filtering_state 声明为 unknown，是在说无人能证明过滤做过，于是产出这个对象的流程成为记录的一部分。四个字段都会原样发给 API。",
  "Register this file": "登记这个文件",
  "build the plan around it": "围绕它构建方案",
  "Nothing is sent until this screen can read the file it is declaring.": "在这个界面能读出它要申报的文件之前，什么也不会发送。",

  // ---------------------------------------------------------------- 方案向导
  "Build a plan": "构建方案",
  "the tool rows, their slots and their resource floors are read from the same registry the run executes against; the design, the partitions and the stage-0 numbers stay yours to state":
    "工具行、它们的 slot 和资源下限，都取自 run 实际执行时所对照的那份注册表；设计、分区与 stage-0 数值仍然由你来声明",
  "launch it from the overview": "在总览页启动它",
  "an h5ad cannot be read in a browser; convert it first, then point this screen at the JSON it writes":
    "浏览器读不了 h5ad；先转换，再让这个界面去读转换写出的那个 JSON",
  "this file is too large to read here, so the design below has to be typed": "这个文件太大，这里读不了，所以下面的设计需要手工填写",
  "this file is not a counts document, so no design could be read from it": "这个文件不是 counts 文档，所以无法从它读出设计",
  "read the design from the data file": "从数据文件读出设计",
  "this uploads nothing: it is the same bytes the data screen registered, opened here to list specimens, donors and groups":
    "这里不上传任何东西：它就是数据页登记过的那份字节，在这里打开只是为了列出样本、供体与分组",
  specimens: "个样本",
  objective: "研究目标",
  "what the run is being asked to settle, in one sentence": "这次 run 被要求回答的问题，用一句话写",
  "derived from the objective when left empty": "留空时由研究目标推导",
  Design: "设计",
  "the column this matrix actually carries": "这份矩阵实际带着的这一列",
  "the design column named on every cell": "每个细胞都写明的那个设计列",
  "donors per group in the whole matrix": "整份矩阵中每组的供体数",
  "defaults to the cut set in force; a run reports the groups under it rather than dropping them":
    "默认取当前生效的阈值集；run 会报告低于它的分组，而不是把分组丢掉",
  "confounding check name": "混杂检查名",
  "what was looked at, e.g. group_vs_donor": "检查的是什么，例如 group_vs_donor",
  "which tool ran it": "是哪个工具跑的它",
  "its result": "它的结果",
  "NOT_RUN is honest and the contract refuses it: a check that has not run is a gap, not a placeholder":
    "NOT_RUN 是诚实的，而契约会拒绝它：没跑过的检查是一个缺口，不是一个占位符",
  "detail (optional)": "补充说明（可选）",
  "Declaring PASS is a claim about a check that ran and passed. This screen cannot run one, so it leaves the field at NOT_RUN and believes what you type.":
    "声明 PASS 是在断言某个检查跑过且通过了。这个界面跑不了任何检查，所以它把该字段留在 NOT_RUN，并且相信你输入的内容。",
  Partitions: "分区",
  "the rule that separated discovery from confirmation, named so a reviewer can check it":
    "把发现集与确认集分开的规则，写下名字复核者才能核对",
  "ids separated by commas; pick them from the list the data file gave": "用逗号分隔的 id；可以从数据文件给出的列表里挑",
  "leave empty when the study has no independent confirmation set; say so in the objective":
    "研究没有独立确认集时留空，并在研究目标里说明",
  Steps: "步骤",
  "the name, version, slots and resource floor of every step below come from the registry this deployment hydrates; a node that emits a conclusion asset or is ad-hoc is gated whatever this screen thinks":
    "下面每一步的名称、版本、slot 与资源下限，都来自本部署实际装载的注册表；产出结论资产或 ad-hoc 的节点，无论这个界面怎么想，都是受门的",
  reads: "读取",
  writes: "写出",
  gated: "需过门",
  "at least": "至少",
  "gene symbols in this matrix": "个基因符号属于这份矩阵",
  "no matrix read here, so the names cannot be checked against one": "这里没有读取任何矩阵，所以这些名字无法核对",
  "the contrast's first arm": "对比的第一组",
  "the contrast's second arm": "对比的第二组",
  "the thresholds handed to this step are the set in force on the project, verbatim:": "交给这一步的阈值，就是项目当前生效的那一套，原样：",
  "which §1.5 reports as unconfirmed, so a finding computed on it cannot be signed off or exported":
    "§1.5 报告它未经确认，所以用它算出的结论不能签署，也不能导出",
  "attest the measured cuts": "对实测阈值作出确认",
  "The QC policy this node runs (§7.7)": "该节点运行的 QC 策略（§7.7）",
  "the step refuses to run without it": "这一步没有它就拒绝运行",
  remove: "移除",
  "add a rule": "增加一条规则",
  "mark keeps the cell and labels it at risk; drop deletes it, and §7.7 only allows that with a named approver, a reason and a report annotation - which this form refuses rather than assuming.":
    "mark 保留细胞并给它标上风险；drop 会删除它，而 §7.7 只有在写明批准人、理由并在报告中注明时才允许这样做——这份表单宁可拒绝，也不会替你假定这些已经具备。",
  "Budget and stops": "预算与停止条件",
  "the quota the project was given is what the server compares this to": "服务端会拿它与项目被授予的配额相比",
  "the three mandatory stops are already here; add more, do not remove them": "三条强制停止条件已经写在这里；可以补充，不要删掉",
  "what this plan promises to produce; conclusion assets are the ones a person has to sign":
    "这份方案承诺产出的东西；其中结论资产必须由人来签署",
  "This screen can see the plan is not complete": "这个界面能看出方案还不完整",
  "Recording is still allowed: the server's own validators have the last word, and a refusal it gives is shown here in its words.":
    "仍然可以登记：最终裁定权在服务端自己的校验器手里，它给出的拒绝会按它自己的话显示在这里。",
  "the body this form sends": "这份表单将要发送的正文",
  "Record this plan": "登记这份方案",
  "the slot the run is handed one registered asset for; this workflow fills exactly one":
    "run 会用一份已登记的资产来应答这个 slot；本流程恰好只填一个",
  "the objective is the sentence an expert reads first; it is empty": "研究目标是专家最先读的那句话，现在是空的",
  "the partition rule names how discovery was separated from confirmation": "分区规则要写明发现集是怎么与确认集分开的",
  "the discovery partition has to hold at least one specimen": "发现分区至少要包含一个样本",
  "a specimen cannot sit in both partitions": "一个样本不能同时位于两个分区",
  "a confounding check has to name the tool that ran it": "混杂检查必须点明跑它的工具",
  "the contract refuses a design matrix whose confounding check has not run: record the check and its result":
    "契约会拒绝一个混杂检查尚未运行的设计矩阵：请记录这项检查及其结果",
  "a plan with no nodes runs nothing": "没有节点的方案什么都不会运行",
  "this workflow fills one root slot and these choices leave": "本流程只填一个根 slot，而这些选择留下了",
  "is the number the step refuses to run without": "是这一步没有它就拒绝运行的数值",
  "qc: the policy has to name its id, version, tissue and platform": "qc：策略必须写明它的 id、版本、组织与平台",
  "qc: a policy with no rules screens nothing; say why in the objective": "qc：没有规则的策略什么都不筛查；请在研究目标里说明原因",
  "qc: every rule needs a name the card can cite": "qc：每条规则都需要一个卡片可以引用的名字",
  "qc rule": "qc 规则",
  "needs a bound": "需要一个界限",
  "needs an upper bound": "需要上界",
  "needs a lower bound": "需要下界",
  "is set by hand, so §7.7 requires the reason": "是手工设定的，所以 §7.7 要求写明理由",
  "cites a claim it does not name": "引用了它并未点名的知识断言",
  "must name the diagnostic its quantile came from": "必须点明它的分位数出自哪个诊断",
  "pseudobulk_de: a contrast needs two different arms to compare": "pseudobulk_de：一个对比需要两个不同的组才能比较",
  "measured on": "实测于",
  "is below the registry's floor for this tool": "低于注册表为该工具设定的下限",
  "under-powered against min_n_per_group in the discovery partition": "在发现分区里低于 min_n_per_group",
  "is not in this matrix's gene list": "不在该矩阵的基因列表里",
  "the pseudobulk step sums counts and refuses a matrix declared as something else":
    "pseudobulk 步骤对计数求和，并会拒绝一个声明为其他状态的矩阵",
  "measure a candidate a finished run named, so they are not offered here":
    "度量的是一个已完成的 run 所点名的候选集，所以这里不提供这些步骤",
  "nothing is registered in this project yet, so a run launched from this plan has no input asset to name":
    "这个项目还没有登记任何对象，所以由这份方案启动的 run 没有可点名的输入资产",
  steps: "步骤数",
  digest: "摘要",
  "recorded by": "登记人",
  "Recorded plans": "已登记的方案",
  "a launch names one of these versions and pins it; recording never authorised a run":
    "一次启动会点名其中一个版本并钉住它；登记本身从不授权 run",
  "Nothing is recorded here yet.": "这里还没有登记任何记录。",
  "the overview launches a recorded version": "总览页会启动某个已登记的版本",
  "write the whole contract as JSON": "我自己把整份契约写成 JSON",
  "the full contract; unknown keys are refused, not ignored - the wizard's review panel shows one that is complete":
    "整份契约；未知的键是被拒绝而不是被忽略——向导的检视面板会给出一份完整可用的",

  // ---------------------------------------------------------------- 导出
  Exports: "导出",
  "No exports yet": "还没有导出记录",
  "Queue an export": "排队一次导出",
  "filters narrow the artefact; an unknown run id is refused rather than answered with everything":
    "筛选条件是收窄产物的；一个不存在的 run id 会被拒绝，而不是把全部内容交出来",
  poll: "轮询",
  "until it reports READY.": "直到它报告 READY。",
  kind: "种类",
  "optional; limits the artefact to one run's products": "可选；把产物限制在某一个 run 的产出",
  "optional; e.g. only the cards that reached supported": "可选；例如只要达到 supported 的卡片",
  "a resend of this click returns the original job": "重发这次点击会返回原来那个任务",
  "queueing…": "排队中…",
  Queue: "排队",
  "a download link is handed out per read and expires; the row itself is the durable thing":
    "下载链接按每次读取发放并且会过期；真正长期存在的是这一行记录",
  export: "导出",
  "requested by": "申请人",
  created: "创建时间",
  "No export has been queued in this project.": "这个项目还没有排过任何导出。",
  artefact: "产物",
  "not written yet": "尚未写出",
  "failure reason": "失败原因",
  "only a READY export has a link": "只有 READY 的导出才有链接",

  // ---------------------------------------------------------------- 项目总览与 run
  "Project overview": "项目总览",
  "Where this stands": "现在走到哪一步",
  "each line is a read the API made just now, in the order the work goes: register, plan, run, review, deliver":
    "每一行都是 API 刚刚给出的读取结果，按工作的顺序排列：登记、方案、运行、复核、交付",
  "What each role does here": "每个角色在这里做什么",
  "separation of duties is the server's rule, not a screen setting: the person who asks for a gate is never the one who opens it (§11.6)":
    "职责分离服务端的规则，不是界面上的开关：申请审批门的人永远不是打开它的人（§11.6）",
  "Register the counts": "登记 counts 矩阵",
  "at least one asset is recorded in this project": "这个项目里至少已登记一份资产",
  "nothing is registered here yet, so a plan would have no input to name":
    "这里还没有任何登记，方案也就没有可点名的输入",
  "Build and record a plan": "构建并登记方案",
  "this project has a plan version a run can pin": "这个项目已有可供 run 钉住的方案版本",
  "a run pins a plan version, and none is recorded yet": "run 要钉住一个方案版本，而目前还没有登记",
  "{n} runs recorded, latest in state {state}": "已记录 {n} 个 run，最近一个处于 {state}",
  "no run has been launched against this plan yet": "还没有针对这个方案发起过 run",
  "Open the gates a run needs": "打开 run 需要的审批门",
  "{n} of them are assigned to you": "其中 {n} 个指派给了你",
  "{n} are assigned to other people; a gate opens only on the subject it names (§11.6)":
    "{n} 个指派给了别人；审批门只有它点名的那个主体才能打开（§11.6）",
  "no gate is waiting in this inbox": "这个收件箱里没有等待处理的门",
  "Review the drafts": "复核草稿",
  "candidates are recorded, so the queue has cards to read": "候选亚群已记录，队列里有卡片可读",
  "a run has to complete before cards exist to review": "要先有跑完的 run，才会有可复核的卡片",
  "Sign off and export": "签字与导出",
  "the cut set is attested, so these exits are open to the people the policy names":
    "判据集合已背书，这些出口对策略指定的人是打开的",
  "the stage-0 cut set is unconfirmed: signing a conclusion and exporting stay closed until a principal investigator confirms it with a second factor (§1.5)":
    "stage-0 判据集合尚未确认：在主要研究者用第二因子确认之前，结论签字与导出保持关闭（§1.5）",
  "Some of these counts could not be read, so the list below is not the whole picture.":
    "其中几项计数没能读到，所以下面的清单不是完整情况。",
  "Each panel that failed says why in the server's own words.": "每个读失败的面板都用服务端原文写明了原因。",
  "This token presents as {subject}, with roles {roles}.": "这个 token 的持有者是 {subject}，角色为 {roles}。",
  "No token is set, so nothing here can be read or acted on.": "还没有设置 token，这里既读不到也无法做任何事。",
  "This list says what a role is for. It is not a permission check: the API decides every action, and a refusal is shown in its own words.":
    "这份清单说的是每个角色的职责，不是权限判定：每一次操作都由 API 决定，拒绝时显示它的原话。",
  done: "已完成",
  "waiting on you": "等你处理",
  "waiting on someone else": "等别人处理",
  "your next step": "你的下一步",
  "registers data, builds the plan and launches runs; asks for a gate but cannot open the one it asked for":
    "登记数据、构建方案、发起 run；可以提出审批门，但打不开自己提出的那一个",
  "owns the project record: decides the gates assigned to them, confirms the cut set, and is the only role that may end a run":
    "对项目记录负责：决定指派给自己的审批门、确认判据集合，也是唯一可以结束 run 的角色",
  "reviews evidence cards in the queue and records the reason behind each decision":
    "在队列里复核证据卡片，并记录每个决定背后的理由",
  "holds the provider terms and the egress approval a cloud call needs before it is made":
    "保管服务商条款，以及云端调用发出前需要的出境审批",
  "curates the private knowledge base a suggestion is retrieved from": "维护检索建议所依据的私有知识库",
  "may execute work a person already authorised, and is refused everywhere a decision belongs to a person":
    "可以执行人格化主体已经授权的工作；凡是属于人的决定，一律拒绝",
  "nothing is read until a token is set — the API resolves the caller from it":
    "设了 token 之前不读取任何东西——API 从它解析出调用者",
  "study goal": "研究目标",
  species: "物种",
  "none declared": "未声明",
  "annotation target": "注释目标",
  "review budget": "评审预算",
  "h/week": "小时/周",
  "unconfirmed stage-0 cut set · sign-off and export are closed (§1.5)":
    "stage-0 切集未经确认 · 签字与导出都是关闭的（§1.5）",
  " · expert queue cap {cap} cards (§13.4)": " · 专家队列上限 {cap} 张卡片（§13.4）",
  Runs: "Run 列表",
  "a launch answers 202 with a queue position; results are read from the cards this run produced, never from this row":
    "启动返回 202 并给出队列位置；结果要从这个 run 产出的卡片读取，永远不是从这一行",
  run: "run",
  plan: "方案",
  queue: "队列",
  started: "开始时间",
  "not queued": "未排队",
  "This project has no runs yet.": "这个项目还没有 run。",
  "Launch a run": "启动一个 run",
  "the plan has to be recorded and its input assets registered first; a launch that names an unknown asset is refused, not guessed around":
    "必须先记录方案、并登记它的输入对象；点名一个不认识的对象去启动会被拒绝，而不是靠猜测绕过去",
  Queued: "已排队",
  ", in state": "，状态为",
  ", position": "，位置",
  "open the run": "打开这个 run",
  "comma separated; each one has to be registered in this project": "逗号分隔；每一个都必须已在本项目登记",
  "the registry version the run is claimed reproducible against": "这个 run 声称可复现时所依据的注册表版本",
  "0 lowest, 9 highest": "0 最低，9 最高",
  "a resend of this click replays the original run; a new intent gets a new key":
    "重发这次点击会复现原来那个 run；新的意图要换新的 key",
  "launching…": "启动中…",
  Launch: "启动",

  // ---------------------------------------------------------------- 方案
  Plans: "方案",
  "nothing is recorded until a token is set": "设了 token 之前不记录任何东西",
  "the body is not JSON": "正文不是 JSON",
  "Record a plan": "记录一个方案",
  "recording stores the plan and its digest; it authorises nothing. A gated plan still needs a person's decision before a node runs (§9.6).":
    "记录只是把方案和它的摘要存下来，不构成任何授权。带门的方案在节点运行前仍然需要一个人类的决定（§9.6）。",
  "Three rules the contract enforces itself, so this form does not argue about them: a design matrix needs a confounding check that has actually run, an ad-hoc tool node has to require approval, and a node that emits a conclusion asset cannot be ungated.":
    "合同自己就强制三条规则，所以这个表单不与人为它们争论：设计矩阵需要一个真正跑过的混杂因素检查，ad-hoc 工具节点必须要求审批，而产出结论对象的节点不能不带门。",
  ", version": "，版本",
  " as digest": "，摘要为",
  "Launch it from the project overview — an approval is requested against the run, not against this row.":
    "在项目总览页启动它——审批是针对 run 申请的，不是针对这一行记录。",
  "plan body": "方案正文",
  "the full contract; unknown keys are refused, not ignored": "完整合同；不认识的键会被拒绝，而不是被忽略",
  "Record plan": "记录方案",
  "reset to template": "恢复为模板",

  // ---------------------------------------------------------------- 专家队列
  "Expert queue": "专家队列",
  "Which run": "哪一个 run",
  "cards are drafted from the products of one run; a cluster label is only an alias inside that run (§5.6)":
    "卡片是从某一个 run 的产物起草的；聚类标签只是那个 run 内部的一个别名（§5.6）",
  "No run to queue yet.": "还没有可排队的 run。",
  "Draft the cards": "起草卡片",
  "drafting files cards whose hypothesis is still unknown; a card a person has already acted on is refused, not overwritten (§9.1)":
    "起草归档的是假设仍为 unknown 的卡片；已经被人类动作过的卡片会被拒绝，而不是被覆盖（§9.1）",
  "{n} cards filed for run": "以下 run 已提交 {n} 张卡片",
  "against thresholds": "所依据阈值",
  "Ceilings:": "上限：",
  "drafting…": "起草中…",
  "Draft cards for this run": "为这个 run 起草卡片",
  "The queue below is re-derived from the drafts, so it cannot drift from the run that produced them. Drafting needs the write role; reading it does not.":
    "下面的队列是从这些草稿重新推导出来的，所以它不会与产出它们的 run 脱节。起草需要写入角色；读取不需要。",
  "score = impact (0-3) × uncertainty (0-3) + client ask (0/2)": "得分 = 影响（0-3）× 不确定度（0-3）+ 项目点名（0/2）",
  "candidate ids the project named as ones it is asking about; each adds 2": "项目点名说想了解的那些候选 id；每个加 2 分",
  limit: "上限",
  "the plan's own cap by default (§13.4: 10 cards per project queue)": "默认用方案自带的上限（§13.4：每个项目队列 10 张卡片）",
  " {n} drafted": " 已起草 {n} 张",
  " · {n} in the queue": "，队列中 {n} 张",
  " · {n} filed as observed": "，{n} 张归档为待观察",
  " · cap {cap}": "，上限 {cap}",
  " · rules": "，规则",
  "on thresholds": "基于阈值",
  population: "群体",
  impact: "影响",
  uncertainty: "不确定度",
  ask: "点名",
  score: "得分",
  "why this place": "为什么排在这里",
  "Filed as observed": "归档为待观察",
  "over the cap, or below the score floor: kept for the report appendix, not shown to an expert this week (§13.4)":
    "超出上限，或低于得分下限：留作报告附录，本周不会展示给专家（§13.4）",
  "What the gates already know": "门已经知道的事",
  "a ceiling is not a reviewer's caution; it is a refusal the numbers force":
    "上限不是评审员的谨慎；它是被这些数字逼出来的拒绝",
  "the design is under-powered for the claim this card would support": "就这张卡片要支撑的结论而言，这个设计效力不足",
  "blocking risk:": "阻断性风险：",

  // ---------------------------------------------------------------- run 详情
  Run: "Run",
  "a run is a claim about inputs, a plan digest and a registry snapshot; the cards it produced are read through the queue, never from this row":
    "一个 run 是关于输入、方案摘要和注册表快照的一次声明；它产出的卡片要经由队列读取，永远不是从这一行",
  "queue position": "队列位置",
  "reproducibility target": "可复现级别目标",
  "registry snapshot": "注册表快照",
  inputs: "输入",
  "{n} assets, each pinned by digest": "{n} 个对象，每一个都用摘要钉住",
  tools: "工具",
  "end this run": "结束这个 run",
  "ending a run is a person's act; a service token is refused here (§9.1)":
    "结束一个 run 是人的动作；服务账号 token 在这里会被拒绝（§9.1）",
  "cancel run": "结束 run",
  "loop budget": "循环预算",
  used: "已用",
  ceiling: "上限",
  iterations: "迭代次数",
  "compute minutes": "计算分钟",
  "model cost usd": "模型花费（美元）",
  "The loop budget is exhausted: another targeted analysis is not permitted until a person raises the ceiling on the plan, which means a new plan version (§9.2). The rule says so in the server's words:":
    "循环预算已经用尽：在一个人类把方案的上限提高（也就是产生一个新方案版本）之前，不允许再做定向分析（§9.2）。规则用服务端自己的话说明如下：",
  "Request a gate": "申请一道门",
  "requesting is not deciding: the decision belongs to someone else, and a service account can neither decide nor delegate (§11.4).":
    "申请不等于决定：决定属于另一个人，而服务账号既不能决定也不能转派（§11.4）。",
  Gate: "门",
  ", assigned to": "，指派给",
  "the project owner": "项目所有者",
  "node_id (not used for this kind)": "node_id（这种种类用不上）",
  "who is accountable; leaving it open routes the gate to the project owner":
    "由谁负责；留空就把这道门派给项目所有者",
  "the version being approved": "被批准的那个版本",
  "only used by a plan gate": "只有方案门会用",
  "requesting…": "申请中…",
  "Request approval": "申请审批",

  // ---------------------------------------------------------------- 运行跟踪（第三批①）
  // 运行日志
  Journal: "运行日志",
  "every state change is written here with the actor who moved it and the reason they gave; a retry and a spend line are the same trail (§9.2)":
    "每一次状态变更都在这里记下动了它的人和他给出的理由；一次重试和一行花费属于同一条轨迹（§9.2）",
  "every kind": "所有种类",
  when: "何时",
  from: "从",
  to: "到",
  who: "谁",
  "why it moved": "它为什么动了",
  attempt: "第几次",
  iteration: "迭代",
  "{compute} compute minutes · {cost} model cost": "{compute} 计算分钟 · {cost} 模型花费",
  "No journal line has been written for this run, so nothing has moved it yet.":
    "这个 run 还没有写下任何一行日志，也就是说还没有任何东西动过它。",
  "watch this run": "盯着这个 run",
  "watching": "正在盯",
  "watching re-reads this run and its journal every five seconds; it never signs anything off":
    "盯着它，意思是每五秒重新读一次这个 run 和它的日志；它永远不会替任何人签字确认",
  // 运行列表
  "one row per run, read from the catalog: the state is where the state machine left it, and the queue position is what the scheduler answered just now":
    "一行一个 run，都从目录里读：状态是状态机把它留在了哪儿，队列位置是调度器刚才的回答",
  "watch this list": "盯着这个列表",
  "watching re-reads this list every five seconds; it changes nothing":
    "盯着它，意思是每五秒重新读一次这个列表；它不改动任何东西",
  "the filter is the server's own enum; an empty choice asks for every state":
    "筛选用的是服务端自己的枚举；留空就是问所有状态",
  "every state": "所有状态",
  "getting underway": "正在起步",
  "being computed": "正在计算",
  "waiting for a person": "在等人",
  "stopped short": "中途停下",
  finished: "已经跑完",
  showing: "正在显示",
  "runs per page, read from the catalog in the server's order": "每页多少个 run，按服务端自己的顺序从目录读取",
  "{n} of this page, from position {offset}": "本页 {n} 条，从第 {offset} 位开始",
  "go on with": "接着做",
  "journal and gates": "日志与门",
  "previous page": "上一页",
  "next page": "下一页",
  "No run in this project is in that state. The list is what the server returned, so nothing is hidden by the filter.":
    "这个项目里没有 run 处在那个状态。列表就是服务端返回的内容，筛选本身没有藏起任何东西。",

  // ---------------------------------------------------------------- 候选检索（第三批②）
  "Find a population": "查找细胞群体",
  "these are the populations this run produced, and a cluster label is only the name it gave them (§5.6); every filter is answered by the index before the page is cut":
    "这里列的是这个 run 产出的细胞群体，而聚类名只是它给它们起的名字（§5.6）；每一个筛选条件都在分页之前由索引回答",
  "the label as this run wrote it; it names nothing outside this run": "这个 run 写下的标签；它指不出本 run 之外的任何东西",
  reviewed: "是否已复核",
  "whether a person has already acted on the card behind it": "它背后的那张卡片是否已经有人做过动作",
  either: "不限",
  "acted on": "已有人做过",
  "nobody yet": "还没人看过",
  "min cells": "最少细胞数",
  "smallest population to keep, in cells": "保留的群体最小有多少个细胞",
  "max cells": "最多细胞数",
  "largest population to keep, in cells": "保留的群体最大有多少个细胞",
  "min donors": "最少供体数",
  "fewest donors the population has to span": "这个群体至少要覆盖几个供体",
  Apply: "应用筛选",
  "clear the filters": "清空筛选",
  "back to the run": "回到这个 run",
  "The address bar is the filter: apply it and the link you send opens this same page, not whatever you were last looking at.":
    "筛选条件就在地址栏里：应用之后，你发出去的链接打开的就是这一页，而不是你刚才正好看到的地方。",
  "{n} populations on this page": "本页 {n} 个群体",
  "at least {n} cells": "至少 {n} 个细胞",
  "at most {n} cells": "最多 {n} 个细胞",
  "at least {n} donors": "至少 {n} 个供体",
  "candidate type": "候选类型",
  "open the card": "打开这张卡片",
  "No population in this run answers to that filter. The list is what the server returned, so the filter is not hiding rows it did not ask for.":
    "这个 run 里没有群体符合那个筛选条件。列表就是服务端返回的内容，筛选并没有藏起它没被要求藏的行。",
  "Search the populations this run produced": "检索这个 run 产出的细胞群体",
  "— {n} populations, each one only a name inside this run (§5.6)": "—— {n} 个群体，每一个都只是本 run 内部的一个名字（§5.6）",

  // ---------------------------------------------------------------- 连续复核（第三批③）
  " · {n} of {m} already carry a decision": " · {n} / {m} 已有人做过决定",
  "who has acted": "谁做过动作",
  "filed, no decision yet": "已建档，还没人做决定",
  "not filed yet": "还没建档",
  "Continue with": "接着看",
  "place {p} of {n} in this queue": "在这份队列里排第 {p}（共 {n}）",
  "the first card in this order that no person has acted on; the order itself is the rule's and does not move as you work (§13.4)":
    "这是这个顺序里第一张还没人做过决定的卡片；顺序本身由规则决定，不会因为你翻过几张而变动（§13.4）",
  "every card in this queue already carries a decision. Nothing here signs anything off: a decision is made on a card, by a named person.":
    "这份队列里每张卡片都已经有人做过决定。这里不会替你签任何字：决定是在某张卡片上、由一个有名字的人做出的。",
  "place {p} of {n} in this run's queue": "在这个 run 的队列里排第 {p}（共 {n}）",
  "previous card": "上一张卡片",
  "no card before this one": "前面没有卡片了",
  "next card": "下一张卡片",
  "no card after this one": "后面没有卡片了",
  " — {name} already carries a decision": "——{name} 已经有人做过决定",
  "this card is not in the queue the rules produce for run {run}, so there is no next card to go to":
    "这张卡片不在规则为 run {run} 生成的队列里，所以没有下一张可去",
  "reading the queue of this run…": "正在读这个 run 的队列……",
  "The queue of this run could not be read, so no next card is offered.": "这个 run 的队列读不出来，所以这里不提供下一张卡片。",

  // ---------------------------------------------------------------- 枚举显示名
  // 可复现级别
  byte: "字节级",
  numeric: "数值级",
  conclusion: "结论级",
  // run 状态
  CREATED: "已创建",
  PREFLIGHT_RUNNING: "预检运行中",
  PREFLIGHT_PASSED: "预检通过",
  PLAN_DRAFTED: "方案已起草",
  PLAN_APPROVED: "方案已批准",
  ANALYSIS_RUNNING: "分析运行中",
  QC_REVIEW: "质控复核",
  CANDIDATE_DISCOVERY: "候选发现",
  EVIDENCE_BUILDING: "证据构建",
  TARGETED_ANALYSIS: "定向分析",
  VALIDATION: "验证",
  HUMAN_REVIEW: "人类评审",
  REPORT_READY: "报告就绪",
  ARCHIVED: "已归档",
  BLOCKED: "被阻塞",
  RETRYABLE_FAILURE: "可重试失败",
  NEEDS_INPUT: "缺少输入",
  POLICY_BLOCKED: "被策略阻断",
  RESOURCE_EXCEEDED: "资源超限",
  BUDGET_EXCEEDED: "预算超限",
  ESCALATED_TO_HUMAN: "已上报人类",
  FAILED: "失败",
  // run 日志里的四种事件（§9.2）
  node_retry: "节点重试",
  business_retry: "业务重试",
  usage: "花费",
  // 动了它的是谁（SubjectKind）
  service: "服务账号",
  // 候选类型（CandidateType，§5.6 的五类）
  C1_unassigned: "C1 未归属",
  C2_reference_conflict: "C2 与参考图谱冲突",
  C3_rare_stable: "C3 稀有且稳定",
  C4_cross_modal_discordant: "C4 跨模态不一致",
  C5_composition_outlier: "C5 组成异常",
  // 审批
  adhoc_tool: "ad-hoc 工具",
  escalation: "上报",
  pending: "待处理",
  approved: "已批准",
  expired: "已过期",
  // 临时工具的失败策略与授权条款状态（§9.6）
  halt: "停下来",
  escalate_to_human: "交给人处理",
  not_required: "无需审阅",
  // 导出
  tables: "表格",
  figures: "图片",
  code_bundle: "代码包",
  delivery_zip: "交付压缩包",
  QUEUED: "已排队",
  RUNNING: "运行中",
  READY: "就绪",
  // 对象元数据
  barcode_filtered: "已过滤 barcode",
  gene_filtered: "已过滤基因",
  both_filtered: "barcode 与基因都已过滤",
  ephemeral: "临时",
  run_linked: "绑定 run",
  deliverable: "交付物",
  archive: "归档",
  this_project_only: "仅本项目",
  internal_improvement: "内部改进",
  cross_project_allowed: "允许跨项目",
  deliverable_visible: "交付可见",
  // 字段来源
  tool: "工具输出",
  retrieval: "检索",
  model: "模型",
  // 评审
  card: "整张卡片",
  membership: "成员归属",
  revise: "修订",
  return_for_analysis: "退回分析",
  reject: "否定",
  mark_conflict: "标记为冲突",
  mark_unknown: "标记为未知",
  full: "完整签署",
  conditional: "有条件签署",
  not_signed: "未签署",
  // 卡片状态
  not_yet_validated: "尚未验证",
  consistent: "一致",
  discordant: "不一致",
  partial: "部分一致",
  // 假设类型（HypothesisType；unknown 已在通用区给出）
  cell_type: "细胞类型",
  cell_state: "细胞状态",
  technical_artifact: "技术假象",
  // 未知的原因（UnknownReason）
  reference_not_covered: "参考未覆盖",
  insufficient_evidence: "证据不足",
  unresolved_conflict: "冲突未解决",
  quality_limited: "质量受限",
  // 标签层级（LabelLevel；cell_state 同上）
  unknown_coarse: "粗粒度：未知",
  cell_type_broad: "宽粒度细胞类型",
  cell_type_fine: "细粒度细胞类型",
  provisional_project: "项目内临时标签",
  // 证据
  differential_expression: "差异表达",
  marker_specificity: "marker 特异性",
  doublet_screening: "doublet 筛查",
  ambient_screening: "环境 RNA 筛查",
  quality_screening: "质量筛查",
  batch_screening: "批次筛查",
  stress_cycle_screening: "应激周期筛查",
  over_split_screening: "过度切分筛查",
  reference_mapping: "参考映射",
  reference_coverage: "参考覆盖",
  continuous_state: "连续状态",
  cross_modal: "跨模态",
  supporting: "支持",
  contradicting: "反对",
  trigger: "触发",
  discovery: "发现集",
  confirmation: "确证集",
  // 对齐种类
  continuation: "延续",
  uncertain_alignment: "对齐不确定",
  distinct: "截然不同",
  split: "拆分",
  merge: "合并",

  // 知识库与 RAG 问答
  "knowledge base": "私有知识库",
  "private knowledge base": "本地私有知识库 & 生信 RAG",
  "upload paper or lab log": "导入论文 / 实验记录 / 分析报告",
  "rag question answering": "本地智能生信问答",
  "cluster marker suggestion": "单细胞聚类 Marker 辅助推断",
  "search documents": "检索知识库…",
  "ask question placeholder": "输入科研或数据分析问题（如：Cluster 3 的高表达 Marker 提示何种细胞亚群？）…",
  "ingest document": "入库文档",
  "document title": "文档标题",
  "document filename": "原始文件名（如 paper.pdf, lab.docx, analysis.md）",
  "document content": "文档文本正文或 Markdown 内容",
  "ask button": "提问",
  "get suggestions": "推断细胞类型与建议",
  "citations": "溯源引用",
  "confidence": "置信度",
  "suggested cell types": "推断细胞类型",
  "biological rationale": "生物学依据与文献溯源",
  "delete doc": "删除",

  // 跨组学冲突与卡片知识库联动
  "Cross-Modal Conflict Alert": "跨组学模态冲突预警",
  "Discordant signals between transcription and orthogonal modality":
    "转录组表达与正交组学（表面蛋白/染色质可及性）检测到显著矛盾。依据 §7.5 规则，系统已显式暴露冲突并硬阻断自动放行，禁止加权掩盖，必须由领域专家人工仲裁。",
  "Consult Knowledge Base": "从私有知识库匹配先验建议",
  "Querying knowledge base...": "正在检索私有文献与实验记录…",
  "Knowledge Base RAG Advice": "私有知识库历史文献与先验建议",

  // 快捷采纳与拖拽上传
  "Adopt AI Memo Action": "采纳备忘录建议并填入评审",
  "Adopt Knowledge Suggestion": "采纳此细胞亚群并填入评审",
  "Cross-Modal Scatter (RNA vs ADT)": "跨组学信号分布 (mRNA 表达量 vs 表面蛋白信号)",
  "Drop files here or click to upload": "拖拽文献/实验记录文件至此处，或点击浏览上传",
  "Parsing & Indexing...": "正在解析分块并建立本地混合索引…",
  "Supported formats": "支持格式：PDF、Word (.docx)、Markdown (.md)、文本 (.txt)",
  "Conflict Quadrant": "冲突象限 (高 mRNA / 阴性蛋白)",
  "Concordant Quadrant": "协同象限 (mRNA 与蛋白共高表达)",
  "Batch Document Upload": "批量拖拽上传",
  "Manual Text Input": "手输 / 粘贴文本",
  "Click or drag files here": "点击选择文件或将文件拖拽至此处",
  "Successfully ingested documents": "已成功解析并建立本地混合索引！",

  "Deployment capabilities": "当前部署能力",
  "Reference demonstration": "参考演示环境",
  "Configured deployment": "已声明部署环境",
  "Deployment not confirmed": "部署状态待确认",
  "Object uploads": "数据对象上传",
  "Analysis execution": "分析执行",
  "Export generation": "导出文件生成",
  "Declared connected (not health-checked)": "声明已接入（未做健康检查）",
  "Not connected": "未接入",
  "Not confirmed": "待确认",
  "Reading deployment declaration…": "正在读取部署声明…",
  "Deployment information could not be read. Capabilities remain unconfirmed; ask the deployment administrator to check the API, then retry.": "无法读取部署信息，能力仍待确认。请部署管理员检查 API 后重试。",
  "These are deployment declarations, not live health checks or permission grants. Project policies still apply.": "以上为部署声明，不代表实时健康检查或权限授予；操作仍受项目策略约束。",
  "Object storage is not connected. Uploads cannot complete in this deployment. Ask the deployment administrator to connect storage before uploading.": "当前部署未接入对象存储，无法完成数据上传。请部署管理员接入存储后再上传。",
  "No automatic analysis worker is connected. Recording a run request does not start analysis. Use an existing demonstration run to inspect results, or ask the deployment administrator how to execute the run.": "当前未接入自动分析执行器，登记运行请求不会启动分析。可查看已有演示运行的结果，或向部署管理员确认执行方式。",
  "No export worker is connected. An export request can be recorded, but it will not generate a file. Ask the deployment administrator to connect a worker; polling cannot resolve this.": "当前未接入导出执行器。可登记导出请求，但不会生成文件；反复刷新无法解决，请部署管理员接入执行器。",
  "This capability has not been confirmed. Check the deployment status above before submitting; a recorded request does not guarantee execution.": "此能力尚待确认。提交前请核查上方部署状态；请求已登记不代表一定会执行。",
  "The request was recorded. A download is available only after a worker generates the file; see the deployment status above.": "请求已登记。只有执行器生成文件后才能下载；请查看上方部署状态。",
  "The card reports discordant modalities. Review the source evidence before drawing a conclusion; the alert alone does not establish a mechanism or statistical significance.": "此卡报告模态信号不一致。请先核查来源证据；此提示本身不证明生物学机制或统计显著性。",
  "Unresolved evidence conflict": "尚未解决的证据冲突",
  "This card has an unresolved conflict. It is not necessarily a cross-modal conflict; inspect its contradicting evidence and gate reasons.": "此卡存在未解决的证据冲突，但不一定属于跨模态冲突。请在复核前查看反对证据。",
  "Cross-modal measured summary": "跨模态实测汇总",
  "Only aggregate measurements are available, not paired cell observations. No scatter plot is drawn. Units, transformations and thresholds must be checked in the source asset.": "目前仅有汇总测量值，缺少成对观测数据，因此不绘制散点图。单位、变换方式和阈值请查阅来源。",
  "No cross-modal measurements are attached to this card.": "此卡未附跨模态测量值。",
  "RNA mean": "RNA 均值",
  "Target modality mean": "目标模态均值",
  "agreement": "一致性",
  "concordant": "一致",
  "complementary": "互补",
  "ambiguous": "不明确",
  "Fill review draft": "填入复核草稿",
  "This button only fills a review draft. Inspect and submit it separately; it does not save or sign a conclusion.": "此按钮仅填写复核草稿，请另行检查并提交；不会保存或签署结论。",
  "Review memo suggestion: {action}": "审查备忘录建议：{action}",
  "Markers are read from marker-specificity evidence, including discovery signals. They are retrieval inputs, not proof of identity.": "Marker 读取自特异性证据，其中包括发现集信号；它们仅作为检索输入，不证明细胞身份。",
  "Recorded markers": "证据记录中的 Marker",
  "No marker genes are recorded on this card. Enter verified markers to query; no example genes will be substituted.": "此卡没有记录 Marker 基因。请填写核实过的 Marker 再检索，系统不会代入示例基因。",
  "Markers for this query": "本次检索的 Marker",
  "The first eight distinct genes are used. Check the exact query below before submitting.": "仅使用前八个不重复基因，提交前请核对下方实际检索内容。",
  "Tissue (optional)": "组织来源（选填）",
  "Tissue is not supplied by this card. Leave blank unless you know the source; a cell label is not a tissue.": "此卡未提供组织来源。不确定时请留空，细胞标签不等于组织来源。",
  "Query preview": "实际检索内容",
  "Tissue not supplied": "未提供组织来源",
  "The source cluster is missing. Refresh the card before querying.": "缺少来源聚类编号，请刷新证据卡后再检索。",
  "The query failed; no suggestion was applied. Check the error and retry.": "检索失败，未应用任何建议。请检查错误信息后重试。",
  "Queried markers": "实际检索的 Marker",
  "No matching knowledge-base citations were found. Any proposed cell types are heuristic leads, not literature-backed conclusions.": "未找到匹配的知识库引用。若返回细胞类型，也仅是启发式线索，不是有文献支持的结论。",
  "Retrieval heuristic score": "检索启发式评分",
  "This score is based on retrieved matches, not a calibrated probability that a cell label is correct.": "此评分依据检索匹配情况计算，不是经过校准的细胞标签正确概率。",
  "Suggested label for review": "待复核的建议标签",
  "Draft change": "草稿变更",
  "no label": "未命名",
  "Only the label is prefilled. Complete hypothesis and ontology fields before submitting; this button does not save a review or sign a conclusion.": "仅预填标签。提交前请补全假说和本体字段；此按钮不会保存复核记录，也不会签署结论。",
  "Knowledge-base suggestion for review: {label}. Rationale: {rationale}": "待复核的知识库建议：{label}。依据：{rationale}",
  "Review draft filled. Inspect and submit it in the Review panel; nothing has been saved yet.": "已填入复核草稿，请在复核面板检查并提交；目前尚未保存。",
  "The retrieved evidence is insufficient to suggest a cell label.": "检索证据不足，无法给出细胞标签建议。",
  "Review changes must be a valid JSON object. No review was submitted.": "复核变更必须是有效的 JSON 对象。本次未提交任何复核。",
  "Search project papers, lab notes and analysis reports. Parsing depends on the installed backends; inspect extracted citations before using a suggestion.": "检索项目论文、实验记录和分析报告。解析效果取决于已安装的后端；使用建议前请检查提取的引用。",
  "No matching citations were found. This answer has no retrieved source support.": "未找到匹配引用，此回答没有检索来源支持。",
  "This score reflects retrieved matches, not the probability that the answer is correct.": "此评分反映检索匹配情况，不代表回答正确的概率。",

  // 阶段 1 & 阶段 2 核心闭环
  "Decision thresholds": "判据阈值",
  "§1.5: the numbers every candidate is judged against. Until a principal investigator attests them, a run is still computed, drafted and queued - only signing a conclusion and exporting stay closed.":
    "§1.5：每个候选结论都据此判定的一组数值。在课题负责人确认之前，计算、起草与排队仍可正常进行——仅结论签字与成果导出保持关闭。",
  "in force": "当前生效",
  attested: "已确认",
  "yes - conclusions may be signed and exported": "是——结论可签字并可导出",
  "no - sign-off and export are closed": "否——签字与导出已关闭",
  "a confirmed set is not frozen: attesting another version replaces it, and the cards computed against the old one keep naming it.":
    "已确认的判据集并非冻结：确认新版本即取而代之，而按旧版本算出的证据卡仍标注旧版本名。",
  "this act asks the API for a second factor; a token without one is refused here, not by this screen.":
    "该操作要求 API 校验双因子；缺少双因子的令牌会被服务端拒绝，而非由本页面拦截。",
  "attest the measured cut set": "确认实测判据集",
  "attest a replacement cut set": "确认新的替代判据集",
  "every field the project carries is sent back, touched or not - that is what makes an untouched placeholder visible instead of silent.":
    "项目携带的每个字段都会原样回传，无论是否改动——正是这一点让未被触碰的占位值显式可见，而不是悄然通过。",
  "the name the products cite; it has to differ from the one in force": "产物将引用的版本名；必须与当前生效版本不同",
  "name the set you measured": "为你实测的判据集命名",
  "the cut set you are attesting has to be named, because the products cite that name":
    "待确认的判据集必须有名称，因为所有产物都要引用该名称",
  "what was measured, on which data, and where the measurement lives; stored in the audit exactly as typed":
    "测了什么、基于哪些数据、实测记录存放在何处；将按输入原文写入审计日志",
  "state your own measurement": "请填写你自己的实测依据",
  "the rationale is the audit record of where these numbers came from, and it is empty":
    "确认依据即是这些数值来源的审计记录，目前为空",
  "is not a number this form can send": "不是本表单可发送的数值",
  "counts cells, donors, neighbours or cards, so it has to be a whole number": "计的是细胞数、供体数、近邻数或卡片数，必须为整数",
  "alignment_uncertain_min has to sit at or below alignment_continuation_min": "alignment_uncertain_min 必须不高于 alignment_continuation_min",
  "the outside-group ceiling has to be below the in-group floor": "组外上限必须低于组内下限",
  "carried by the record, unnamed here": "由记录携带，本页面未命名",
  "the schema gained a cut this screen has not been told about; it is sent as it arrived unless you change it.":
    "数据模型新增了本页面尚未登记的判据字段；若不修改，将按原值发送。",
  "changed from the set in force": "相对生效判据集的改动",
  "carried over unchanged": "原样沿用的项数",
  "no number has been changed yet, so this would attest the placeholders as measured.":
    "尚无任何数值被修改，此时的确认等同于把占位值宣称为实测结果。",
  "this form is not ready to send": "表单尚未填写完成，无法发送",
  "the API says that version name is already the one in force - name the new measurement differently.":
    "API 提示该版本名已是当前生效版本——请为新的实测判据集另取名称。",
  "refusals are quoted as the API wrote them.": "以下拒绝理由按 API 原文引用。",
  "Confirm the measured cut set": "确认实测判据集",
  "confirming…": "正在确认…",
  "rare populations": "稀有群体",
  "candidate confidence and coverage": "候选可信度与覆盖度",
  "clustering stability": "聚类稳定性",
  alignment: "对齐",
  "group power": "组别样本量",
  "differential expression support": "差异表达支撑",
  "reproduction and labels": "复现与标签",
  "summary pages": "摘要页面",
  "largest share of cells a population may hold and still be called rare": "某群体仍可被判定为稀有的最大细胞占比",
  "fewest cells a population has to be seen in to be carried at all": "某群体被纳入分析所需的最少细胞数",
  "fewest donors a rare population has to appear in": "稀有群体必须出现在其中的最少供体数",
  "percentile of the support score under which a card is called low-confidence": "证据卡被判定为低可信时的支持度分数百分位",
  "smallest margin a state assignment needs to be trusted": "状态判定可被采信所需的最小间隔",
  "how many neighbours a cell's local coverage is measured over": "衡量细胞局部覆盖度时使用的近邻数",
  "smallest local-to-reference distance ratio still counted as covered": "仍被计为已覆盖的局部/参考距离比下限",
  "lowest agreement two clusterings need to be called stable": "两次聚类被判定为稳定所需的最低一致性",
  "lowest cell overlap two clusterings need": "两次聚类所需的最小细胞重叠度",
  "share of the stability tests a candidate has to pass": "候选结论必须通过的稳定性检验占比",
  "score at which a matched cell is treated as the same population": "匹配细胞被视作同一群体的分数阈值",
  "score below which the alignment is called uncertain; sits under the cut above": "低于该值时对齐结果被判定为不确定；必须不高于上一项",
  "smallest group that may be tested at all": "允许进入检验的最小组规模",
  "largest adjusted p-value a differential result may carry": "差异结果可接受的最大校正后 p 值",
  "smallest absolute fold change worth reporting": "值得报告的最小绝对倍数变化",
  "lowest share of a group's donors that have to express the gene": "组内供体必须表达该基因的最低占比",
  "highest share outside the group still allowed to express it; under the floor above": "组外供体仍被允许表达该基因的最高占比；必须低于上面的下限",
  "lowest share of donors that have to agree with the reported direction": "与所报告方向一致的供体最低占比",
  "difference two products may show before they are called a mismatch": "两份产物在被判定为不一致前允许的最大偏差",
  "lowest share of labels two products have to agree on": "两份产物必须保持一致的标签最低占比",
  "most cards one reviewer is handed at a time": "一次分发给单名复核者的证据卡上限",
  "share above which the summary page calls a population rare": "摘要页面将某群体判为稀有的细胞占比上限",
  "cell count below which the summary page calls a population thin": "摘要页面将某群体判为过少的细胞数下限",
  "Run the analysis now": "现在运行分析",
  "Execute DAG nodes (qc → normalize → pca → cluster → markers → pseudobulk_de → robustness), discover C1-C3 candidates, and draft evidence cards.":
    "顺序执行计算节点 (QC → 标准化 → 降维 → 聚类 → Marker → 差异表达 → 鲁棒性)，自动识别 C1-C3 候选亚群并起草证据卡。",
  "executing pipeline...": "正在执行计算分析…",
  "Nothing ran and nothing moved: the run keeps the state it had. The sentence below is the server's own.":
    "本次执行没有运行任何节点，运行状态也保持原样。下方一句是服务端原文。",
  "Every node in the plan ran, and its outputs are recorded.": "方案中的每个节点都已运行完成，其产出已记入台账。",
  "{node} did not complete, so this run is not finished.": "{node} 未完成，本次运行尚未结束。",
  "This run did not complete.": "本次运行未完成。",
  "{n} nodes executed": "已执行 {n} 个节点",
  "{n} outputs recorded": "已记录 {n} 项产出",
  "{n} candidate populations found": "发现 {n} 个候选亚群",
  "{n} evidence cards drafted": "起草 {n} 张证据卡",
  "The run stopped with the server's own sentence:": "运行停在服务端自己给出的这句话：",
  "These nodes wait for a person's decision before they can run:": "以下节点需要先由他人作出审批决定才能运行：",
  "Review the drafts in the expert queue": "在专家队列中复核这些草稿",
  "open the approval inbox": "打开审批收件箱",

  // ---------------------------------------------------------------- 单人模式与快速角色切换
  "Quick Role Switcher (Reference / Dev)": "快速身份切换 (参考部署 / 本地演练)",
  "Solo Mode (All-in-One)": "单人全流程模式 (Solo Mode)",
  "Solo Mode Active": "单人全流程模式已激活",
  "Solo Mode (Active)": "单人模式 (已激活)",
  "Solo Mode active. Click to switch to standard Analyst role.": "单人模式生效中。点击切换回标准分析师角色。",
  "Click to enter Solo Mode (All-in-One researcher)": "点击进入单人全流程模式（全能研究员）",
  "⚡ Solo Mode Quickstart": "⚡ 单人模式快速通道",
  "For solo researchers, evaluators, and full-pipeline walkthroughs without SoD blocks.":
    "专为单人生信研究员、算法评估人员与全流程快速演练设计，免除职责分离阻断。",
  "Experience BioAdapt as a unified Solo Operator (PI + Analyst + Expert). Bypass separation-of-duties blocks to self-request, self-approve gates, issue adhoc execution grants, and sign off findings.":
    "以复合单人全能操作员身份（兼具 PI、生信分析师与领域专家全套权能）使用 BioAdapt。豁免职责分离（SoD）自审阻断，支持自提自批审批门、发放临时工具执行凭据并自主签字交付结论。",
  "Launch Reference Project in Solo Mode": "以单人模式进入演示项目 (prj_scrnaseq)",
  "Separation-of-duties restrictions are bypassed. You can approve gates requested by yourself.":
    "已免除职责分离限制，您可以直接批准由您自己发起的审批门。",
  "You can sign off on your own findings without independent expert block.":
    "已解除作者自签限制，您可以直接对由您起草的证据卡执行签字确认。",
  "Separation of duties (SoD) restrictions are bypassed. You can request and self-approve gates, issue adhoc grants, and sign off conclusions as the author.":
    "已豁免职责分离（SoD）自审阻断。您可以作为单一操作人完成自提自批审批门、发放临时工具授权并对由您起草的卡片执行最终签字交付。",
  "Full permissions without SoD blocks: self-approvals and self-sign-offs enabled":
    "全能角色无职责分离阻断：支持自批审批门与作者自签结论",
  "Solo Mode": "单人模式",
  "Solo Operator": "单人全能操作员",
  solo_operator: "单人全能操作员",
  "combines PI, analyst, and domain expert duties without separation-of-duties restrictions for walkthroughs and rapid evaluations":
    "融合课题负责人（PI）、生信分析师与领域专家全套职责，豁免职责分离限制，专用于全流程演练与快速评测",
  "Analyst": "分析师",
  "PI (Lead)": "课题负责人 (PI)",
  "Domain Expert": "领域专家",
  "Expert 2 (Independent)": "独立专家 (Expert 2)",

  // ---------------------------------------------------------------- 登录密钥与身份目录（§11.2）
  "sign-in keys": "登录密钥",
  "Keys that may sign in": "可登录的密钥",
  "A key file is authorised here, with the roles and projects a login with it is granted. The key itself never carries authority: revoking a row closes the next login rather than waiting for a token to expire (§11.2). Revoking is final for that key pair - the directory refuses to register the same public key twice, so a person whose key was revoked signs in with a newly generated one.":
    "密钥文件在此被授权，并写明用它登录会获得哪些角色与项目范围。密钥本身不携带权限：吊销这一行后，下一次登录即被拒绝，而不必等待已发出的 token 过期（§11.2）。吊销对那一对密钥是终局的：目录不会让同一份公钥登记两次，被吊销的人要重新生成一份密钥才能再登录。",
  "hide revoked keys": "隐藏已吊销的密钥",
  subject: "主体",
  roles: "角色",
  reaches: "可访问范围",
  "a login with this key is": "用此密钥登录时是",
  fingerprint: "指纹",
  added: "登记于",
  "No key is registered here yet, so a key file cannot sign in - the routes refuse it until an admin writes a row.":
    "此处尚未登记任何密钥，因此密钥文件无法登录——在管理员写入一行之前，相关路由会直接拒绝。",
  "How a person gets a key file": "如何取得密钥文件",
  "The private half is written on their own machine and never sent anywhere; the public half is what is pasted above. A token bought at login expires on its own, which is why there is nothing to rotate.":
    "私钥只写在本人机器上，从不出网；上面粘贴的是公钥。登录换来的 token 会自行过期，因此没有需要轮换的机密。",
  "Losing the file costs a login, not the project: register a new public key for the same subject and revoke the old row. There is no second place a secret has to be rotated.":
    "丢失文件只损失一次登录，不会损失项目：为同一主体登记新的公钥，并吊销旧的那一行即可。不存在第二处需要同步轮换的机密。",
  "every project": "全部项目",
  "a pipeline, never a person (§11.4)": "一条流水线，绝不是人（§11.4）",
  "a person": "一个人",
  "second factor attested": "已认证第二因子",
  "no second factor": "无第二因子",
  "revoked by": "吊销者",
  by: "由",
  revoked: "已吊销",
  "revoking…": "正在吊销…",
  "Revoke this key": "吊销此密钥",
  "Authorise a key": "授权一把密钥",
  "Paste the contents of someone's .pub file. The key's own id and fingerprint are read off the key, so nothing here has to be copied into another system; what is chosen below is what a login with that key is granted.":
    "粘贴对方 .pub 文件的内容。密钥的 id 与指纹都由密钥本身算出，无需再抄进其他系统；下面选定的内容，就是用它登录时获得的授权。",
  "Registered as": "已登记为",
  "the person or the pipeline this key belongs to; a login cannot claim another one":
    "这把密钥属于哪个人或哪条流水线；登录无法声称是其他主体",
  project: "项目",
  "the scope every route a login reaches is checked against (§11.2)": "登录后每条路由都会按此范围校验（§11.2）",
  "public key": "公钥",
  "the PEM block of a .pub file; the trailing note line is ignored, and the private half is never pasted anywhere":
    "粘贴 .pub 文件的 PEM 段；末尾的说明行会被忽略，私钥任何时候都不要粘贴到这里",
  "§13.1 refuses a pair that a live session could not hold together, so a key cannot be granted around the rules":
    "§13.1 会拒绝实时会话无法同时持有的角色组合，因此登记密钥无法绕过规则",
  "what the login is granted": "登录获得的授权",
  "these three answers are the difference between a person, a pipeline and a shortcut":
    "这三项区分的正是一个人、一条流水线与一条捷径",
  "a second factor was attested for this subject": "已为该主体认证第二因子",
  "without it the platform refuses the actions that need one: promoting a tool, deleting data, confirming thresholds, granting a role (§11.6)":
    "缺少它时，平台会拒绝一切需要第二因子的操作：提升工具、删除数据、确认阈值、授予角色（§11.6）",
  "this key signs in as a pipeline, not as a person": "这把密钥以流水线身份登录，而非以人的身份",
  "it may only hold the service account role, and every gate a person has to open stays closed to it (§11.4)":
    "它只能持有服务账号角色，所有必须由人打开的门禁对它依旧关闭（§11.4）",
  "this subject works alone": "该主体独自工作",
  "a solo operator is exempt from separation-of-duties blocks, which is a decision about a person, not a convenience setting":
    "单人操作者可豁免职责分离阻断，但这是关于某个人的决定，不是图方便的开关",
  label: "标注",
  "where this key lives, so a revocation can be explained to the person who holds it":
    "写下这把密钥存放在哪里，吊销时才能向持有者说明是哪一把",
  "workstation, office laptop": "工位机、办公笔记本",
  "writing…": "正在写入…",
  "Authorise this key": "授权这把密钥",

  // ---------------------------------------------------------------- 原文定位（第三批④）
  "page {n}": "第 {n} 页",
  "characters {from} to {to}": "第 {from}–{to} 个字符",
  "the offsets count characters in the text the parser read out of the file, not bytes of the file itself":
    "这里的偏移量按解析器从文件里读出的文本字符计数，不是文件本身的字节数",
  "this passage was indexed before positions were recorded, so the index cannot say where it sits; the text is what was stored":
    "这段内容入库时还没有记录位置，所以索引说不出它在哪儿；下面显示的就是当时存下的原文",
  "open the cited passage": "打开被引用的这段",
  "re-read the passage": "重新读一遍这段",
  "opening…": "正在读取…",
  "{n} stored pieces before and {m} after": "索引在这段前面存着 {n} 段、后面存着 {m} 段",
  cited: "被引用的那段",
  licence: "许可",

  // ---------------------------------------------------------------- 导出预览与通知（第三批⑤）
  "preview the choice first: the counts are rows already filed, not the file a worker would write":
    "先预览这次选择：这些数字是已经建档的行，不是工作进程将要写出的那个文件",
  "which run": "哪个 run",
  "optional; limits the artefact to one run's products. The list is this project's runs, so nothing has to be typed.":
    "可留空；只取某一个 run 的产物。列表就是本项目已有的 run，不需要手工输入编号",
  "every run in this project": "本项目的所有 run",
  "which conclusions": "哪些结论",
  "optional; only cards filed at this status. A status nobody picked asks for every card.":
    "可留空；只取处于这个状态的证据卡。不选状态，要的就是全部的卡",
  "every status": "所有状态",
  "preview what these filters select": "预览这些筛选会选到什么",
  "these are rows stored in this project right now, counted by the filters shown above. They are not the file: a worker writes that, and until one runs nothing here has left the platform.":
    "这些是此刻本项目里存着的行，按上面选择的筛选计数。它们不是那个文件：文件由工作进程写出，在它跑起来之前，这里没有任何东西离开过平台。",
  "cards this delivery would draw from": "这次交付会取用的证据卡",
  "candidate sets": "候选集",
  assets: "资产",
  "the status filter moves the card line only, because it is a column of the card table; the run filter moves all three.":
    "状态筛选只改变证据卡那一行，因为它只是卡表的一列；run 筛选会同时改变这三行。",
  "threshold set": "阈值集",
  "nothing matches this choice, so the job would have nothing to write. Widen the filters or re-read after a review is filed.":
    "这个选择没有匹配到任何行，也就是说这个任务将无内容可写。请放宽筛选，或等有人提交复核之后再重读。",
  "what the filters chose": "筛选选了什么",
  "no filter: every row in this project": "无筛选：本项目的全部行",
  "this page holds {n} export rows": "本页有 {n} 行导出任务",
  "this project has more runs than one page, so the list stops at {n}; the run screen pages through the rest.":
    "本项目的 run 多过一页，所以这里只列到 {n} 个；其余要在运行列表那一屏翻页",
  "a queued row here is the platform agreeing to a job, not a job in progress: this deployment reports export generation as {state}, so re-reading cannot produce a file.":
    "这里的已排队只是平台答应接单，不是任务正在执行：本次部署把导出生成报告为 {state}，所以反复重读也不会变出文件。",

  // ---------------------------------------------------------------- 启动闭环（第四批①）
  "what to run": "要跑哪一个方案",
  "which plan": "方案",
  "only a version this project recorded is offered, and its plan id comes with the choice":
    "下拉里只有本项目登记过的版本；选中就一起带上它的 plan id 与版本号，不需要手打",
  "choose a recorded plan": "选择一个已登记的方案",
  "no plan is recorded in this project yet, so there is nothing to launch.":
    "本项目还没有登记任何方案，所以没有可以启动的运行。",
  "build one": "去方案向导建一个",
  "the plan chosen is no longer on this list; re-read and pick again rather than launching a version this project does not hold":
    "刚才选中的方案已经不在这份列表里了；请重读之后重新选择，不要启动一个本项目并不持有的版本",
  "the plan starts from these slots": "这个方案从这些输入槽开始",
  "§9.2: the workflow fills one root slot": "§9.2：这条工作流只填一个根输入槽",
  "{n} root slots on one plan that takes one": "方案声明了 {n} 个根输入槽，而工作流只能填一个",
  "launching this plan is allowed and records a run; executing it is refused until one plan declares one slot":
    "启动它仍然会被接受并记下一个 run，但执行会被拒绝，直到这个方案只声明一个槽位",
  "the one asset this run is handed": "这次运行交给它的那一个资产",
  "a launch fills a single root slot, so exactly one registered asset is chosen here; a run handed two of them is a row that cannot execute (§9.2)":
    "一次启动只填一个根输入槽，所以这里只选一个已登记的资产；交了两个资产的 run 是一行永远执行不了的记录（§9.2）",
  "produced by run": "由 run 产出",
  "nothing is registered as data in this project yet.": "本项目还没有登记任何数据。",
  "register it first": "先去登记数据",
  "what the run is pinned against": "这次运行钉在什么之上",
  "not read": "还没读到",
  "tools offered here": "这套部署在这里提供的工具",
  "re-read the registry": "重新读一遍工具注册表",
  "read from this project's tools route, which is the same registry the executor hydrates, so the value is neither typed nor remembered: execution refuses a run pinned against a snapshot this deployment does not hold (§4.1). The snapshot counts the registered steps only - what an ad-hoc node may run is pinned by its own grant row (§9.6)":
    "这个值读自本项目的工具路由，和执行进程加载的是同一份注册表，所以既不靠手打也不靠记忆：钉着本部署并不持有的快照的 run，会在执行时被拒绝（§4.1）。快照只统计已注册的步骤——临时工具能跑什么由它自己的授权行钉住（§9.6）",
  "the claim this run sets out to support": "这次运行准备支撑到哪一档可复现结论",
  "pick a recorded plan first": "先选一个已登记的方案",
  "pick the one asset this run is handed as its root input": "再选出这次运行要作为根输入交给它的那一个资产",
  "the registry snapshot could not be read, so nothing can be pinned": "读不到工具注册表快照，所以现在还没有可钉住的版本",
  "how this request enters the queue": "这个请求如何进入队列",
  "not a product format": "不是平台产物格式",
  "the reference workflow reads only the platform's own canonical JSON product, so an asset recorded in another format is refused when the run executes (§6.4)":
    "参考工作流只读平台自己的规范化 JSON 产物，所以按其他格式登记的资产会在 run 执行时被拒（§6.4）",

  // ---------------------------------------------------------------- 字段级复核编辑（第四批②）
  "which fields this review restates": "这次复核重写哪些字段",
  "every line below starts as the value the card holds now, and only a line you change is sent; the fields you change are re-labelled as human-sourced against this review's id, and the value they replace stays readable in the trail (§5.4)":
    "下面每一行都以卡片当前保存的值起步，只有你改动的那一行会被发出；改过的字段会被重新标成人工来源并挂上这次复核的 id，被替换掉的原值仍然可以在轨迹里读到（§5.4）",
  "an unknown hypothesis has to say why it is unknown; the card contract refuses it otherwise":
    "判定为未知的假设必须说明为什么未知；不填的话卡片契约会直接拒绝",
  "keep what the card states": "沿用卡片已经写明的值",
  "naming an unknown hypothesis as unknown clears the label this card carries":
    "把假设说成未知，就同时清空了这张卡片带着的标签",
  "the words this card says the population is; a label nobody can read is not a conclusion":
    "这张卡片说这群细胞是什么；一个谁都读不懂的标签不算结论",
  "a provisional project label cannot carry an ontology id, so this choice clears all three lines":
    "项目内的临时标签不能带本体 term id，所以选了它就清空这三行",
  "a cell type label has to name the term, the ontology it comes from and that ontology's version; the contract refuses any one of the three missing":
    "细胞类型标签必须同时给出 term、它来自哪个本体、以及那个本体的版本；三行缺任何一行，契约都会拒绝",
  "these three lines are the reference this label cites; leaving them as they are keeps the run's own mapping provenance":
    "这三行是这个标签引用的出处；不去改动它就保留了 run 自己的映射来源信息",
  "a term id has to be named by an ontology and a version, and an ontology name has to point at a term: the contract refuses either half on its own":
    "term id 要有本体名和版本来说明它是谁的，本体名也要指向一个具体的 term：只写一半契约就会拒绝",
  "the sentence that says why the card stands where it stands": "说明这张卡片为什么停在当前状态的那句话",
  "the conflict this card carries is still unresolved": "这张卡片带着的冲突仍未解决",
  "as read from the card": "按卡片读到的值",
  "{n} of these lines differ from the card as it was read, and those are the fields this review would overwrite":
    "其中 {n} 行与读到的卡片不一致，这次复核要覆盖的就是这些字段",
  "nothing below differs from the card as it was read, so this review would have nothing to restate":
    "下面没有哪一行和读到的卡片不同，所以这次复核并没有要重写的字段",
  "what the run measured is not offered here: evidence rows, ids, the thresholds version the card was judged against, its status and its signature are decided by the pipeline, the gate and the sign-off form, not by this list":
    "run 测到的东西不在这里给出：证据行、各类 id、卡片判读所依据的阈值版本、它的状态与签名，都由流水线、门禁和签署表单决定，而不是由这份列表改",
  "a field review has to restate at least one field; nothing below differs from the card":
    "字段级复核至少要重写一个字段；下面没有哪一行和读到的卡片不同",

  // ---------------------------------------------------------------- ad-hoc 执行凭据如实填表（第四批③）
  "Issue the execution grant": "发放这次执行的授权凭据",
  "the gate says a person allowed this submission; the grant is what turns that decision into one execution. Its row pins the exact bytes of the spec, so the lines below are the ones being signed - not a template (they cannot be read back from anywhere else, because this is the first time the platform sees them)":
    "审批门说的是有人允许了这份提交；凭据才把这个决定变成一次真正的执行。凭据行钉住的是这份工具说明的逐字节内容，所以下面这些行就是被签字的东西——不是一套模板（它们别处读不回来，因为这是平台第一次看到这份提交）",
  "the node this grant covers": "这份凭据覆盖的节点",
  "planned as": "方案里定的是",
  "read from": "读自",
  "decided by": "由谁裁定",
  "the name and the version are not asked for: the route refuses a grant whose spec names anything other than this node's plan entry, so the screen reads them from the plan instead":
    "名字与版本不需要填：方案里这个节点写的是哪支工具，路由就只认哪支工具，所以屏幕把方案读出来的值直接用上了",
  "this gate does not name a node, so there is nothing here a grant could cover; the route that raised it should have carried the node id":
    "这扇门没有指名节点，所以这里没有凭据能覆盖的东西；开出这扇门的接口本应带上节点 id",
  "the plan version this run pins is no longer listed for the project, so the node it names cannot be read here; a grant is issued against the plan the run actually pins":
    "这次运行钉住的方案版本已经不在本项目的列表里，所以它指名的节点在这里读不到；凭据是发在运行真正钉住的那份方案上的",
  "this plan version has no node with that id, so the gate points at nothing the run can execute":
    "这个方案版本里没有这个 id 的节点，所以这扇门指向的是一个运行执行不了的东西",
  "this node is planned as a registered tool, and a registered tool needs no grant; only the plan's ad-hoc nodes can be made executable here":
    "这个节点在方案里定的是已注册工具，而已注册工具不需要凭据；这里只能把方案里的临时工具节点变成可执行",
  "what the submission is": "这份提交是什么",
  "the step this code performs, in the words the plan uses for it": "这段代码做的那一步，用方案自己的说法",
  "one per line, as this code really reads them": "一行一个，按这段代码实际读取的模态写",
  "a species nobody checked cannot be claimed; leave it out rather than widen it":
    "没人验证过的物种不能声称支持；宁可少写，也不要放宽",
  "the image that runs": "真正跑起来的那个镜像",
  "a tag names a moving target, so the row is pinned by the digest the registry actually holds. Nobody can sign a permission over an image that was looked up later: a digest copied from the push is the only answer that means anything here":
    "标签指的是一个会动的目标，所以这一行钉的是注册里实际保存的摘要。没有人能对着一个事后才查的镜像签授权：只有从那次推送里抄下来的摘要在这里才算数",
  "sha256: followed by 64 hexadecimal characters; anything else is refused here and by the contract":
    "sha256: 开头，后接 64 位十六进制字符；其他写法在这里和契约那边都会被拒",
  "what it asks for, and what it was measured on": "它要多少资源，又是在什么上实测过的",
  "the number a run of this image actually produced, not the number asked for":
    "这个镜像真跑一次实测出来的峰值，不是你申请的那个数",
  "the dataset or run the measurement was taken on": "这次实测是在哪份数据或哪次运行上做的",
  "the deployment caps what an ad-hoc node may ask for, and this screen does not restate those caps: ask for more and the grant route refuses, in its own words, below":
    "本次部署对临时节点能申请的资源设有上限，而这个屏幕不替那些上限报数：申请超过上限，发放凭据的接口会在下方用它自己的话拒绝",
  "the licence as it was reviewed": "按审过的授权条款写",
  "an approved licence has to carry the date it was reviewed; not_required is the only status that needs none":
    "状态为已通过审阅的授权条款必须带上审阅日期；只有“无需审阅”这一档可以不带",
  "what a reader has to be able to check": "读这份结果的人必须能核对的东西",
  "one per line": "一行一个",
  "one per line; the contract refuses a submission that cannot say when its own result stops holding":
    "一行一个；说不出自己的结论什么时候不再成立的提交，契约会被拒",
  "whether this code needs raw counts or a normalised matrix": "这段代码要的是原始 counts 还是已归一化的矩阵",
  "not stated": "还没写",
  "provenance_fields": "provenance_fields",
  " - these four are what the contract requires every tool to leave behind, so they are not offered as a choice":
    " —— 这四个是契约要求每个工具都必须留下的字段，所以这里不作为可选项给出",
  "how it may fail, and how long the grant stays open": "它失败时怎么走，凭据开多久",
  "the cage admits only these two: stop, or hand the node to a person":
    "沙箱只接受这两种：停下来，或者把这个节点交给人",
  "left as it stands: no network, no GPU, no egress, no retry, project-scoped assets, and the grant is spent by the one execution it authorises (§9.6). A retry would spend a gate twice, so the platform does not offer one here":
    "照原样固定：不联网、不给 GPU、不出流量、不重试、资产只限本项目，而且这份凭据就被它授权的那一次执行用掉（§9.6）。重试会把一扇门花两次，所以平台这里不提供重试",
  "optional; the deployment's own window is the ceiling and asking for less is allowed":
    "可不填；本次部署自己的有效期就是上限，填得更短是可以的",
  "optional; left open, the row records the gate's own decision reason":
    "可不填；留空时这一行记的就是那扇门裁定时写下的理由",
  "{n} lines still have to be stated:": "还有 {n} 行需要由你来写：",
  "issuing…": "发放中…",
  "this grant is the execution of one named decision, so the route issues it only for {who}, the subject that decided the gate - not for anyone who merely agrees with it (§11.6)":
    "这份凭据执行的是一个具名的决定，所以接口只认 {who}——裁定那扇门的那个人；旁人哪怕同意这个决定，也发不出它（§11.6）",
  "Grant {id} is on the record: {tool}@{version} may execute at node {node}, live until {until}.":
    "凭据 {id} 已记入台账：{tool}@{version} 可以在节点 {node} 上执行，有效到 {until}。",
  " one gate buys one grant, so sending these same lines again answers with the row that already stands rather than a second permission (§9.6)":
    " 一扇门只买一份凭据，所以把这些行原样再发一次，答复的是台账上已经站着的那一行，而不是第二份许可（§9.6）",

  // ---------------------------------------------------------------- 顶栏、身份抽屉与 404
  screens: "页面导航",
  "identity and project settings": "身份与项目设置",
  "sign in": "登录",
  language: "语言",
  close: "关闭",
  menu: "菜单",
  "what this declaration does and does not mean": "这份声明说明了什么、又没说明什么",

  // ---------------------------------------------------------------- 证据卡与空态
  "show the {n} field-level sources": "查看 {n} 个字段级来源",
  "not recorded": "未记录",
  "The rule placed no card in this queue. Nothing is hidden here: an empty queue means the scores or thresholds produced no ask this week.":
    "规则没有把任何证据卡放进这个队列。这里没有隐藏什么：空队列意味着本周的分数或阈值没有产生可问的问题。",
  "This page could not be found.": "找不到这个页面。",
  "A card is reachable only through a run and the expert queue; every screen starts from the project list.":
    "证据卡只能从一次运行和专家队列进入；每个屏幕都从项目列表开始。",
  "Back to the start": "回到首页",
};
