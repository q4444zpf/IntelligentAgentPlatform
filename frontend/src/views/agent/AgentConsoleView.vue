<template>
  <a-card class="section-card" :body-style="{ padding: 0 }">
    <div class="chat-panel">
      <div class="chat-list">
        <a-typography-title :level="5">可用智能体</a-typography-title>
        <a-list :data-source="agents">
          <template #renderItem="{ item }">
            <a-list-item>
              <a-list-item-meta :title="item.name" :description="item.desc" />
              <a-tag :color="item.color">{{ item.scope }}</a-tag>
            </a-list-item>
          </template>
        </a-list>
        <a-divider />
        <a-typography-title :level="5">可用工具</a-typography-title>
        <a-list :data-source="tools" size="small">
          <template #renderItem="{ item }">
            <a-list-item>
              <a-list-item-meta :title="item.name" :description="item.desc" />
              <a-tag :color="item.color">{{ item.type }}</a-tag>
            </a-list-item>
          </template>
        </a-list>
      </div>
      <div class="chat-room">
        <div class="message agent">
          我是通用智能体平台的演示助手。当前可结合知识库、Skill、MCP、流程编排和多智能体协同完成任务，并对所有工具调用进行审计。
        </div>
        <a-alert
          type="success"
          show-icon
          message="当前对话可调用范围"
          description="普通用户可调用公用智能体、公用 Skill/MCP，以及自己维护的个人智能体、Skill、MCP。管理员还可调用和管理公共资源。"
        />
        <div class="message user">请帮我检查一个个人 MCP 是否可以发布到公用资源库。</div>
        <div class="message agent">
          可以。发布前需要完成连接测试、同步工具 Schema、检查环境变量是否加密、确认网络白名单，并为高风险工具配置人工确认。通过后可提交发布审核。
        </div>
        <a-alert
          type="info"
          show-icon
          message="工具调用将进入沙箱"
          description="Web 版智能体调用 MCP、Skill、脚本和文件处理能力时，不直接访问服务器宿主环境。"
        />
        <a-divider />
        <a-space direction="vertical" style="width: 100%">
          <a-textarea :rows="4" placeholder="输入问题，例如：创建一个文档问答智能体并绑定知识库" />
          <a-space>
            <a-button type="primary">发送</a-button>
            <a-button>调用 Skill</a-button>
            <a-button>触发流程</a-button>
            <a-button>多智能体协同</a-button>
          </a-space>
        </a-space>
      </div>
    </div>
  </a-card>
</template>

<script setup lang="ts">
const agents = [
  { name: '知识库问答智能体', desc: '文档检索、引用溯源、多轮问答', scope: '公用', color: 'green' },
  { name: '工具调用智能体', desc: '参数补全、Tool 调用、结果解释', scope: '个人', color: 'blue' },
  { name: '流程执行智能体', desc: '触发流程、处理人工确认节点', scope: '系统', color: 'purple' },
];

const tools = [
  { name: '文档处理 MCP', desc: '解析 Word、PDF、Excel，运行于沙箱', type: '公用 MCP', color: 'purple' },
  { name: '审批摘要 Skill', desc: '根据工具结果生成审批摘要', type: '个人 Skill', color: 'blue' },
  { name: 'HTTP API Tool 调用器', desc: '标准 API 调用、Schema 校验和审计', type: '公用 Skill', color: 'green' },
];
</script>
