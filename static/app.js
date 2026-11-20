(() => {
  "use strict";

  const params = new URLSearchParams(location.search);
  const MODE = params.get("mode") === "gemini" ? "gemini" : "hawkai";
  const PID = (params.get("pid") || "").slice(0, 64);
  const TOTAL = Number(document.body.dataset.total || 0);

  const $ = (id) => document.getElementById(id);
  const thread = $("thread");
  const input = $("input");
  const composer = $("composer");
  const sendBtn = $("sendBtn");
  const dockActions = $("dockActions");
  const newConceptBtn = $("newConceptBtn");
  const reader = $("reader");

  const SESSION = (() => {
    const make = () => (crypto.randomUUID ? crypto.randomUUID() : String(Date.now()) + Math.random().toString(16).slice(2));
    try {
      let id = sessionStorage.getItem("hawkai-session");
      if (!id) { id = make(); sessionStorage.setItem("hawkai-session", id); }
      return id;
    } catch { return make(); }
  })();

  const state = {
    phase: "start",
    query: "",
    concept: "",
    options: [],
    optionsList: null,
    narrative: null,
    initial: "",
    quote: "",
    history: [],
    controller: null,
  };

  function el(tag, attrs = {}, ...children) {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (v == null || v === false) continue;
      if (k === "class") node.className = v;
      else if (k === "text") node.textContent = v;
      else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
      else node.setAttribute(k, v === true ? "" : v);
    }
    for (const c of children.flat()) if (c != null) node.append(c);
    return node;
  }

  const nearBottom = () => window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 160;
  function follow(stick) { if (stick) window.scrollTo({ top: document.documentElement.scrollHeight }); }
  function reveal(node) {
    requestAnimationFrame(() => {
      const rect = node.getBoundingClientRect();
      const dock = $("dock").getBoundingClientRect().height;
      if (rect.top < 70 || rect.top > window.innerHeight - dock - 40) {
        window.scrollTo({ top: window.scrollY + rect.top - 80, behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
      }
    });
  }

  const SOURCE_TAGS = {
    "N+G": '<span class="src src-ng">Essay + general sociology</span>',
    "N": '<span class="src src-n">From the essay</span>',
    "G": '<span class="src src-g">General sociology</span>',
  };

  function visible(text) {
    const cut = text.indexOf("<<");
    return cut === -1 ? text : text.slice(0, cut);
  }

  function renderMarkdown(text) {
    const html = DOMPurify.sanitize(marked.parse(visible(text), { breaks: false, gfm: true }));
    return html.replace(/\[(N\+G|N|G)\]/g, (_, tag) => SOURCE_TAGS[tag]);
  }

  function noteText(topics) {
    if (!topics || !topics.length) return "";
    const list = topics.length < 3 ? topics.join(" and ") : `${topics.slice(0, -1).join(", ")} and ${topics.at(-1)}`;
    return `Content note: this essay discusses ${list}.`;
  }

  function status(text) {
    return el("div", { class: "status", role: "status" }, el("span", { class: "spinner", "aria-hidden": "true" }), el("span", { text }));
  }

  function errorBlock(message, retry) {
    return el("div", { class: "error", role: "alert" }, el("span", { text: message }),
      retry ? el("button", { type: "button", class: "text-btn", text: "Try again", onclick: (e) => { e.currentTarget.closest(".error").remove(); retry(); } }) : null);
  }

  function setBusy(busy) {
    document.body.classList.toggle("busy", busy);
    sendBtn.textContent = busy ? "Stop" : sendLabel();
    document.querySelectorAll(".concepts button, .suggest button").forEach((b) => (b.disabled = busy));
    $("otherStoriesBtn").disabled = busy;
    updateSend();
  }

  function updateSend() {
    const busy = document.body.classList.contains("busy");
    sendBtn.disabled = !busy && !input.value.trim();
  }

  function autosize() {
    input.style.height = "auto";
    input.style.height = Math.min(input.scrollHeight, 180) + "px";
  }

  const sendLabel = () => (MODE === "gemini" || state.phase === "reading" || state.phase === "chat" ? "Ask" : "Search");

  function setPhase(phase) {
    state.phase = phase;
    document.body.classList.toggle("started", phase !== "start");
    const slot = phase === "start" ? $("startSlot") : $("dockInner");
    if (composer.parentElement !== slot) slot.append(composer);
    if (!document.body.classList.contains("busy")) sendBtn.textContent = sendLabel();
    newConceptBtn.hidden = phase === "start";
    dockActions.hidden = !(MODE === "hawkai" && phase === "reading");
    const label = $("inputLabel");
    if (MODE === "gemini") {
      input.placeholder = phase === "start" ? "Type a concept, like anomie" : "Ask a follow-up question";
      label.textContent = "Message";
    } else if (phase === "reading") {
      input.placeholder = "Ask a question about this essay or the concept";
      label.textContent = "Follow-up question";
    } else {
      input.placeholder = phase === "choosing" ? "Search for a different concept" : "Type a concept, like anomie";
      label.textContent = "Concept to search for";
    }
  }

  async function postJSON(url, body, signal) {
    const res = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ...body, session_id: SESSION, pid: PID, mode: MODE }), signal });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || `The server answered with status ${res.status}.`);
    return data;
  }

  async function stream(url, body, { onDelta, signal }) {
    const res = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ...body, session_id: SESSION, pid: PID, mode: MODE }), signal });
    if (!res.ok || !res.body) {
      const data = await res.json().catch(() => ({}));
      throw new Error(data.error || `The server answered with status ${res.status}.`);
    }
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "", done = null;
    for (;;) {
      const { value, done: finished } = await reader.read();
      if (finished) break;
      buffer += decoder.decode(value, { stream: true });
      let nl;
      while ((nl = buffer.indexOf("\n")) !== -1) {
        const line = buffer.slice(0, nl).trim();
        buffer = buffer.slice(nl + 1);
        if (!line) continue;
        const evt = JSON.parse(line);
        if (evt.type === "delta") onDelta(evt.text);
        else if (evt.type === "done") done = evt;
        else if (evt.type === "error") throw new Error(evt.message);
      }
    }
    if (!done) throw new Error("The answer was cut off. Try again.");
    return done;
  }

  function liveRenderer(md) {
    let text = "", queued = false;
    md.classList.add("caret");
    return {
      push(piece) {
        text += piece;
        if (queued) return;
        queued = true;
        requestAnimationFrame(() => {
          queued = false;
          const stick = nearBottom();
          md.innerHTML = renderMarkdown(text);
          follow(stick);
        });
      },
      finish(finalText) {
        const stick = nearBottom();
        md.classList.remove("caret");
        md.innerHTML = renderMarkdown(finalText);
        follow(stick);
      },
      get text() { return text; },
    };
  }

  function suggestionsBlock(list) {
    if (!list || !list.length) return null;
    return el("div", { class: "suggest" }, el("p", { text: "Questions you could ask next" }),
      el("ul", {}, list.map((q) => el("li", {}, el("button", { type: "button", text: q, onclick: () => submit(q) })))));
  }

  function clearSuggestions() { thread.querySelectorAll(".suggest").forEach((s) => s.remove()); }

  function addUser(text) {
    const node = el("div", { class: "msg-user", text });
    thread.append(node);
    return node;
  }

  async function search(query) {
    state.query = query;
    clearSuggestions();
    setPhase("choosing");
    const box = el("div", { class: "msg-ai" });
    const wait = status(`Searching ${TOTAL || "the"} essays for “${query}”`);
    box.append(wait);
    thread.append(box);
    reveal(box);
    const slow = setTimeout(() => { wait.lastChild.textContent = "Picking the essays that show it best"; }, 2600);

    state.controller = new AbortController();
    setBusy(true);
    try {
      const data = await postJSON("/api/search", { query }, state.controller.signal);
      box.replaceChildren(optionsBlock(data));
    } catch (err) {
      if (err.name === "AbortError") box.replaceChildren(el("p", { class: "note", text: "Search stopped." }));
      else box.replaceChildren(errorBlock(err.message, () => { box.remove(); search(query); }));
    } finally {
      clearTimeout(slow);
      state.controller = null;
      setBusy(false);
    }
  }

  function optionsBlock(data) {
    if (!data.options || !data.options.length) {
      setPhase(state.narrative ? "reading" : "choosing");
      return el("p", { class: "note", text: data.message || `No essays matched “${state.query}”. Try another name for the concept.` });
    }
    data.options.forEach((o) => (o.concept = data.concept));
    state.concept = data.concept;
    state.options = data.options;
    const n = data.options.length;
    const list = el("ul", { class: "opt-list" }, data.options.map((o) =>
      el("li", {}, el("button", { type: "button", class: "opt", "aria-pressed": "false", "data-id": o.id, onclick: (e) => choose(o, e.currentTarget) },
        el("span", { class: "opt-title", text: o.title }),
        el("span", { class: "opt-time", text: `${Math.max(1, Math.round(o.words / 230))} min read` }),
        el("span", { class: "opt-preview", text: o.preview }),
        o.content_note && o.content_note.length ? el("span", { class: "opt-note", text: noteText(o.content_note) }) : null))));
    state.optionsList = list;
    return el("section", { class: "options" },
      el("h2", { text: data.concept }),
      data.definition ? el("p", { class: "definition", text: data.definition }) : null,
      el("p", { class: "count", text: n === 1 ? "1 essay by a past UNCW student. Choose it to read about it." : `${n} essays by past UNCW students. Choose one to read about it.` }),
      list);
  }

  function markChosen(list, id) {
    list.querySelectorAll(".opt").forEach((b) => {
      const chosen = b.dataset.id === id;
      b.setAttribute("aria-pressed", String(chosen));
    });
    list.classList.add("locked");
  }

  function choose(option, button) {
    if (document.body.classList.contains("busy")) return;
    if (state.narrative && state.narrative.id === option.id && state.phase === "reading") return;
    markChosen(button.closest(".opt-list"), option.id);
    state.concept = option.concept || state.concept;
    state.narrative = { id: option.id, title: option.title };
    state.history = [];
    state.initial = "";
    clearSuggestions();
    explain(option);
  }

  async function explain(option) {
    setPhase("reading");
    const md = el("div", { class: "md" });
    const head = el("div", { class: "story-head" },
      el("h3", { text: option.title }),
      el("button", { type: "button", class: "text-btn", text: "Read the full essay", onclick: () => openReader(option.id) }));
    const wait = status("Reading the essay");
    const note = option.content_note && option.content_note.length ? el("p", { class: "content-note", text: noteText(option.content_note) }) : null;
    const box = el("article", { class: "msg-ai" }, head, note, wait, md);
    thread.append(box);
    reveal(box);

    state.controller = new AbortController();
    setBusy(true);
    const live = liveRenderer(md);
    try {
      const done = await stream("/api/explain", { concept: state.concept, narrative_id: option.id }, {
        signal: state.controller.signal,
        onDelta: (t) => { wait.remove(); live.push(t); },
      });
      wait.remove();
      live.finish(done.text);
      state.initial = done.text;
      state.quote = done.quote || "";
      settleQuote(md, done.quote_check);
      const s = suggestionsBlock(done.suggestions);
      if (s) { const stick = nearBottom(); box.append(s); follow(stick); }
    } catch (err) {
      wait.remove();
      md.classList.remove("caret");
      if (err.name === "AbortError") { live.finish(live.text); state.initial = visible(live.text); }
      else box.append(errorBlock(err.message, () => { box.remove(); explain(option); }));
    } finally {
      state.controller = null;
      setBusy(false);
      input.focus({ preventScroll: true });
    }
  }

  function settleQuote(md, check) {
    const quote = md.querySelector("blockquote");
    if (!quote) return;
    if (check === "exact" || check === "close") {
      const note = el("div", { class: "quote-check" });
      note.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12.5l4.2 4.2L19 7" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/></svg>';
      note.append(check === "exact" ? "Matches the essay word for word" : "Matches the essay, with small wording differences");
      quote.after(note);
    }
  }

  async function chat(message) {
    clearSuggestions();
    const userNode = addUser(message);
    const md = el("div", { class: "md" });
    const wait = status("Writing an answer");
    const box = el("article", { class: "msg-ai" }, wait, md);
    thread.append(box);
    reveal(userNode);
    if (MODE === "gemini") setPhase("chat");

    state.controller = new AbortController();
    setBusy(true);
    const live = liveRenderer(md);
    const body = { message, history: state.history };
    if (MODE === "hawkai") Object.assign(body, { concept: state.concept, narrative_id: state.narrative.id, initial: state.initial });
    try {
      const done = await stream("/api/chat", body, { signal: state.controller.signal, onDelta: (t) => { wait.remove(); live.push(t); } });
      wait.remove();
      live.finish(done.text);
      state.history.push({ role: "user", text: message }, { role: "model", text: done.text });
      if (MODE === "hawkai" && /\[(N\+G|N|G)\]/.test(done.text) && !thread.querySelector(".legend")) {
        box.append(el("p", { class: "legend", text: "The labels show whether each point comes from the essay or from general sociology." }));
      }
      const s = suggestionsBlock(done.suggestions);
      if (s) { const stick = nearBottom(); box.append(s); follow(stick); }
    } catch (err) {
      wait.remove();
      md.classList.remove("caret");
      if (err.name === "AbortError") {
        live.finish(live.text);
        if (visible(live.text).trim()) state.history.push({ role: "user", text: message }, { role: "model", text: visible(live.text) });
      } else {
        box.append(errorBlock(err.message, () => { box.remove(); userNode.remove(); chat(message); }));
      }
    } finally {
      state.controller = null;
      setBusy(false);
      input.focus({ preventScroll: true });
    }
  }

  const normalise = (s) => s.replace(/[‘’]/g, "'").replace(/[“”]/g, '"');

  async function openReader(id) {
    $("readerTitle").textContent = state.options.find((o) => o.id === id)?.title || state.narrative?.title || "Essay";
    const body = $("readerBody");
    const readerNote = $("readerNote");
    readerNote.hidden = true;
    body.replaceChildren(status("Opening the essay"));
    reader.showModal();
    try {
      const res = await fetch(`/api/narrative/${encodeURIComponent(id)}`);
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "This essay could not be opened.");
      readerNote.textContent = noteText(data.content_note);
      readerNote.hidden = !readerNote.textContent;
      const quote = state.narrative?.id === id ? normalise(state.quote).trim() : "";
      const paragraphs = data.text.split(/\n+/).filter((p) => p.trim());
      let marked = null;
      body.replaceChildren(...paragraphs.map((p) => {
        const node = el("p");
        const at = quote ? normalise(p).indexOf(quote.slice(0, 80)) : -1;
        if (at !== -1 && !marked) {
          const end = Math.min(p.length, at + quote.length);
          marked = el("mark", { text: p.slice(at, end) });
          node.append(p.slice(0, at), marked, p.slice(end));
        } else node.textContent = p;
        return node;
      }));
      body.scrollTop = 0;
      if (marked) requestAnimationFrame(() => marked.scrollIntoView({ block: "center" }));
    } catch (err) {
      body.replaceChildren(errorBlock(err.message));
    }
  }
  $("readerClose").addEventListener("click", () => reader.close());
  reader.addEventListener("click", (e) => { if (e.target === reader) reader.close(); });

  function submit(text) {
    text = (text || "").trim();
    if (!text || document.body.classList.contains("busy")) return;
    input.value = "";
    autosize();
    updateSend();
    if (MODE === "gemini") return chat(text);
    if (state.phase === "reading") return chat(text);
    return search(text);
  }

  composer.addEventListener("submit", (e) => {
    e.preventDefault();
    if (document.body.classList.contains("busy")) { state.controller?.abort(); return; }
    submit(input.value);
  });
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); composer.requestSubmit(); }
  });
  input.addEventListener("input", () => { autosize(); updateSend(); });

  document.querySelectorAll(".concepts button").forEach((b) => b.addEventListener("click", () => submit(MODE === "gemini" ? `Help me understand ${b.dataset.q}.` : b.dataset.q)));

  $("otherStoriesBtn").addEventListener("click", () => {
    if (!state.optionsList) return;
    clearSuggestions();
    state.narrative = null;
    state.optionsList.classList.remove("locked");
    state.optionsList.querySelectorAll(".opt").forEach((b) => b.setAttribute("aria-pressed", "false"));
    setPhase("choosing");
    const section = state.optionsList.closest(".options");
    window.scrollTo({ top: window.scrollY + section.getBoundingClientRect().top - 80, behavior: "smooth" });
  });

  function reset() {
    state.controller?.abort();
    Object.assign(state, { query: "", concept: "", options: [], optionsList: null, narrative: null, initial: "", quote: "", history: [] });
    thread.replaceChildren();
    setPhase("start");
    window.scrollTo({ top: 0 });
    input.focus();
  }
  newConceptBtn.addEventListener("click", reset);
  $("homeLink").addEventListener("click", (e) => { e.preventDefault(); reset(); });

  if (MODE === "gemini") {
    document.title = "Sociology Assistant";
    $("homeLink").textContent = "Sociology Assistant";
    $("homeLink").removeAttribute("aria-label");
    $("heroTitle").textContent = "What concept are you studying?";
    $("heroLede").textContent = "Ask about any sociology concept, then keep asking follow-up questions.";
    $("conceptsIntro").textContent = "Or start with a concept from the course:";
  }

  setPhase("start");
  updateSend();
  input.focus();
})();
