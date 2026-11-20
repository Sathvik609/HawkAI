(() => {
  "use strict";

  const params = new URLSearchParams(location.search);
  const MODE = params.get("mode") === "gemini" ? "gemini" : "hawkai";
  const PID = (params.get("pid") || "").slice(0, 64);
  const TOTAL = Number(document.body.dataset.total || 0);

  const $ = (id) => document.getElementById(id);
  const VIEWS = { home: "viewHome", results: "viewResults", study: "viewStudy", talk: "viewTalk", chat: "viewChat" };

  const SESSION = (() => {
    const make = () => (crypto.randomUUID ? crypto.randomUUID() : String(Date.now()) + Math.random().toString(16).slice(2));
    try {
      let id = sessionStorage.getItem("hawkai-session");
      if (!id) { id = make(); sessionStorage.setItem("hawkai-session", id); }
      return id;
    } catch { return make(); }
  })();

  const state = {
    view: "home",
    query: "",
    concept: "",
    definition: "",
    options: [],
    current: -1,
    essays: {},
    chatHistory: [],
    controller: null,
  };

  function el(tag, attrs = {}, ...children) {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (v == null || v === false) continue;
      if (k === "class") node.className = v;
      else if (k === "text") node.textContent = v;
      else if (k === "html") node.innerHTML = v;
      else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
      else node.setAttribute(k, v === true ? "" : v);
    }
    for (const c of children.flat()) if (c != null) node.append(c);
    return node;
  }

  const ICON_NEXT = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 6l6 6-6 6"/></svg>';
  const SOURCE_TAGS = {
    "N+G": '<span class="src src-ng">Narrative + general sociology</span>',
    "N": '<span class="src src-n">From the narrative</span>',
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

  const SECTION_HELP = [
    [/narrative quote/i, "The student's own words, copied exactly from their narrative."],
    [/narrative context/i, "What was happening in the student's life, summarized from the narrative."],
    [/sociological explanation/i, "What the concept means in sociology, and how it shows up in this student's experience."],
  ];

  function decorate(md) {
    numberSteps(md);
    md.querySelectorAll("h3").forEach((h) => {
      const help = SECTION_HELP.find(([re]) => re.test(h.textContent));
      if (help && !h.nextElementSibling?.classList.contains("section-help")) h.after(el("p", { class: "section-help", text: help[1] }));
    });
  }

  function numberSteps(md) {
    md.querySelectorAll("h3").forEach((h) => {
      const list = h.nextElementSibling;
      if (!/narrative context/i.test(h.textContent) || !list || list.tagName !== "UL") return;
      const ol = el("ol", { class: "steps" }, [...list.children]);
      list.replaceWith(ol);
    });
  }


  function noteText(topics) {
    if (!topics || !topics.length) return "";
    return `Content note: this narrative discusses ${topicList(topics)}.`;
  }

  function topicList(topics) {
    return topics.length < 3 ? topics.join(" and ") : `${topics.slice(0, -1).join(", ")} and ${topics.at(-1)}`;
  }

  function status(text) {
    return el("div", { class: "status", role: "status" }, el("span", { class: "spinner", "aria-hidden": "true" }), el("span", { text }));
  }

  function errorBlock(message, retry) {
    return el("div", { class: "error", role: "alert" }, el("span", { text: message }),
      retry ? el("button", { type: "button", class: "link-btn", text: "Try again", onclick: (e) => { e.currentTarget.closest(".error").remove(); retry(); } }) : null);
  }

  const isBusy = () => document.body.classList.contains("busy");

  function setBusy(on) {
    document.body.classList.toggle("busy", on);
    $("sendBtn").textContent = on ? "Stop" : "Ask";
    $("sendBtn").classList.toggle("stop", on);
    document.querySelectorAll(".concept, .essay, .suggest button").forEach((b) => (b.disabled = on));
    updateButtons();
  }

  function updateButtons() {
    $("sendBtn").disabled = !isBusy() && !$("input").value.trim();
  }

  function begin() {
    const ctrl = new AbortController();
    state.controller = ctrl;
    setBusy(true);
    return ctrl;
  }

  function end(ctrl) {
    if (state.controller !== ctrl) return;
    state.controller = null;
    setBusy(false);
  }

  function stop() { state.controller?.abort(); }

  function show(view, push = true) {
    state.view = view;
    document.body.dataset.view = view;
    for (const [name, id] of Object.entries(VIEWS)) $(id).hidden = name !== view;
    $("dock").hidden = view !== "chat" && view !== "talk";
    $("barBackLabel").textContent = view === "talk" ? "Back to the narrative" : view === "study" ? state.concept : "Concepts";
    if (view === "talk") $("input").placeholder = "Ask about this narrative or the concept";
    if (push) history.pushState({ view, current: state.current }, "");
    window.scrollTo({ top: 0 });
  }

  window.addEventListener("popstate", (e) => {
    const s = e.state || { view: "home" };
    stop();
    if (s.view === "study" && state.options[s.current]) return openStudy(s.current, false);
    if (s.view === "talk" && state.options[s.current]) {
      state.current = s.current;
      if (state.essays[state.options[s.current].id]?.done) return openTalk(false);
      return openStudy(s.current, false);
    }
    if (s.view === "results" && !state.query) return show("home", false);
    show(s.view, false);
  });

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

  function scroller(box) {
    const isWindow = !box || getComputedStyle(box).overflowY === "visible";
    return {
      atEnd: () => isWindow ? window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 160
        : box.scrollTop + box.clientHeight >= box.scrollHeight - 80,
      toEnd: () => isWindow ? window.scrollTo({ top: document.documentElement.scrollHeight }) : (box.scrollTop = box.scrollHeight),
    };
  }

  function liveRenderer(md, { box = null, follow = true, after = null } = {}) {
    const sc = scroller(box);
    let text = "", frame = 0;
    md.classList.add("caret");
    const paint = (t) => {
      const stick = follow && sc.atEnd();
      md.innerHTML = renderMarkdown(t);
      if (after) after(md);
      if (stick) sc.toEnd();
    };
    return {
      push(piece) {
        text += piece;
        if (frame) return;
        frame = requestAnimationFrame(() => { frame = 0; paint(text); });
      },
      finish(finalText) {
        cancelAnimationFrame(frame);
        frame = 0;
        md.classList.remove("caret");
        paint(finalText);
      },
      get text() { return text; },
    };
  }

  function suggestionList(list, onPick) {
    return el("div", {}, el("p", { text: "Questions you could ask" }),
      el("ul", {}, list.map((q) => el("li", {}, el("button", { type: "button", text: q, onclick: () => onPick(q) })))));
  }

  async function search(query) {
    query = query.trim();
    if (!query) return;
    stop();
    $("homeInput").value = "";
    $("topInput").value = "";
    $("topInput").blur();
    Object.assign(state, { query, concept: "", definition: "", options: [], current: -1, essays: {} });
    $("rConcept").textContent = query;
    const wait = status(`Searching ${TOTAL || "the"} narratives for “${query}”`);
    $("rBody").replaceChildren(wait);
    show("results");
    const slow = setTimeout(() => { wait.lastChild.textContent = "Picking the narratives that show it best"; }, 2600);

    const ctrl = begin();
    try {
      const data = await postJSON("/api/search", { query }, ctrl.signal);
      renderResults(data);
    } catch (err) {
      if (err.name === "AbortError") $("rBody").replaceChildren(el("p", { class: "note", text: "Search stopped." }));
      else $("rBody").replaceChildren(errorBlock(err.message, () => search(query)));
    } finally {
      clearTimeout(slow);
      end(ctrl);
    }
  }

  function renderResults(data) {
    if (!data.options || !data.options.length) {
      $("rConcept").textContent = data.concept || state.query;
      $("rBody").replaceChildren(el("p", { class: "note", text: data.message || `No narratives matched “${state.query}”. Try another name for the concept.` }));
      return;
    }
    Object.assign(state, { concept: data.concept, definition: data.definition || "", options: data.options });
    $("rConcept").textContent = data.concept;
    const n = data.options.length;
    const side = el("aside", { class: "results-side" },
      el("div", { class: "definition" },
        data.definition ? el("p", { class: "label", text: "Definition" }) : null,
        data.definition ? el("p", { class: "def-text", text: data.definition }) : null,
        el("p", { class: "def-hint", text: "Choose a narrative to see a quote from it, its context, and a sociological explanation." })));
    $("rBody").replaceChildren(el("div", { class: "results-grid" }, side, el("div", { class: "results-main" },
      el("h2", { class: "results-count" }, `${n} ${n === 1 ? "narrative" : "narratives"} by UNCW students`),
      el("ul", { class: "essay-list" }, data.options.map((o, i) =>
        el("li", {}, el("button", { type: "button", class: "essay", "data-id": o.id, onclick: () => openStudy(i) },
          el("span", {},
            el("span", { class: "essay-title", text: o.title }),
            el("span", { class: "essay-preview", text: o.preview }),
            o.content_note && o.content_note.length ? el("span", { class: "essay-note", text: noteText(o.content_note) }) : null),
          el("span", { html: ICON_NEXT }).firstChild)))))));
  }

  function openStudy(i, push = true) {
    const o = state.options[i];
    if (!o) return;
    stop();
    state.current = i;
    const n = state.options.length;
    $("studyPos").textContent = `${i + 1} of ${n}`;
    pagerButton($("prevBtn"), state.options[i - 1], "Previous narrative");
    pagerButton($("nextBtn"), state.options[i + 1], "Next narrative");
    $("studyTitle").textContent = o.title;
    $("narrList").replaceChildren(...state.options.map((x, j) => el("li", {},
      el("button", { type: "button", class: j === i ? "current" : "", "aria-current": j === i ? "true" : null, onclick: () => j !== state.current && openStudy(j) },
        el("span", { class: "narr-num", text: String(j + 1) }),
        el("span", { class: "narr-text" }, el("span", { class: "narr-title", text: x.title }),
          x.content_note && x.content_note.length ? el("span", { class: "narr-note", text: `Content note: ${topicList(x.content_note)}` }) : null)))));
    $("studyDef").hidden = !state.definition;
    $("studyDefText").textContent = state.definition;
    $("sideNote").textContent = noteText(o.content_note);
    $("sideNote").hidden = !o.content_note || !o.content_note.length;
    $("convo").hidden = true;
    show("study", push);
    const entry = state.essays[o.id];
    if (entry && entry.done) renderStudy(entry);
    else explain(o);
  }

  function pagerButton(btn, o, label) {
    btn.hidden = !o;
    if (!o) return;
    const back = btn.id === "prevBtn";
    const arrow = `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="${back ? "M19 12H5M11 6l-6 6 6 6" : "M5 12h14M13 6l6 6-6 6"}"/></svg>`;
    const dir = el("span", { class: "pager-dir", html: back ? arrow + label : label + arrow });
    btn.replaceChildren(dir, el("span", { class: "pager-title", text: o.title }));
    if (o.content_note && o.content_note.length) btn.append(el("span", { class: "pager-note", text: `Content note: ${topicList(o.content_note)}` }));
  }

  function quoteRow(md) {
    const quote = md.querySelector("blockquote");
    if (!quote || !quote.querySelector("strong")) return;
    quote.after(el("p", { class: "quote-key" }, el("i", { "aria-hidden": "true" }),
      `Yellow marks the words that show ${state.concept} most clearly.`));
  }

  function renderStudy(entry) {
    const md = el("div", { class: "md", html: renderMarkdown(entry.text) });
    decorate(md);
    quoteRow(md);
    $("studyMain").replaceChildren(md);
    renderSuggestions(entry.suggestions);
    showConvo(entry);
  }

  async function explain(o) {
    const entry = state.essays[o.id] = { text: "", quote: "", quoteCheck: "", suggestions: [], history: [], done: false };
    const md = el("div", { class: "md" });
    const wait = status("Reading the narrative");
    $("studyMain").replaceChildren(wait, md);
    $("suggestBox").replaceChildren();

    const ctrl = begin();
    const live = liveRenderer(md, { follow: false, after: decorate });
    try {
      const done = await stream("/api/explain", { concept: state.concept, narrative_id: o.id }, {
        signal: ctrl.signal,
        onDelta: (t) => { wait.remove(); live.push(t); },
      });
      wait.remove();
      Object.assign(entry, { text: done.text, quote: done.quote || "", quoteCheck: done.quote_check, suggestions: done.suggestions || [], done: true });
      live.finish(done.text);
      quoteRow(md);
      if (state.options[state.current]?.id === o.id) { renderSuggestions(entry.suggestions); showConvo(entry); }
    } catch (err) {
      wait.remove();
      md.classList.remove("caret");
      if (err.name === "AbortError") { live.finish(live.text); entry.text = visible(live.text); }
      else { delete state.essays[o.id]; $("studyMain").append(errorBlock(err.message, () => explain(o))); }
    } finally {
      end(ctrl);
    }
  }

  function showConvo(entry) {
    $("talkBtn").textContent = entry.history.length ? "Return to the conversation" : "Ask your own question";
    $("talkBtn").append(el("span", { html: ICON_NEXT }).firstChild);
    $("convo").hidden = false;
  }

  function renderSuggestions(list) {
    const box = $("suggestBox");
    box.replaceChildren();
    if (list && list.length) box.append(suggestionList(list, (q) => askEssay(q)));
    if (isBusy()) box.querySelectorAll("button").forEach((b) => (b.disabled = true));
  }

  function legendFor(md) {
    return /src-/.test(md.innerHTML) ? el("p", { class: "legend", text: "Labels show whether each point comes from the narrative or from general sociology." }) : null;
  }

  function talkSuggestions(list) {
    return list && list.length ? el("div", { class: "suggest" }, suggestionList(list, askEssay)) : null;
  }

  function openTalk(push = true) {
    const o = state.options[state.current];
    const entry = o && state.essays[o.id];
    if (!entry || !entry.done) return;
    $("talkLabel").textContent = `Conversation about ${state.concept}`;
    $("talkTitle").textContent = o.title;
    const thread = $("talkThread");
    thread.replaceChildren();
    for (let i = 0; i + 1 < entry.history.length; i += 2) {
      const md = el("div", { class: "md", html: renderMarkdown(entry.history[i + 1].text) });
      thread.append(el("div", { class: "msg-user", text: entry.history[i].text }), el("article", {}, md, legendFor(md)));
    }
    const last = thread.lastElementChild;
    const sug = talkSuggestions(entry.suggestions);
    if (sug) (last || thread).append(sug);
    show("talk", push);
    if (last) last.scrollIntoView({ block: "end" });
    $("input").focus({ preventScroll: true });
  }

  async function askEssay(message) {
    message = message.trim();
    const o = state.options[state.current];
    const entry = o && state.essays[o.id];
    if (!message || !entry || !entry.done || isBusy()) return;
    if (state.view !== "talk") openTalk();
    const thread = $("talkThread");
    thread.querySelectorAll(".suggest").forEach((x) => x.remove());
    const user = el("div", { class: "msg-user", text: message });
    const md = el("div", { class: "md" });
    const wait = status("Writing an answer");
    const box = el("article", {}, wait, md);
    thread.append(user, box);
    user.scrollIntoView({ block: "start", behavior: "smooth" });

    const ctrl = begin();
    const live = liveRenderer(md);
    try {
      const done = await stream("/api/chat", { message, history: entry.history, concept: state.concept, narrative_id: o.id, initial: entry.text }, {
        signal: ctrl.signal,
        onDelta: (t) => { wait.remove(); live.push(t); },
      });
      wait.remove();
      live.finish(done.text);
      entry.history.push({ role: "user", text: message }, { role: "model", text: done.text });
      entry.suggestions = done.suggestions || [];
      box.append(...[legendFor(md), talkSuggestions(entry.suggestions)].filter(Boolean));
      renderSuggestions(entry.suggestions);
      showConvo(entry);
    } catch (err) {
      wait.remove();
      md.classList.remove("caret");
      if (err.name === "AbortError") {
        live.finish(live.text);
        if (visible(live.text).trim()) entry.history.push({ role: "user", text: message }, { role: "model", text: visible(live.text) });
      } else {
        box.append(errorBlock(err.message, () => { box.remove(); user.remove(); askEssay(message); }));
      }
    } finally {
      end(ctrl);
      $("input").focus({ preventScroll: true });
    }
  }

  async function chatGemini(message) {
    message = message.trim();
    if (!message || isBusy()) return;
    if (state.view !== "chat") show("chat");
    const thread = $("thread");
    thread.querySelectorAll(".suggest").forEach((s) => s.remove());
    const user = el("div", { class: "msg-user", text: message });
    const md = el("div", { class: "md" });
    const wait = status("Writing an answer");
    const box = el("article", {}, wait, md);
    thread.append(user, box);
    user.scrollIntoView({ block: "start", behavior: "smooth" });

    const ctrl = begin();
    const live = liveRenderer(md);
    try {
      const done = await stream("/api/chat", { message, history: state.chatHistory }, { signal: ctrl.signal, onDelta: (t) => { wait.remove(); live.push(t); } });
      wait.remove();
      live.finish(done.text);
      state.chatHistory.push({ role: "user", text: message }, { role: "model", text: done.text });
      if (done.suggestions && done.suggestions.length) box.append(el("div", { class: "suggest" }, suggestionList(done.suggestions, chatGemini)));
    } catch (err) {
      wait.remove();
      md.classList.remove("caret");
      if (err.name === "AbortError") {
        live.finish(live.text);
        if (visible(live.text).trim()) state.chatHistory.push({ role: "user", text: message }, { role: "model", text: visible(live.text) });
      } else {
        box.append(errorBlock(err.message, () => { box.remove(); user.remove(); chatGemini(message); }));
      }
    } finally {
      end(ctrl);
      $("input").focus({ preventScroll: true });
    }
  }

  const normalise = (s) => s.replace(/[‘’]/g, "'").replace(/[“”]/g, '"');
  const reader = $("reader");

  function paragraphs(text) {
    const lines = text.split("\n").map((l) => l.trim()).filter(Boolean);
    const lens = lines.map((l) => l.length).sort((x, y) => x - y);
    const median = lens[Math.floor(lens.length / 2)] || 0;
    if (median > 150) return lines;
    const out = [];
    let cur = "";
    for (const line of lines) {
      cur = !cur ? line : /[A-Za-z]-$/.test(cur) ? cur + line : `${cur} ${line}`;
      if (/[.!?]["”’)]?$/.test(line) && line.length < median * 0.8) { out.push(cur); cur = ""; }
    }
    if (cur) out.push(cur);
    return out;
  }

  async function openReader() {
    const o = state.options[state.current];
    if (!o) return;
    const entry = state.essays[o.id];
    $("readerTitle").textContent = o.title;
    const body = $("readerBody");
    const readerNote = $("readerNote");
    readerNote.hidden = true;
    body.replaceChildren(status("Opening the narrative"));
    reader.showModal();
    try {
      const res = await fetch(`/api/narrative/${encodeURIComponent(o.id)}`);
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "This narrative could not be opened.");
      readerNote.textContent = noteText(data.content_note);
      readerNote.hidden = !readerNote.textContent;
      const quote = normalise(entry?.quote || "").trim();
      let marked = null;
      body.replaceChildren(...paragraphs(data.text).map((p) => {
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

  function autosize(t) {
    t.style.height = "";
    if (t.value && t.scrollHeight) t.style.height = Math.min(t.scrollHeight, 160) + "px";
  }

  const CONCEPTS = (() => {
    try { return JSON.parse($("conceptNames").textContent); } catch { return []; }
  })();

  function matches(query) {
    const q = query.trim().toLowerCase();
    if (q.length < 2) return [];
    const scored = [];
    CONCEPTS.forEach((name, order) => {
      const n = name.toLowerCase();
      const at = n.indexOf(q);
      if (at === -1) return;
      const rank = at === 0 || n.startsWith("the " + q) ? 0 : n[at - 1] === " " || n[at - 1] === "-" ? 1 : 2;
      scored.push({ name, at, rank, order });
    });
    return scored.sort((a, b) => a.rank - b.rank || a.order - b.order).slice(0, 6);
  }

  function attachSuggestions(form, input, onPick) {
    const list = el("ul", { class: "ac-list", id: `${input.id}-list`, role: "listbox", "aria-label": "Suggested concepts", hidden: true });
    form.append(list);
    input.setAttribute("role", "combobox");
    input.setAttribute("aria-autocomplete", "list");
    input.setAttribute("aria-controls", list.id);
    input.setAttribute("aria-expanded", "false");
    let items = [], active = -1;

    const close = () => {
      list.hidden = true;
      items = [];
      active = -1;
      input.setAttribute("aria-expanded", "false");
      input.removeAttribute("aria-activedescendant");
    };
    const highlight = (i) => {
      active = i;
      [...list.children].forEach((li, j) => li.setAttribute("aria-selected", String(j === i)));
      if (i >= 0) input.setAttribute("aria-activedescendant", list.children[i].id);
      else input.removeAttribute("aria-activedescendant");
    };
    const pick = (name) => { close(); input.value = ""; onPick(name); };

    input.addEventListener("input", () => {
      items = matches(input.value);
      if (!items.length) return close();
      const qlen = input.value.trim().length;
      list.replaceChildren(...items.map((m, i) => el("li", {
        id: `${list.id}-${i}`, role: "option", "aria-selected": "false",
        onmousedown: (e) => { e.preventDefault(); pick(m.name); },
      }, m.name.slice(0, m.at), el("strong", { text: m.name.slice(m.at, m.at + qlen) }), m.name.slice(m.at + qlen))));
      list.hidden = false;
      input.setAttribute("aria-expanded", "true");
      highlight(-1);
    });
    input.addEventListener("keydown", (e) => {
      if (list.hidden) return;
      if (e.key === "ArrowDown") { e.preventDefault(); highlight((active + 1) % items.length); }
      else if (e.key === "ArrowUp") { e.preventDefault(); highlight(active <= 0 ? items.length - 1 : active - 1); }
      else if (e.key === "Escape") { e.preventDefault(); close(); }
      else if (e.key === "Enter" && active >= 0) { e.preventDefault(); pick(items[active].name); }
    });
    input.addEventListener("blur", close);
    form.addEventListener("submit", close);
  }

  function goHome() {
    stop();
    if (MODE === "gemini") { state.chatHistory = []; $("thread").replaceChildren(); }
    show("home");
    $("homeInput").focus({ preventScroll: true });
  }

  $("homeSearch").addEventListener("submit", (e) => {
    e.preventDefault();
    const q = $("homeInput").value.trim();
    if (!q) return $("homeInput").focus();
    if (MODE === "gemini") { $("homeInput").value = ""; return chatGemini(q); }
    search(q);
  });
  $("topSearch").addEventListener("submit", (e) => { e.preventDefault(); search($("topInput").value); });
  attachSuggestions($("homeSearch"), $("homeInput"), (name) => (MODE === "gemini" ? chatGemini(`Help me understand ${name}.`) : search(name)));
  attachSuggestions($("topSearch"), $("topInput"), search);
  document.querySelectorAll(".concept").forEach((b) => b.addEventListener("click", () =>
    MODE === "gemini" ? chatGemini(`Help me understand ${b.dataset.q}.`) : search(b.dataset.q)));

  $("homeLink").addEventListener("click", (e) => { e.preventDefault(); goHome(); });
  $("barBack").addEventListener("click", () => {
    stop();
    if (state.view === "talk") openStudy(state.current);
    else if (state.view === "study") show("results");
    else goHome();
  });
  $("talkBtn").addEventListener("click", () => openTalk());
  $("nextBtn").addEventListener("click", () => openStudy(state.current + 1));
  $("prevBtn").addEventListener("click", () => openStudy(state.current - 1));
  $("readFullBtn").addEventListener("click", openReader);
  $("readerClose").addEventListener("click", () => reader.close());
  reader.addEventListener("click", (e) => { if (e.target === reader) reader.close(); });

  for (const [form, input] of [[$("composer"), $("input")]]) {
    form.addEventListener("submit", (e) => {
      e.preventDefault();
      if (isBusy()) return stop();
      (state.view === "talk" ? askEssay : chatGemini)(input.value);
      input.value = "";
      autosize(input);
      updateButtons();
    });
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); form.requestSubmit(); }
    });
    input.addEventListener("input", () => { autosize(input); updateButtons(); });
  }

  if (MODE === "gemini") {
    document.title = "Sociology Assistant";
    $("homeLink").textContent = "Sociology Assistant";
    $("homeLink").removeAttribute("aria-label");
    $("heroLede").textContent = "Ask about any sociology concept, then keep asking follow-up questions.";
    $("homeInput").placeholder = "Ask about a concept, like anomie or stigma";
    $("homeSearch").querySelector("button").textContent = "Ask";
    document.querySelectorAll(".c-count").forEach((c) => (c.textContent = "Ask about it"));
  }

  history.replaceState({ view: "home" }, "");
  show("home", false);
  updateButtons();
  $("homeInput").focus();
})();
