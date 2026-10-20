"use strict";

(() => {
  const API = ""; // mesma origem: o backend FastAPI serve este frontend
  const MAX_CHARS = 1000;
  const TIMEOUT_MS = 60000;

  const CATEGORY = {
    financeiro: "Financeiro",
    informacoes_internas: "Informações internas",
    ouvidoria: "Ouvidoria",
    indefinido: "Domínio não identificado",
  };
  const MODE = {
    deterministic: "Calculado sobre os dados",
    extractive: "Trechos recuperados (sem IA)",
    llm: "Gerado por IA a partir das fontes",
  };
  const STATUS = {
    not_found: "Sem evidência suficiente",
    clarification: "Precisa de esclarecimento",
    refused: "Solicitação recusada",
    out_of_scope: "Fora do escopo",
  };

  const $ = (sel) => document.querySelector(sel);
  const messages = $("#messages");
  const emptyState = $("#empty-state");
  const form = $("#composer");
  const input = $("#question");
  const sendBtn = $("#send");
  const counter = $("#counter");
  const statusPill = $("#service-status");

  let busy = false;
  let controller = null;

  // ---------------------------------------------------------------- utilitários

  function el(tag, attrs = {}, children = []) {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (v === undefined || v === null) continue;
      if (k === "class") node.className = v;
      else if (k === "text") node.textContent = v;
      else node.setAttribute(k, v);
    }
    for (const c of [].concat(children)) if (c) node.append(c);
    return node;
  }

  function icon(id) {
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("class", "icon");
    svg.setAttribute("aria-hidden", "true");
    const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
    use.setAttribute("href", `#${id}`);
    svg.append(use);
    return svg;
  }

  /** Converte o texto da resposta em nós DOM (negrito e listas), sem innerHTML. */
  function inline(text) {
    const frag = document.createDocumentFragment();
    text.split(/(\*\*[^*]+\*\*)/g).forEach((part) => {
      if (/^\*\*[^*]+\*\*$/.test(part)) frag.append(el("strong", { text: part.slice(2, -2) }));
      else if (part) frag.append(document.createTextNode(part));
    });
    return frag;
  }

  function renderText(text) {
    const frag = document.createDocumentFragment();
    let list = null;
    for (const raw of text.split("\n")) {
      const line = raw.trimEnd();
      if (/^\s*[-•]\s+/.test(line)) {
        if (!list) frag.append((list = el("ul")));
        list.append(el("li", {}, inline(line.replace(/^\s*[-•]\s+/, ""))));
      } else if (line.trim()) {
        list = null;
        frag.append(el("p", {}, inline(line)));
      } else {
        list = null;
      }
    }
    return frag;
  }

  function scrollToEnd() {
    messages.scrollTop = messages.scrollHeight;
  }

  // ---------------------------------------------------------------- mensagens

  function addUser(text) {
    emptyState.hidden = true;
    messages.append(el("div", { class: "msg msg-user" }, el("div", { class: "bubble" }, renderText(text))));
    scrollToEnd();
  }

  function sourcesBlock(sources) {
    if (!sources || !sources.length) return null;
    const list = el("ol");
    for (const s of sources) {
      const meta = [s.section ? `seção: ${s.section}` : null, s.page ? `página ${s.page}` : null,
        typeof s.score === "number" && s.score > 0 ? `relevância BM25 ${s.score.toFixed(2)}` : null]
        .filter(Boolean).join(" · ");
      list.append(el("li", {}, [
        el("span", { class: "src-doc", text: s.document }),
        meta ? el("span", { class: "src-meta", text: ` — ${meta}` }) : null,
        el("p", { class: "src-excerpt", text: s.excerpt }),
      ]));
    }
    return el("details", { class: "sources" }, [
      el("summary", {}, [icon("i-doc"), document.createTextNode(`Fontes consultadas (${sources.length})`)]),
      list,
    ]);
  }

  function addAssistant(data) {
    const meta = el("div", { class: "meta" }, [
      el("span", { class: "badge", text: CATEGORY[data.category] || data.category }),
      data.agent ? el("span", { text: data.agent }) : null,
      MODE[data.mode] ? el("span", { class: "tag", text: MODE[data.mode] }) : null,
      STATUS[data.status] ? el("span", { class: "tag", text: STATUS[data.status] }) : null,
      typeof data.confidence === "number"
        ? el("span", { class: "tag", title: "Confiança do roteamento por regras",
          text: `roteamento ${Math.round(data.confidence * 100)}%` })
        : null,
    ]);
    const bubble = el("div", { class: "bubble" }, [renderText(data.answer)]);
    if (data.notice) bubble.append(el("div", { class: "notice", text: data.notice }));
    const src = sourcesBlock(data.sources);
    if (src) bubble.append(src);
    messages.append(el("div", { class: "msg msg-assistant", "data-domain": data.category }, [meta, bubble]));
    scrollToEnd();
  }

  function addError(message, retryQuestion) {
    const bubble = el("div", { class: "bubble" }, [icon("i-alert"), document.createTextNode(message)]);
    if (retryQuestion) {
      const btn = el("button", { class: "btn retry", type: "button", text: "Tentar novamente" });
      btn.addEventListener("click", () => {
        btn.closest(".msg").remove();
        ask(retryQuestion, { echo: false });
      });
      bubble.append(el("div", {}, btn));
    }
    messages.append(el("div", { class: "msg msg-assistant msg-error", role: "alert" }, bubble));
    scrollToEnd();
  }

  // ---------------------------------------------------------------- envio

  function setBusy(value) {
    busy = value;
    sendBtn.disabled = value || !input.value.trim() || input.value.length > MAX_CHARS;
    input.setAttribute("aria-busy", String(value));
  }

  async function ask(question, { echo = true } = {}) {
    if (busy) return; // impede envios duplicados
    if (echo) addUser(question);
    setBusy(true);
    const loading = $("#tpl-loading").content.firstElementChild.cloneNode(true);
    messages.append(loading);
    scrollToEnd();

    controller = new AbortController();
    const timer = setTimeout(() => controller.abort("timeout"), TIMEOUT_MS);
    try {
      const res = await fetch(`${API}/api/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question }),
        signal: controller.signal,
      });
      let body = null;
      try { body = await res.json(); } catch { /* resposta sem JSON */ }
      if (!res.ok) {
        const detail = body && typeof body.detail === "string" ? body.detail : null;
        if (res.status === 422) addError(detail || "Pergunta inválida.");
        else addError(detail || `O serviço respondeu com erro (${res.status}).`, question);
        return;
      }
      addAssistant(body);
    } catch (err) {
      if (controller.signal.aborted && controller.signal.reason === "reset") return;
      if (controller.signal.aborted) addError("A resposta demorou demais. Tente novamente.", question);
      else addError("Não foi possível conectar ao serviço. Verifique se a API está em execução.", question);
      checkHealth();
    } finally {
      clearTimeout(timer);
      loading.remove();
      controller = null;
      setBusy(false);
    }
  }

  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const q = input.value.trim();
    if (!q || busy) return;
    if (q.length > MAX_CHARS) return;
    input.value = "";
    updateCounter();
    ask(q);
  });

  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
      e.preventDefault();
      form.requestSubmit();
    }
  });

  function updateCounter() {
    const n = input.value.length;
    counter.textContent = `${n}/${MAX_CHARS}`;
    counter.classList.toggle("over", n > MAX_CHARS);
    input.style.height = "auto";
    input.style.height = `${Math.min(input.scrollHeight, 160)}px`;
    setBusy(busy);
  }
  input.addEventListener("input", updateCounter);

  document.querySelectorAll(".suggestion").forEach((btn) =>
    btn.addEventListener("click", () => ask(btn.textContent.trim())));

  $("#new-chat").addEventListener("click", () => {
    if (controller) controller.abort("reset");
    messages.querySelectorAll(".msg").forEach((m) => m.remove());
    emptyState.hidden = false;
    input.value = "";
    updateCounter();
    input.focus();
  });

  // ---------------------------------------------------------------- estado do serviço

  async function checkHealth() {
    try {
      const res = await fetch(`${API}/health`, { cache: "no-store" });
      const h = await res.json();
      if (h.status !== "ok") {
        statusPill.className = "status-pill warn";
        statusPill.textContent = "Base de conhecimento não indexada";
      } else if (h.llm && h.llm.configured) {
        statusPill.className = "status-pill ok";
        statusPill.textContent = `Online · IA: ${h.llm.provider}`;
      } else {
        statusPill.className = "status-pill ok";
        statusPill.textContent = "Online · modo extrativo";
        statusPill.title = (h.llm && h.llm.reason) || "";
      }
    } catch {
      statusPill.className = "status-pill down";
      statusPill.textContent = "Serviço indisponível";
    }
  }

  async function loadDocCounts() {
    try {
      const res = await fetch(`${API}/api/knowledge/status`);
      const st = await res.json();
      document.querySelectorAll(".domain-card").forEach((card) => {
        const d = st.domains && st.domains[card.dataset.domain];
        if (!d) return;
        card.querySelector("div").append(
          el("span", { class: "doc-count", text: `${d.documents} arquivos · ${d.chunks} trechos indexados` }));
      });
    } catch { /* indicador opcional */ }
  }

  updateCounter();
  checkHealth();
  loadDocCounts();
})();
