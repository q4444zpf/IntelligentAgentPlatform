from .schemas import ModelInfo, ProviderInfo


def model(model_id: str, name: str, model_type: str = "文本", **kwargs) -> ModelInfo:
    return ModelInfo(id=model_id, name=name, type=model_type, builtin=True, **kwargs)


def builtin_providers() -> dict[str, ProviderInfo]:
    providers = [
        ProviderInfo(id="deepseek", name="DeepSeek", base_url="https://api.deepseek.com/v1", api_key_prefixes=["sk-"], models=[model("deepseek-chat", "DeepSeek Chat"), model("deepseek-reasoner", "DeepSeek Reasoner", "推理", max_tokens=32768, context_window=131072), model("deepseek-v4-flash", "DeepSeek V4 Flash"), model("deepseek-v4-pro", "DeepSeek V4 Pro")]),
        ProviderInfo(id="dashscope", name="Aliyun", base_url="https://dashscope.aliyuncs.com/compatible-mode/v1", api_key_prefixes=["sk-"], provider_group="aliyun", provider_variant="DashScope", models=[model("qwen-max", "Qwen Max"), model("qwen-plus", "Qwen Plus"), model("text-embedding-v3", "通用文本向量 v3", "Embedding")]),
        ProviderInfo(id="zhipu", name="智谱 AI", base_url="https://open.bigmodel.cn/api/paas/v4", is_free_tier=True, models=[model("glm-4.7-flash", "GLM-4.7 Flash"), model("glm-5", "GLM-5")]),
        ProviderInfo(id="volcengine", name="Volcano Engine", base_url="https://ark.cn-beijing.volces.com/api/v3", freeze_url=True, support_model_discovery=False, models=[model("doubao-seed-2.0-pro", "Doubao Seed 2.0 Pro", "多模态", supports_image=True, supports_video=True, supports_multimodal=True, probe_source="documentation")]),
        ProviderInfo(id="moonshot", name="Kimi", base_url="https://api.moonshot.cn/v1", freeze_url=True, models=[model("kimi-k2.5", "Kimi K2.5", "多模态", supports_image=True, supports_video=True, supports_multimodal=True, probe_source="documentation")]),
        ProviderInfo(id="gemini", name="Google Gemini", base_url="https://generativelanguage.googleapis.com", protocol="GeminiChatModel", freeze_url=True, support_connection_check=False, support_model_discovery=False, is_free_tier=True, models=[model("gemini-2.5-pro", "Gemini 2.5 Pro", "多模态", supports_image=True, supports_video=True, supports_multimodal=True, probe_source="documentation"), model("gemini-2.5-flash", "Gemini 2.5 Flash", "多模态", supports_image=True, supports_video=True, supports_multimodal=True, probe_source="documentation")]),
        ProviderInfo(id="minimax", name="MiniMax", base_url="https://api.minimaxi.com/anthropic", protocol="AnthropicChatModel", freeze_url=True, support_connection_check=False, support_model_discovery=False, models=[model("MiniMax-M2.5", "MiniMax M2.5"), model("MiniMax-M2.7", "MiniMax M2.7")]),
        ProviderInfo(id="mimo-tokenplan", name="Xiaomi MiMo Token Plan", base_url="https://token-plan-cn.xiaomimimo.com/v1", freeze_url=True, models=[model("mimo-v2.5-pro", "MiMo V2.5 Pro"), model("mimo-v2.5", "MiMo V2.5", "多模态", supports_image=True, supports_video=True, supports_multimodal=True, probe_source="documentation")]),
        ProviderInfo(id="modelscope", name="ModelScope", base_url="https://api-inference.modelscope.cn/v1", api_key_prefixes=["ms"], freeze_url=True, models=[model("Qwen/Qwen3.5-122B-A10B", "Qwen3.5-122B-A10B", "多模态", supports_image=True, supports_video=True, supports_multimodal=True, probe_source="documentation"), model("ZhipuAI/GLM-5", "GLM-5")]),
        ProviderInfo(id="siliconflow", name="SiliconFlow", base_url="https://api.siliconflow.cn/v1", api_key_prefixes=["sk-"], freeze_url=True, is_free_tier=True, models=[]),
        ProviderInfo(id="azure-openai", name="Azure OpenAI", base_url="", support_model_discovery=False, models=[model("gpt-4.1", "GPT-4.1", "多模态", supports_image=True, supports_multimodal=True, probe_source="documentation"), model("gpt-4o", "GPT-4o", "多模态", supports_image=True, supports_multimodal=True, probe_source="documentation")]),
        ProviderInfo(id="openrouter", name="OpenRouter", base_url="https://openrouter.ai/api/v1", is_free_tier=True),
        ProviderInfo(id="openai", name="OpenAI", base_url="https://api.openai.com/v1", api_key_prefixes=["sk-"], models=[model("gpt-4.1", "GPT-4.1", "多模态"), model("text-embedding-3-large", "Embedding 3 Large", "Embedding")]),
        ProviderInfo(id="anthropic", name="Anthropic", base_url="https://api.anthropic.com", api_key_prefixes=["sk-ant-"], protocol="AnthropicChatModel", support_model_discovery=False, models=[model("claude-sonnet-4", "Claude Sonnet 4", "多模态")]),
        ProviderInfo(id="ollama", name="Ollama", kind="local", base_url="http://127.0.0.1:11434/v1", require_api_key=False, models=[]),
        ProviderInfo(id="vllm", name="vLLM 推理服务", kind="local", base_url="http://127.0.0.1:8001/v1", require_api_key=False, models=[]),
    ]
    return {provider.id: provider for provider in providers}
