// 私人工作助理前端：原生 JS，无依赖。所有用户内容用 textContent 渲染，防止 XSS。
"use strict";

const $ = (sel) => document.querySelector(sel);
const PRI_CN = { high: "高", medium: "中", low: "低" };
const STATUS_CN = { todo: "待办", doing: "进行中", done: "已完成" };
const REM_CN = { pending: "待提醒", sent: "已提醒", cancelled: "已取消" };
let today = "";
let integ = {};

// ---------- 通用 ----------
async function api(method, path, body) {
  const opts = { method, headers: {} };
  if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(path, opts);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || "请求失败 (" + res.status + ")");
  return data;
}

function toast(msg, kind) {
  const t = $("#toast");
  t.textContent = msg;
  t.className = "toast" + (kind === "info" ? " info" : "");
  t.hidden = false;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => (t.hidden = true), kind === "info" ? 8000 : 4000);
}

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v);
  }
  for (const c of children) {
    if (c == null || c === false) continue;
    node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return node;
}

function renderList(listEl, items, renderItem, emptyText) {
  listEl.replaceChildren();
  if (!items.length) {
    listEl.append(el("li", { class: "empty" }, emptyText));
    return;
  }
  items.forEach((it) => listEl.append(renderItem(it)));
}

function loading(listEl, text) {
  listEl.replaceChildren(el("li", { class: "empty" }, text || "加载中…"));
}

async function guarded(fn) {
  try { await fn(); } catch (e) { toast(e.message); }
}

function delBtn(label, onConfirm, confirmText) {
  return el("button", {
    type: "button", class: "link danger", "aria-label": label,
    onclick: () => guarded(async () => {
      if (confirmText && !confirm(confirmText)) return;
      await onConfirm();
    }),
  }, label.split("：")[0]);
}

function pad(n) { return String(n).padStart(2, "0"); }
function localInputValue(d) {
  return d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate()) +
    "T" + pad(d.getHours()) + ":" + pad(d.getMinutes());
}

const presetActions = {
  add_note: "保存笔记", search_notes: "搜索笔记", add_task: "添加任务",
  list_tasks: "查询任务", add_worklog: "记录工作日志", daily_summary: "读取日报数据",
};

async function loadPresets() {
  const items = await api("GET", "/api/tool-presets");
  renderList($("#presets"), items, (item) => {
    const pending = item.state === "pending";
    const buttons = el("div", { class: "actions" });
    function decision(label, action) {
      return el("button", { type: "button", onclick: () => guarded(async () => {
        await api("POST", "/api/tool-presets/" + item.id + "/" + action, {});
        await loadPresets();
        toast({ approve: "已启用，可在普通对话中调用", reject: "已拒绝草案", revoke: "已撤销工具" }[action], "info");
      }) }, label);
    }
    if (pending) {
      const replaces = items.some((other) => other.state === "active" && other.name === item.name);
      buttons.append(decision(replaces ? "批准并替换同名工具" : "批准启用", "approve"), decision("拒绝", "reject"));
    } else {
      buttons.append(decision("撤销", "revoke"));
    }
    return el("li", {}, el("div", { class: "main" },
      el("strong", {}, item.name), el("span", { class: "muted" }, pending ? " · 待审批" : " · 已启用"),
      el("div", {}, item.description),
      el("div", {}, "基础动作 · " + presetActions[item.action]),
      el("pre", {}, "固定参数 " + JSON.stringify(item.fixed, null, 2)), buttons));
  }, "还没有工具草案");
}

$("#tool-studio").addEventListener("toggle", () => {
  if ($("#tool-studio").open) {
    guarded(loadPresets);
  } else if ($("#developing").checked) {
    $("#developing").checked = false;
    $("#developing").dispatchEvent(new Event("change"));
  }
});
$("#developing").addEventListener("change", () => {
  $("#chat-send").textContent = $("#developing").checked ? "设计" : "发送";
  $("#chat-input").placeholder = $("#developing").checked ? "描述工具的用途和固定参数…" : "输入消息，Enter 发送，Shift+Enter 换行";
  $("#chat-input").focus();
});

// ---------- 状态 ----------
async function loadStatus() {
  const s = await api("GET", "/api/status");
  today = s.today;
  integ = s.integrations || {};
  if (integ.calendar_local) $("#calendar-hint").textContent = "日程保存在本机。可以在对话中让助理新建日程。";
  const st = $("#status");
  const chip = (text, on) => el("span", { class: on ? "on" : "off" }, text);
  st.replaceChildren(
    chip(s.llm_ready ? "模型 " + s.model : "未配置 API Key", s.llm_ready),
    chip("日历" + (integ.calendar ? " ✓" : " ✗"), integ.calendar),
    chip("邮箱" + (integ.email ? " ✓" : " 未配置"), integ.email),
    chip("微信" + (integ.wechat ? " ✓" : " 未配置"), integ.wechat),
    chip("搜索 " + integ.search, true),
  );
  $("#email-off").hidden = !!integ.email;
  $("#inbox-box").hidden = !integ.email;
  $("#rem-wechat-hint").textContent = integ.wechat ? "，并推送到微信" : "";
  $("#wechat-state").textContent = integ.wechat
    ? "已配置，提醒会同时推送到微信。"
    : "未配置。在 .env 填写 SERVERCHAN_KEY（推送到个人微信）或 WECOM_WEBHOOK_URL（企业微信群）后重启。";
  $("#btn-wechat-test").hidden = !integ.wechat;
}

// ---------- 任务 ----------
async function loadTasks() {
  const status = $("#task-filter").value;
  const tasks = await api("GET", "/api/tasks?status=" + encodeURIComponent(status));
  renderList($("#task-list"), tasks, (t) => {
    const overdue = t.status !== "done" && t.due_date && t.due_date < today;
    const box = el("input", {
      type: "checkbox", "aria-label": "标记完成：" + t.title,
      onchange: () => guarded(async () => {
        await api("PATCH", "/api/tasks/" + t.id, { status: box.checked ? "done" : "todo" });
        loadTasks();
      }),
    });
    box.checked = t.status === "done";
    return el("li", { class: t.status === "done" ? "done" : "" },
      box,
      el("div", { class: "main" },
        el("div", { class: "title" }, t.title),
        el("div", { class: "meta" },
          el("span", { class: "pri-" + t.priority }, "优先级 " + PRI_CN[t.priority]),
          el("span", {}, STATUS_CN[t.status]),
          t.due_date && el("span", { class: overdue ? "overdue" : "" },
            "截止 " + t.due_date + (overdue ? "（逾期）" : "")),
          t.project && el("span", {}, "#" + t.project),
        ),
      ),
      delBtn("删除：" + t.title, async () => {
        await api("DELETE", "/api/tasks/" + t.id);
        loadTasks();
      }, "删除任务「" + t.title + "」？"),
    );
  }, "没有任务");
}

$("#task-form").addEventListener("submit", (e) => {
  e.preventDefault();
  guarded(async () => {
    await api("POST", "/api/tasks", {
      title: $("#task-title").value,
      priority: $("#task-priority").value,
      due_date: $("#task-due").value,
    });
    $("#task-title").value = "";
    $("#task-due").value = "";
    loadTasks();
  });
});
$("#task-filter").addEventListener("change", () => guarded(loadTasks));

// ---------- 团队（多智能体） ----------
let teamMeta = { agents: [], templates: [], tool_groups: [] };
let editingAgentId = null;

function groupLabel(id) {
  const g = teamMeta.tool_groups.find((x) => x.id === id);
  return g ? g.label : id;
}

function openAgentForm(agent) {
  editingAgentId = agent && agent.id ? agent.id : null;
  $("#agent-form-title").textContent = editingAgentId ? "编辑：" + agent.name : "新建智能体";
  $("#agent-name").value = (agent && agent.name) || "";
  $("#agent-role").value = (agent && agent.role) || "";
  $("#agent-instructions").value = (agent && agent.instructions) || "";
  const chosen = new Set((agent && agent.tool_groups) || []);
  const box = $("#agent-groups");
  box.replaceChildren();
  teamMeta.tool_groups.forEach((g) => {
    const cb = el("input", { type: "checkbox", value: g.id });
    cb.checked = chosen.has(g.id);
    box.append(el("label", { class: g.available ? "" : "unavailable" },
      cb, g.label + (g.available ? "" : "（未配置）")));
  });
  $("#agent-form").hidden = false;
  $("#agent-new").hidden = true;
  $("#agent-name").focus();
}

function closeAgentForm() {
  $("#agent-form").hidden = true;
  $("#agent-new").hidden = false;
  editingAgentId = null;
}

async function loadTeam() {
  teamMeta = await api("GET", "/api/agents");
  const tpl = $("#agent-templates");
  tpl.replaceChildren();
  teamMeta.templates.forEach((t) => {
    const exists = teamMeta.agents.some((a) => a.name === t.name);
    const b = el("button", {
      type: "button", class: "ghost small",
      onclick: () => openAgentForm(Object.assign({}, t, { id: null })),
    }, t.name);
    b.disabled = exists;
    if (exists) b.title = "已经创建";
    tpl.append(b);
  });
  renderList($("#agent-list"), teamMeta.agents, (a) => {
    const toggle = el("input", {
      type: "checkbox", "aria-label": "启用：" + a.name,
      onchange: () => guarded(async () => {
        await api("PATCH", "/api/agents/" + a.id, { enabled: toggle.checked });
        loadTeam();
      }),
    });
    toggle.checked = a.enabled;
    return el("li", { class: a.enabled ? "" : "agent-off" },
      toggle,
      el("div", { class: "main" },
        el("div", { class: "title" }, el("strong", {}, a.name), "　" + a.role),
        el("div", { class: "meta" },
          el("span", {}, "工具：" + (a.tool_groups.map(groupLabel).join("、") || "无")),
          !a.enabled && el("span", {}, "已停用"),
        ),
        a.instructions && el("div", { class: "note-content" }, a.instructions),
      ),
      el("div", { class: "btns" },
        el("button", {
          type: "button", class: "link", "aria-label": "点名：" + a.name,
          onclick: () => {
            $("#chat-input").value = "@" + a.name + " ";
            $("#chat-input").focus();
          },
        }, "@它"),
        el("button", {
          type: "button", class: "link", "aria-label": "编辑：" + a.name,
          onclick: () => openAgentForm(a),
        }, "编辑"),
        delBtn("删除：" + a.name, async () => {
          await api("DELETE", "/api/agents/" + a.id);
          loadTeam();
        }, "删除智能体「" + a.name + "」？"),
      ),
    );
  }, "还没有成员。点上面的模板快速创建，或者对 AI 说「帮我建一个负责……的智能体」。");
}

$("#agent-new").addEventListener("click", () => openAgentForm(null));
$("#agent-cancel").addEventListener("click", closeAgentForm);
$("#agent-form").addEventListener("submit", (e) => {
  e.preventDefault();
  guarded(async () => {
    const body = {
      name: $("#agent-name").value.trim(),
      role: $("#agent-role").value.trim(),
      instructions: $("#agent-instructions").value.trim(),
      tool_groups: Array.from(document.querySelectorAll("#agent-groups input:checked")).map((c) => c.value),
    };
    if (editingAgentId) await api("PATCH", "/api/agents/" + editingAgentId, body);
    else await api("POST", "/api/agents", body);
    closeAgentForm();
    loadTeam();
  });
});

// 协作过程的文字描述
function describeEvent(ev) {
  if (ev.type === "delegate") {
    const task = ev.task.length > 60 ? ev.task.slice(0, 60) + "…" : ev.task;
    return ev.from + " 交给 " + ev.to + "：" + task;
  }
  if (ev.type === "tool") return ev.agent + " " + (TOOL_CN[ev.name] || ev.name);
  if (ev.type === "result") return ev.agent + " 完成";
  return "";
}

// ---------- 提醒 ----------
async function loadReminders() {
  const rems = await api("GET", "/api/reminders?status=" + $("#rem-filter").value);
  renderList($("#rem-list"), rems, (r) => el("li", { class: r.status === "pending" ? "" : "done" },
    el("div", { class: "main" },
      el("div", { class: "title" }, r.content),
      el("div", { class: "meta" },
        el("span", {}, "⏰ " + r.remind_at),
        el("span", { class: "badge " + r.status }, REM_CN[r.status]),
      ),
    ),
    r.status === "pending" && delBtn("取消：" + r.content, async () => {
      await api("DELETE", "/api/reminders/" + r.id);
      loadReminders();
    }, "取消提醒「" + r.content + "」？"),
  ), "没有待提醒的事项。可以对 AI 说「下午3点提醒我开会」。");
}

$("#rem-form").addEventListener("submit", (e) => {
  e.preventDefault();
  guarded(async () => {
    await api("POST", "/api/reminders", {
      content: $("#rem-content").value,
      remind_at: $("#rem-at").value,
    });
    $("#rem-content").value = "";
    toast("提醒已添加", "info");
    loadReminders();
  });
});
$("#rem-filter").addEventListener("change", () => guarded(loadReminders));

function setupNotifyButton() {
  const btn = $("#btn-notify");
  if (!("Notification" in window) || Notification.permission !== "default") return;
  btn.hidden = false;
  btn.addEventListener("click", async () => {
    await Notification.requestPermission();
    btn.hidden = true;
  });
}

async function checkReminders() {
  let fired = [];
  try { fired = await api("GET", "/api/reminders/unseen"); } catch (e) { return; }
  fired.forEach((r) => {
    toast("⏰ 提醒：" + r.content, "info");
    addMsg("bot", "⏰ 提醒（" + r.remind_at + "）：" + r.content);
    if ("Notification" in window && Notification.permission === "granted") {
      new Notification("私人工作助理 · 提醒", { body: r.content });
    }
  });
  if (fired.length && currentTab === "reminders") guarded(loadReminders);
}

// ---------- 日程 ----------
async function loadCalendar() {
  if (!integ.calendar) {
    loading($("#cal-list"), "当前系统暂不支持日历");
    return;
  }
  loading($("#cal-list"), "正在读取日历…（日历事件多时需要十几秒）");
  const date = $("#cal-date").value || today;
  const evs = await api("GET", "/api/calendar?date=" + date + "&days=" + $("#cal-days").value);
  renderList($("#cal-list"), evs, (ev) => {
    const start = ev.start.replace("T", " ").slice(5, 16);
    const end = ev.end.replace("T", " ").slice(11, 16);
    return el("li", {},
      el("div", { class: "main" },
        el("div", { class: "title" }, ev.title || "（无标题）"),
        el("div", { class: "meta" },
          el("span", {}, ev.all_day ? start.slice(0, 5) + " 全天" : start + " – " + end),
          el("span", {}, "📅 " + ev.calendar),
          ev.location && el("span", {}, "📍 " + ev.location),
        ),
      ),
    );
  }, "这段时间没有日程");
}
$("#cal-load").addEventListener("click", () => guarded(loadCalendar));

// ---------- 邮件 ----------
async function loadDrafts() {
  const drafts = await api("GET", "/api/email/drafts");
  renderList($("#draft-list"), drafts, (d) => el("li", {},
    el("div", { class: "main" },
      el("div", { class: "title" }, el("strong", {}, d.subject)),
      el("div", { class: "meta" }, el("span", {}, "收件人：" + d.to_addr)),
      el("div", { class: "mail-body" }, d.body),
    ),
    el("div", { class: "btns" },
      el("button", {
        type: "button", "aria-label": "确认发送：" + d.subject,
        onclick: (ev) => guarded(async () => {
          if (!confirm("确认把「" + d.subject + "」发送给 " + d.to_addr + "？")) return;
          ev.target.disabled = true;
          try {
            await api("POST", "/api/email/drafts/" + d.id + "/send", {});
            toast("邮件已发送", "info");
          } finally { ev.target.disabled = false; }
          loadDrafts();
        }),
      }, "确认发送"),
      delBtn("放弃：" + d.subject, async () => {
        await api("DELETE", "/api/email/drafts/" + d.id);
        loadDrafts();
      }, "放弃这封草稿？"),
    ),
  ), "没有待发送的草稿。可以对 AI 说「帮我给 xx@xx.com 写封邮件……」。");
}

async function loadInbox() {
  if (!integ.email) return;
  loading($("#mail-list"), "正在读取邮件…");
  const q = encodeURIComponent($("#mail-q").value);
  const mails = await api("GET", "/api/email/inbox?days=" + $("#mail-days").value + "&q=" + q);
  renderList($("#mail-list"), mails, (m) => {
    const bodyBox = el("div", { class: "mail-body" });
    bodyBox.hidden = true;
    const btn = el("button", {
      type: "button", class: "link", "aria-label": "查看邮件：" + m.subject,
      onclick: () => guarded(async () => {
        if (!bodyBox.hidden) { bodyBox.hidden = true; btn.textContent = "查看"; return; }
        if (!bodyBox.textContent) {
          bodyBox.textContent = "加载中…";
          bodyBox.hidden = false;
          const full = await api("GET", "/api/email/message/" + m.uid);
          bodyBox.textContent = full.body || "（无正文）";
          if (full.attachments.length) bodyBox.textContent += "\n\n附件：" + full.attachments.join("、");
        }
        bodyBox.hidden = false;
        btn.textContent = "收起";
      }),
    }, "查看");
    return el("li", {},
      el("div", { class: "main" },
        el("div", { class: "title" }, m.subject),
        el("div", { class: "meta" }, el("span", {}, m.from), el("span", {}, m.date)),
        bodyBox,
      ),
      btn,
    );
  }, "这段时间没有邮件");
}

async function loadEmail() {
  await loadDrafts();
  await loadInbox();
}
$("#mail-load").addEventListener("click", () => guarded(loadInbox));

// ---------- 日志 ----------
async function loadLogs() {
  const logs = await api("GET", "/api/worklogs");
  const total = logs.reduce((n, l) => n + (l.minutes || 0), 0);
  $("#log-total").textContent = "今天共记录 " + logs.length + " 条，" +
    (total >= 60 ? Math.floor(total / 60) + " 小时 " : "") + (total % 60) + " 分钟";
  renderList($("#log-list"), logs, (l) => el("li", {},
    el("div", { class: "main" },
      el("div", { class: "title" }, l.content),
      el("div", { class: "meta" },
        l.minutes != null && el("span", {}, l.minutes + " 分钟"),
        l.project && el("span", {}, "#" + l.project),
        el("span", {}, l.created_at.slice(11, 16)),
      ),
    ),
    delBtn("删除：日志", async () => {
      await api("DELETE", "/api/worklogs/" + l.id);
      loadLogs();
    }, "删除这条日志？"),
  ), "今天还没有日志");
}

$("#log-form").addEventListener("submit", (e) => {
  e.preventDefault();
  guarded(async () => {
    await api("POST", "/api/worklogs", {
      content: $("#log-content").value,
      minutes: $("#log-minutes").value,
    });
    $("#log-content").value = "";
    $("#log-minutes").value = "";
    loadLogs();
  });
});

// ---------- 笔记 ----------
async function loadNotes() {
  const q = $("#note-search").value;
  const notes = await api("GET", "/api/notes?q=" + encodeURIComponent(q));
  renderList($("#note-list"), notes, (n) => el("li", {},
    el("div", { class: "main" },
      el("div", { class: "title" }, el("strong", {}, n.title)),
      el("div", { class: "note-content" }, n.content),
      el("div", { class: "meta" },
        el("span", {}, n.created_at.slice(0, 10)),
        n.tags && el("span", {}, "标签：" + n.tags),
      ),
    ),
    delBtn("删除：" + n.title, async () => {
      await api("DELETE", "/api/notes/" + n.id);
      loadNotes();
    }, "删除笔记「" + n.title + "」？"),
  ), q ? "没有匹配的笔记" : "还没有笔记");
}

$("#note-form").addEventListener("submit", (e) => {
  e.preventDefault();
  guarded(async () => {
    await api("POST", "/api/notes", {
      title: $("#note-title").value,
      content: $("#note-content").value,
      tags: $("#note-tags").value,
    });
    $("#note-form").reset();
    loadNotes();
  });
});
let searchTimer;
$("#note-search").addEventListener("input", () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => guarded(loadNotes), 250);
});

// ---------- 记忆 / 微信 ----------
async function loadMemories() {
  const mems = await api("GET", "/api/memories");
  renderList($("#mem-list"), mems, (m) => el("li", {},
    el("div", { class: "main" }, el("div", { class: "title" }, m.content)),
    delBtn("删除：记忆", async () => {
      await api("DELETE", "/api/memories/" + m.id);
      loadMemories();
    }),
  ), "还没有记忆。告诉助理「记住……」，或在上面添加。");
}

$("#mem-form").addEventListener("submit", (e) => {
  e.preventDefault();
  guarded(async () => {
    await api("POST", "/api/memories", { content: $("#mem-content").value });
    $("#mem-content").value = "";
    loadMemories();
  });
});
$("#btn-wechat-test").addEventListener("click", () => guarded(async () => {
  const r = await api("POST", "/api/wechat/test", {});
  toast("已发送到：" + r.sent_via.join("、"), "info");
}));

// ---------- 标签页 ----------
const loaders = {
  tasks: loadTasks, team: loadTeam, reminders: loadReminders, calendar: loadCalendar, email: loadEmail,
  worklogs: loadLogs, notes: loadNotes, memories: loadMemories,
};
let currentTab = "tasks";

function switchTab(name) {
  currentTab = name;
  document.querySelectorAll(".tab").forEach((b) => {
    const on = b.dataset.tab === name;
    b.classList.toggle("active", on);
    b.setAttribute("aria-selected", String(on));
  });
  document.querySelectorAll(".tab-body").forEach((p) => {
    p.hidden = p.id !== "tab-" + name;
  });
  guarded(loaders[name]);
}
document.querySelectorAll(".tab").forEach((btn) => {
  btn.addEventListener("click", () => switchTab(btn.dataset.tab));
});

// AI 调用了哪些工具，就刷新/跳转到对应标签页
const TOOL_TAB = {
  add_task: "tasks", update_task: "tasks", delete_task: "tasks",
  add_reminder: "reminders", cancel_reminder: "reminders",
  calendar_add_event: "calendar", draft_email: "email",
  add_worklog: "worklogs", add_note: "notes", remember: "memories", forget: "memories",
  create_agent: "team",
};
const TOOL_CN = {
  add_task: "新建任务", list_tasks: "查看任务", update_task: "更新任务", delete_task: "删除任务",
  add_note: "保存笔记", search_notes: "搜索笔记", add_worklog: "记录日志",
  daily_summary: "汇总今日数据", remember: "记住信息", forget: "删除记忆",
  add_reminder: "设置提醒", list_reminders: "查看提醒", cancel_reminder: "取消提醒",
  web_search: "上网搜索", fetch_url: "读取网页", calendar_events: "查看日历",
  calendar_add_event: "新建日程", list_emails: "查看邮件", read_email: "读取邮件",
  draft_email: "草拟邮件", send_wechat: "推送微信",
  ask_agent: "委托团队成员", list_agents: "查看团队", create_agent: "新建智能体",
};

// ---------- 对话 ----------
// 模型偶尔仍会输出 Markdown，这里统一转成纯文本
function toPlainText(md) {
  return String(md || "")
    .replace(/^[ \t]*```.*$/gm, "")                                // 代码块围栏
    .replace(/!?\[([^\]]*)\]\((https?:\/\/[^)\s]+)\)/g, "$1 $2")    // [文字](链接)
    .replace(/^[ \t]{0,3}#{1,6}[ \t]+/gm, "")                      // # 标题
    .replace(/^[ \t]*>[ \t]?/gm, "")                               // > 引用
    .replace(/^[ \t]*\|[ \t:|-]*-{3,}[ \t:|-]*\|?[ \t]*\n/gm, "")  // 表格分隔线
    .replace(/^[ \t]*\|(.*)\|[ \t]*$/gm, (_, row) => row.split("|").map((c) => c.trim()).join("  "))
    .replace(/^([ \t]*)[-*+][ \t]+/gm, "$1· ")                     // 列表符号
    .replace(/(\*\*|__)(.+?)\1/g, "$2")                            // 加粗
    .replace(/`([^`]+)`/g, "$1")                                   // 行内代码
    .replace(/^[ \t]*([-*_][ \t]*){3,}$/gm, "")                    // 分隔线
    .replace(/\n[ \t]*\n([ \t]*\n)+/g, "\n\n")
    .trim();
}

// 把文本里的网址变成可点击链接（用 DOM 节点构造，不拼接 HTML）
function linkify(text) {
  const frag = document.createDocumentFragment();
  const re = /https?:\/\/[^\s<>"'（）()，。、；]+/g;
  let last = 0, m;
  while ((m = re.exec(text))) {
    frag.append(text.slice(last, m.index));
    frag.append(el("a", { href: m[0], target: "_blank", rel: "noopener noreferrer" }, m[0]));
    last = m.index + m[0].length;
  }
  frag.append(text.slice(last));
  return frag;
}

function addMsg(role, text, tools, events) {
  const node = el("div", { class: "msg " + role },
    role === "user" ? text : linkify(toPlainText(text)));
  const steps = (events || []).map(describeEvent).filter(Boolean);
  if (steps.length) {
    const members = new Set(events.filter((e) => e.type === "delegate").map((e) => e.to));
    node.append(el("details", { class: "trace" },
      el("summary", {}, "协作过程（" + members.size + " 位成员参与，" + steps.length + " 步）"),
      el("ol", {}, ...steps.map((s) => el("li", {}, s)))));
  }
  if (tools && tools.length) {
    node.append(el("div", { class: "tools" },
      "已执行：" + tools.map((t) => TOOL_CN[t.name] || t.name).join("、")));
    if (tools.some((t) => t.name === "draft_email")) {
      node.append(el("div", { class: "msg-action" },
        el("button", { type: "button", onclick: () => switchTab("email") }, "去确认发送邮件")));
    }
  }
  $("#messages").append(node);
  node.scrollIntoView({ block: "end" });
  return node;
}

function setBusy(busy) {
  ["#chat-send", "#btn-report", "#btn-reset", "#developing"].forEach((s) => ($(s).disabled = busy));
}

function afterTools(tools) {
  const tabs = new Set((tools || []).map((t) => TOOL_TAB[t.name]).filter(Boolean));
  if (tabs.size === 1) {
    const [only] = tabs;
    if (only !== currentTab && only !== "calendar") return switchTab(only);
  }
  if (currentTab !== "calendar") guarded(loaders[currentTab]);
}

async function sendChat(path, body, userText) {
  if (userText) addMsg("user", userText);
  const pending = addMsg("bot", "思考中…");
  const progress = el("div", { class: "progress" });
  pending.append(progress);
  setBusy(true);
  // 每秒查询一次协作进度，实时显示各成员在做什么
  let since = 0, polling = true;
  const poll = async () => {
    while (polling) {
      try {
        const p = await api("GET", "/api/chat/progress?since=" + since);
        since = p.next;
        p.events.forEach((ev) => {
          const line = describeEvent(ev);
          if (line) progress.textContent += (progress.textContent ? "\n" : "") + line;
        });
        if (p.events.length) pending.scrollIntoView({ block: "end" });
      } catch (e) { /* 进度查询失败不影响主请求 */ }
      await new Promise((r) => setTimeout(r, 1000));
    }
  };
  poll();
  try {
    const data = await api("POST", path, body);
    polling = false;
    pending.remove();
    if (path === "/api/report") {
      addMsg("bot", data.text + "\n\n已保存到：" + data.path);
    } else {
      addMsg("bot", data.reply, data.tools, data.events);
      afterTools(data.tools);
    }
  } catch (e) {
    polling = false;
    pending.remove();
    addMsg("bot error", e.message);
  } finally {
    polling = false;
    if ($("#tool-studio").open || body.developing) guarded(loadPresets);
    setBusy(false);
    $("#chat-input").focus();
  }
}

$("#chat-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const text = $("#chat-input").value.trim();
  if (!text || $("#chat-send").disabled) return;
  $("#chat-input").value = "";
  sendChat("/api/chat", { message: text, developing: $("#developing").checked }, text);
});
$("#chat-input").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
    e.preventDefault();
    $("#chat-form").requestSubmit();
  }
});
$("#btn-report").addEventListener("click", () => sendChat("/api/report", {}, "生成今日日报"));
$("#btn-reset").addEventListener("click", () => guarded(async () => {
  await api("POST", "/api/chat/reset", {});
  $("#messages").replaceChildren();
  addMsg("bot", "对话已清空（任务、笔记、记忆仍保留）。");
}));

// 恢复上次的对话
async function loadChatHistory() {
  const history = await api("GET", "/api/chat/history");
  if (!history.length) return;
  $("#messages").append(el("div", { class: "divider" }, "以下是之前的对话"));
  history.forEach((m) => addMsg(m.role === "user" ? "user" : "bot", m.content, m.tools));
}

// ---------- 启动 ----------
guarded(async () => {
  await loadStatus();
  $("#cal-date").value = today;
  $("#rem-at").value = localInputValue(new Date(Date.now() + 30 * 60 * 1000));
  setupNotifyButton();
  await loadChatHistory();
  await loadTasks();
  checkReminders();
  setInterval(checkReminders, 20000);
});
