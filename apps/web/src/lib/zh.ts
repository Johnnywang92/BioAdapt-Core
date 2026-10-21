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
  "no default on purpose": "故意不设默认值",
  choose: "请选择",
  "an hgr profile forces the tier to hgr_controlled or strict_local":
    "带 hgr 画像时，层级只能选 hgr_controlled 或 strict_local",
  "creating…": "创建中…",
  "Create project": "创建项目",
  "paste a token first": "先粘贴 token",
  "public": "公开",
  restricted: "受限",
  strict_local: "严格本地",
  hgr_controlled: "人类遗传资源受控",
  "model calls and exports are allowed within the provider terms":
    "在服务商条款允许的范围内可以调用模型、可以导出",
  "no raw egress; aggregate derivation only, and any cloud model call is checked field by field":
    "原始数据不得出境；只做聚合派生，且每一次云端模型调用都逐字段核对",
  "raw data and sensitive derivations stay local; no cloud model call at all":
    "原始数据与敏感派生一律留在本地；完全不调用云端模型",
  "human genetic resource controlled: local compute, export refused, approval required":
    "人类遗传资源受控：本地计算、导出直接拒绝、必须走审批",
  hgr: "人类遗传资源",
  pipl: "个人信息保护法",
  gdpr: "GDPR",
  hipaa: "HIPAA",
  commercial_only: "仅商业条款",

  // ---------------------------------------------------------------- token 里的角色
  principal_investigator: "主要研究者（PI）",
  analyst: "分析员",
  domain_expert: "领域专家",
  platform_admin: "平台管理员",
  compliance_officer: "合规官",

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
  'the card fields to overwrite, e.g. {"candidate_label": "intermediate state", "label_level": "cell_state"} — they become human-sourced':
    '要覆盖的卡片字段，例如 {"candidate_label": "intermediate state", "label_level": "cell_state"}——它们会被标为人工来源',
  "a re-read that keeps the earlier verdict still gets its own row": "即使重读后结论不变，也要单独记一行",
  "recording…": "记录中…",
  "Record review": "记录评审",
  "this decision moves the status, and the gate decides whether it may": "这个决定会改变状态，而门会裁定它能不能改",
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
  "leave it at the project tier unless this object is genuinely less sensitive":
    "除非这个对象确实更不敏感，否则保持与项目相同的层级",
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
  tier: "层级",
  retention: "保留",
  filtering: "过滤",
  "produced by": "产出者",
  "registered by hand": "人工登记",
  download: "下载",
  "Nothing is registered in this project yet.": "这个项目还没有登记任何对象。",

  // ---------------------------------------------------------------- 导出
  Exports: "导出",
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
  "This project is {tier}: the export route refuses it whatever the role of the caller (§5.9). Delivery happens from the local side, so nothing is queued here.":
    "本项目的层级是 {tier}：无论调用者是什么角色，导出路由都会拒绝（§5.9）。交付要从本地这一侧完成，所以这里不会排队。",
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
  "nothing is read until a token is set — the API resolves the caller from it":
    "设了 token 之前不读取任何东西——API 从它解析出调用者",
  "the tier on this row is what every later screen inherits: it decides egress, model calls and whether an export is possible at all":
    "这一行上的层级是后面每一屏继承的约束：它决定能否出境、能否调用模型、以及导出这件事是否可能",
  "study goal": "研究目标",
  species: "物种",
  "privacy tier": "隐私层级",
  "regulatory profile": "监管画像",
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
  // 审批
  adhoc_tool: "ad-hoc 工具",
  escalation: "上报",
  pending: "待处理",
  approved: "已批准",
  expired: "已过期",
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
};
