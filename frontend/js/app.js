"use strict";

(() => {
  const API = ""; // mesma origem: o FastAPI serve este frontend
  const MAX_CHARS = 1000;
  const TIMEOUT_MS = 60000;
  // Pausas da "conversa": a Aurora analisa, o agente começa a digitar, o texto aparece.
  // Com prefers-reduced-motion o CSS troca deslocamentos por esmaecimento; a digitação
  // continua, porque é revelação de texto (sem movimento na tela) e pode ser pulada.
  const THINK_MS = 650;
  const HANDOFF_MS = 600;
  const MS_PER_CHAR = 16;
  const TYPE_MIN_MS = 500;
  const TYPE_MAX_MS = 2400;

  const AGENT = {
    financeiro: { name: "Agente Financeiro", icon: "i-chart" },
    informacoes_internas: { name: "Agente de Informações Internas", icon: "i-book" },
    ouvidoria: { name: "Agente de Ouvidoria", icon: "i-chat" },
    aurora: { name: "Aurora", icon: "i-aurora" },
  };
  const MODE = {
    deterministic: { text: "Calculado diretamente nos dados da base", icon: "i-calc" },
    extractive: { text: "Trechos dos documentos, sem modelo de IA", icon: "i-quote" },
    llm: { text: "Escrito por IA a partir das fontes", icon: "i-spark" },
  };
  const STATUS = {
    not_found: ["Sem evidência suficiente", "attention"],
    clarification: ["Precisa de mais detalhes", "attention"],
    refused: ["Pedido recusado", "denied"],
    out_of_scope: ["Fora do escopo", "attention"],
  };

  const $ = (sel) => document.querySelector(sel);
  const scroller = $("#messages");
  const thread = $("#thread");
  const welcome = $("#welcome");
  const form = $("#composer");
  const input = $("#question");
  const sendBtn = $("#send");
  const counter = $("#counter");
  const statusEl = $("#service-status");

  let busy = false;
  let controller = null;
  let generation = 0; // muda a cada "Nova conversa": etapas antigas param de agir
  let skipTyping = null;

  // ---------------------------------------------------------------- utilitários

  function el(tag, attrs = {}, children = []) {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (v === undefined || v === null || v === false) continue;
      if (k === "class") node.className = v;
      else if (k === "text") node.textContent = v;
      else node.setAttribute(k, v);
    }
    for (const c of [].concat(children)) if (c) node.append(c);
    return node;
  }

  function icon(id) {
    const ns = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(ns, "svg");
    svg.setAttribute("class", "icon");
    svg.setAttribute("aria-hidden", "true");
    const use = document.createElementNS(ns, "use");
    use.setAttribute("href", `#${id}`);
    svg.append(use);
    return svg;
  }

  function avatar(domain) {
    const info = AGENT[domain] || AGENT.aurora;
    return el("span", { class: "avatar", "aria-hidden": "true" }, icon(info.icon));
  }

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const domainOf = (category) => (AGENT[category] ? category : "aurora");

  function nearBottom() {
    return scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < 140;
  }
  function scrollToEnd() {
    scroller.scrollTop = scroller.scrollHeight;
  }

  // ---------------------------------------------------------------- texto da resposta (sem innerHTML)

  function inline(text) {
    const frag = document.createDocumentFragment();
    text = text.replace(/R\$ (?=\d)/g, "R$ "); // não separa "R$" do valor na quebra de linha
    for (const part of text.split(/(\*\*[^*]+\*\*)/g)) {
      if (/^\*\*[^*]+\*\*$/.test(part)) frag.append(el("strong", { text: part.slice(2, -2) }));
      else if (part) frag.append(document.createTextNode(part));
    }
    return frag;
  }

  function renderText(text) {
    const frag = document.createDocumentFragment();
    let list = null;
    let listType = null;
    for (const raw of text.split("\n")) {
      const line = raw.trim();
      const bullet = line.match(/^[-•]\s+(.*)$/);
      const num = line.match(/^(\d+)[.)]\s+(.*)$/);
      if (bullet || num) {
        const type = bullet ? "ul" : "ol";
        if (!list || listType !== type) {
          list = el(type, num && num[1] !== "1" ? { start: num[1] } : {});
          listType = type;
          frag.append(list);
        }
        list.append(el("li", {}, inline(bullet ? bullet[1] : num[2])));
        continue;
      }
      list = null;
      if (!line) continue;
      // "**arquivo.md** (seção)" ou "Fonte: **arquivo**" viram um rótulo de origem
      if (/^\*\*[^*]+\*\*(\s*\([^)]*\))?:?$/.test(line) || /^Fonte: \*\*/.test(line)) {
        frag.append(el("p", { class: "label" }, inline(line)));
      } else if (/^Crit[ée]rio:/.test(line)) {
        frag.append(el("p", { class: "criteria" }, inline(line)));
      } else {
        frag.append(el("p", {}, inline(line)));
      }
    }
    return frag;
  }

  // ---------------------------------------------------------------- blocos da conversa

  function addUser(text) {
    welcome.hidden = true;
    thread.append(el("div", { class: "row row-user" }, el("div", { class: "bubble-user", text })));
    scrollToEnd();
  }

  function buildTyping() {
    let av = avatar("aurora");
    const name = el("span", { class: "bot-name", text: "Aurora" });
    const caption = el("p", { class: "typing-caption", text: "A Aurora está analisando sua pergunta…" });
    const dots = el("span", { class: "dots", "aria-hidden": "true" }, [el("i"), el("i"), el("i")]);
    const node = el("div", { class: "row row-bot typing", "data-domain": "aurora", role: "status" }, [
      av,
      el("div", { class: "bot-body" }, [el("div", { class: "bot-head" }, name), el("div", { class: "bubble-bot" }, dots), caption]),
    ]);
    return {
      node,
      handOff(domain, agentName) {
        node.dataset.domain = domain;
        const next = avatar(domain);
        av.replaceWith(next);
        av = next;
        name.textContent = agentName;
        caption.textContent = `${agentName} está digitando…`;
      },
    };
  }

  function buildRoute(data) {
    const r = data.routing || {};
    const known = data.category !== "indefinido";
    const text = known
      ? [document.createTextNode("A Aurora encaminhou ao "), el("strong", { text: data.agent })]
      : [document.createTextNode(data.status === "clarification"
        ? "A Aurora encontrou mais de um assunto possível"
        : "A Aurora não identificou um assunto atendido pelos agentes")];
    let chip = null;
    if (r.method === "guard") chip = "regra de segurança";
    else if (r.method === "llm") chip = "classificado por IA";
    else if (known && typeof data.confidence === "number") chip = `confiança de ${Math.round(data.confidence * 100)}%`;
    return el("div", { class: "route", "data-domain": domainOf(data.category), title: r.reason || null }, [
      icon("i-route"),
      el("span", {}, text),
      chip ? el("span", { class: "route-conf", text: chip }) : null,
    ]);
  }

  function sourcesBlock(sources) {
    if (!sources || !sources.length) return null;
    const list = el("ol");
    for (const s of sources) {
      const meta = [s.section, s.page ? `página ${s.page}` : null].filter(Boolean).join(", ");
      const score = typeof s.score === "number" && s.score > 0 ? `Relevância BM25: ${s.score.toFixed(2)}` : null;
      list.append(el("li", {}, [
        el("div", { class: "src-head" }, [
          el("span", { class: "src-doc", text: s.document, title: score }),
          meta ? el("span", { class: "src-meta", text: meta }) : null,
        ]),
        el("p", { class: "src-excerpt", text: s.excerpt }),
      ]));
    }
    const n = sources.length;
    return el("details", { class: "sources" }, [
      el("summary", {}, [icon("i-doc"), el("span", { text: n === 1 ? "1 fonte consultada" : `${n} fontes consultadas` })]),
      list,
    ]);
  }

  function buildAnswer(data) {
    const domain = domainOf(data.category);
    const head = el("div", { class: "bot-head" }, el("span", { class: "bot-name", text: data.agent || "Aurora" }));
    const st = STATUS[data.status];
    if (st) head.append(el("span", { class: `chip ${st[1]}`, text: st[0] }));

    const answer = el("div", { class: "answer" }, renderText(data.answer || ""));
    const bubble = el("div", { class: "bubble-bot" }, answer);
    const foot = el("div", { class: "bubble-foot reveal" });
    const mode = MODE[data.mode];
    if (mode) foot.append(el("p", { class: "mode" }, [icon(mode.icon), document.createTextNode(mode.text)]));
    if (data.notice) foot.append(el("p", { class: "notice", text: data.notice }));
    const src = sourcesBlock(data.sources);
    if (src) foot.append(src);
    if (foot.childElementCount) bubble.append(foot);

    const node = el("article", { class: "row row-bot", "data-domain": domain }, [
      avatar(domain), el("div", { class: "bot-body" }, [head, bubble]),
    ]);
    return { node, answer, bubble, foot };
  }

  function buildError(message, retryQuestion) {
    const bubble = el("div", { class: "bubble-bot" }, el("p", { class: "err" }, [icon("i-alert"), el("span", { text: message })]));
    const node = el("div", { class: "row row-bot row-error", "data-domain": "aurora", role: "alert" }, [
      avatar("aurora"),
      el("div", { class: "bot-body" }, [el("div", { class: "bot-head" }, el("span", { class: "bot-name", text: "Aurora" })), bubble]),
    ]);
    if (retryQuestion) {
      const btn = el("button", { class: "retry", type: "button", text: "Tentar novamente" });
      btn.addEventListener("click", () => {
        if (busy) return;
        node.remove();
        ask(retryQuestion, { echo: false });
      });
      bubble.append(btn);
    }
    return node;
  }

  // ---------------------------------------------------------------- digitação

  /** Revela o texto já renderizado, caractere a caractere. Clique ou Esc mostram tudo. */
  function typeOut(answerEl, bubble) {
    const items = [];
    const walker = document.createTreeWalker(answerEl, NodeFilter.SHOW_TEXT);
    while (walker.nextNode()) items.push({ node: walker.currentNode, text: walker.currentNode.nodeValue });
    const total = items.reduce((n, it) => n + it.text.length, 0);
    if (total === 0) return Promise.resolve();

    answerEl.querySelectorAll("p, li, ul, ol").forEach((b) => b.classList.add("pending"));
    for (const it of items) it.node.nodeValue = "";
    const caret = el("span", { class: "caret", "aria-hidden": "true" });
    const duration = Math.min(TYPE_MAX_MS, Math.max(TYPE_MIN_MS, total * MS_PER_CHAR));
    bubble.setAttribute("aria-busy", "true");
    bubble.title = "Clique para mostrar a resposta inteira";

    // setTimeout em vez de requestAnimationFrame: rAF pode ficar parado em janelas ocultas
    // ou webviews embutidos, e a resposta nunca apareceria. O progresso usa o tempo real.
    return new Promise((resolve) => {
      const start = performance.now();
      let timer = 0;
      let done = false;

      const show = (count) => {
        let left = count;
        let last = null;
        for (const it of items) {
          if (left <= 0) break;
          const take = Math.min(it.text.length, left);
          if (it.node.nodeValue.length !== take) it.node.nodeValue = it.text.slice(0, take);
          last = it.node;
          for (let p = it.node.parentElement; p && p !== answerEl; p = p.parentElement) p.classList.remove("pending");
          left -= take;
        }
        if (last && last.nextSibling !== caret) last.after(caret);
      };

      const finish = () => {
        if (done) return;
        done = true;
        clearTimeout(timer);
        for (const it of items) it.node.nodeValue = it.text;
        answerEl.querySelectorAll(".pending").forEach((b) => b.classList.remove("pending"));
        caret.remove();
        bubble.removeAttribute("aria-busy");
        bubble.removeAttribute("title");
        bubble.removeEventListener("click", finish);
        skipTyping = null;
        resolve();
      };

      const frame = () => {
        const stick = nearBottom();
        const progress = Math.min(1, (performance.now() - start) / duration); // velocidade constante
        show(Math.ceil(total * progress));
        if (stick) scrollToEnd();
        if (progress >= 1) finish();
        else timer = setTimeout(frame, 24);
      };

      skipTyping = finish;
      bubble.addEventListener("click", finish);
      frame();
    });
  }

  // ---------------------------------------------------------------- envio

  function setBusy(value) {
    busy = value;
    const n = input.value.trim().length;
    sendBtn.disabled = value || n === 0 || input.value.length > MAX_CHARS;
  }

  async function request(question) {
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
      if (res.ok) return { data: body };
      const detail = body && typeof body.detail === "string" ? body.detail : null;
      if (res.status === 422) return { error: detail || "A pergunta não pôde ser processada. Revise o texto e envie de novo." };
      return { error: detail || `O serviço respondeu com erro ${res.status}. Tente de novo em instantes.`, retry: true };
    } catch {
      if (controller.signal.reason === "reset") return { reset: true };
      if (controller.signal.reason === "timeout") return { error: "A resposta demorou mais de 60 segundos. Tente de novo.", retry: true };
      checkHealth();
      return { error: "Não foi possível conectar ao serviço. Verifique se a API está em execução e tente de novo.", retry: true };
    } finally {
      clearTimeout(timer);
      controller = null;
    }
  }

  async function ask(question, { echo = true } = {}) {
    if (busy) return; // impede envios duplicados
    const gen = generation;
    if (echo) addUser(question);
    setBusy(true);

    const typing = buildTyping();
    thread.append(typing.node);
    scrollToEnd();

    try {
      const started = performance.now();
      const result = await request(question);
      await sleep(Math.max(0, THINK_MS - (performance.now() - started)));
      if (gen !== generation || result.reset) return;

      if (result.error) {
        typing.node.replaceWith(buildError(result.error, result.retry ? question : null));
        scrollToEnd();
        return;
      }

      const data = result.data;
      thread.insertBefore(buildRoute(data), typing.node);
      if (data.category !== "indefinido") {
        typing.handOff(data.category, data.agent);
        scrollToEnd();
        await sleep(HANDOFF_MS);
        if (gen !== generation) return;
      }

      const view = buildAnswer(data);
      typing.node.replaceWith(view.node);
      scrollToEnd();
      await typeOut(view.answer, view.bubble);
      if (gen !== generation) return;
      setTimeout(() => view.foot.classList.add("is-in"), 0);
    } finally {
      if (gen === generation) setBusy(false);
    }
  }

  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const q = input.value.trim();
    if (!q || busy || q.length > MAX_CHARS) return;
    input.value = "";
    updateInput();
    ask(q);
  });

  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
      e.preventDefault();
      form.requestSubmit();
    }
  });

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && skipTyping) skipTyping();
  });

  function updateInput() {
    const n = input.value.length;
    counter.textContent = n > MAX_CHARS * 0.8 ? `${n}/${MAX_CHARS}` : "";
    counter.classList.toggle("over", n > MAX_CHARS);
    input.style.height = "auto";
    input.style.height = `${Math.min(input.scrollHeight, 168)}px`;
    setBusy(busy);
  }
  input.addEventListener("input", updateInput);

  document.querySelectorAll(".suggestion").forEach((btn) =>
    btn.addEventListener("click", () => ask(btn.textContent.trim())));

  $("#new-chat").addEventListener("click", () => {
    generation += 1;
    if (controller) controller.abort("reset");
    if (skipTyping) skipTyping();
    thread.querySelectorAll(".row, .route").forEach((n) => n.remove());
    welcome.hidden = false;
    input.value = "";
    setBusy(false);
    updateInput();
    scroller.scrollTop = 0;
    input.focus();
  });

  // ---------------------------------------------------------------- estado do serviço

  async function checkHealth() {
    try {
      const res = await fetch(`${API}/health`, { cache: "no-store" });
      const h = await res.json();
      if (h.status !== "ok") {
        statusEl.className = "status warn";
        statusEl.textContent = "Base de conhecimento não indexada";
        statusEl.title = "";
      } else if (h.llm && h.llm.configured) {
        statusEl.className = "status ok";
        statusEl.textContent = "Online com IA";
        statusEl.title = `Provedor: ${h.llm.provider}${h.llm.model ? `, modelo ${h.llm.model}` : ""}`;
      } else {
        statusEl.className = "status ok";
        statusEl.textContent = "Online, sem modelo de IA";
        statusEl.title = (h.llm && h.llm.reason) || "";
      }
    } catch {
      statusEl.className = "status down";
      statusEl.textContent = "Serviço indisponível";
      statusEl.title = "";
    }
  }

  async function loadCounts() {
    try {
      const st = await (await fetch(`${API}/api/knowledge/status`)).json();
      document.querySelectorAll(".agent").forEach((agent) => {
        const d = st.domains && st.domains[agent.dataset.domain];
        const out = agent.querySelector(".agent-count");
        if (!d || !out) return;
        out.textContent = `${d.chunks} trechos de ${d.documents} ${d.documents === 1 ? "arquivo" : "arquivos"}`;
        out.hidden = false;
      });
    } catch { /* indicador opcional */ }
  }

  updateInput();
  checkHealth();
  loadCounts();
})();
