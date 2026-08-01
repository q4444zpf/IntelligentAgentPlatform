<template>
  <div class="topology-catalog">
    <section class="filter-bar">
      <label><span>拓扑名称：</span><a-input v-model:value="draftSearch" allow-clear placeholder="请输入拓扑名称" /></label>
      <label><span>拓扑类型：</span><a-select v-model:value="draftType" :options="typeFilterOptions" /></label>
      <a-button type="primary" @click="applyFilters">查询</a-button>
      <a-button @click="resetFilters">重置</a-button>
    </section>

    <div class="create-row">
      <a-button type="primary" @click="openForm()"><PlusOutlined /> 创建拓扑</a-button>
    </div>

    <section class="topology-grid">
      <article v-for="item in pagedItems" :key="item.id" class="topology-card">
        <h2>{{ item.name }}</h2>
        <dl>
          <div><dt>拓扑类型</dt><dd>{{ item.type }}</dd></div>
          <div><dt>版本号</dt><dd><a-select v-model:value="item.version" :options="item.versions.map((version) => ({ value: version, label: version }))" /></dd></div>
          <div><dt>对象数量</dt><dd>{{ item.objectCount }} 个对象 / {{ item.relationCount }} 条关系</dd></div>
          <div><dt>备注</dt><dd>{{ item.remark || '-' }}</dd></div>
        </dl>
        <footer>
          <button @click="openForm(item)"><EditOutlined /> 编辑</button>
          <a-dropdown trigger="click">
            <button>操作 <DownOutlined /></button>
            <template #overlay>
              <a-menu>
                <a-menu-item @click="openDetail(item)"><EyeOutlined /> 查看详情</a-menu-item>
                <a-menu-item @click="duplicateItem(item)"><CopyOutlined /> 复制拓扑</a-menu-item>
                <a-menu-divider />
                <a-menu-item danger @click="requestDelete(item)"><DeleteOutlined /> 删除</a-menu-item>
              </a-menu>
            </template>
          </a-dropdown>
        </footer>
      </article>
      <a-empty v-if="!pagedItems.length" description="没有匹配的拓扑数据" />
    </section>

    <div class="pagination-row">
      <span>共 {{ filteredItems.length }} 条</span>
      <a-pagination v-model:current="currentPage" :total="filteredItems.length" :page-size="pageSize" :show-size-changer="false" show-less-items />
      <a-select v-model:value="pageSize" :options="pageSizeOptions" />
    </div>

    <a-modal v-model:open="formOpen" :title="editingId ? '编辑拓扑' : '创建拓扑'" ok-text="保存" cancel-text="取消" :ok-button-props="{ disabled: !form.name.trim() || !form.code.trim() }" @ok="saveItem">
      <a-form layout="vertical" class="topology-form">
        <div class="form-grid">
          <a-form-item label="拓扑名称" required><a-input v-model:value="form.name" placeholder="请输入拓扑名称" /></a-form-item>
          <a-form-item label="拓扑编码" required><a-input v-model:value="form.code" placeholder="例如 TP-BEIJ-001" /></a-form-item>
          <a-form-item label="拓扑类型" required><a-select v-model:value="form.type" :options="topologyTypeOptions" /></a-form-item>
          <a-form-item label="初始版本"><a-input v-model:value="form.version" placeholder="例如 v1.0.0" /></a-form-item>
        </div>
        <a-form-item label="所属流域"><a-input v-model:value="form.basin" placeholder="例如 北江流域" /></a-form-item>
        <a-form-item label="备注"><a-textarea v-model:value="form.remark" :rows="3" placeholder="填写数据范围、用途或维护说明" /></a-form-item>
      </a-form>
    </a-modal>

    <a-modal v-model:open="deleteOpen" title="删除拓扑" ok-text="删除" cancel-text="取消" ok-type="danger" @ok="deleteItem">
      <a-alert type="warning" show-icon :message="`确定删除“${pendingDelete?.name || ''}”吗？`" description="删除后，该拓扑的数据对象、连接关系和版本配置将无法继续使用。" />
    </a-modal>

    <a-drawer v-model:open="detailOpen" title="拓扑详情" width="480">
      <template v-if="detailItem">
        <div class="detail-title"><span><ApartmentOutlined /></span><div><h3>{{ detailItem.name }}</h3><code>{{ detailItem.code }}</code></div></div>
        <a-descriptions bordered :column="1" size="small">
          <a-descriptions-item label="拓扑类型">{{ detailItem.type }}</a-descriptions-item>
          <a-descriptions-item label="当前版本">{{ detailItem.version }}</a-descriptions-item>
          <a-descriptions-item label="所属流域">{{ detailItem.basin }}</a-descriptions-item>
          <a-descriptions-item label="拓扑对象">{{ detailItem.objectCount }} 个</a-descriptions-item>
          <a-descriptions-item label="连接关系">{{ detailItem.relationCount }} 条</a-descriptions-item>
          <a-descriptions-item label="更新时间">{{ detailItem.updatedAt }}</a-descriptions-item>
          <a-descriptions-item label="备注">{{ detailItem.remark || '-' }}</a-descriptions-item>
        </a-descriptions>
        <a-button class="detail-button" type="primary" block>进入拓扑工作区</a-button>
      </template>
    </a-drawer>
  </div>
</template>

<script setup lang="ts">
import { ApartmentOutlined, CopyOutlined, DeleteOutlined, DownOutlined, EditOutlined, EyeOutlined, PlusOutlined } from '@ant-design/icons-vue';
import { computed, ref, watch } from 'vue';

interface TopologyDataset { id: number; name: string; code: string; type: string; version: string; versions: string[]; basin: string; objectCount: number; relationCount: number; remark: string; updatedAt: string }

const topologyTypeOptions = [{ value: '流域河网拓扑', label: '流域河网拓扑' }, { value: '水库工程拓扑', label: '水库工程拓扑' }, { value: '城市排水拓扑', label: '城市排水拓扑' }, { value: '灌区工程拓扑', label: '灌区工程拓扑' }];
const typeFilterOptions = [{ value: 'all', label: '请选择拓扑类型' }, ...topologyTypeOptions];
const pageSizeOptions = [{ value: 8, label: '8 条/页' }, { value: 12, label: '12 条/页' }, { value: 16, label: '16 条/页' }];
const items = ref<TopologyDataset[]>([
  { id: 1, name: '北江流域防洪调度拓扑', code: 'TP-BEIJ-001', type: '流域河网拓扑', version: 'v1.3.2', versions: ['v1.3.2', 'v1.2.0', 'v1.0.0'], basin: '北江流域', objectCount: 223, relationCount: 286, remark: '飞来峡至清远河段', updatedAt: '2026-07-24 10:12' },
  { id: 2, name: '飞来峡水库工程拓扑', code: 'TP-FLX-002', type: '水库工程拓扑', version: 'v2.1.0', versions: ['v2.1.0', 'v2.0.0', 'v1.5.3'], basin: '北江流域', objectCount: 46, relationCount: 61, remark: '水库、闸门及监测设施', updatedAt: '2026-07-23 16:40' },
  { id: 3, name: '清远城市排水拓扑', code: 'TP-QY-003', type: '城市排水拓扑', version: 'v1.0.0', versions: ['v1.0.0'], basin: '清远城区', objectCount: 318, relationCount: 402, remark: '中心城区排水管网与泵站', updatedAt: '2026-07-22 09:18' },
  { id: 4, name: '英德灌区工程拓扑', code: 'TP-YD-004', type: '灌区工程拓扑', version: 'v1.2.0', versions: ['v1.2.0', 'v1.1.0'], basin: '英德灌区', objectCount: 157, relationCount: 190, remark: '干渠、支渠、闸门和泵站', updatedAt: '2026-07-21 14:05' },
  { id: 5, name: '潖江支流水系拓扑', code: 'TP-PJ-005', type: '流域河网拓扑', version: 'v1.1.0', versions: ['v1.1.0', 'v1.0.0'], basin: '潖江流域', objectCount: 86, relationCount: 104, remark: '支流汇入口及控制断面', updatedAt: '2026-07-20 11:32' },
  { id: 6, name: '大燕河水库群拓扑', code: 'TP-DYH-006', type: '水库工程拓扑', version: 'v1.0.0', versions: ['v1.0.0'], basin: '大燕河流域', objectCount: 74, relationCount: 92, remark: '水库群联合调度', updatedAt: '2026-07-19 17:20' },
]);

const draftSearch = ref(''); const draftType = ref('all'); const search = ref(''); const typeFilter = ref('all');
const currentPage = ref(1); const pageSize = ref(12); const formOpen = ref(false); const editingId = ref<number | null>(null);
const deleteOpen = ref(false); const pendingDelete = ref<TopologyDataset | null>(null); const detailOpen = ref(false); const detailItem = ref<TopologyDataset | null>(null);
const form = ref({ name: '', code: '', type: '流域河网拓扑', version: 'v1.0.0', basin: '', remark: '' });
const filteredItems = computed(() => items.value.filter((item) => (!search.value || `${item.name}${item.code}`.toLowerCase().includes(search.value.toLowerCase())) && (typeFilter.value === 'all' || item.type === typeFilter.value)));
const pagedItems = computed(() => filteredItems.value.slice((currentPage.value - 1) * pageSize.value, currentPage.value * pageSize.value));
watch(pageSize, () => { currentPage.value = 1; });
function applyFilters() { search.value = draftSearch.value.trim(); typeFilter.value = draftType.value; currentPage.value = 1; }
function resetFilters() { draftSearch.value = ''; draftType.value = 'all'; applyFilters(); }
function openForm(item?: TopologyDataset) { editingId.value = item?.id || null; form.value = item ? { name: item.name, code: item.code, type: item.type, version: item.version, basin: item.basin, remark: item.remark } : { name: '', code: '', type: '流域河网拓扑', version: 'v1.0.0', basin: '', remark: '' }; formOpen.value = true; }
function saveItem() { if (editingId.value) { const item = items.value.find((entry) => entry.id === editingId.value)!; Object.assign(item, form.value, { versions: item.versions.includes(form.value.version) ? item.versions : [form.value.version, ...item.versions], updatedAt: '刚刚' }); } else items.value.unshift({ id: Date.now(), ...form.value, versions: [form.value.version], objectCount: 0, relationCount: 0, updatedAt: '刚刚' }); formOpen.value = false; applyFilters(); }
function duplicateItem(item: TopologyDataset) { items.value.unshift({ ...item, id: Date.now(), name: `${item.name}-副本`, code: `${item.code}-COPY`, updatedAt: '刚刚' }); currentPage.value = 1; }
function requestDelete(item: TopologyDataset) { pendingDelete.value = item; deleteOpen.value = true; }
function deleteItem() { if (pendingDelete.value) items.value = items.value.filter((item) => item.id !== pendingDelete.value?.id); deleteOpen.value = false; pendingDelete.value = null; }
function openDetail(item: TopologyDataset) { detailItem.value = item; detailOpen.value = true; }
</script>

<style scoped>
.topology-catalog { min-height: calc(100dvh - 103px); padding-bottom: 20px; }.filter-bar { display: flex; min-height: 58px; align-items: center; gap: 12px; padding: 10px 14px; background: #fff; border: 1px solid #e1e8ee; border-radius: 6px; }.filter-bar label { display: flex; align-items: center; gap: 8px; font-weight: 600; white-space: nowrap; }.filter-bar .ant-input { width: 220px; }.filter-bar .ant-select { width: 180px; }.create-row { padding: 12px 0; }.topology-grid { display: grid; grid-template-columns: repeat(4,minmax(260px,1fr)); gap: 14px; }.topology-card { display: flex; min-width: 0; min-height: 258px; flex-direction: column; overflow: hidden; background: #fff; border: 1px solid #e4e9ee; border-radius: 6px; }.topology-card h2 { margin: 0; padding: 18px 20px 14px; overflow: hidden; font-size: 16px; line-height: 24px; text-overflow: ellipsis; white-space: nowrap; }.topology-card dl { margin: 0 20px 20px; overflow: hidden; border: 1px solid #e6ebef; border-radius: 5px; }.topology-card dl div { display: grid; min-height: 40px; grid-template-columns: 38% 62%; border-bottom: 1px solid #e6ebef; }.topology-card dl div:last-child { border-bottom: 0; }.topology-card dt,.topology-card dd { display: flex; align-items: center; padding: 7px 12px; }.topology-card dt { font-weight: 600; background: #fafafa; border-right: 1px solid #e6ebef; }.topology-card dd { min-width: 0; margin: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }.topology-card dd .ant-select { width: 100%; }.topology-card footer { display: grid; height: 44px; grid-template-columns: 1fr 1fr; margin-top: auto; border-top: 1px solid #edf0f2; }.topology-card footer > button,.topology-card footer .ant-dropdown-trigger { color: #1677ff; background: transparent; border: 0; cursor: pointer; }.topology-card footer > * + * { border-left: 1px solid #edf0f2 !important; }.pagination-row { display: flex; align-items: center; justify-content: flex-end; gap: 12px; padding: 14px 0; }.pagination-row > .ant-select { width: 104px; }.topology-form { margin-top: 14px; }.form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 0 14px; }.detail-title { display: flex; align-items: center; gap: 12px; margin-bottom: 20px; }.detail-title > span { display: grid; width: 44px; height: 44px; place-items: center; color: #fff; background: #2563eb; border-radius: 6px; font-size: 20px; }.detail-title h3 { margin: 0; }.detail-title code { color: #718695; }.detail-button { margin-top: 18px; }
@media (max-width: 1500px) { .topology-grid { grid-template-columns: repeat(3,minmax(260px,1fr)); } }
@media (max-width: 1100px) { .topology-grid { grid-template-columns: repeat(2,minmax(260px,1fr)); }.filter-bar { align-items: flex-start; flex-wrap: wrap; } }
@media (max-width: 680px) { .filter-bar { display: grid; grid-template-columns: 1fr 1fr; }.filter-bar label { grid-column: 1 / -1; }.filter-bar .ant-input,.filter-bar .ant-select { width: 100%; }.filter-bar label span { min-width: 80px; }.topology-grid { grid-template-columns: 1fr; }.form-grid { grid-template-columns: 1fr; }.pagination-row { justify-content: space-between; flex-wrap: wrap; } }
</style>
