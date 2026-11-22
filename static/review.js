(() => {
  const $ = (id) => document.getElementById(id);
  const MARKS = [["correct", "Correct"], ["partly", "Partly"], ["wrong", "Wrong"]];
  const state = { reviewer: "", narratives: [], marks: {}, current: 0 };
  const timers = {};

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

  async function save(nid, chunk, statusEl) {
    const { mark, note } = entry(nid, chunk);
    statusEl.textContent = "Saving";
    try {
      const res = await fetch("/api/review/mark", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ reviewer: state.reviewer, narrative_id: nid, chunk, mark, note }),
      });
      if (!res.ok) throw new Error((await res.json()).error || "Could not save");
      statusEl.textContent = "Saved";
      showError("");
    } catch (err) {
      statusEl.textContent = "Not saved";
      showError(`${err.message}. Check your connection and try again; your last change is still on screen.`);
    }
    renderNav();
  }

  function update(nid, chunk, change, statusEl, delay = 0) {
    state.marks[key(nid, chunk)] = { ...entry(nid, chunk), ...change };
    clearTimeout(timers[key(nid, chunk)]);
    timers[key(nid, chunk)] = setTimeout(() => save(nid, chunk, statusEl), delay);
  }

  function marked(n) {
    return n.chunks.filter((c) => entry(n.id, c.chunk).mark).length;
  }

  function renderNav() {
    const list = $("narrList");
    list.replaceChildren();
    let done = 0;
    let all = 0;
    state.narratives.forEach((n, i) => {
      const count = marked(n);
      done += count;
      all += n.chunks.length;
      const btn = el("button", "rv-item" + (i === state.current ? " current" : ""));
      btn.type = "button";
      if (i === state.current) btn.setAttribute("aria-current", "true");
      btn.append(el("span", "rv-num", String(i + 1)));
      const text = el("span", "rv-item-text");
      text.append(el("span", "rv-item-title", n.title));
      text.append(el("span", "rv-item-count" + (count === n.chunks.length ? " done" : ""),
        count === n.chunks.length ? "All chunks marked" : `${count} of ${n.chunks.length} marked`));
      btn.append(text);
      btn.addEventListener("click", () => open(i));
      const li = el("li");
      li.append(btn);
      list.append(li);
    });
    $("total").textContent = `${done} of ${all} chunks marked`;
  }

  function chunkCard(n, c) {
    const card = el("article", `rv-chunk ${c.kind}`);
    const head = el("div", "rv-chunk-head");
    head.append(el("span", "rv-chunk-num", `Chunk ${c.chunk}`));
    head.append(el("span", `rv-kind ${c.kind}`, c.kind === "experience" ? "Experience" : "Reflection"));
    card.append(head);
    card.append(el("h3", "rv-label", c.label));
    card.append(el("p", "rv-what", c.what_happened));

    if (c.kind === "experience") {
      const facts = el("dl", "rv-facts");
      [["Feelings", c.feelings], ["People", c.people], ["Life area", c.life_area]].forEach(([k, v]) => {
        const row = el("div");
        row.append(el("dt", null, k), el("dd", null, v));
        facts.append(row);
      });
      card.append(facts);
    }

    const words = el("div", "rv-words");
    words.append(el("p", "rv-words-label", "The student's words"));
    c.text.split(/\n\n+/).forEach((para) => words.append(el("p", null, para)));
    card.append(words);

    const status = el("p", "rv-saved");
    status.setAttribute("aria-live", "polite");
    const current = entry(n.id, c.chunk);

    const group = el("div", "rv-marks");
    group.setAttribute("role", "radiogroup");
    group.setAttribute("aria-label", `Is chunk ${c.chunk} right?`);
    MARKS.forEach(([value, label]) => {
      const b = el("button", `rv-mark ${value}`, label);
      b.type = "button";
      b.setAttribute("role", "radio");
      b.setAttribute("aria-checked", String(current.mark === value));
      b.addEventListener("click", () => {
        const next = entry(n.id, c.chunk).mark === value ? null : value;
        group.querySelectorAll(".rv-mark").forEach((x) => x.setAttribute("aria-checked", String(x === b && next !== null)));
        update(n.id, c.chunk, { mark: next }, status);
      });
      group.append(b);
    });

    const note = el("textarea", "rv-note");
    note.rows = 2;
    note.placeholder = "Note (optional): what should change?";
    note.value = current.note || "";
    note.setAttribute("aria-label", `Note for chunk ${c.chunk}`);
    note.addEventListener("input", () => update(n.id, c.chunk, { note: note.value }, status, 700));

    const controls = el("div", "rv-controls");
    const row = el("div", "rv-controls-row");
    row.append(el("span", "rv-question", "Is this chunk right?"), group, status);
    controls.append(row, note);
    card.append(controls);
    return card;
  }

  const LEVELS = {
    clear: "has a clear lived experience",
    brief: "mentions a lived experience only briefly",
    none: "has no lived experience",
  };

  function renderCheck(n) {
    const box = $("check");
    if (!n.experience_level) { box.hidden = true; return; }
    const parts = [el("p", "rv-check-line", `Whole narrative: HawkAI found that it ${LEVELS[n.experience_level] || n.experience_level}.`)];
    if (n.experience_evidence) {
      parts.push(el("p", "rv-check-label", "The sentence that shows it"), el("p", "rv-check-quote", n.experience_evidence));
    }
    box.replaceChildren(...parts);
    box.hidden = false;
  }

  function open(i) {
    state.current = Math.max(0, Math.min(state.narratives.length - 1, i));
    const n = state.narratives[state.current];
    $("pos").textContent = `Narrative ${state.current + 1} of ${state.narratives.length}`;
    $("title").textContent = n.title;
    renderCheck(n);
    $("chunks").replaceChildren(...n.chunks.map((c) => chunkCard(n, c)));
    const overall = $("overallNote");
    overall.value = entry(n.id, 0).note || "";
    overall.oninput = () => update(n.id, 0, { note: overall.value }, $("overallSaved"), 700);
    $("overallSaved").textContent = "";
    $("prevBtn").disabled = state.current === 0;
    $("nextBtn").hidden = state.current === state.narratives.length - 1;
    store.set("hawkai-review-current", String(state.current));
    renderNav();
    window.scrollTo({ top: 0 });
  }

  async function begin(name) {
    state.reviewer = name;
    showError("");
    try {
      const res = await fetch(`/api/review?reviewer=${encodeURIComponent(name)}`);
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
