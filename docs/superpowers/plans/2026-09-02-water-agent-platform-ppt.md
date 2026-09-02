# 水利智能体平台建设方案 PPT Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 生成一份面向领导汇报的三页可编辑 PowerPoint，依次说明水利智能体平台总体架构、核心功能和应用场景。

**Architecture:** 采用从零重建的轻量视觉路线，用参考稿中已确认的水利科技汇报语言、蓝青主色、清晰标题层级和结构图表达，但不导入 624 MB 原稿。构建脚本使用 `@oai/artifact-tool` 生成全部可编辑对象，同时输出逐页 PNG、布局 JSON 和蒙太奇用于验证。

**Tech Stack:** JavaScript ES modules、`@oai/artifact-tool`、PowerPoint `.pptx`、Bundled presentation render and overflow tools。

## Global Constraints

- 最终文件必须恰好三页，不另设封面、目录或结束页。
- 三页顺序固定为总体架构、核心功能、应用场景。
- 页面面向水利行业领导，标题必须表达可直接复述的结论。
- 平台能力以仓库 `README.md`、总体架构设计和详细功能设计为依据，不虚构建设成果或量化指标。
- 控制类操作必须明确保留权限校验、风险审批和人工确认。
- 使用参考稿的蓝青科技色、白底内容页、深蓝标题和水利业务视觉语汇，不声称继承原稿母版。
- 所有中间文件放在 `.tmp/ppt-platform-plan/`；最终文件放在仓库根目录 `水利智能体平台建设方案-领导汇报-3页.pptx`。
- 使用 `@oai/artifact-tool` 编写 `.mjs` 构建脚本，禁止使用 `python-pptx`。
- 正式交付前必须完成一次发现问题、修正、再次验证的闭环。

---

### Task 1: 固化内容契约与视觉参数

**Files:**
- Create: `.tmp/ppt-platform-plan/content-contract.json`
- Create: `.tmp/ppt-platform-plan/source-notes.txt`
- Create: `.tmp/ppt-platform-plan/visual-audit.txt`
- Read: `README.md`
- Read: `docs/superpowers/specs/2026-07-30-intelligent-agent-platform-overall-architecture-design.md`
- Read: `docs/智能体平台详细功能设计与现状改造清单.md`
- Read: `I:/智能体平台/0-南水北调中线工程多水源供水保障与智慧调控技术-中期汇报定稿版.pptx`

**Interfaces:**
- Consumes: 已批准的设计说明 `docs/superpowers/specs/2026-09-02-water-agent-platform-ppt-design.md`。
- Produces: `content-contract.json`，包含 `slides[3]`、每页 `title`、`claim`、`sections`、`footer`；`visual-audit.txt`，包含画布比例、字体、主色、标题位置和图形风格。

- [ ] **Step 1: 建立内容契约**

将三页可见文字写入 `content-contract.json`。固定标题为：

```json
{
  "slides": [
    {"title": "构建统一、安全、可扩展的水利智能体平台", "section": "总体架构"},
    {"title": "围绕资源管理、智能编排与安全运行形成完整闭环", "section": "核心功能"},
    {"title": "优先落地四类高价值水利业务场景", "section": "应用场景"}
  ]
}
```

- [ ] **Step 2: 提取参考视觉证据**

使用只读 ZIP/XML 检查提取原稿 `ppt/theme/theme*.xml`、`ppt/slideMasters/*.xml`、画布尺寸和字体证据，并记录到 `visual-audit.txt`。不要复制或改写原稿。

- [ ] **Step 3: 记录来源边界**

在 `source-notes.txt` 中记录仓库三份内容来源、参考稿仅用于视觉方向，以及“无外部统计数据、无外部图片”的事实。

- [ ] **Step 4: 检查内容契约**

运行：

```powershell
$contract = Get-Content -Raw '.tmp\ppt-platform-plan\content-contract.json' | ConvertFrom-Json
if ($contract.slides.Count -ne 3) { throw 'Expected exactly 3 slides' }
if (($contract.slides.section | Select-String '总体架构|核心功能|应用场景').Count -ne 3) { throw 'Missing required section' }
```

预期：命令成功，无输出。

### Task 2: 先写验证器，再生成可编辑 PPT

**Files:**
- Create: `.tmp/ppt-platform-plan/validate-deck.mjs`
- Create: `.tmp/ppt-platform-plan/build-deck.mjs`
- Create: `.tmp/ppt-platform-plan/source-notes.txt`
- Create: `水利智能体平台建设方案-领导汇报-3页.pptx`

**Interfaces:**
- Consumes: `content-contract.json` 中的三页标题、结论和模块文案；`visual-audit.txt` 中的色彩与字体参数。
- Produces: `buildDeck({ outputPath, previewDir })` 生成三页 PPTX、PNG、layout JSON 和 montage；`validateDeck(pptxPath)` 验证页数、标题和禁用文本。

- [ ] **Step 1: 编写交付物验证器**

`validate-deck.mjs` 必须通过 `PresentationFile.importPptx` 读取输出，断言：

```js
assert.equal(presentation.slides.items.length, 3);
for (const title of expectedTitles) assert.ok(allText.includes(title));
for (const forbidden of ["T" + "BD", "T" + "ODO", "Lorem", "点击添加"]) {
  assert.ok(!allText.includes(forbidden));
}
```

- [ ] **Step 2: 运行验证器并确认失败**

运行：

```powershell
& $env:RUNTIME_NODE '.tmp\ppt-platform-plan\validate-deck.mjs' 'I:\智能体平台\IntelligentAgentPlatform\水利智能体平台建设方案-领导汇报-3页.pptx'
```

预期：因最终 PPTX 尚不存在而失败，错误包含 `ENOENT` 或 `cannot find`。

- [ ] **Step 3: 运行 artifact 操作标记**

在 Presentations skill 目录执行一次：

```powershell
node container_tools/mark_artifact_operation_started.mjs --operation-kind create --expected-output-count 1 --output-format pptx
```

预期：命令成功。后续不得重复运行该标记。

- [ ] **Step 4: 编写最小构建脚本**

`build-deck.mjs` 使用 `Presentation.create({ slideSize: { width: 1280, height: 720 } })`，并实现：

```js
async function buildDeck({ outputPath, previewDir }) {
  // slide 1: 五层架构 + 贯穿式安全治理
  // slide 2: 一条能力闭环 + 六项核心能力
  // slide 3: 四类场景 + 五步业务闭环
  // 每个文本框和图形使用稳定 name，导出逐页 PNG、layout JSON、montage 和 PPTX
}
```

视觉参数以 `visual-audit.txt` 为准，默认回退为深蓝 `#123B67`、湖蓝 `#1C7EA8`、青绿 `#2A9D8F`、浅蓝灰 `#EAF3F7`、正文 `#243746`，中文字体使用参考稿识别出的字体；若无法识别则使用 `Microsoft YaHei`。

- [ ] **Step 5: 生成 PPTX 和预览**

运行：

```powershell
& $env:RUNTIME_NODE '.tmp\ppt-platform-plan\build-deck.mjs'
```

预期：生成最终 PPTX、3 张 PNG、3 份 layout JSON 和 1 张 montage。

- [ ] **Step 6: 运行验证器并确认通过**

运行 Task 2 Step 2 的同一命令。

预期：输出 `validated: 3 slides`，退出码为 0。

### Task 3: 渲染审阅、修正与最终验证

**Files:**
- Modify: `.tmp/ppt-platform-plan/build-deck.mjs`
- Create: `.tmp/ppt-platform-plan/qa-ledger.txt`
- Modify: `水利智能体平台建设方案-领导汇报-3页.pptx`

**Interfaces:**
- Consumes: Task 2 生成的最终 PPTX、PNG 和 layout JSON。
- Produces: 无溢出、无遮挡、无异常换行且叙事一致的最终三页 PPTX。

- [ ] **Step 1: 程序化检查溢出**

运行：

```powershell
& $env:RUNTIME_NODE '.tmp\ppt-platform-plan\validate-deck.mjs' 'I:\智能体平台\IntelligentAgentPlatform\水利智能体平台建设方案-领导汇报-3页.pptx'
& 'C:\Users\Administrator\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' 'C:\Users\Administrator\.codex\plugins\cache\openai-primary-runtime\presentations\26.826.12353\skills\presentations\container_tools\slides_test.py' 'I:\智能体平台\IntelligentAgentPlatform\水利智能体平台建设方案-领导汇报-3页.pptx'
```

预期：页数和内容验证通过，`slides_test.py` 不报告超出画布的元素。

- [ ] **Step 2: 独立逐页视觉审阅**

将三张最终 PNG 交给独立审阅者，逐页检查重叠、裁切、间距、层级、低对比度、异常换行、模板一致性和领导汇报可读性。所有发现写入 `qa-ledger.txt`。

- [ ] **Step 3: 完成至少一轮修正**

根据 `qa-ledger.txt` 修改 `build-deck.mjs`。即使首轮没有严重问题，也至少完成一次可说明的细节修正，例如标题留白、模块间距或对比度，然后重新生成全部输出。

- [ ] **Step 4: 复核修正结果**

重新运行验证器、溢出检查和逐页渲染检查。确认三页顺序正确，所有标题保持单行，控制类场景保留“人工确认/审批”表达。

- [ ] **Step 5: 最终文件检查**

运行：

```powershell
Get-Item 'I:\智能体平台\IntelligentAgentPlatform\水利智能体平台建设方案-领导汇报-3页.pptx' | Select-Object FullName,Length,LastWriteTime
```

预期：文件存在、长度大于 0，修改时间为本次任务执行时间。
