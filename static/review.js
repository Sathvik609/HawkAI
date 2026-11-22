(() => {
  const $ = (id) => document.getElementById(id);
  const MARKS = [["correct", "Correct"], ["partly", "Partly"], ["wrong", "Wrong"]];
  const state = { reviewer: "", narratives: [], marks: {}, drafts: {}, current: 0 };
  const clearTimers = {};

  const store = {
    get(key) { try { return localStorage.getItem(key) || ""; } catch { return ""; } },
    set(key, value) { try { localStorage.setItem(key, value); } catch { /* private mode */ } },
  };

  const key = (nid, chunk) => `${nid}|${chunk}`;
  const entry = (nid, chunk) => state.marks[key(nid, chunk)] || { mark: null, note: "" };

  function el(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text;
    return node;
  }

  function showError(message) {
    $("error").textContent = message;
    $("error").hidden = !message;
  }

  async function save(nid, chunk, change) {
    const next = { ...entry(nid, chunk), ...change };
    try {
      const res = await fetchRetry("/api/review/mark", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ reviewer: state.reviewer, narrative_id: nid, chunk, mark: next.mark, note: next.note }),
      });
      if (!res.ok) throw new Error((await res.json()).error || "Could not save");
      state.marks[key(nid, chunk)] = next;
      showError("");
      return true;
    } catch (err) {
      showError(`${err.message}. Please try again.`);
      return false;
    }
  }

  function noteBox(textarea, button, nid, chunk) {
    const k = key(nid, chunk);
    const saved = () => entry(nid, chunk).note || "";
    textarea.value = k in state.drafts ? state.drafts[k] : saved();
    const empty = () => !textarea.value.trim();
    const refresh = () => { button.hidden = empty() || textarea.value === saved(); };
    const persist = async (value) => {
      if (await save(nid, chunk, { note: value }) && textarea.value === value) delete state.drafts[k];
      refresh();
    };
    textarea.oninput = () => {
      clearTimeout(clearTimers[k]);
      if (textarea.value === saved()) delete state.drafts[k];
      else state.drafts[k] = textarea.value;
      if (empty() && saved()) {
        clearTimers[k] = setTimeout(() => { if (empty() && saved()) persist(""); }, 1000);
      }
      refresh();
    };
    button.onclick = async () => {
      button.disabled = true;
      await persist(textarea.value);
      button.disabled = false;
    };
    refresh();
  }

  function renderNav() {
    const list = $("narrList");
    list.replaceChildren();
    state.narratives.forEach((n, i) => {
      const btn = el("button", "rv-item" + (i === state.current ? " current" : ""));
      btn.type = "button";
      if (i === state.current) btn.setAttribute("aria-current", "true");
      btn.append(el("span", "rv-num", String(i + 1)));
      const text = el("span", "rv-item-text");
      text.append(el("span", "rv-item-title", n.title));
      btn.append(text);
      btn.addEventListener("click", () => open(i));
      const li = el("li");
      li.append(btn);
      list.append(li);
    });
  }

  function chunkCard(n, c) {
    const card = el("article", `rv-chunk ${c.kind}`);
    const head = el("div", "rv-chunk-head");
    head.append(el("span", "rv-chunk-num", `Chunk ${c.chunk}`));
    head.append(el("span", `rv-kind ${c.kind}`, c.kind === "experience" ? "Experience" : "Reflection"));
    card.append(head);
    const words = el("div", "rv-words");
    c.text.split(/\n\n+/).forEach((para) => words.append(el("p", null, para)));
    card.append(words);

    const current = entry(n.id, c.chunk);

    const group = el("div", "rv-marks");
    group.setAttribute("role", "radiogroup");
    group.setAttribute("aria-label", `Is chunk ${c.chunk} right?`);
    MARKS.forEach(([value, label]) => {
      const b = el("button", `rv-mark ${value}`, label);
      b.type = "button";
      b.setAttribute("role", "radio");
      b.setAttribute("aria-checked", String(current.mark === value));
      b.addEventListener("click", async () => {
        const before = entry(n.id, c.chunk).mark;
        const next = before === value ? null : value;
        const show = (m) => group.querySelectorAll(".rv-mark").forEach((x) => x.setAttribute("aria-checked", String(x.classList.contains(m))));
        show(next);
        if (!(await save(n.id, c.chunk, { mark: next }))) show(before);
      });
      group.append(b);
    });

    const note = el("textarea", "rv-note");
    note.rows = 2;
    note.placeholder = "Note (optional): what should change?";
    note.setAttribute("aria-label", `Note for chunk ${c.chunk}`);
    const saveBtn = el("button", "btn-primary rv-save", "Save");
    saveBtn.type = "button";
    noteBox(note, saveBtn, n.id, c.chunk);

    const controls = el("div", "rv-controls");
    const row = el("div", "rv-controls-row");
    row.append(el("span", "rv-question", "Is this chunk right?"), group);
    controls.append(row, note, saveBtn);
    card.append(controls);
    return card;
  }

  function open(i) {
    state.current = Math.max(0, Math.min(state.narratives.length - 1, i));
    const n = state.narratives[state.current];
    $("pos").textContent = `Narrative ${state.current + 1} of ${state.narratives.length}`;
    $("title").textContent = n.title;
    $("chunks").replaceChildren(...n.chunks.map((c) => chunkCard(n, c)));
    noteBox($("overallNote"), $("overallSave"), n.id, 0);
    $("prevBtn").disabled = state.current === 0;
    $("nextBtn").hidden = state.current === state.narratives.length - 1;
    store.set("hawkai-review-current", String(state.current));
    renderNav();
    window.scrollTo({ top: 0 });
  }

  async function fetchRetry(url, options, tries = 4) {
    for (let i = 1; ; i++) {
      try {
        return await fetch(url, options);
      } catch (err) {
        if (i >= tries) throw new Error("Could not reach HawkAI. Please wait a moment and try again.");
        await new Promise((r) => setTimeout(r, 1500 * i));
      }
    }
  }

  async function begin(name) {
    state.reviewer = name;
    showError("");
    try {
      const res = await fetchRetry(`/api/review?reviewer=${encodeURIComponent(name)}`);
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "Could not load the review");
      state.narratives = data.narratives;
      state.marks = {};
      data.marks.forEach((m) => { state.marks[key(m.narrative_id, m.chunk)] = { mark: m.mark, note: m.note || "" }; });
    } catch (err) {
      showError(err.message);
      return;
    }
    store.set("hawkai-reviewer", name);
    $("whoName").textContent = `Reviewing as ${name}`;
    $("who").hidden = false;
    $("start").hidden = true;
    $("workspace").hidden = false;
    open(Number(store.get("hawkai-review-current")) || 0);
  }

  $("nameForm").addEventListener("submit", (e) => {
    e.preventDefault();
    const name = $("nameInput").value.trim();
    if (name) begin(name);
  });
  $("switchBtn").addEventListener("click", () => {
    store.set("hawkai-reviewer", "");
    $("workspace").hidden = true;
    $("who").hidden = true;
    $("start").hidden = false;
    $("nameInput").value = "";
    $("nameInput").focus();
  });
  $("prevBtn").addEventListener("click", () => open(state.current - 1));
  $("nextBtn").addEventListener("click", () => open(state.current + 1));

  const saved = store.get("hawkai-reviewer");
  if (saved) begin(saved);
  else $("start").hidden = false;
})();
