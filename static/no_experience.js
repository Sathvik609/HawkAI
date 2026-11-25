(() => {
  const $ = (id) => document.getElementById(id);
  const MARKS = [["correct", "Correct"], ["wrong", "Wrong"]];
  const GROUPS = [["sample", "Random sample"], ["none", "No experience"], ["brief", "Brief mention only"]];
  const state = { reviewer: "", all: [], list: [], group: "sample", current: 0, marks: {}, drafts: {}, texts: {} };
  let clearTimer;

  const store = {
    get(key) { try { return localStorage.getItem(key) || ""; } catch { return ""; } },
    set(key, value) { try { localStorage.setItem(key, value); } catch { /* private mode */ } },
  };

  const entry = (nid) => state.marks[nid] || { mark: null, note: "" };

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

  async function save(nid, change) {
    const next = { ...entry(nid), ...change };
    try {
      const res = await fetchRetry("/api/no-experience/mark", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ reviewer: state.reviewer, narrative_id: nid, mark: next.mark, note: next.note }),
      });
      if (!res.ok) throw new Error((await res.json()).error || "Could not save");
      state.marks[nid] = next;
      showError("");
      renderNav();
      return true;
    } catch (err) {
      showError(`${err.message}. Please try again.`);
      return false;
    }
  }

  function inGroup(n, group) {
    return group === "sample" ? n.sample : n.group === group;
  }

  function renderGroups() {
    const box = $("groups");
    box.replaceChildren();
    GROUPS.forEach(([value, label]) => {
      const b = el("button", "nx-group");
      b.type = "button";
      b.setAttribute("role", "tab");
      b.setAttribute("aria-selected", String(state.group === value));
      b.append(el("span", null, label), el("span", "nx-group-count", String(state.all.filter((n) => inGroup(n, value)).length)));
      b.addEventListener("click", () => setGroup(value, 0));
      box.append(b);
    });
  }

  function renderNav() {
    const list = $("narrList");
    list.replaceChildren();
    state.list.forEach((n, i) => {
      const btn = el("button", "rv-item" + (i === state.current ? " current" : ""));
      btn.type = "button";
      if (i === state.current) btn.setAttribute("aria-current", "true");
      btn.append(el("span", "rv-num", String(i + 1)));
      const text = el("span", "rv-item-text");
      text.append(el("span", "rv-item-title", n.title));
      const mark = entry(n.id).mark;
      if (mark) text.append(el("span", `nx-item-mark ${mark}`, mark === "wrong" ? "Has an experience" : "Correct"));
      btn.append(text);
      btn.addEventListener("click", () => open(i));
      const li = el("li");
      li.append(btn);
      list.append(li);
    });
    const current = list.querySelector(".current");
    if (current) current.scrollIntoView({ block: "nearest" });
  }

  function setGroup(group, index) {
    state.group = group;
    state.list = state.all.filter((n) => inGroup(n, group));
    store.set("hawkai-noexp-group", group);
    renderGroups();
    open(index);
  }

  function renderMarks(nid) {
    const group = $("marks");
    group.replaceChildren();
    const current = entry(nid).mark;
    MARKS.forEach(([value, label]) => {
      const b = el("button", `rv-mark ${value}`, label);
      b.type = "button";
      b.setAttribute("role", "radio");
      b.setAttribute("aria-checked", String(current === value));
      b.addEventListener("click", async () => {
        const before = entry(nid).mark;
        const next = before === value ? null : value;
        const show = (m) => group.querySelectorAll(".rv-mark").forEach((x) => x.setAttribute("aria-checked", String(x.classList.contains(m))));
        show(next);
        if (!(await save(nid, { mark: next }))) show(before);
      });
      group.append(b);
    });
  }

  function noteBox(nid) {
    const textarea = $("note");
    const button = $("noteSave");
    const saved = () => entry(nid).note || "";
    textarea.value = nid in state.drafts ? state.drafts[nid] : saved();
    const empty = () => !textarea.value.trim();
    const refresh = () => { button.hidden = empty() || textarea.value === saved(); };
    const persist = async (value) => {
      if (await save(nid, { note: value }) && textarea.value === value) delete state.drafts[nid];
      refresh();
    };
    clearTimeout(clearTimer);
    textarea.oninput = () => {
      clearTimeout(clearTimer);
      if (textarea.value === saved()) delete state.drafts[nid];
      else state.drafts[nid] = textarea.value;
      if (empty() && saved()) {
        clearTimer = setTimeout(() => { if (empty() && saved()) persist(""); }, 1000);
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

  async function loadText(n) {
    const box = $("text");
    if (!state.texts[n.id]) {
      box.replaceChildren(el("p", null, "Loading the narrative..."));
      try {
        const res = await fetchRetry(`/api/no-experience/${encodeURIComponent(n.id)}`);
        const data = await res.json();
        if (!res.ok) throw new Error(data.error || "Could not load the narrative");
        state.texts[n.id] = data.text;
      } catch (err) {
        showError(err.message);
        return;
      }
    }
    if (state.list[state.current] !== n) return;
    box.replaceChildren(...state.texts[n.id].split(/\n\n+/).map((p) => el("p", null, p)));
  }

  function open(i) {
    if (!state.list.length) return;
    state.current = Math.max(0, Math.min(state.list.length - 1, i));
    const n = state.list[state.current];
    const label = GROUPS.find(([g]) => g === state.group)[1];
    $("pos").textContent = `${label} · ${state.current + 1} of ${state.list.length}`;
    $("title").textContent = n.title;
    const brief = $("brief");
    brief.hidden = !n.brief;
    brief.replaceChildren();
    if (n.brief) brief.append(el("strong", null, "Brief mention found: "), n.brief);
    renderMarks(n.id);
    noteBox(n.id);
    $("prevBtn").disabled = state.current === 0;
    $("nextBtn").hidden = state.current === state.list.length - 1;
    store.set("hawkai-noexp-current", String(state.current));
    renderNav();
    window.scrollTo({ top: 0 });
    loadText(n);
  }

  async function begin(name) {
    state.reviewer = name;
    showError("");
    try {
      const res = await fetchRetry(`/api/no-experience?reviewer=${encodeURIComponent(name)}`);
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "Could not load the narratives");
      state.all = data.narratives;
      state.marks = {};
      data.marks.forEach((m) => { state.marks[m.narrative_id] = { mark: m.mark, note: m.note || "" }; });
    } catch (err) {
      showError(err.message);
      return;
    }
    store.set("hawkai-reviewer", name);
    $("whoName").textContent = `Reviewing as ${name}`;
    $("who").hidden = false;
    $("start").hidden = true;
    $("workspace").hidden = false;
    const group = GROUPS.some(([g]) => g === store.get("hawkai-noexp-group")) ? store.get("hawkai-noexp-group") : "sample";
    setGroup(group, Number(store.get("hawkai-noexp-current")) || 0);
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
