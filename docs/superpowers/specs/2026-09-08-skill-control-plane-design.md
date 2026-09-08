# Skill 项目管理 API 设计

日期：2026-09-08。

状态：2026-09-08 用户已确认本书面设计，[逐文件实施计划](../plans/2026-09-08-skill-control-plane.md) 已编写并自检，待选择执行方式；尚未实施新接口，本文不是测试通过或上线声明。

基线：`51c1aad`。上阶段结果见 [基础模块验收记录](../plans/2026-09-08-skill-foundation-verification.md)；整体方向见 [Skill 生产化设计](2026-09-07-skill-productionization-design.md)。

## 1. 交付范围与接入边界

本子阶段交付可独立测试的项目 Skill 管理服务及 FastAPI 路由：读取、创建草稿、编辑正文、原子 ZIP 导入、发布、历史版本查询。复用已有包解析、对象保存、草稿和不可变版本仓储。

接口前缀为 `/api/project-skills`，与按名称操作目录的旧 `/api/skills` 分开。作用域来自经过认证的当前项目，而不是 URL、请求体或调用者声明的单位和项目。Skill 和版本均使用 UUID。

本轮不在 `app.main` 导入或挂载新路由，不增加可意外开启的生产配置开关；契约与集成测试使用显式挂载新路由的测试应用。对外挂载、前端切换和部署须在后续接入批次完成，不把测试应用当作可部署的生产入口。

旧路由、旧 SkillService、本地目录、现有页面、Agent/Team 名称绑定及历史运行快照保持不变。新服务不得回退读取旧目录，旧路由也不得隐式双写新表。

排除：停用/删除、附件下载或运行时读取、引用查询、回退发布、Agent/Team 确定版本绑定、菜单和管理页面改版、旧数据导入与切换、知识库、Embedding、脚本执行、依赖安装、远端推送和部署。新接口的安全验收不能代表旧路由的认证缺口已解决。

### 方案比较

- 采用独立路由和服务，测试中挂载：可明确验证新契约，旧调用保持不变；代价是本批暂不能通过现有页面使用。
- 直接替换旧路由：能较快提供页面入口，但名称与 UUID、数组与分页响应、目录编辑与草稿发布不兼容，会迫使本批同时改前端和运行时。
- 同一路由按开关或参数兼容两套后端：过渡入口较少，但容易形成授权差异、名称回退和双写歧义，本批不采用。

## 2. 技术与模块职责

沿用 Python、FastAPI、Pydantic、SQLAlchemy 2、Alembic、PostgreSQL、Boto3/MinIO、pytest；不引入新框架、任务队列或微服务。

| 模块 | 本批职责 |
| --- | --- |
| `backend/app/skills/project_router.py`（新增） | 独立路由、认证依赖、请求/响应契约及预期错误的 HTTP 映射 |
| `backend/app/skills/project_schemas.py`（新增） | UUID、revision、分页、导入清单、幂等键和有界正文校验，不复用旧 SkillInfo 响应 |
| `backend/app/skills/project_service.py`（新增） | 作用域及资源授权、包重建与验证、导入编排、发布验证、事务和审计 |
| `backend/app/skills/repository.py`（增量扩展） | 作用域与所有者过滤、分页和计数、读取草稿/版本列表、发布幂等结果查询；仍不隐式 commit |
| `backend/app/skills/package.py`、`package_storage.py` | 复用有界解析、规范化摘要及不可变对象读写；仅为真实需要补充窄接口，不复制解析器 |
| `backend/app/identity/catalogue.py` 与新增权限迁移 | 为内置 project_admin 补充项目范围 skill.manage，不更改旧菜单映射 |
| `backend/tests/skills/`、`backend/tests/identity/`、`backend/tests/integration/` | 服务、HTTP、授权目录、真实存储及事务验证 |

模块导入、路由构造及只读元数据查询不得创建本地目录、连接 MinIO 或初始化旧 SkillService。存储通过可注入的惰性工厂取得；只有确实需要读写包时才构造客户端。同步 Boto3 和包处理不直接阻塞 async 路由事件循环，沿用 FastAPI 同步处理或明确的线程边界。

## 3. 身份与权限

路由复用 `require_request_context`；服务也必须拒绝缺失 `authorization_context` 的上下文。验证 user_id、unit_id、current_project_id 与 RequestContext 一致，并查询确认当前项目存在、属于该单位且 active。没有选择有效项目时返回 403 `skill_project_required`，不能自动选择另一项目。

生产 Cookie 身份完全由服务端会话和授权仓储构造，伪造 X-User-Roles/X-Project-ID 不能改变作用域。测试开发身份仅沿用现有明确启用的 dev/test 限制，不能作为生产授权回退。项目切换沿用现有会话机制，不在新 Skill API 中增加切换能力。

读取使用 `skill.read`；创建、保存、导入、发布使用 `skill.manage`。管理权限不自动等于任意查询权限；变更接口可返回该次变更必要的结果。`skill.invoke` 本轮不使用，也不能用来替代管理或读取权限。

资源授权调用 `AuthorizationService` 并传入真实 ResourceScope。项目内全量和 own 授权必须区分：own 范围使用 Skill.created_by 判断，不能把每个被访问资源的 owner 都填成请求者。列表和 total 在 SQL 查询阶段使用同样的可见范围过滤，不能先分页再过滤，也不能通过版本、导入冲突响应泄露其他项目或不允许访问的资源详情。

授权顺序固定为：先认证及当前项目检查，再检查本次操作的入口能力，最后查询该操作数据范围内的资源。没有入口能力返回 403；具备入口能力后，跨单位、跨项目或不在该操作 own 范围内的资源统一返回 404，不再用第二个 403 分支泄露其存在。所有资源查询先限定作用域，不以全局查找结果判断其真实归属。

project_admin 默认增加本项目的 skill.manage；单位管理员仍需在现有会话中选定有效项目操作，不能通过本 API 绕过项目选择策略。其他内置和自定义角色保持现有授权。旧 Skill 菜单继续是单位范围，不因新增项目授权自动显示。

代码交付包含增量权限迁移：仅为内置 project_admin 添加缺失的 `(role_id, skill.manage, project)` 授权，更新新建单位的默认目录定义；不重置其他授权、不启用停用角色、不调用可能删除额外授权的全量重播种逻辑。迁移不删除授权作为回滚捷径，降级保留该项追加授权并明确记录。迁移版本承接实施时实际 Alembic head；本轮只在专用测试库执行，不修改业务数据库或会话。

## 4. API 契约

以下路径均相对 `/api/project-skills`。所有响应通过显式 Schema 输出，禁止直接序列化 ORM 的 object_key、桶名、存储端点或凭据。`content` 表示含 YAML frontmatter 的完整 SKILL.md 文本，不是额外拼接的正文片段。

| 方法与路径 | 请求 | 成功结果 | 权限 |
| --- | --- | --- | --- |
| `GET /` | offset 默认 0，limit 默认 20 且 1–100；可选 q，最长 120 字符 | 200 `{items,total,offset,limit}`，摘要不含正文和文件正文 | skill.read |
| `POST /` | `{content}`；清单 name 为资源名 | 201 资源摘要，包含 UUID 和 draft_revision=1 | skill.manage |
| `GET /{skill_id}` | UUID | 200 资源摘要、当前草稿 revision、当前发布版本标识 | skill.read |
| `GET /{skill_id}/draft` | UUID | 200 草稿正文、元数据、revision、摘要及文件清单 | skill.read |
| `PUT /{skill_id}/draft` | `{expected_revision,content}` | 200 保存后的草稿和递增 revision | skill.manage |
| `POST /import` | multipart 的 file 和可选 manifest JSON 字段 | 200 `{items,created_count,updated_count,skipped_count}`；items 只含每个包的结果摘要 | skill.manage |
| `POST /{skill_id}/publish` | `{expected_revision}` 及必需的 `Idempotency-Key` 头 | 200 发布版本元数据，包含版本 UUID、数字版本号、source_revision 和 package_digest | skill.manage |
| `GET /{skill_id}/versions` | offset、limit 同上 | 200 分页版本摘要，无完整正文 | skill.read |
| `GET /{skill_id}/versions/{version_id}` | 两个 UUID | 200 不可变版本正文、元数据、摘要和文件清单 | skill.read |

分页按资源 created_at、id 稳定升序，版本列表按 version 降序；q 按 name 字面包含匹配，不将 `%`、`_` 当作查询通配符。total 与 items 使用完全相同过滤条件。并发修改下各请求反映读取时状态，不承诺跨请求分页快照。

资源名称在本批创建后固定；编辑 SKILL.md 的 name 必须与资源名称一致，不支持隐式重命名。清单验证复用 parse_skill_bundle/parse_skill_markdown 的规则；description、display_version 由清单解析，不接受彼此可能矛盾的重复字段。新 JSON 文本输入沿用现有编辑上限 200,000 字符；ZIP 导入继续受包限额约束，超过编辑上限的既有导入正文可读取、发布，但 JSON 编辑要先缩减正文，不能静默截断。

请求 Schema 拒绝多余字段；不接收 unit_id、project_id、created_by、object_key 或客户端计算的可信摘要。revision 为严格正整数，幂等键 1–128 个可打印 ASCII 字符，不自动去空格或规范化为另一键。

## 5. 草稿与文件保存

创建只生成含 SKILL.md 的包，经过相同 ZIP 校验和摘要计算，再以服务端预分配 UUID 写入新对象，最后持久化资源与草稿。禁止把未经解析的请求正文直接构造成“已验证包”。

编辑已有草稿时，读取并验证该 revision 的对象包，保留所有非 SKILL.md 文件，替换清单文本后完整重验并写入新对象。不得因编辑正文而丢弃附件或覆盖原对象。对象上传前校验预期 revision；上传后数据库事务中仍必须使用 revision 比较交换，处理上传期间发生的并发变更。

ZIP 沿用 10 MiB 压缩包、20 MiB 实际解压内容和 500 条目上限。上传处理不得无限制 `read()`；分块计数超过上限立即拒绝，最终关闭 UploadFile。认证和权限拒绝必须发生在业务解析、对象读写和数据库变更之前；框架接收 multipart 时的暂存行为不视为业务对象写入，不宣称路由依赖能阻止所有入站字节接收。

## 6. 原子批量导入

整个 ZIP 首先完成全部校验，再处理冲突；ZIP 内重复名称、非法路径或损坏压缩数据令整批失败。

未提供 manifest 时，全部按原清单名称创建；任一同名冲突返回 409，整批不写入数据库。显式 manifest 是按 source_name 一一对应全部包的数组，每项只允许以下字段组合：

- `create`：`{source_name, action:"create", target_name?}`。target_name 不同于原名时，使用 YAML 结构化解析和序列化改写清单 name，并重新验证包和摘要；不使用字符串替换。最终目标名必须在批内唯一。
- `update`：`{source_name, action:"update", skill_id, expected_revision}`。目标必须是当前作用域内可管理资源；将导入清单 name 规范为目标资源名后重新验证，替换该 revision 的整个草稿包。不能更新任何历史发布版本。
- `skip`：`{source_name, action:"skip"}`。只跳过上传包，不查询或返回其他资源详情，不执行对象上传。

未知、遗漏或重复 source_name、同一目标 UUID 更新多次，以及不属于 action 的字段均返回 422。manifest 最多 500 项、UTF-8 编码最多 256 KiB；所有长度和名称仍由 Schema 校验。支持显式改名，不设计自动递增后缀或失败后自动重试覆盖。

对所有非 skip 包先完成授权、冲突预检和对象准备；再用一个数据库事务创建/更新全部草稿并追加审计。多个既有目标按 UUID 固定顺序加行锁，重新校验 revision；同名并发创建依赖唯一约束并映射为 409。任一对象失败、并发冲突、审计异常或提交异常都不留下部分可见草稿；已有草稿、发布指针和历史版本不变。

上传成功但数据库回滚可能留下无引用对象，这是本批接受的补偿边界；不在异常处理中删除桶、共享目录或任意对象，也不引入未验证的自动回收。未引用对象不可通过本 API 读取，部署前另行制定基于数据库引用的回收策略。接口不承诺跨 MinIO/PostgreSQL 的分布式事务。

## 7. 发布、并发与幂等

每次发布先检查当前身份和资源管理权限，已提交请求重放也必须重新授权。

先在作用域内查询既有幂等结果：同 Skill、同 expected_revision、同发布用户及同键返回原版本；参数不同返回 409。该步骤在读取当前草稿、联系 MinIO 和校验当前 revision 之前进行，避免发布后草稿已变或存储暂不可用使已成功请求不能重放；重放不再次追加成功审计。

没有既有结果时，读取预期 revision 的草稿及其受信对象引用，通过 SkillPackageStorage.read 验证长度、存储元数据和归档摘要，再 parse_skill_bundle 校验包。必须恰好得到该 Skill 的一个包，正文、清单、展示版本、文件清单和规范化 package_digest 与草稿一致。对象失联、篡改或数据不一致不能切换发布指针。

存储读取期间不持有数据库行锁；完成外部验证后使用短写事务锁定资源，并再次查询幂等结果、核对 revision 及已验证的草稿快照指纹，然后调用仓储 publish。SQLAlchemy 查询会自动开启事务，服务必须明确管理预检读取和写事务的边界，不能在已开启的请求会话上无条件再 begin，也不能提交不属于本次变更的工作。所有草稿写入与发布使用同一资源锁边界。禁止在验证了旧草稿后发布未验证的新草稿；直接绕过服务篡改草稿不属于支持的写入路径，但第二次快照比对仍须拒绝不一致。

版本插入、revision 消耗、发布指针切换与成功审计同事务提交。审计失败必须回滚版本和指针；事务内异常显式 rollback，不返回伪成功。使用版本 ID 构成审计幂等键，确保并发同键重放只产生一份成功发布记录。响应使用固定发布元数据，不附带重放时可能已变化的当前草稿 revision。

实现须专门覆盖两条竞态：同键竞争者在存储验证前或验证期间已经提交时，优先返回其已提交结果；没有既有同键结果的陈旧 revision 返回 409。同键重放不能借助宽泛的异常捕获掩盖无关数据库故障。

## 8. 审计及错误契约

复用 AuditRecorder，不新增 `skill` source 枚举；使用 category=management、source=system、resource_type=skill、project 事件范围和 skill.create / skill.draft.save / skill.import / skill.publish 动作。

成功变更与审计同事务；批量导入为每个实际变更资源写同批 trace_id 的事件。审计仅包含允许的版本标识、revision、摘要、计数和结果，不记录 SKILL.md、附件正文、请求 Cookie、幂等请求原文或存储凭据。只读和 skip 项不伪造变更成功事件。本批不扩展独立失败审计事务或全局安全日志架构。

| HTTP | 应用错误/行为 |
| --- | --- |
| 401 | 复用身份入口的缺失、失效会话语义；不得接受客户端角色作为认证 |
| 403 | skill_project_required、skill_permission_denied；既有首次改密限制保持原样 |
| 404 | skill_not_found、skill_version_not_found，不透露资源真实归属 |
| 409 | skill_name_conflict、skill_revision_conflict、skill_idempotency_conflict |
| 413 | 实测上传字节超过压缩包上限 |
| 422 | Pydantic 请求错误沿用默认结构；业务包或导入清单错误使用 skill_package_invalid / skill_import_manifest_invalid，错误说明不得暴露其他资源或存储细节 |
| 503 | skill_storage_unavailable，包含存储连接失败及完整性验证失败，均拒绝发布 |
| 500 | 未预期错误或审计/提交故障，回滚并返回通用错误，不把所有 Exception 映射为用户输入错误 |

新读响应设置 Cache-Control: no-store。Cookie 写请求在最终挂载时必须经过主应用的现有 CSRF/Origin 防护；本批用包含实际主应用中间件的隔离装配测试验证该边界，但不更改共享中间件策略，也不宣称它提供了新设计的安全保证。

## 9. 验收与安全资源

- 对每个新方法验证未认证拒绝、没有权限拒绝、跨单位/项目不可见、own 过滤和分页 total；使用真实授权数据与 Cookie 会话补充开发身份测试，拒绝伪造请求头升级权限。
- project_admin 在自己的项目可管理，其他项目拒绝；单位管理员通过既有项目选择机制操作；停用项目、撤销成员身份及撤销授权在下一请求生效。内置目录与增量权限迁移分别覆盖新单位和已有单位；不改变其他角色、旧菜单及自定义授权。
- 在真实存储边界验证新建、编辑保留附件、显式改名导入、skip、原子批处理、无身份时不构造客户端，以及网络/摘要失败不留下可见数据库变更。
- 使用真实 PostgreSQL 双连接验证同名创建、陈旧草稿、同键发布重放、不同参数同键拒绝和发布中草稿变化。检查版本、指针、revision 与审计同时提交/回滚；不能用 SQLite 通过替代锁竞争验收。
- 测试主应用不包含新路由；旧路由路径及响应契约不变。新路由在独立装配中实测 HTTP 和 Cookie 写保护，避免仅测试私有服务函数。
- 回归包含旧 Skill、身份授权、Team 服务、迁移图、包/存储/仓储；最终再执行完整后端回归和指定真实集成。跳过项单列，既有计时失败如再现先诊断，不放宽原断言。
- 只使用新子计划命名的专用数据库、临时目录与 UUID MinIO 测试桶。已有测试库不直接复用为新权限迁移试验库；业务库、旧目录和用户文档不变。测试凭据仅进程注入，不写入 Git。

本轮无 UI 变更，不以浏览器中的旧管理页面演示作为新接口验收。API 验收完成后，后续仍需运行时确定版本绑定、界面接入及旧数据迁移/切换，才具备生产上线条件。

## 10. 本次设计状态与自检

- [x] 核对基础模块、身份上下文、授权服务、审计记录器、旧路由与前端契约。
- [x] 用户确认独立开发测试、旧链路暂不切换；本轮无需要视觉对照的界面问题。
- [x] 记录方案取舍及本批范围，明确不做隐式生产挂载或数据迁移。
- [x] 写出接口、冲突处理、事务、幂等、权限迁移及验收边界。
- [x] 完成书面自检；本文件纳入独立设计提交，不包含业务源码变更。
- [x] 用户已审阅并确认本文件。
- [x] 完成逐文件实施计划和自检。
- [ ] 选择执行方式，再进入 TDD 与任务审查。

自检结果：已明确 403/404 的判断顺序，补充 SQLAlchemy 自动事务与发布写事务的边界，区分数据库原子提交与对象存储补偿；未发现待填占位或与独立测试范围相冲突的生产切换要求。本批 enabled 字段、删除、附件运行与迁移切换均未实现，不输出伪造能力；旧接口认证风险仍属后续上线门禁。

设计分支基线复核：现有 `backend/tests/skills/test_repository.py` 和 `backend/tests/test_skills.py` 共 29 项通过，1 条既有 Starlette/AnyIO 警告，38.92 秒；没有运行或声称通过尚未实现的新 API 测试。后续实施不得以本文概览替代具体步骤、失败测试和真实集成证据。
