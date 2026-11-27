"use strict";

const byId = (id) => document.getElementById(id);
const prefix = document.querySelector('meta[name="api-prefix"]').content;
const pages = Array.from(document.querySelectorAll("[data-panel]"));
const stages = { queued: "排队", parsing: "解析", chunking: "切片", embedding: "向量化", indexing: "写入索引", retrying: "等待重试", completed: "已完成", failed: "失败" };
const labels = { bm25_reference: "BM25 Reference", bm25: "BM25", dense: "Dense", hybrid: "Hybrid", adaptive: "Adaptive", adaptive_rerank: "Adaptive + Reranker" };
let state;
let sessionId = null;
let conversationRequest = null;
let pollVersion = 0;
let documentPage = 0;
let documentRequest = 0;

function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = String(text);
  if (className) node.className = className;
  return node;
}

function notice(id, message, failed = false) {
  const node = byId(id);
  node.textContent = message;
  node.dataset.error = String(failed);
}

function card(title, detail) {
  const node = element("article", undefined, "card");
  node.append(element("h2", title));
  if (detail) node.append(element("p", detail, "caption"));
  return node;
}

function metric(label, value) {
  return element("span", label + " " + (value ?? "—"), "metric");
}

function table(headers, rows) {
  const wrapper = element("div", undefined, "table-scroll");
  const node = element("table");
  const head = node.createTHead().insertRow();
  for (const header of headers) {
    const cell = element("th", header);
    cell.scope = "col";
    head.append(cell);
  }
  const body = node.createTBody();
  for (const row of rows) {
    const tr = body.insertRow();
    for (const value of row) {
      const td = tr.insertCell();
      if (value instanceof Node) td.append(value);
      else td.textContent = value ?? "—";
    }
  }
  wrapper.append(node);
  return wrapper;
}

async function api(path, { json, ...options } = {}) {
  if (json !== undefined) {
    options.method = "POST";
    options.body = JSON.stringify(json);
    options.headers = { ...options.headers, "Content-Type": "application/json" };
  }
  const response = await fetch(prefix + path, { cache: "no-store", ...options });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    const detail = payload.detail;
    let message = typeof detail === "string" ? detail : detail?.message || payload.code;
    if (Array.isArray(detail)) message = detail.map((entry) => entry.msg).join("；");
    const retry = response.headers.get("Retry-After");
    const requestId = response.headers.get("X-Request-ID");
    const error = new Error([message || "请求失败（HTTP " + response.status + "）",
      retry && "可在 " + retry + " 秒后重试", requestId && "请求 " + requestId].filter(Boolean).join(" · "));
    error.task = detail?.task_id ? detail : null;
    throw error;
  }
  return response;
}

async function readJson(path, options) {
  return (await api(path, options)).json();
}

function selectPanel() {
  const hash = location.hash.slice(1);
  if (hash === "main" && document.querySelector("[aria-current=page]")) return;
  const selected = pages.find((page) => page.dataset.panel === hash) || pages[0];
  for (const page of pages) {
    const current = page === selected;
    byId(page.dataset.panel).hidden = !current;
    if (current) page.setAttribute("aria-current", "page");
    else page.removeAttribute("aria-current");
  }
  byId("location").textContent = selected.textContent.trim().slice(1).trim();
  if (hash) window.scrollTo(0, 0);
  if (selected.dataset.panel === "documents" && state?.run_mode === "local") refreshDocuments(0);
  if (selected.dataset.panel === "experiments") loadExperiments();
}
window.addEventListener("hashchange", selectPanel);
selectPanel();

async function refreshStatus() {
  try {
    state = await readJson("/system/status");
    const demo = state.run_mode === "demo";
    byId("run-mode").textContent = demo ? "公网演示模式" : "本地开发模式";
    byId("ready-value").textContent = state.ready ? "就绪" : "待就绪";
    byId("revision-value").textContent = state.index_revision ?? "—";
    byId("versions-value").textContent = state.visible_versions ?? "—";
    byId("concurrency-value").textContent = state.limits.concurrency;
    byId("sidebar-dot").dataset.ok = String(state.ready);
    byId("sidebar-health").textContent = state.ready ? "依赖服务就绪" : "依赖服务待就绪";
    byId("dependencies").replaceChildren(...Object.entries(state.dependencies).map(([name, ok]) => {
      const row = element("div");
      const value = element("dd", ok ? "就绪" : "不可用");
      value.dataset.ok = String(ok);
      row.append(element("dt", { sqlite: "SQLite", redis: "Redis", qdrant: "Qdrant" }[name] || name), value);
      return row;
    }));
    byId("limits").textContent = "普通 API " + state.limits.api_per_minute + " 次/分钟 · Chat " +
      state.limits.chat_per_minute + " 次/分钟 · 单文件 " + state.limits.max_upload_mb + " MiB";
    byId("admin-field").hidden = !demo;
    byId("search-query").maxLength = demo ? 500 : 2000;
    byId("chat-query").maxLength = demo ? 500 : 2000;
    byId("search-form").elements.top_k.max = demo ? 10 : 50;
    const rerank = byId("search-form").elements.rerank;
    rerank.disabled = !state.rerank_enabled;
    if (rerank.disabled) rerank.checked = false;
    byId("chat-form").querySelector('[type="submit"]').disabled = !state.chat_enabled || Boolean(conversationRequest);
    byId("tool-form").querySelector('[type="submit"]').disabled = !state.tool_chat_enabled;
    if (!state.chat_enabled) notice("chat-state", demo ? "演示问答已关闭。可继续使用检索。" : "服务端尚未配置模型凭据。");
    else if (!conversationRequest && !byId("conversation").children.length) notice("chat-state", "先输入一个知识库问题。");
    if (!state.tool_chat_enabled) notice("tool-state", demo ? "工具问答在本地开发模式可用。" : "服务端尚未配置模型凭据。");
    if (location.hash === "#documents") {
      if (!demo) refreshDocuments(0);
      else notice("document-state", "输入管理员令牌后刷新版本。");
    }
  } catch (error) {
    byId("sidebar-dot").dataset.ok = "false";
    byId("sidebar-health").textContent = "无法读取后端状态";
    byId("ready-value").textContent = "未知";
    byId("limits").textContent = error.message;
  }
}
byId("refresh-status").addEventListener("click", refreshStatus);
refreshStatus();

byId("search-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const submit = form.querySelector('[type="submit"]');
  const fields = new FormData(form);
  const query = String(fields.get("query")).trim();
  if (!query) { notice("search-state", "请输入查询。", true); return; }
  submit.disabled = true;
  byId("search-results").replaceChildren();
  byId("search-metrics").replaceChildren();
  notice("search-state", "正在检索…");
  try {
    const result = await readJson("/search", { json: {
      query, mode: fields.get("mode"), top_k: Number(fields.get("top_k")), rerank: fields.has("rerank")
    } });
    const weights = result.fusion_weights ? Object.entries(result.fusion_weights).map(([key, value]) => key + " " + value).join(" / ") : "—";
    byId("search-metrics").replaceChildren(
      metric("实际策略", labels[result.effective_mode] || result.effective_mode),
      metric("修订版", result.index_revision), metric("耗时", result.latency_ms + " ms"),
      metric("缓存", result.cache_hit ? "命中" : "未命中"),
      metric("融合权重", weights), metric("路由类型", result.query_type),
      metric("Reranker", result.rerank_applied ? "已应用" : "未应用"),
      metric("计分", result.score_type)
    );
    byId("search-results").replaceChildren(...result.hits.map((hit) => {
      const node = card(hit.filename, [hit.page && "第 " + hit.page + " 页", "片段 " + hit.chunk_index,
        "得分 " + Number(hit.score).toFixed(4)].filter(Boolean).join(" · "));
      node.append(element("p", hit.text, "result-text"), element("p", "版本 " + hit.version_id, "caption result-text"));
      return node;
    }));
    const message = (result.degraded ? "检索已降级 · " : "") +
      (result.hits.length ? "返回 " + result.hits.length + " 个片段。" : "没有找到匹配片段。");
    notice("search-state", [message, ...result.warnings].join(" "), result.degraded);
  } catch (error) {
    notice("search-state", error.message, true);
  } finally {
    submit.disabled = false;
  }
});

function renderFinal(turn, answer) {
  turn.output.textContent = answer.answer;
  turn.details.replaceChildren(metric("状态", answer.refused ? "证据不足" : "已校验"),
    metric("修订版", answer.index_revision), metric("检索", answer.retrieval_mode),
    metric("耗时", answer.latency_ms + " ms"));
  if (answer.ttft_ms != null) turn.details.append(metric("TTFT", answer.ttft_ms + " ms"));
  if (answer.llm_ttft_ms != null) turn.details.append(metric("模型 TTFT", answer.llm_ttft_ms + " ms"));
  if (answer.usage) {
    const usage = answer.usage;
    turn.details.append(metric("输入 / 输出 / 总 Token",
      [usage.input_tokens, usage.output_tokens, usage.total_tokens].map((value) => value ?? "—").join(" / ")));
  }
  if (answer.degraded) turn.details.append(metric("降级", "是"));
  turn.citations.replaceChildren(...answer.citations.map((reference) => element("li", reference.filename +
    (reference.page ? " · 第 " + reference.page + " 页" : "") + " · 片段 " + reference.chunk_index +
    " · 版本 " + reference.version_id)));
}

async function consumeEvents(response, onEvent) {
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    while (true) {
      const { done, value } = await reader.read();
      buffer += done ? decoder.decode() : decoder.decode(value, { stream: true });
      let boundary;
      while ((boundary = /\r?\n\r?\n/.exec(buffer))) {
        const lines = buffer.slice(0, boundary.index).split(/\r?\n/);
        buffer = buffer.slice(boundary.index + boundary[0].length);
        const event = lines.find((line) => line.startsWith("event:"));
        const data = lines.filter((line) => line.startsWith("data:"))
          .map((line) => line.slice(5).trimStart()).join("\n");
        if (event && data) onEvent(event.slice(6).trim(), JSON.parse(data));
      }
      if (done) break;
    }
  } finally {
    await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
}

function addTurn(question) {
  const node = card(question);
  const output = element("p", "检索证据…", "result-text");
  const details = element("div", undefined, "metrics");
  const citations = element("ul", undefined, "citations");
  node.append(output, details, citations);
  byId("conversation").prepend(node);
  return { output, details, citations };
}

byId("chat-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (conversationRequest) return;
  const form = event.currentTarget;
  const fields = new FormData(form);
  const query = String(fields.get("query")).trim();
  if (!query) { notice("chat-state", "请输入问题。", true); return; }
  const controller = new AbortController();
  conversationRequest = controller;
  const submit = form.querySelector('[type="submit"]');
  submit.disabled = true;
  byId("cancel-chat").disabled = false;
  byId("reset-chat").disabled = true;
  form.elements.query.value = "";
  const turn = addTurn(query);
  let provisional = "";
  let finalized = false;
  const streaming = fields.has("stream");
  const payload = { query, stream: streaming, ...(sessionId && { session_id: sessionId }) };
  try {
    notice("chat-state", "正在处理问题…");
    const response = await api("/chat/completions", { json: payload, signal: controller.signal });
    if (streaming) {
      await consumeEvents(response, (name, data) => {
        if (name === "retrieval") {
          notice("chat-state", "已检索 " + data.hit_count + " 个证据片段。");
          turn.details.replaceChildren(metric("检索耗时", data.latency_ms + " ms"));
        } else if (name === "delta") {
          provisional += data.text;
          turn.output.textContent = provisional;
          notice("chat-state", "流式草稿，等待引用校验。");
        } else if (name === "final") {
          sessionId = data.session_id;
          renderFinal(turn, data);
          finalized = true;
        } else if (name === "error") {
          throw new Error(data.message || data.code);
        }
      });
      if (!finalized) throw new Error("连接在收到最终答案前终止。");
    } else {
      const answer = await response.json();
      sessionId = answer.session_id;
      renderFinal(turn, answer);
    }
    notice("chat-state", "已收到最终结果，可继续追问。");
  } catch (error) {
    if (finalized) {
      notice("chat-state", "已收到最终结果，可继续追问。");
      return;
    }
    const cancelled = controller.signal.aborted;
    turn.output.textContent = cancelled ? "生成已停止。" : "问答失败。";
    turn.details.replaceChildren();
    notice("chat-state", cancelled ? "已清除临时草稿。" : error.message, !cancelled);
  } finally {
    conversationRequest = null;
    submit.disabled = state?.chat_enabled === false;
    byId("cancel-chat").disabled = true;
    byId("reset-chat").disabled = false;
  }
});
byId("cancel-chat").addEventListener("click", () => conversationRequest?.abort());
byId("reset-chat").addEventListener("click", () => {
  sessionId = null;
  byId("conversation").replaceChildren();
  notice("chat-state", state?.chat_enabled ? "新会话已就绪。" : "问答当前不可用。");
});

function adminHeaders() {
  const token = byId("admin-token").value.trim();
  return token ? { Authorization: "Bearer " + token } : {};
}

async function refreshDocuments(page = documentPage) {
  const requestId = ++documentRequest;
  const size = 20;
  byId("document-list").hidden = true;
  byId("document-pagination").hidden = true;
  try {
    const documents = await readJson("/documents?limit=" + (size + 1) + "&offset=" + (size * page), { headers: adminHeaders() });
    if (requestId !== documentRequest) return;
    documentPage = page;
    byId("document-list").replaceChildren(table(["文件", "版本", "状态", "进度 / 片段", "文档 ID"],
      documents.slice(0, size).map((item) => {
        const id = element("code", item.document_id);
        const select = element("button", "更新此文档", "secondary");
        select.type = "button";
        select.addEventListener("click", () => {
          byId("upload-form").elements.document_id.value = item.document_id;
          byId("upload-form").elements.file.focus();
        });
        const controls = element("div", undefined, "actions");
        controls.append(id, select);
        return [item.filename, "v" + item.version_number + (item.is_active ? " · 生效" : ""),
          stages[item.status] || item.status, item.progress + "% / " + item.chunk_count, controls];
      })));
    byId("document-list").hidden = documents.length === 0;
    byId("document-pagination").hidden = page === 0 && documents.length <= size;
    byId("previous-documents").disabled = page === 0;
    byId("next-documents").disabled = documents.length <= size;
    notice("document-state", documents.length ? "已读取文档版本。" : "当前没有文档版本。");
  } catch (error) {
    if (requestId === documentRequest) notice("document-state", error.message, true);
  }
}

async function watchTask(task, run) {
  const node = card("最近提交的入库任务", "任务 " + task.task_id);
  const progress = element("progress");
  progress.max = 100;
  progress.setAttribute("aria-label", "入库进度");
  const summary = element("p");
  const ids = element("p", "文档 " + task.document_id + " · 版本 " + task.version_id, "caption result-text");
  node.append(progress, summary, ids);
  byId("document-task").replaceChildren(node);
  for (let elapsed = 0; elapsed <= 180; elapsed += 2) {
    if (run !== pollVersion) return;
    try {
      const result = await readJson("/documents/tasks/" + encodeURIComponent(task.task_id), { headers: adminHeaders() });
      if (run !== pollVersion) return;
      progress.value = result.progress;
      summary.textContent = (stages[result.status] || result.status) + " · " + result.progress + "%";
      if (result.error) summary.textContent += " · " + result.error.message;
      if (result.status === "completed" || result.status === "failed") {
        refreshDocuments(0);
        return;
      }
    } catch (error) {
      summary.textContent = error.message;
      return;
    }
    await new Promise((resolve) => setTimeout(resolve, 2000));
  }
  summary.textContent += " · 已结束轮询，可刷新版本查看后续状态。";
}

byId("upload-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const submit = form.querySelector('[type="submit"]');
  const fields = new FormData(form);
  const documentId = String(fields.get("document_id")).trim();
  if (documentId) fields.set("document_id", documentId);
  else fields.delete("document_id");
  submit.disabled = true;
  notice("document-state", "正在上传…");
  try {
    const task = await readJson("/documents/upload", { method: "POST", body: fields, headers: adminHeaders() });
    notice("document-state", "已登记任务。");
    form.elements.file.value = "";
    watchTask(task, ++pollVersion);
  } catch (error) {
    notice("document-state", error.message, true);
    if (error.task) watchTask(error.task, ++pollVersion);
  } finally {
    submit.disabled = false;
  }
});
byId("refresh-documents").addEventListener("click", () => refreshDocuments(0));
byId("previous-documents").addEventListener("click", () => refreshDocuments(documentPage - 1));
byId("next-documents").addEventListener("click", () => refreshDocuments(documentPage + 1));
byId("admin-token").addEventListener("change", () => refreshDocuments(0));

byId("tool-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const submit = form.querySelector('[type="submit"]');
  const query = form.elements.query.value.trim();
  if (!query) { notice("tool-state", "请输入问题。", true); return; }
  submit.disabled = true;
  byId("tool-results").replaceChildren();
  notice("tool-state", "等待工具执行…");
  try {
    const result = await readJson("/chat/tool-completions", { json: { query } });
    const output = card("工具问答结果", "修订版 " + result.index_revision + " · " + result.latency_ms + " ms");
    output.append(element("p", result.answer, "result-text"));
    const calls = card("工具调用记录");
    if (result.tool_calls.length) calls.append(table(["工具", "状态", "耗时", "来源"],
      result.tool_calls.map((call) => [call.tool, call.status, call.latency_ms + " ms", call.source_ids?.join(", ") || "—"])));
    else calls.append(element("p", "本次响应未执行工具。"));
    const evidence = Object.entries(result.evidence).map(([id, source]) => {
      const node = card("[" + id + "] " + source.filename, "版本 " + source.version_id +
        (result.cited_sources.includes(id) ? " · 已被引用" : ""));
      if (source.text) node.append(element("p", source.text, "result-text"));
      else node.append(element("p", "版本序号 " + source.version_number + " · 文本块 " + source.chunk_count));
      return node;
    });
    byId("tool-results").replaceChildren(output, calls, ...evidence);
    notice("tool-state", result.refused ? "证据不足，服务已拒答。" : "已收到结果与证据。");
  } catch (error) {
    notice("tool-state", error.message, true);
  } finally {
    submit.disabled = state?.tool_chat_enabled === false;
  }
});

async function loadExperiments() {
  notice("experiment-state", "正在读取实验归档…");
  try {
    const reports = await readJson("/system/experiments");
    if (reports.benchmark) {
      const report = reports.benchmark;
      byId("benchmark-caption").textContent = report.corpus_size + " 篇文档 · " + report.dev_queries +
        " 个开发查询 · " + report.test_queries + " 个测试查询 · 嵌入 " + report.embedding_model;
      byId("benchmark-results").replaceChildren(table(["策略", "Recall@10", "MRR@10", "NDCG@10", "P95 (ms)"],
        Object.entries(report.test).map(([key, row]) =>
          [labels[key] || key, row.recall_at_10, row.mrr_at_10, row.ndcg_at_10, row.p95_ms])));
    } else {
      byId("benchmark-caption").textContent = "当前环境未包含检索归档。";
    }
    const loadNames = { users_100: "原始 100 用户", protected_100: "原限流验证（配置异常）",
      protected_100_audit: "审计复测（独立语料）" };
    const rows = [];
    for (const [key, label] of Object.entries(loadNames)) {
      const report = reports[key];
      if (!report) continue;
      for (const [workload, row] of Object.entries(report.workloads)) {
        rows.push([label + " · " + (workload === "search_hot" ? "热点" : "冷查询"),
          row.successful_requests + " / " + row.requests, row.successful_qps,
          row.p95_success_ms, row.status_codes["429"] ?? 0, row.status_codes["503"] ?? 0]);
      }
    }
    byId("load-results").replaceChildren(rows.length ? table(["实验 / 负载", "成功 / 请求", "成功 QPS",
      "成功 P95 (ms)", "429", "503"], rows) : element("p", "当前环境未包含压测归档。"));
    const coverage = reports.coverage;
    byId("coverage-results").textContent = coverage ?
      coverage.tests + " 项通过 · " + coverage.coverage_percent + "% 语句覆盖率 · Python " + coverage.python :
      "当前环境未包含本轮 Python 3.11 覆盖率归档。";
    notice("experiment-state", "");
  } catch (error) {
    notice("experiment-state", error.message, true);
  }
}
