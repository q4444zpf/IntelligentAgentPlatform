# Runtime TLS Deadline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复已复现的四项运行时绝对截止时间失败，保持 HTTPS 验证和失败上报能力。

**Architecture:** 在现有 Skill 分支中单独提交运行时修复。三个 HTTPX 客户端共享一个异步 TLS 上下文创建函数；只将本地 CA 读取和解析交给可取消等待的 AnyIO 工作线程，不在后台发送请求。每次顶层操作创建独立 SSLContext；Workflow 的健康检查和提交在同一次 asyncio.run 中使用一个上下文和一个客户端，继续在同一绝对截止时间内初始化、请求和关闭资源。

**Tech Stack:** Python 3.12、asyncio、现有 HTTPX/AnyIO/certifi、ssl、pytest、cryptography 测试证书。

## 当前执行状态

最新进度：用户批准的单次 Workflow 异步操作及最终审查修正已完成。最终源码 `e500c0e` 完整后端复核 1286 通过、63 跳过、2 条既有警告；真实集成 10 项通过，pip check 通过，整分支审查及定向复审通过。源码已本地快进合入 main，相同提交树在 main 目录受影响模块复核 286 项通过。此前一次既有 watchdog 总耗时 0.453 秒超过 0.450 秒断言的失败仍保留为计时敏感性风险，未放宽阈值或扩大生产修复。详细结果见 [验收记录](2026-09-08-skill-foundation-verification.md)。以下暂停记录均为历史；本轮未推送。

2026-09-08：Task 1 尚未验收、未提交。首版异步 TLS 改造的新测试及原四项测试曾通过，五文件聚焦回归曾为 117 通过、1 失败，随后一次为 118 通过；这些结果不能证明稳定性。追加不可变 PEM 缓存后，冷进程 Workflow 探测仍有 1/5 未观察到 POST；再追加 AnyIO asyncio 后端预加载后，与控制端全量测试并行的探测有 3/10 未观察到 POST。该并行结果存在负载干扰，不计作最终验收。

冻结代码、停止全量测试后，10 次独立进程分段测量均观察到 POST，请求到达服务端耗时约 108.3–178.2 毫秒，共享截止预算为 200 毫秒。该数据表明剩余预算较小，但不足以证明所有负载下都能在截止前发出请求。当前工作区保留异步 TLS、PEM 缓存及 AnyIO 预加载候选代码，尚未审查或提交；不放宽原测试阈值。

按系统化调试的三轮调整停止规则，暂停第四轮实现，需确认是否扩展为客户端初始化/就绪生命周期设计，将可预先完成的初始化移出业务请求关键路径，同时保留每次独立验证上下文和请求绝对截止时间。该扩展尚未实现，不能据此声称已修复。

进一步审视后，优先建议将 Workflow 的健康检查与提交作为一次顶层异步操作，在该操作内使用一个事件循环、一个独立 TLS 上下文和一个 HTTPX 客户端，避免重复初始化；不同顶层操作之间仍不共享可变 SSLContext。保留注入传输兼容、原绝对截止时间、TLS 验证及超时后不发 POST 的行为。该方案改变了原计划按请求分别创建上下文的边界，需用户确认后再设计实现与测试；它只减少初始化开销，不承诺任意系统负载下均能发出请求。

控制端完整回归主动中止（退出码 1），不作为验收证据。独立真实 PostgreSQL、MinIO 和迁移检查已再次完成：10 通过、0 跳过、1 条既有 Starlette/AnyIO 弃用警告，5.40 秒。main 保持原状态，未合并、未推送；专用测试库及未提交文档保留。

## Global Constraints

- 用户已确认按先修复四项运行时失败、再回归和合并的方案执行。
- 保持 TLS CERT_REQUIRED、主机名校验及与 HTTPX trust_env=False 相同的 certifi 信任根；禁止 verify=False，禁止把私有测试 CA 或凭据提交到仓库。
- 不放宽原四项耗时断言，不延长业务截止时间，不改执行快照、鉴权或业务重试策略。
- 慢 TLS 初始化发生超时后不得发起网络请求；asyncio.run 返回不得等待被取消的默认 executor 工作结束。
- 不在不同顶层操作之间共享可变 SSLContext，不做模块导入时的证书 I/O；Workflow 单次健康检查及提交可共用一个上下文/客户端。环境代理和 SSL_CERT_FILE/SSL_CERT_DIR 继续不参与这些内部客户端的连接策略。
- 所有请求保留现有上下文管理器关闭机制及错误映射。后台工作仅构造无网络资源的 SSLContext。
- 复用已有工作区 .worktrees/skill-version-foundation，当前代码基线 a33e49f；保留四份既有未提交文档和主目录其他用户文件。
- 本轮已成功 fetch；merge origin/main 返回 Already up to date。任何新增提交前再次确认远端协调，不强推或绕过 TLS 校验。
- 数据验证仅使用独立测试库与临时 MinIO 桶，不修改业务数据。知识库、Embedding、Skill API 和界面仍不切换。

## 诊断与方案选择

2026-09-08 原四项测试在 a33e49f 再次全部失败（21.16 秒）；本次 sandbox 完成上报还出现被初始化耗时耗尽的情况。

同一 certifi PEM、同一信任根的测量：文件读取 0.025 秒，ssl.create_default_context(cafile=...) 0.350/0.319 秒，ssl.create_default_context(cadata=...) 0.027/0.026 秒，两者均加载 121 个 CA。将相同 PEM 通过内存传入降低 Windows 文件加载开销，但单独优化无法保证慢 I/O 时的截止时间。因此配合 AnyIO abandon_on_cancel 工作线程；独立实验中 1 秒工作在 asyncio.timeout(0.05) 下取消，asyncio.run 约 0.087 秒返回。

不采用全局共享 SSLContext：容易造成配置污染，且冷启动仍会超时。不采用 asyncio.to_thread：asyncio.run 会等待默认 executor 关闭，可能重新引入取消后等待。选用已有 AnyIO 工作线程的可放弃等待语义，并仅在其中处理无网络副作用的证书构造。

### Task 1: 可取消 TLS 初始化及三个运行时客户端接入

**Files:**
- Create `backend/app/runtime/http_tls.py`：独立验证上下文与可取消异步创建。
- Modify `backend/app/runtime/launcher_client.py`：仅接入异步请求分支。
- Modify `backend/app/runtime/workflow_runner.py`：接入异步请求分支，并将真实 HTTP transport 的顶层健康检查及提交合并为一次异步操作；其他自定义传输保持兼容。
- Modify `backend/app/runtime/runner_gateway_client.py`：仅接入真实异步请求分支，不改变注入传输分支。
- Create `backend/tests/runtime/test_http_tls.py`：根集合、配置隔离、初始化失败、超时取消和本地 TLS 验证。
- Modify the existing runtime test files only to add narrowly scoped slow-initialization/cleanup cases; do not edit original slow-drip assertions.
- Modify `backend/requirements.txt` only to declare the newly direct imports of existing transitive dependencies: `anyio>=4.1.0,<5`, `certifi>=2025.1.31`.

**Interfaces:** `async def create_runtime_ssl_context() -> ssl.SSLContext`; local certificate initialization failures become sanitized `httpx.ConnectError`, cancellation and TimeoutError propagate normally.

#### 用户批准的 Workflow 修订（本节替代旧版按 GET/POST 分别初始化的要求）

- 保持 `WorkflowRunnerClient.submit` 的公开参数、返回字典、RunnerUnavailableError / RunnerDeadlineExceededError 语义、快照/鉴权/截止时间字段不变。
- 为真实 `WorkflowRunnerHttpTransport` 添加窄范围组合提交能力，建议 `submit_when_healthy(payload, *, monotonic_deadline=None) -> dict[str, Any]`。Client 仅对真实、未注入 request 的 HTTP transport 使用该能力；自定义 RunnerTransport 和注入 request 仍调用原 health_check 再 submit，不要求外部实现新方法。不绕过已有自定义/覆盖的 health_check 或 submit 行为。
- 组合操作恰好调用一次 asyncio.run、一次 create_runtime_ssl_context、一次 AsyncClient。用同一客户端先 GET /health，健康字段满足现有条件后再 POST /runs。两阶段不可并发。顶层操作结束后客户端关闭，下次调用不复用上下文或客户端。
- 调用者提供 deadline 时全过程使用原单一绝对 deadline；不提供时保留每个请求默认 10 秒的既有语义，不能将健康检查及提交原来的默认阶段预算无意缩短为共享 10 秒。默认预算处理仍在同一客户端中完成。
- 显式 deadline 的 X-Request-Deadline-At 与当前剩余时间对应，两个请求不延长同一绝对截止时间。每次网络请求前重新检查剩余时间和设置 HTTPX phase timeout。初始化、health 及 submit 均受取消控制；health 结束已经过期时不发送 POST。
- health 不健康、HTTP 错误、非法 JSON/非对象或连接失败均不得 POST；保留既有 504 和实际截止时间耗尽的错误分类。客户端在成功、拒绝、异常和取消路径关闭。
- 去掉上一轮未证明必要的 PEM 缓存和 AnyIO 模块预加载，回到无缓存的可取消 certifi cadata 构造；新的生命周期合并是本轮唯一设计调整。若仍有性能失败，先给出阶段证据，不自行叠加缓存或放宽测试。

新增生命周期红测试可在本地真实 HTTP server 上包裹真实 TLS helper，收集事件循环及上下文身份；服务器观察两个路径和提交正文。测试入口必须调用公开 Client.submit，而不是只测试新私有方法。核心断言示例：

```python
assert observed_paths == ["/health", "/runs"]
assert result == {"status": "accepted"}
assert len(created_contexts) == 1
assert len({id(loop) for loop in observed_loops}) == 1
assert len(created_clients) == 1
assert created_clients[0].is_closed
```

包裹对象必须委托真实实现，不使用计数桩代替真实网络行为。补充连续两次 submit 的上下文隔离、已过期/初始化取消/health 消耗预算时无 POST、失败关闭客户端、504 映射、无 deadline 的默认阶段预算以及注入传输兼容测试。关键新测试须先看到因旧版重复初始化导致的行为 RED。

- [x] **Step 1: Write behavioral regressions.** Existing four failing tests are the RED for caller integration. Add independent SSLContext verification and same-root-set tests, an explicit per-call context isolation check, a blocked initialization test, and real local TLS handshakes rejecting an untrusted CA/wrong hostname while accepting the trusted test CA. Generate ephemeral certificates using existing cryptography in tmp_path; no external server or credentials.

Core regression shape (the implementation test may use the equivalent local fixture):

```python
def test_runtime_context_retains_certificate_and_hostname_verification():
    context = asyncio.run(create_runtime_ssl_context())
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True
    expected = ssl.create_default_context(cafile=certifi.where())
    assert set(context.get_ca_certs(binary_form=True)) == set(expected.get_ca_certs(binary_form=True))

def test_tls_initialization_does_not_delay_asyncio_run_shutdown(monkeypatch):
    started = threading.Event()
    release = threading.Event()

    def blocked_build():
        started.set()
        assert release.wait(3)
        return ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)

    monkeypatch.setattr(http_tls, "_build_runtime_ssl_context", blocked_build)

    async def attempt():
        async with asyncio.timeout(0.05):
            await create_runtime_ssl_context()

    before = time.monotonic()
    try:
        with pytest.raises(TimeoutError):
            asyncio.run(attempt())
        assert started.is_set()
        assert time.monotonic() - before < 0.5
    finally:
        release.set()
```

Run `python -m pytest backend/tests/runtime/test_http_tls.py -q`; capture expected behavior failures, not only missing-module errors. Supplement each relevant caller with a test that blocks certificate construction, lets the deadline expire, and observes no requests at the local server after the worker is released. Reuse existing server helpers where appropriate.

- [x] **Step 2: Implement the bounded change.** Candidate helper below describes the intended boundary; preserve equivalent safe behavior if adapting to current library APIs. Loading through cadata is a performance choice with the same CA contents; the worker cancellation is the deadline fix.

```python
from pathlib import Path
import ssl

import anyio.to_thread
import certifi
import httpx


def _build_runtime_ssl_context() -> ssl.SSLContext:
    pem = Path(certifi.where()).read_text(encoding="ascii")
    return ssl.create_default_context(cadata=pem)


async def create_runtime_ssl_context() -> ssl.SSLContext:
    try:
        return await anyio.to_thread.run_sync(
            _build_runtime_ssl_context, abandon_on_cancel=True
        )
    except TimeoutError:
        raise
    except (OSError, UnicodeError, ValueError) as error:
        raise httpx.ConnectError("Unable to initialize TLS verification") from error
```

In each caller's existing `asyncio.timeout(remaining)` block, await the context, then verify the injected/current monotonic deadline has not expired before constructing/sending through HTTPX. Pass `verify=context`, preserve `trust_env=False`, and recompute phase timeouts using remaining budget after initialization where needed. Workflow additionally follows the approved combined-operation revision above; reuse a narrow request helper to avoid copying its request/error/deadline logic. Keep `async with httpx.AsyncClient(...)` and response streaming/closure, existing headers and post-response checks unchanged. SSL errors are OSError subclasses; do not catch cancellation broadly.

- [x] **Step 3: Verify scoped and original behavior.** Run the new TLS tests and all four original test files. Run the four original slow-drip test node IDs in a fresh Python process twice to cover cold initialization without relying on an earlier context cache. Record pass counts, skips, warnings and elapsed times; explicitly prove timeout leaves no deferred request.

Exact original nodes (do not replace sandbox with completion in the report):

```text
backend/tests/runtime/test_launcher_client.py::test_launcher_prepare_enforces_execution_deadline_during_slow_response
backend/tests/runtime/test_runner_gateway_client.py::test_execution_request_enforces_absolute_slow_drip_deadline
backend/tests/runtime/test_sandbox_runtime.py::test_real_gateway_slow_drip_expiry_completes_as_sandbox_timeout
backend/tests/runtime/test_workflow_runner.py::test_workflow_runner_submit_enforces_absolute_slow_drip_deadline
```

Additionally run the unchanged Workflow slow-drip test in five sequential fresh Python processes, without competing controller tests. Record every result, not only the final pass. Freeze code and report stable HEAD before controller starts the full suite; never mutate code during that run.

```powershell
python -m pytest backend/tests/runtime/test_http_tls.py backend/tests/runtime/test_launcher_client.py backend/tests/runtime/test_runner_gateway_client.py backend/tests/runtime/test_sandbox_runtime.py backend/tests/runtime/test_workflow_runner.py -q
```

- [x] **Step 4: Review and targeted commit.** Run diff-check, preserve unrelated formatting, fetch and merge origin/main before staging only this task's code/tests/dependency declarations. Commit `fix: keep runtime TLS initialization within deadlines`. Write a full RED/GREEN report and obtain task spec/quality review. Controller subsequently runs broad final review and the full backend suite once, not one full suite per small edit.

## Controller Acceptance And Integration

- [x] Confirm existing isolated worktree and remote coordination; reproduce original four failures.
- [x] Task 1 implementation and task review.
- [x] Full backend regression on the final code, plus the 10 real PostgreSQL/MinIO/migration checks and pip check. No skip counted as a pass. Final confirmation: 1286 passed, 63 skipped, 2 warnings; earlier watchdog timing failure retained in verification record.
- [x] Final whole-branch review, incorporating previous Skill review evidence rather than reimplementing completed work. Both consolidated findings addressed in e500c0e and scoped re-review passed.
- [x] Update both current and Skill foundation verification records; fetch/merge and commit documents only after actual evidence is available. Evidence and final checklists are included in this documentation commit; explicit verified-TLS fetch succeeded and origin/main was already integrated.
- [x] After a green full suite and review, locally merge the branch into main as the user approved, verify the merged tree and preserve unrelated main files. Main source fast-forwarded to e500c0e; identical tree and 286 affected-module tests passed. No push. Keep the worktree Python environment and diagnostic records.

If remote fetch fails, preserve tested changes and state exactly which commit/merge remains pending. If tests reveal additional unrelated failures, diagnose first and do not silently broaden the requested repair.
