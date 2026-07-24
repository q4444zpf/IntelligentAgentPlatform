<template>
  <div class="skill-page">
    <a-alert v-if="loadError" type="error" show-icon closable message="技能池服务暂时不可用" :description="loadError" />

    <section class="skill-heading">
      <div>
        <div class="heading-label">CAPABILITY LIBRARY</div>
        <h2>技能池</h2>
        <p>维护智能体可发现、可复用的任务说明、脚本与参考资料</p>
      </div>
      <a-space wrap>
        <a-input v-model:value="query" allow-clear placeholder="搜索 Skill 名称或说明" class="search-input"><template #prefix><SearchOutlined /></template></a-input>
        <a-select v-model:value="statusFilter" class="status-filter" :options="statusOptions" />
        <a-tooltip title="刷新技能池"><a-button aria-label="刷新技能池" :loading="loading" @click="loadSkills"><template #icon><ReloadOutlined /></template></a-button></a-tooltip>
        <a-button @click="openImport"><template #icon><ImportOutlined /></template>导入 ZIP</a-button>
        <a-button type="primary" @click="openCreate"><template #icon><PlusOutlined /></template>添加 Skill</a-button>
      </a-space>
    </section>

    <div class="summary-strip">
      <div><span>技能总数</span><strong>{{ skills.length }}</strong></div>
      <div><span>已启用</span><strong>{{ enabledCount }}</strong></div>
      <div><span>导入技能</span><strong>{{ importedCount }}</strong></div>
      <div><span>资源文件</span><strong>{{ totalFiles }}</strong></div>
    </div>

    <div v-if="allTags.length" class="tag-rail">
      <button type="button" :class="{ active: !activeTag }" @click="activeTag = ''">全部</button>
      <button v-for="tag in allTags" :key="tag" type="button" :class="{ active: activeTag === tag }" @click="activeTag = activeTag === tag ? '' : tag">{{ tag }}</button>
    </div>

    <a-spin :spinning="loading && !skills.length">
      <div v-if="filteredSkills.length" class="skill-list">
        <article v-for="skill in filteredSkills" :key="skill.name" class="skill-item">
          <div class="skill-glyph"><CodeOutlined /></div>
          <div class="skill-main">
            <div class="skill-title">
              <strong>{{ skill.name }}</strong>
              <a-tag v-if="skill.version" color="blue">v{{ skill.version }}</a-tag>
              <a-tag :color="skill.source === 'imported' ? 'cyan' : 'default'">{{ skill.source === 'imported' ? 'ZIP 导入' : '平台创建' }}</a-tag>
            </div>
            <p>{{ skill.description }}</p>
            <div class="skill-tags"><a-tag v-for="tag in skill.tags" :key="tag">{{ tag }}</a-tag><span v-if="!skill.tags.length">未设置标签</span></div>
          </div>
          <div class="file-count"><strong>{{ skill.file_count }}</strong><span>文件</span></div>
          <div class="updated"><strong>{{ skill.enabled ? '已启用' : '已停用' }}</strong><span>{{ formatTime(skill.updated_at) }}</span></div>
          <div class="item-actions">
            <a-tooltip title="查看与编辑"><a-button aria-label="查看与编辑 Skill" @click="openEdit(skill)"><template #icon><EditOutlined /></template></a-button></a-tooltip>
            <a-switch :checked="skill.enabled" :loading="busyName === skill.name" checked-children="开" un-checked-children="关" @change="toggleSkill(skill)" />
            <a-popconfirm title="删除后将移除整个 Skill 目录，确定继续？" ok-text="删除" cancel-text="取消" @confirm="deleteSkill(skill)"><a-button danger aria-label="删除 Skill"><template #icon><DeleteOutlined /></template></a-button></a-popconfirm>
          </div>
        </article>
      </div>
      <a-empty v-else :description="query || activeTag || statusFilter !== 'all' ? '没有匹配的 Skill' : '技能池还是空的'">
        <a-space v-if="!query && !activeTag && statusFilter === 'all'"><a-button @click="openImport">导入 ZIP</a-button><a-button type="primary" @click="openCreate">添加 Skill</a-button></a-space>
      </a-empty>
    </a-spin>

    <a-modal v-model:open="editorOpen" :title="editingName ? `编辑 ${editingName}` : '添加 Skill'" width="860px" :confirm-loading="saving" ok-text="保存" cancel-text="取消" @ok="saveSkill">
      <div class="editor-grid">
        <a-form layout="vertical">
          <a-form-item label="Skill 名称" required :validate-status="formErrors.name ? 'error' : undefined" :help="formErrors.name || '小写字母开头，可使用数字、- 和 _。'">
            <a-input v-model:value="form.name" :disabled="Boolean(editingName)" placeholder="flood-forecast" />
          </a-form-item>
          <a-form-item label="说明" required :validate-status="formErrors.description ? 'error' : undefined" :help="formErrors.description"><a-textarea v-model:value="form.description" :rows="3" maxlength="500" show-count /></a-form-item>
          <a-form-item label="标签"><a-select v-model:value="form.tags" mode="tags" :token-separators="[',']" placeholder="输入标签后回车" /></a-form-item>
          <a-form-item label="状态"><a-switch v-model:checked="form.enabled" checked-children="启用" un-checked-children="停用" /></a-form-item>
        </a-form>
        <div class="manifest-editor">
          <div class="editor-label"><span>SKILL.md</span><a-tooltip title="根据左侧字段重新生成基础模板"><a-button size="small" @click="generateTemplate"><template #icon><FileSyncOutlined /></template>生成模板</a-button></a-tooltip></div>
          <a-textarea v-model:value="form.content" :rows="19" spellcheck="false" placeholder="---&#10;name: flood-forecast&#10;description: 洪水预报&#10;---" />
          <div v-if="formErrors.content" class="content-error">{{ formErrors.content }}</div>
        </div>
      </div>
    </a-modal>

    <a-modal v-model:open="importOpen" title="导入 Skill ZIP" width="680px" :confirm-loading="importing" ok-text="开始导入" cancel-text="取消" :ok-button-props="{ disabled: !importFile }" @ok="importSkills">
      <div class="import-layout">
        <a-upload-dragger :file-list="uploadList" accept=".zip,application/zip" :max-count="1" :before-upload="captureFile" @remove="removeFile">
          <p class="ant-upload-drag-icon"><InboxOutlined /></p>
          <p class="ant-upload-text">拖放 Skill ZIP 到这里</p>
          <p class="ant-upload-hint">压缩包不超过 10 MB，可同时包含多个 Skill 目录</p>
        </a-upload-dragger>
        <div class="import-rules">
          <div><FolderOpenOutlined /><span><strong>目录要求</strong>每个 Skill 必须包含一个 SKILL.md，可附带 scripts、references 和 assets。</span></div>
          <div><SafetyCertificateOutlined /><span><strong>安全检查</strong>自动拒绝路径穿越、符号链接、超量文件和异常压缩包。</span></div>
        </div>
        <a-form layout="vertical"><a-form-item label="同名 Skill 处理方式"><a-radio-group v-model:value="conflictStrategy"><a-radio-button value="rename">自动重命名</a-radio-button><a-radio-button value="skip">跳过</a-radio-button><a-radio-button value="overwrite">覆盖</a-radio-button></a-radio-group></a-form-item></a-form>
      </div>
    </a-modal>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue';
import { message, type UploadFile } from 'ant-design-vue';
import { CodeOutlined, DeleteOutlined, EditOutlined, FileSyncOutlined, FolderOpenOutlined, ImportOutlined, InboxOutlined, PlusOutlined, ReloadOutlined, SafetyCertificateOutlined, SearchOutlined } from '@ant-design/icons-vue';
import { skillsApi, type SkillInfo } from '@/api/skills';

const skills = ref<SkillInfo[]>([]); const loading = ref(false); const loadError = ref(''); const query = ref(''); const statusFilter = ref('all'); const activeTag = ref(''); const busyName = ref('');
const editorOpen = ref(false); const editingName = ref(''); const saving = ref(false); const form = reactive({ name: '', description: '', content: '', tags: [] as string[], enabled: true }); const formErrors = reactive({ name: '', description: '', content: '' });
const importOpen = ref(false); const importFile = ref<File>(); const uploadList = ref<UploadFile[]>([]); const importing = ref(false); const conflictStrategy = ref<'rename' | 'overwrite' | 'skip'>('rename');
const statusOptions = [{ label: '全部状态', value: 'all' }, { label: '已启用', value: 'enabled' }, { label: '已停用', value: 'disabled' }, { label: 'ZIP 导入', value: 'imported' }];
const enabledCount = computed(() => skills.value.filter((item) => item.enabled).length); const importedCount = computed(() => skills.value.filter((item) => item.source === 'imported').length); const totalFiles = computed(() => skills.value.reduce((sum, item) => sum + item.file_count, 0));
const allTags = computed(() => Array.from(new Set(skills.value.flatMap((item) => item.tags))).sort((a, b) => a.localeCompare(b, 'zh-CN')));
const filteredSkills = computed(() => { const term = query.value.trim().toLowerCase(); return skills.value.filter((item) => { const text = !term || `${item.name} ${item.description} ${item.tags.join(' ')}`.toLowerCase().includes(term); const status = statusFilter.value === 'all' || (statusFilter.value === 'enabled' && item.enabled) || (statusFilter.value === 'disabled' && !item.enabled) || (statusFilter.value === 'imported' && item.source === 'imported'); return text && status && (!activeTag.value || item.tags.includes(activeTag.value)); }); });
async function loadSkills() { loading.value = true; loadError.value = ''; try { skills.value = await skillsApi.list(); } catch (error) { loadError.value = error instanceof Error ? error.message : '加载失败'; } finally { loading.value = false; } }
function resetForm() { Object.assign(form, { name: '', description: '', content: '', tags: [], enabled: true }); Object.assign(formErrors, { name: '', description: '', content: '' }); }
function openCreate() { resetForm(); editingName.value = ''; editorOpen.value = true; }
function openEdit(skill: SkillInfo) { resetForm(); editingName.value = skill.name; Object.assign(form, { name: skill.name, description: skill.description, content: skill.content, tags: [...skill.tags], enabled: skill.enabled }); editorOpen.value = true; }
function yamlQuote(value: string) { return JSON.stringify(value || '请填写 Skill 说明'); }
function generateTemplate() { const name = /^[a-z][a-z0-9_-]{0,63}$/.test(form.name) ? form.name : 'my-skill'; form.content = `---\nname: ${name}\ndescription: ${yamlQuote(form.description)}\nversion: "1.0.0"\nmetadata:\n  author: 水利智能体平台\n---\n# ${form.description || 'Skill 使用说明'}\n\n说明触发条件、输入、处理步骤和输出要求。\n`; }
function validateForm() { Object.assign(formErrors, { name: '', description: '', content: '' }); if (!/^[a-z][a-z0-9_-]{0,63}$/.test(form.name)) formErrors.name = '请输入有效的 Skill 名称'; if (!form.description.trim()) formErrors.description = '请输入说明'; if (!form.content.startsWith('---') || !form.content.includes(`name: ${form.name}`)) formErrors.content = 'SKILL.md 需要有效 frontmatter，且 name 必须与 Skill 名称一致'; return !Object.values(formErrors).some(Boolean); }
async function saveSkill() { if (!validateForm()) return; saving.value = true; try { const payload = { description: form.description, content: form.content, tags: form.tags, enabled: form.enabled }; if (editingName.value) await skillsApi.update(editingName.value, payload); else await skillsApi.create({ name: form.name, ...payload }); message.success(editingName.value ? 'Skill 已更新' : 'Skill 已添加'); editorOpen.value = false; await loadSkills(); } catch (error) { message.error(error instanceof Error ? error.message : '保存失败'); } finally { saving.value = false; } }
async function toggleSkill(skill: SkillInfo) { busyName.value = skill.name; try { await skillsApi.toggle(skill.name); await loadSkills(); } catch (error) { message.error(error instanceof Error ? error.message : '切换状态失败'); } finally { busyName.value = ''; } }
async function deleteSkill(skill: SkillInfo) { try { await skillsApi.remove(skill.name); message.success('Skill 已删除'); await loadSkills(); } catch (error) { message.error(error instanceof Error ? error.message : '删除失败'); } }
function openImport() { importFile.value = undefined; uploadList.value = []; conflictStrategy.value = 'rename'; importOpen.value = true; }
function captureFile(file: File) { importFile.value = file; uploadList.value = [{ uid: file.name, name: file.name, status: 'done', originFileObj: file } as UploadFile]; return false; }
function removeFile() { importFile.value = undefined; uploadList.value = []; return true; }
async function importSkills() { if (!importFile.value) return; importing.value = true; try { const result = await skillsApi.importZip(importFile.value, conflictStrategy.value); message.success(result.count ? `已导入 ${result.count} 个 Skill` : `未导入新 Skill，跳过 ${result.skipped.length} 个`); importOpen.value = false; await loadSkills(); } catch (error) { message.error(error instanceof Error ? error.message : '导入失败'); } finally { importing.value = false; } }
function formatTime(value: string) { return new Date(value).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }); }
onMounted(loadSkills);
</script>

<style scoped>
.skill-page { display: grid; gap: 16px; }
.skill-heading { display: flex; align-items: center; justify-content: space-between; gap: 20px; padding: 18px 20px; color: #172033; background: #fff; border: 1px solid #e1e9f2; border-left: 4px solid #17856b; border-radius: 8px; }
.heading-label { margin-bottom: 4px; color: #17856b; font: 700 10px Consolas, monospace; letter-spacing: 0; }
.skill-heading h2 { margin: 0; font-size: 20px; } .skill-heading p { margin: 5px 0 0; color: #667085; } .search-input { width: 250px; } .status-filter { width: 126px; }
.summary-strip { display: grid; grid-template-columns: repeat(4, 1fr); background: #fff; border: 1px solid #e1e9f2; border-radius: 8px; }
.summary-strip div { display: flex; align-items: baseline; justify-content: space-between; padding: 14px 20px; border-right: 1px solid #e8eef5; } .summary-strip div:last-child { border-right: 0; } .summary-strip span { color: #667085; } .summary-strip strong { color: #156b59; font-size: 23px; }
.tag-rail { display: flex; gap: 7px; overflow-x: auto; } .tag-rail button { padding: 5px 11px; color: #596579; white-space: nowrap; background: #fff; border: 1px solid #dce5ee; border-radius: 5px; cursor: pointer; } .tag-rail button.active { color: #fff; background: #237a68; border-color: #237a68; }
.skill-list { display: grid; gap: 10px; } .skill-item { display: grid; grid-template-columns: 44px minmax(280px, 1fr) 75px 130px auto; align-items: center; gap: 16px; min-height: 112px; padding: 16px 18px; background: #fff; border: 1px solid #dfe8f1; border-radius: 8px; transition: border-color .2s, box-shadow .2s; } .skill-item:hover { border-color: #9bc6bb; box-shadow: 0 5px 16px rgb(24 100 83 / 7%); }
.skill-glyph { display: grid; width: 42px; height: 42px; place-items: center; color: #156b59; background: #e7f5f0; border-radius: 6px; font-size: 20px; } .skill-title { display: flex; align-items: center; gap: 7px; } .skill-title strong { font: 700 15px Consolas, "Microsoft YaHei", monospace; } .skill-main p { margin: 7px 0; color: #596579; font-size: 13px; } .skill-tags span { color: #98a2b3; font-size: 12px; }
.file-count, .updated { display: flex; flex-direction: column; gap: 4px; } .file-count strong { color: #156b59; font-size: 18px; } .file-count span, .updated span { color: #7b8798; font-size: 12px; } .updated strong { font-size: 13px; } .item-actions { display: flex; align-items: center; gap: 8px; }
.editor-grid { display: grid; grid-template-columns: 280px 1fr; gap: 20px; } .editor-label { display: flex; align-items: center; justify-content: space-between; margin-bottom: 8px; font-weight: 700; } .manifest-editor textarea { font-family: Consolas, "SFMono-Regular", monospace; line-height: 1.55; } .content-error { margin-top: 5px; color: #ff4d4f; font-size: 12px; }
.import-layout { display: grid; gap: 18px; } .import-rules { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; } .import-rules > div { display: grid; grid-template-columns: 24px 1fr; gap: 8px; padding: 12px; color: #526071; background: #f6f9fb; border: 1px solid #e1e9f2; border-radius: 6px; } .import-rules strong { display: block; margin-bottom: 3px; color: #172033; }
@media (max-width: 1100px) { .skill-heading { align-items: flex-start; flex-direction: column; } .skill-item { grid-template-columns: 44px 1fr auto; } .file-count, .updated { display: none; } }
@media (max-width: 700px) { .skill-heading :deep(.ant-space) { width: 100%; } .search-input { width: 100%; } .summary-strip { grid-template-columns: 1fr 1fr; } .summary-strip div:nth-child(2) { border-right: 0; } .summary-strip div:nth-child(-n+2) { border-bottom: 1px solid #e8eef5; } .skill-item { grid-template-columns: 40px 1fr; } .item-actions { grid-column: 1 / -1; justify-content: flex-end; } .editor-grid, .import-rules { grid-template-columns: 1fr; } }
</style>
