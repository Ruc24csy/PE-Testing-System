"use strict";
/* 体育成绩录入平台前端：无构建、无依赖的单页应用 */
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmt = (v, d) => (v === null || v === undefined || v === "" ? "" : Number(v).toFixed(d));
const state = { me: null, semesters: [], semId: null, cls: null };

async function api(path, opts = {}) {
  const o = { method: opts.method || "GET", headers: {}, credentials: "same-origin" };
  if (opts.body instanceof FormData) o.body = opts.body;
  else if (opts.body !== undefined) { o.body = JSON.stringify(opts.body); o.headers["Content-Type"] = "application/json"; }
  const r = await fetch(path, o);
  if (r.status === 401) { location.hash = "#/login"; throw new Error("请先登录"); }
  const ct = r.headers.get("content-type") || "";
  const data = ct.includes("json") ? await r.json() : await r.blob();
  if (!r.ok) {
    if (r.status === 403 && data.detail === "首次登录请先修改初始口令") location.hash = "#/password";
    throw new Error((data && data.detail) || `请求失败（${r.status}）`);
  }
  return data;
}

function toast(msg, err = false) {
  const t = $("#toast"); t.textContent = msg; t.className = "toast" + (err ? " err" : ""); t.hidden = false;
  clearTimeout(toast._t); toast._t = setTimeout(() => (t.hidden = true), err ? 5000 : 2200);
}
function modal(html, onMount) {
  const m = $("#modal"); $(".modal-body", m).innerHTML = html; m.hidden = false;
  if (onMount) onMount($(".modal-body", m));
}
function closeModal() { $("#modal").hidden = true; }
$("#modal .modal-close").onclick = closeModal;
$("#modal").addEventListener("mousedown", (e) => { if (e.target.id === "modal") closeModal(); });

async function download(path, body) {
  try {
    const r = await fetch(path, body === undefined ? { credentials: "same-origin" } :
      { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    if (!r.ok) { const d = await r.json().catch(() => ({})); throw new Error(d.detail || "下载失败"); }
    const cd = r.headers.get("content-disposition") || "";
    const m = cd.match(/filename\*=UTF-8''([^;]+)/);
    const name = m ? decodeURIComponent(m[1]) : "下载.xlsx";
    const url = URL.createObjectURL(await r.blob());
    const a = document.createElement("a"); a.href = url; a.download = name; a.click();
    setTimeout(() => URL.revokeObjectURL(url), 5000);
  } catch (e) { toast(e.message, true); }
}

/* ------------------------------------------------------------ 路由 */
const routes = [
  [/^#\/login/, viewLogin], [/^#\/password/, viewPassword], [/^#\/class\/(\d+)/, viewClass],
  [/^#\/admin\/?(\w*)/, viewAdmin], [/^#\/?$/, viewHome],
];
window.addEventListener("hashchange", route);
async function route() {
  const h = location.hash || "#/";
  if (state.cls && state.cls.flush) await state.cls.flush();
  if (!h.startsWith("#/login")) {
    if (!state.me) { try { state.me = await api("/api/me"); } catch { return; } }
    if (state.me.must_change_password && !h.startsWith("#/password")) { location.hash = "#/password"; return; }
    if (!state.semesters.length) await loadSemesters();
  }
  renderTop();
  for (const [re, fn] of routes) { const m = h.match(re); if (m) { return fn(...m.slice(1)); } }
  location.hash = "#/";
}
async function loadSemesters() {
  state.semesters = await api("/api/semesters");
  const saved = Number(sessionStorage.getItem("semId"));
  const cur = state.semesters.find((s) => s.id === saved) || state.semesters.find((s) => s.is_current) || state.semesters[0];
  state.semId = cur ? cur.id : null;
}
function renderTop() {
  const me = state.me;
  $("#userBox").innerHTML = me ? `<span>${esc(me.name || "")} ${esc(me.staff_no)}${me.is_admin ? " · 体育部" : ""}</span><button id="logoutBtn">退出</button>` : "";
  if (me) $("#logoutBtn").onclick = async () => { const r = await api("/auth/logout", { method: "POST" }); state.me = null; state.semesters = []; location.href = r.redirect === "/" ? "/#/login" : r.redirect; };
  const h = location.hash;
  $("#nav").innerHTML = me ? `<a href="#/" class="${!h.startsWith("#/admin") ? "on" : ""}">我的教学班</a>` + (me.is_admin ? `<a href="#/admin" class="${h.startsWith("#/admin") ? "on" : ""}">体育部管理</a>` : "") : "";
  const sp = $("#semPicker");
  sp.hidden = !me || !state.semesters.length || h.startsWith("#/class");
  $("#semSelect").innerHTML = state.semesters.map((s) => `<option value="${s.id}" ${s.id === state.semId ? "selected" : ""}>${esc(s.name)}${s.is_current ? "（本学期）" : ""}</option>`).join("");
  $("#semSelect").onchange = (e) => { state.semId = Number(e.target.value); sessionStorage.setItem("semId", state.semId); route(); };
}
const curSem = () => state.semesters.find((s) => s.id === state.semId);

/* ------------------------------------------------------------ 登录 */
async function viewLogin() {
  const mode = await api("/api/auth/mode");
  const errs = { sso: "统一身份认证未通过，请重试。", expired: "登录请求已失效，请重新点击登录。", unavailable: "统一身份认证服务暂时不可用，请稍后再试。", category: "本平台仅供任课教师和体育部使用。" };
  const em = location.hash.match(/error=(\w+)/);
  const err = em && errs[em[1]] ? `<div class="notice err">${errs[em[1]]}</div>` : "";
  $("#app").innerHTML = `<div class="login"><div class="card"><h2>登录</h2>${err}
    ${mode.mode === "sso" ? `<p>使用数字人大统一身份认证（职工号与密码）登录。</p><a class="btn primary" href="/auth/login">统一身份认证登录</a>` : ""}
    ${mode.mode === "dev" ? `<div class="notice info">当前为开发测试模式：输入工号即可登录。正式部署时改为数字人大统一身份认证。</div>
       <label>工号<input type="text" id="devNo" lang="en" inputmode="text" autocomplete="username" autocapitalize="off" autocorrect="off" spellcheck="false" autofocus></label><button class="primary" id="devBtn">登录</button>` : ""}
    ${mode.mode === "local" ? `<p class="muted small">请使用体育部发放的工号和口令登录，首次登录须修改口令。</p>` : ""}
    ${mode.mode === "local" ? "<div>" : `<details style="margin-top:18px"><summary class="muted small">应急管理员登录</summary>`}
      <label>工号<input type="text" id="locNo" lang="en" inputmode="text" autocomplete="username" autocapitalize="off" autocorrect="off" spellcheck="false"></label><label>口令<input type="password" id="locPw"></label>
      <button id="locBtn" class="${mode.mode === "local" ? "primary" : ""}">登录</button>${mode.mode === "local" ? "</div>" : "</details>"}</div></div>`;
  const done = async () => { state.me = null; state.semesters = []; location.hash = "#/"; };
  const devNo = $("#devNo");
  if (devNo) {
    requestAnimationFrame(() => devNo.focus());
    const go = async () => { try { await api("/api/auth/dev-login", { method: "POST", body: { staff_no: devNo.value } }); done(); } catch (e) { toast(e.message, true); } };
    $("#devBtn").onclick = go; devNo.onkeydown = (e) => e.key === "Enter" && go();
  }
  $("#locPw").onkeydown = (e) => e.key === "Enter" && $("#locBtn").click();
  $("#locBtn").onclick = async () => {
    try { await api("/api/auth/local-login", { method: "POST", body: { staff_no: $("#locNo").value, password: $("#locPw").value } }); done(); }
    catch (e) { toast(e.message, true); }
  };
}
function viewPassword() {
  $("#app").innerHTML = `<div class="login"><div class="card"><h2>修改口令</h2>
   <p class="muted small">初始口令须在首次登录时修改。新口令不少于8位，并包含数字、大写字母、小写字母、特殊字符中的至少三类。</p>
   <label>原口令<input type="password" id="pw0"></label><label>新口令<input type="password" id="pw1"></label><label>再输一次<input type="password" id="pw2"></label>
   <button class="primary" id="pwBtn">保存</button></div></div>`;
  $("#pwBtn").onclick = async () => {
    if ($("#pw1").value !== $("#pw2").value) return toast("两次输入的新口令不一致", true);
    try { await api("/api/me/password", { method: "POST", body: { old: $("#pw0").value, new: $("#pw1").value } }); state.me = null; toast("口令已修改"); location.hash = "#/"; }
    catch (e) { toast(e.message, true); }
  };
}

/* ------------------------------------------------------------ 教学班列表 */
async function viewHome(scope) {
  const sem = curSem();
  if (!sem) {
    $("#app").innerHTML = `<div class="card"><div class="notice">平台还没有建立任何学期。${state.me.is_admin ? `请到<a href="#/admin/semesters">体育部管理</a>新建学期并导入课程信息。` : "请联系体育部。"}</div></div>`;
    return;
  }
  const filt = JSON.parse(sessionStorage.getItem("classFilter") || "{}");
  const all = state.me.is_admin && filt.scope === "all";
  const q = new URLSearchParams({ course: filt.course || "", campus: filt.campus || "", scope: all ? "all" : "mine" });
  const d = await api(`/api/semesters/${sem.id}/classes?${q}`);
  const readonly = !sem.is_current && !sem.entry_open;
  let body;
  if (!d.imported) body = `<div class="notice">${esc(sem.name)}的课程信息尚未导入，请联系体育部。</div>`;
  else if (!d.classes.length && !filt.course && !filt.campus) body = `<div class="notice">${all ? "本学期没有教学班。" : `本学期没有找到您（工号 ${esc(state.me.staff_no)}）的教学班，请联系体育部核对课程安排信息。`}</div>`;
  else body = `<table class="list"><thead><tr><th>课程名称</th><th>教学班名称</th><th>上课时间</th><th>上课地点</th><th>校区</th><th>选课人数</th>${all ? "<th>授课教师</th>" : ""}<th>录入进度</th><th>状态</th></tr></thead><tbody>
    ${d.classes.map((c) => `<tr class="clickable" data-id="${c.id}"><td>${esc(c.course_name)}</td><td><a href="#/class/${c.id}">${esc(c.class_name)}</a>${c.pending ? ` <span class="tag warn" title="有学生的年级或性别待体育部确认">待确认 ${c.pending}</span>` : ""}</td>
      <td>${esc(c.weekday)} ${esc(fmtPeriods(c.periods))}<div class="muted small">${esc(c.weeks)}</div></td><td>${esc(c.location)}</td><td>${esc(c.campus)}</td><td>${c.roster}</td>
      ${all ? `<td>${esc(c.teacher_name)}${c.teacher_pending ? ' <span class="tag err">教师待补</span>' : ""}</td>` : ""}
      <td>${c.entered}/${c.roster}</td><td><span class="tag s-${c.status}">${c.status}</span></td></tr>`).join("")}</tbody></table>`;
  $("#app").innerHTML = `<div class="card"><div class="row"><h2 style="margin:0">${esc(sem.name)} ${sem.is_current ? '<span class="tag cur">本学期</span>' : ""}</h2>
    <span class="spacer"></span>
    <select id="fCourse"><option value="">全部课程</option>${d.courses.map((c) => `<option ${c === filt.course ? "selected" : ""}>${esc(c)}</option>`).join("")}</select>
    <select id="fCampus"><option value="">全部校区</option>${["中关村校区", "通州校区"].map((c) => `<option ${c === filt.campus ? "selected" : ""}>${c}</option>`).join("")}</select>
    ${state.me.is_admin ? `<label><input type="checkbox" id="fAll" ${all ? "checked" : ""}> 显示全部教学班</label>` : ""}</div>
    ${readonly ? `<div class="notice info" style="margin-top:12px">以往学期为只读。如需补录或更正，请联系体育部开放该学期的录入窗口。</div>` : ""}
    <div style="margin-top:12px">${body}</div></div>`;
  const save = () => { sessionStorage.setItem("classFilter", JSON.stringify({ course: $("#fCourse").value, campus: $("#fCampus").value, scope: $("#fAll") && $("#fAll").checked ? "all" : "mine" })); viewHome(); };
  $("#fCourse").onchange = save; $("#fCampus").onchange = save; if ($("#fAll")) $("#fAll").onchange = save;
  $$("tr.clickable").forEach((tr) => (tr.onclick = (e) => { if (e.target.tagName !== "A") location.hash = `#/class/${tr.dataset.id}`; }));
}
function fmtPeriods(p) { const m = String(p || "").match(/\d{2}/g); return m ? `第${m.map(Number).join("-")}节` : p || ""; }

/* ------------------------------------------------------------ 录入界面 */
const COLS = ["a_raw", "a_status", "b_raw", "b_status", "skill", "regular"];
async function viewClass(id) {
  const d = await api(`/api/classes/${id}`);
  const C = { id: Number(id), d, rows: d.rows, byId: new Map(d.rows.map((r) => [r.enrollment_id, r])), queue: new Map(), undo: [], sel: new Set(), filter: "", errors: new Map() };
  state.cls = C;
  C.flush = () => flushQueue(C);
  restoreDraft(C);
  renderClass(C);
  clearInterval(state.autosave);
  state.autosave = setInterval(() => { if (state.cls === C) flushQueue(C); else clearInterval(state.autosave); }, 30000);
}
function draftKey(C) { return `draft:${state.me.staff_no}:${C.id}`; }
function saveDraft(C) { localStorage.setItem(draftKey(C), JSON.stringify([...C.queue.values()])); }
function restoreDraft(C) {
  const raw = localStorage.getItem(draftKey(C)); if (!raw) return;
  const items = JSON.parse(raw); if (!items.length) return;
  for (const it of items) C.queue.set(`${it.enrollment_id}:${it.field}`, it);
  toast(`恢复了 ${items.length} 处上次未保存的修改，正在保存…`);
  setTimeout(() => flushQueue(C), 300);
}

function renderClass(C) {
  const { d } = C; const c = d.class; const cfg = d.config; const sem = d.semester;
  const lab = (k) => cfg.slots.find((s) => s.key === k).label;
  const itemName = (k) => { const l = d.config_labels[k]; return l.M === l.F ? l.M : `${l.M}/${l.F}`; };
  const wt = (k) => (cfg.slots.find((s) => s.key === k).weight * 100).toFixed(0);
  const pendingN = C.rows.filter((r) => r.pending || !r.gender_code).length;
  $("#app").innerHTML = `
  <div class="card">
    <div class="row"><a href="#/">← 返回教学班列表</a><span class="spacer"></span><span class="status-bar" id="saveState"></span></div>
    <h2 style="margin-top:8px">${esc(c.class_name)} <span class="tag s-${c.status}">${c.status}</span></h2>
    <div class="class-head">
      <div><span>学年学期</span>${esc(sem.name)}</div><div><span>课程名称</span>${esc(c.course_name)}</div>
      <div><span>教学班号</span>${esc(c.class_no)}</div><div><span>授课教师</span>${esc(c.teacher_name)}</div>
      <div><span>上课地点</span>${esc(c.location)}</div><div><span>周次节次</span>${esc(c.weeks)} ${esc(c.weekday)} ${esc(fmtPeriods(c.periods))}</div>
      <div><span>选课人数</span>${C.rows.length}</div><div><span>评分标准</span>按学生逐人匹配（大一大二／大三大四）</div>
      <div><span>评分表版本</span>${esc(d.standard_version)}</div>
      <div><span>权重</span>${esc(itemName("a"))} ${wt("a")}%、${esc(itemName("b"))} ${wt("b")}%、技能 ${(cfg.skill_weight * 100).toFixed(0)}%、平时 ${(cfg.regular_weight * 100).toFixed(0)}% ${d.editable ? '<button class="link" id="cfgBtn">修改</button>' : ""}</div>
    </div>
    ${!d.editable ? `<div class="notice info" style="margin-top:10px">${esc(d.readonly_reason)}${d.unlock_request && d.unlock_request.status === "待审核" ? "（撤回申请审核中）" : ""}</div>` : ""}
    ${pendingN ? `<div class="notice" style="margin-top:10px">本班有 ${pendingN} 名学生的年级或性别待体育部确认（黄色行），确认前按暂定标准档计算，班级不能提交。</div>` : ""}
    <div class="toolbar">
      <label><input type="checkbox" id="fMissing"> 仅显示未录入</label>
      <label><input type="checkbox" id="fIssue"> 仅显示异常</label>
      <label><input type="checkbox" id="fOpt" ${localStorage.getItem("showOpt") === "1" ? "checked" : ""}> 显示学院、专业、上课班级</label>
      <span class="muted small" id="selInfo"></span>
      ${d.editable ? `<select id="batchSlot"><option value="a_status">${esc(lab("a"))}</option><option value="b_status">${esc(lab("b"))}</option></select>
        <select id="batchStatus">${d.statuses.map((s) => `<option>${s}</option>`).join("")}</select><button id="batchBtn">批量置状态</button>` : ""}
      <span class="spacer"></span>
      ${d.editable ? `<button id="saveBtn">保存</button>` : ""}
      <button id="exportBtn">导出Excel</button>
      ${d.editable ? `<button id="tplBtn">下载录入模板</button><button id="uploadBtn">整班上传</button><button class="primary" id="submitBtn">提交</button>` : ""}
      ${c.status === "已提交" && (!d.unlock_request || d.unlock_request.status !== "待审核") ? `<button id="unlockBtn">申请撤回</button>` : ""}
    </div>
    <div class="muted small">只需填写原始成绩、运动技能评定和平时成绩，其余列自动计算。Enter/Tab 向下，Shift+Tab 向上，Esc 取消输入，Ctrl+Z 撤销；可从 Excel 复制一整列粘贴。点得分可查看计算过程。</div>
  </div>
  <div class="grid-wrap"><table class="grid ${localStorage.getItem("showOpt") === "1" ? "" : "compact"}" id="grid">
    <thead><tr>
      <th rowspan="2" class="sticky"><input type="checkbox" id="selAll" title="全选"></th>
      <th rowspan="2" class="sticky2">学号</th><th rowspan="2" class="sticky3">姓名</th><th rowspan="2">性别</th><th rowspan="2" class="opt">学院</th><th rowspan="2" class="opt">专业</th><th rowspan="2" class="opt">上课班级</th><th rowspan="2">年级/标准档</th>
      ${["a", "b"].map((k) => `<th colspan="4">${esc(itemName(k))}（${wt(k)}%）<span class="qm" data-std="${k}" title="查看评分表与加分规则">?</span></th>`).join("")}
      <th rowspan="2">体测小计</th><th rowspan="2">运动技能<br><span class="small muted">0—${(cfg.skill_weight * 100).toFixed(0)}</span></th><th rowspan="2">平时成绩<br><span class="small muted">0—${(cfg.regular_weight * 100).toFixed(0)}</span></th><th rowspan="2">课程总成绩</th><th rowspan="2"></th>
    </tr><tr>${["a", "b"].map(() => `<th>原始成绩</th><th>状态</th><th>单项得分</th><th>折算分</th>`).join("")}</tr></thead>
    <tbody></tbody></table></div>`;
  renderRows(C);
  bindClass(C);
}

function rowVisible(C, r) {
  if ($("#fMissing") && $("#fMissing").checked) {
    const v = r.values; const miss = (k) => v[`${k}_status`] === "正常" && v[`${k}_raw`] === null;
    if (!(miss("a") || miss("b") || v.skill === null || v.regular === null)) return false;
  }
  if ($("#fIssue") && $("#fIssue").checked) {
    if (!(r.pending || !r.gender_code || r.issues.length || r.sudden.length || r.info_error || [...C.errors.keys()].some((k) => k.startsWith(r.enrollment_id + ":")))) return false;
  }
  return true;
}
function renderRows(C) {
  const tb = $("#grid tbody");
  tb.innerHTML = C.rows.filter((r) => rowVisible(C, r)).map((r) => rowHtml(C, r)).join("") || `<tr><td colspan="24" class="muted" style="padding:20px">没有符合条件的学生</td></tr>`;
  updateSelInfo(C);
}
function rowHtml(C, r) {
  const ed = C.d.editable;
  const v = r.values;
  const cell = (field, extra = "") => {
    const k = field[0]; const slot = field.endsWith("_raw") ? r.display[k] : null;
    const ro = !ed || (slot && v[`${k}_status`] !== "正常") || (slot && !r.gender_code);
    const key = `${r.enrollment_id}:${field}`; const err = C.errors.get(key);
    const pend = C.queue.get(key);
    const val = pend ? pend.value : field.endsWith("_raw") ? slot.raw_text : v[field] ?? "";
    let hint = "";
    if (err) hint = `<div class="hint err">${esc(err)}</div>`;
    else if (slot) {
      if (slot.status_display && slot.status !== "正常") hint = `<div class="hint">${esc(slot.status_display)}</div>`;
      else if (slot.hint) hint = `<div class="hint ${slot.below_min ? "warnc" : ""}">${esc(slot.hint)}</div>`;
      if (!r.gender_code) hint = `<div class="hint warnc">性别待确认，暂不能录入</div>`;
    }
    const ph = field.endsWith("_raw") && slot.item_label ? slot.item_label : "";
    return `<td class="in ${err ? "bad" : ""} ${ro ? "ro" : ""}" ${extra}><input data-e="${r.enrollment_id}" data-f="${field}" value="${esc(val)}" placeholder="${esc(ph)}" ${ro ? "readonly" : ""} autocomplete="off">${hint}</td>`;
  };
  const statusCell = (k) => {
    const f = `${k}_status`;
    return `<td class="in st"><select data-e="${r.enrollment_id}" data-f="${f}" ${ed ? "" : "disabled"}>${C.d.statuses.map((s) => `<option ${v[f] === s ? "selected" : ""}>${s === "正常" ? "正常实测" : s}</option>`).join("")}</select></td>`;
  };
  const scoreCells = (k) => {
    const s = r.display[k];
    const sc = s.score === null ? (s.status_display || "") : `${fmt(s.score, s.score % 1 ? 1 : 0)}${s.bonus ? `<div class="bonus">含加分+${s.bonus}</div>` : ""}`;
    return `<td class="lock num score-cell" data-ex="${r.enrollment_id}:${k}">${sc}</td><td class="lock num score-cell" data-ex="${r.enrollment_id}:${k}">${fmt(s.folded, 2)}</td>`;
  };
  const flags = [];
  if (r.pending) flags.push(`<span class="tag warn" title="${esc(r.pending_reasons.join("；"))}">待确认</span>`);
  if (r.flags.includes("band_changed")) flags.push(`<span class="tag warn" title="体育部已变更该生标准档">档已变更</span>`);
  if (r.info_error) flags.push(`<span class="tag err" title="${esc(r.info_error)}">信息有误</span>`);
  if (r.sudden.length) flags.push(`<span class="tag warn" title="${esc(r.sudden.join("；"))}">突变</span>`);
  return `<tr data-e="${r.enrollment_id}" class="${r.pending || !r.gender_code ? "pending" : ""} ${C.sel.has(r.enrollment_id) ? "selected" : ""}">
    <td class="sticky lock"><input type="checkbox" class="rowSel" data-e="${r.enrollment_id}" ${C.sel.has(r.enrollment_id) ? "checked" : ""}> ${r.seq}</td>
    <td class="sticky2 lock">${esc(r.student_no)}</td><td class="sticky3 lock">${esc(r.name)}</td><td class="lock">${esc(r.gender)}</td>
    <td class="lock trunc opt" title="${esc(r.college)}">${esc(r.college)}</td><td class="lock trunc opt" title="${esc(r.major)}">${esc(r.major)}</td><td class="lock trunc opt" title="${esc(r.class_label)}">${esc(r.class_label)}</td>
    <td class="lock">${r.grade_num ? `大${r.grade_num}` : ""} ${esc(r.band_label)} ${flags.join(" ")}</td>
    ${cell("a_raw")}${statusCell("a")}${scoreCells("a")}${cell("b_raw")}${statusCell("b")}${scoreCells("b")}
    <td class="lock num"><b>${fmt(r.subtotal, 2)}</b></td>${cell("skill")}${cell("regular")}
    <td class="lock num"><b>${fmt(r.total, 1)}</b></td>
    <td class="lock"><button class="link rowMenu" data-e="${r.enrollment_id}">更多</button></td></tr>`;
}

function setSaveState(t) { const el = $("#saveState"); if (el) el.textContent = t; }
function enqueue(C, eid, field, value, { undo = true } = {}) {
  const r = C.byId.get(eid);
  const key = `${eid}:${field}`;
  const old = field.endsWith("_raw") ? r.display[field[0]].raw_text : r.values[field] ?? "";
  if (String(old) === String(value) && !C.queue.has(key)) return;
  if (undo) C.undo.push({ eid, field, value: old });
  C.queue.set(key, { enrollment_id: eid, field, value });
  saveDraft(C);
  setSaveState("有未保存的修改…");
  clearTimeout(C._t); C._t = setTimeout(() => flushQueue(C), 400);
}
async function flushQueue(C) {
  if (!C.queue.size || C._busy) return;
  C._busy = true;
  const items = [...C.queue.values()]; C.queue.clear();
  setSaveState("保存中…");
  try {
    const res = await api(`/api/classes/${C.id}/cells`, { method: "POST", body: { changes: items } });
    const warns = [];
    for (const it of res.results) {
      const key = `${it.enrollment_id}:${it.field}`;
      if (it.ok) { C.errors.delete(key); (it.warnings || []).forEach((w) => warns.push(`${C.byId.get(it.enrollment_id).name}：${w}`)); }
      else C.errors.set(key, it.error);
    }
    for (const r of res.rows) { const i = C.rows.findIndex((x) => x.enrollment_id === r.enrollment_id); if (i >= 0) { r.seq = C.rows[i].seq; C.rows[i] = r; C.byId.set(r.enrollment_id, r); } }
    if (C.d.class.status !== res.status) C.d.class.status = res.status;
    saveDraft(C);
    refreshRows(C, new Set(items.map((i) => i.enrollment_id)));
    const nErr = res.results.filter((x) => !x.ok).length;
    setSaveState(nErr ? `有 ${nErr} 处输入未通过校验（标红）` : `已自动保存 ${new Date().toLocaleTimeString()}`);
    if (warns.length) toast(warns.slice(0, 3).join("；"));
  } catch (e) {
    for (const it of items) if (!C.queue.has(`${it.enrollment_id}:${it.field}`)) C.queue.set(`${it.enrollment_id}:${it.field}`, it);
    saveDraft(C);
    setSaveState("保存失败，修改已暂存在本机，网络恢复后自动保存");
    toast(e.message, true);
  } finally { C._busy = false; if (C.queue.size) setTimeout(() => flushQueue(C), 1500); }
}
function refreshRows(C, ids) {
  const active = document.activeElement; const af = active && active.dataset ? [active.dataset.e, active.dataset.f] : null;
  for (const id of ids) {
    const tr = $(`#grid tr[data-e="${id}"]`); if (!tr) continue;
    const t = document.createElement("tbody"); t.innerHTML = rowHtml(C, C.byId.get(id)); tr.replaceWith(t.firstElementChild);
  }
  if (af && ids.has(Number(af[0]))) { const el = $(`#grid [data-e="${af[0]}"][data-f="${af[1]}"]`); if (el && el !== active) { el.focus(); if (el.select) el.select(); } }
}
function moveFocus(from, dRow) {
  const inputs = $$(`#grid [data-f="${from.dataset.f}"]`);
  const i = inputs.indexOf(from); const n = inputs[i + dRow];
  if (n) { n.focus(); if (n.select) n.select(); }
}
function updateSelInfo(C) { const el = $("#selInfo"); if (el) el.textContent = C.sel.size ? `已选 ${C.sel.size} 人` : ""; }

function bindClass(C) {
  const grid = $("#grid");
  grid.addEventListener("focusin", (e) => { if (e.target.tagName === "INPUT" && e.target.dataset.f) e.target.dataset.orig = e.target.value; });
  grid.addEventListener("change", (e) => {
    const t = e.target;
    if (t.classList.contains("rowSel")) { const id = Number(t.dataset.e); t.checked ? C.sel.add(id) : C.sel.delete(id); t.closest("tr").classList.toggle("selected", t.checked); updateSelInfo(C); return; }
    if (!t.dataset.f || t.readOnly) return;
    enqueue(C, Number(t.dataset.e), t.dataset.f, t.value.trim());
  });
  grid.addEventListener("keydown", (e) => {
    const t = e.target; if (!t.dataset || !t.dataset.f) return;
    if (e.key === "Enter" || (e.key === "Tab")) { e.preventDefault(); if (t.tagName === "INPUT" && t.value !== t.dataset.orig && !t.readOnly) enqueue(C, Number(t.dataset.e), t.dataset.f, t.value.trim()); t.dataset.orig = t.value; moveFocus(t, e.shiftKey ? -1 : 1); }
    else if (e.key === "ArrowDown" && t.tagName === "INPUT") { e.preventDefault(); moveFocus(t, 1); }
    else if (e.key === "ArrowUp" && t.tagName === "INPUT") { e.preventDefault(); moveFocus(t, -1); }
    else if (e.key === "Escape") { t.value = t.dataset.orig ?? t.value; t.blur(); }
  });
  grid.addEventListener("paste", (e) => {
    const t = e.target; if (!t.dataset || !t.dataset.f || t.tagName !== "INPUT" || !C.d.editable) return;
    const text = (e.clipboardData || window.clipboardData).getData("text");
    const lines = text.replace(/\r/g, "").split("\n"); if (lines.length && lines[lines.length - 1] === "") lines.pop();
    if (lines.length <= 1) return;
    e.preventDefault();
    const inputs = $$(`#grid [data-f="${t.dataset.f}"]`); const start = inputs.indexOf(t);
    let n = 0;
    lines.forEach((line, i) => { const el = inputs[start + i]; if (!el || el.readOnly) return; const v = line.split("\t")[0].trim(); el.value = v; enqueue(C, Number(el.dataset.e), el.dataset.f, v); n++; });
    toast(`已粘贴 ${n} 行，逐行校验中`);
  });
  grid.addEventListener("click", (e) => {
    const t = e.target;
    const ex = t.closest("[data-ex]"); if (ex) return showExplain(C, ...ex.dataset.ex.split(":"));
    if (t.dataset.std) return showStandard(C, t.dataset.std);
    if (t.classList.contains("rowMenu")) return rowMenu(C, Number(t.dataset.e));
  });
  document.onkeydown = (e) => {
    if (state.cls !== C) return;
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "z" && !(document.activeElement && document.activeElement.tagName === "INPUT" && document.activeElement.value !== document.activeElement.dataset.orig)) {
      const u = C.undo.pop(); if (!u) return toast("没有可撤销的修改");
      e.preventDefault(); enqueue(C, u.eid, u.field, u.value, { undo: false });
      const el = $(`#grid [data-e="${u.eid}"][data-f="${u.field}"]`); if (el) el.value = u.value;
      toast("已撤销上一次修改");
    }
  };
  $("#selAll").onchange = (e) => { const ids = $$("#grid .rowSel").map((x) => Number(x.dataset.e)); ids.forEach((id) => (e.target.checked ? C.sel.add(id) : C.sel.delete(id))); renderRows(C); };
  $("#fOpt").onchange = (e) => { localStorage.setItem("showOpt", e.target.checked ? "1" : "0"); $("#grid").classList.toggle("compact", !e.target.checked); };
  $("#fMissing").onchange = () => renderRows(C); $("#fIssue").onchange = () => renderRows(C);
  const on = (id, fn) => { const el = $(id); if (el) el.onclick = fn; };
  on("#batchBtn", () => {
    if (!C.sel.size) return toast("请先勾选学生", true);
    const f = $("#batchSlot").value, s = $("#batchStatus").value;
    for (const id of C.sel) enqueue(C, id, f, s);
    toast(`已将 ${C.sel.size} 人置为“${s}”`);
  });
  on("#saveBtn", async () => { await flushQueue(C); if (!C.queue.size) toast("已保存"); });
  on("#exportBtn", async () => { await flushQueue(C); download(`/api/classes/${C.id}/export`); });
  on("#tplBtn", () => download(`/api/classes/${C.id}/template`));
  on("#uploadBtn", () => uploadDialog(C));
  on("#submitBtn", () => submitDialog(C));
  on("#unlockBtn", () => unlockDialog(C));
  on("#cfgBtn", () => configDialog(C));
  window.onbeforeunload = () => (C.queue.size ? "还有未保存的修改" : undefined);
}

function showExplain(C, eid, k) {
  const r = C.byId.get(Number(eid)); const s = r.display[k];
  modal(`<h3>${esc(r.name)} · ${esc(s.item_label || "")}</h3>${s.explain ? `<ol>${s.explain.map((x) => `<li>${esc(x)}</li>`).join("")}</ol>` : `<p class="muted">${esc(s.status_display || "尚未录入，暂无计算过程")}</p>`}
   <p class="muted small">标准档：${r.grade_num ? `大${r.grade_num}，` : ""}${esc(r.band_label)}（判定依据：${esc(r.band_source || "—")}）${s.level ? `；等级：${s.level}（仅供参考）` : ""}</p>`);
}
async function showStandard(C, k) {
  const r = [...C.rows].find((x) => x.gender_code) || {}; const slot = C.d.config.slots.find((s) => s.key === k);
  const items = [...new Set(Object.values(slot.items))];
  let html = `<h3>${esc(slot.label)} 评分表（${esc(C.d.standard_version)}）</h3>`;
  for (const it of items) {
    const t = await api(`/api/standards/${it}`);
    const f = (v) => (t.time && it !== "run50" ? secToTxt(v) : v);
    for (const g of Object.keys(t.tables)) {
      html += `<h4>${g === "M" ? "男生" : "女生"}${esc(t.name)}（单位：${t.time && it !== "run50" ? "分·秒" : esc(t.unit)}，${t.direction === "high" ? "成绩不低于阈值即取该档" : "用时不慢于阈值即取该档"}）</h4>
      <table class="std-table"><tr><th>单项得分</th><th>大一大二</th><th>大三大四</th></tr>${t.tables[g].map((l) => `<tr><td>${l.score}</td><td>${f(l["12"])}</td><td>${f(l["34"])}</td></tr>`).join("")}</table>`;
      if (t.bonus && t.bonus[g]) html += `<p class="small">加分规则：成绩优于100分档后，${t.direction === "low" ? "每减少" : "每超过"}下列${t.time ? "秒数" : "次数"}对应加分（单项目最高加10分）：<br>${t.bonus[g].map((b) => `+${b.bonus}分：${b["12"]}${t.time ? "秒" : "次"}`).join("；")}</p>`;
      else html += `<p class="small muted">该项目不设加分。成绩低于10分档记0分。</p>`;
    }
  }
  modal(html);
}
function secToTxt(v) { const m = Math.floor(v / 60); const s = +(v - m * 60).toFixed(1); return `${m}'${String(s).padStart(2, "0")}"`; }

async function rowMenu(C, eid) {
  const r = C.byId.get(eid);
  const h = await api(`/api/classes/${C.id}/history/${eid}`);
  const st = h.standard;
  const fieldName = { a_raw: "项目一原始成绩", a_status: "项目一状态", b_raw: "项目二原始成绩", b_status: "项目二状态", skill: "运动技能评定", regular: "平时成绩", note: "备注" };
  modal(`<h3>${esc(r.student_no)} ${esc(r.name)}</h3>
    <h4>标准档判定依据</h4>${st ? `<div class="kv"><div>标准档</div><div>${st.grade_num ? `大${st.grade_num}，` : ""}${esc(st.band_label)}${st.pending ? " <span class='tag warn'>待确认</span>" : ""}</div>
      <div>判定来源</div><div>${esc(st.source || "—")}</div><div>来源字段</div><div>${esc(Object.entries(st.detail || {}).map(([k, v]) => `${k}：${typeof v === "object" && v ? JSON.stringify(v) : v ?? "—"}`).join("；"))}</div>
      ${st.reasons.length ? `<div>待确认原因</div><div>${esc(st.reasons.join("；"))}</div>` : ""}<div>认定</div><div>${esc(st.decided_by)} ${esc(st.decided_at)}</div></div>` : "<p class='muted'>暂无</p>"}
    <h4>信息有误</h4><div class="row"><input type="text" id="ieNote" style="flex:1" placeholder="例如：性别应为女；实际为2024级" value="${esc(r.info_error || "")}"><button id="ieBtn">${r.info_error ? "更新" : "标记"}</button>${r.info_error ? '<button id="ieClr">取消标记</button>' : ""}</div>
    <p class="muted small">性别、年级、标准档不在表格内修改，由体育部统一更正。</p>
    ${C.d.editable ? `<h4>备注</h4><div class="row"><input type="text" id="noteIn" style="flex:1" placeholder="作弊或违纪时请写明原因，会导出到成绩单备注" value="${esc(r.values.note || "")}"><button id="noteBtn">保存备注</button></div>` : ""}
    <h4>修改记录</h4>${h.history.length ? `<table class="list small"><tr><th>时间</th><th>字段</th><th>原值</th><th>新值</th><th>换算</th><th>版本</th><th>操作人</th></tr>
      ${h.history.map((x) => `<tr><td>${esc(x.at)}</td><td>${esc(fieldName[x.field] || x.field)}</td><td>${esc(x.old ?? "")}</td><td>${esc(x.new ?? "")}</td>
      <td>${x.computed && x.computed.slot && x.computed.slot.score !== null ? `${x.computed.slot.level ?? ""}分档→${x.computed.slot.score}分→折算${x.computed.slot.folded}` : esc((x.computed && x.computed.reason) || "")}${x.computed && x.computed.total !== undefined && x.computed.total !== null ? `；总${x.computed.total}` : ""}</td>
      <td>${esc(x.version)}</td><td>${esc(x.operator)}</td></tr>`).join("")}</table>` : "<p class='muted'>暂无</p>"}`, (el) => {
    const mark = async (note) => { try { await api(`/api/classes/${C.id}/info-error`, { method: "POST", body: { enrollment_id: eid, note } }); r.info_error = note || null; refreshRows(C, new Set([eid])); closeModal(); toast(note ? "已标记，体育部会统一核对更正" : "已取消标记"); } catch (e) { toast(e.message, true); } };
    $("#ieBtn", el).onclick = () => { const v = $("#ieNote", el).value.trim(); if (!v) return toast("请写明哪里有误", true); mark(v); };
    if ($("#ieClr", el)) $("#ieClr", el).onclick = () => mark("");
    if ($("#noteBtn", el)) $("#noteBtn", el).onclick = () => { enqueue(C, eid, "note", $("#noteIn", el).value.trim()); closeModal(); };
  });
}

async function submitDialog(C) {
  await flushQueue(C);
  const ck = await api(`/api/classes/${C.id}/check`);
  const sec = (title, list, tip) => `<h4>${title}（${list.length}）</h4>${list.length ? `<ul>${list.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : `<p class="muted small">无</p>`}${tip && list.length ? `<p class="small muted">${tip}</p>` : ""}`;
  modal(`<h3>提交前校验</h3><div class="checklist">
    ${sec("未录入项", ck.missing, "未录入的单元格不会按0分处理。")}
    ${sec("年级待确认项", ck.pending, "这些学生须由体育部确认后才能提交。")}
    ${sec("加分超限项", ck.bonus)}
    ${sec("成绩突变项（与上学期同项目偏差超过25%）", ck.sudden)}</div>
    <p>提交后本班成绩锁定，如需更正须提交撤回申请，由体育部审核解锁。</p>
    <div class="row"><span class="spacer"></span><button id="cancelSub">返回修改</button><button class="primary" id="doSub" ${ck.blocking ? "disabled" : ""}>确认提交</button></div>
    ${ck.blocking ? `<div class="notice err" style="margin-top:10px">本班存在年级或性别待确认的学生，暂不能提交。</div>` : ""}`, (el) => {
    $("#cancelSub", el).onclick = closeModal;
    $("#doSub", el).onclick = async () => { try { await api(`/api/classes/${C.id}/submit`, { method: "POST" }); closeModal(); toast("已提交"); viewClass(C.id); } catch (e) { toast(e.message, true); } };
  });
}
function unlockDialog(C) {
  modal(`<h3>申请撤回</h3><p>说明需要更正的原因，体育部审核通过后本班恢复为“录入中”。</p><textarea id="ulReason" rows="4" style="width:100%"></textarea>
    <div class="row" style="margin-top:10px"><span class="spacer"></span><button class="primary" id="ulBtn">提交申请</button></div>`, (el) => {
    $("#ulBtn", el).onclick = async () => { try { await api(`/api/classes/${C.id}/unlock-request`, { method: "POST", body: { reason: $("#ulReason", el).value } }); closeModal(); toast("撤回申请已提交"); viewClass(C.id); } catch (e) { toast(e.message, true); } };
  });
}
function configDialog(C) {
  const cfg = JSON.parse(JSON.stringify(C.d.config));
  const opts = { M: [["run1000", "1000米跑"], ["run50", "50米跑"], ["long_jump", "立定跳远"], ["pull_up", "引体向上"]], F: [["run800", "800米跑"], ["run50", "50米跑"], ["long_jump", "立定跳远"], ["sit_up", "一分钟仰卧起坐"]] };
  const sel = (k, g) => `<select data-k="${k}" data-g="${g}">${opts[g].map(([v, l]) => `<option value="${v}" ${cfg.slots.find((s) => s.key === k).items[g] === v ? "selected" : ""}>${l}</option>`).join("")}</select>`;
  modal(`<h3>本班体测项目与权重</h3><p class="muted small">修改会留痕（操作人、时间、原值与新值），并重算本班全部学生的折算分与总成绩。已录入原始成绩后只能调整权重。</p>
    <table class="list"><tr><th></th><th>名称</th><th>男生项目</th><th>女生项目</th><th>权重（%）</th></tr>
    ${cfg.slots.map((s) => `<tr><td>${s.key === "a" ? "项目一" : "项目二"}</td><td><input type="text" data-label="${s.key}" value="${esc(s.label)}" size="10"></td><td>${sel(s.key, "M")}</td><td>${sel(s.key, "F")}</td><td><input type="number" data-w="${s.key}" value="${s.weight * 100}" min="0" max="100" step="1" style="width:80px"></td></tr>`).join("")}
    <tr><td></td><td>运动技能评定</td><td colspan="2" class="muted small">满分＝权重</td><td><input type="number" id="wSkill" value="${cfg.skill_weight * 100}" style="width:80px"></td></tr>
    <tr><td></td><td>平时成绩</td><td colspan="2" class="muted small">满分＝权重</td><td><input type="number" id="wReg" value="${cfg.regular_weight * 100}" style="width:80px"></td></tr></table>
    <p id="wSum" class="small"></p><div class="row"><span class="spacer"></span><button class="primary" id="cfgSave">保存并重算</button></div>`, (el) => {
    const sum = () => { const t = $$("[data-w]", el).reduce((a, x) => a + Number(x.value), 0) + Number($("#wSkill", el).value) + Number($("#wReg", el).value); $("#wSum", el).innerHTML = t === 100 ? "合计 100%" : `<span style="color:var(--err)">合计 ${t}%，通常应为100%</span>`; };
    el.addEventListener("input", sum); sum();
    $("#cfgSave", el).onclick = async () => {
      for (const s of cfg.slots) { s.weight = Number($(`[data-w="${s.key}"]`, el).value) / 100; s.label = $(`[data-label="${s.key}"]`, el).value; s.items = { M: $(`[data-k="${s.key}"][data-g="M"]`, el).value, F: $(`[data-k="${s.key}"][data-g="F"]`, el).value }; }
      cfg.skill_weight = Number($("#wSkill", el).value) / 100; cfg.regular_weight = Number($("#wReg", el).value) / 100;
      try { await api(`/api/classes/${C.id}/config`, { method: "PUT", body: { cfg } }); closeModal(); toast("已保存并重算"); viewClass(C.id); } catch (e) { toast(e.message, true); }
    };
  });
}
function uploadDialog(C) {
  modal(`<h3>整班上传成绩</h3><p class="muted small">先“下载录入模板”，线下填好后上传。系统按学号匹配、逐行校验；空单元格不修改原值。导入后可回退。</p>
    <input type="file" id="upFile" accept=".xlsx"><div id="upRes" style="margin-top:12px"></div>`, (el) => {
    $("#upFile", el).onchange = async (e) => {
      const fd = new FormData(); fd.append("file", e.target.files[0]);
      try {
        const r = await api(`/api/classes/${C.id}/upload`, { method: "POST", body: fd });
        $("#upRes", el).innerHTML = `<p>本班 ${r.roster} 人，可写入 <b>${r.students}</b> 人共 <b>${r.cells}</b> 个单元格，将覆盖这些单元格的现有值。</p>
          ${r.errors.length ? `<div class="notice err">${r.errors.length} 个单元格校验不通过，不会写入：<ul>${r.errors.slice(0, 50).map((x) => `<li>第${x.row}行 ${esc(x.student_no)} ${esc(x.name)}：${esc(x.value)} — ${esc(x.error)}</li>`).join("")}</ul></div>` : ""}
          ${r.unknown.length ? `<div class="notice">${r.unknown.length} 行学号不在本班：${esc(r.unknown.slice(0, 20).join("、"))}</div>` : ""}
          <div class="row"><span class="spacer"></span><button class="primary" id="upGo" ${r.cells ? "" : "disabled"}>确认导入</button></div>`;
        $("#upGo", el).onclick = async () => {
          try {
            const res = await api(`/api/classes/${C.id}/upload/commit`, { method: "POST", body: { token: r.token } });
            $("#upRes", el).innerHTML = `<p>已导入 ${res.saved} 个单元格。</p><button id="upUndo">回退这次导入</button> <button id="upClose" class="primary">完成</button>`;
            $("#upClose", el).onclick = () => { closeModal(); viewClass(C.id); };
            $("#upUndo", el).onclick = async () => { await api(`/api/classes/${C.id}/upload/${res.batch}/revert`, { method: "POST" }); toast("已回退"); closeModal(); viewClass(C.id); };
          } catch (e2) { toast(e2.message, true); }
        };
      } catch (err) { toast(err.message, true); }
    };
  });
}

/* ------------------------------------------------------------ 管理端 */
async function viewAdmin(tab) {
  if (!state.me.is_admin) { location.hash = "#/"; return; }
  tab = tab || "overview";
  const tabs = [["overview", "概览"], ["semesters", "学期管理"], ["import", "课程信息导入"], ["pending", "待确认清单"], ["unlock", "撤回审核"], ["logs", "操作日志"], ["users", "账号"]];
  $("#app").innerHTML = `<div class="card"><div class="tabs">${tabs.map(([k, l]) => `<a href="#/admin/${k}" class="${k === tab ? "on" : ""}">${l}</a>`).join("")}</div><div id="adminBody"></div></div>`;
  const fn = { overview: adOverview, semesters: adSemesters, import: adImport, pending: adPending, unlock: adUnlock, logs: adLogs, users: adUsers }[tab];
  try { await fn($("#adminBody")); } catch (e) { $("#adminBody").innerHTML = `<div class="notice err">${esc(e.message)}</div>`; }
}
async function adOverview(el) {
  const sem = curSem();
  if (!sem) { el.innerHTML = `<div class="notice">还没有学期，请先到“学期管理”新建。</div>`; return; }
  const o = await api(`/api/admin/semesters/${sem.id}/overview`);
  el.innerHTML = `<h2>${esc(sem.name)}</h2>
   ${!o.classes ? `<div class="notice">本学期课程信息尚未导入。请到“课程信息导入”依次导入学生基础数据表、课程安排表、学生选课表。</div>` : ""}
   <div class="kv"><div>教学班</div><div>${o.classes}（未开始 ${o.status["未开始"] || 0}／录入中 ${o.status["录入中"] || 0}／已提交 ${o.status["已提交"] || 0}）</div>
   <div>有效选课记录</div><div>${o.enrollments}</div><div>待确认学生</div><div>${o.pending} <a href="#/admin/pending">去处理</a></div>
   <div>教师待补</div><div>${o.teacher_pending}</div><div>待审核撤回申请</div><div>${o.unlock_waiting} <a href="#/admin/unlock">去审核</a></div></div>
   <div class="row" style="margin-top:14px"><button class="primary" id="expAll">导出本学期全部成绩</button><a class="btn" href="#/">查看全部教学班</a></div>`;
  $("#expAll").onclick = () => download(`/api/admin/semesters/${sem.id}/export`);
  sessionStorage.setItem("classFilter", JSON.stringify({ scope: "all" }));
}
async function adSemesters(el) {
  const sems = await api("/api/semesters");
  const label = (it) => ({ run1000: "1000米跑", run800: "800米跑", run50: "50米跑", long_jump: "立定跳远", pull_up: "引体向上", sit_up: "仰卧起坐" }[it] || it);
  el.innerHTML = `<div class="row"><input type="text" id="newSem" placeholder="例如 2026-2027学年秋季学期" size="30"><button class="primary" id="newSemBtn">新建学期</button>
    <span class="muted small">新建学期时自动带入默认体测项目配置：秋季耐力跑20%、立定跳远10%；春季50米跑20%、引体向上/仰卧起坐10%。</span></div>
    <table class="list" style="margin-top:12px"><tr><th>学期</th><th>体测项目配置（默认）</th><th>状态</th><th>操作</th></tr>
    ${sems.map((s) => `<tr><td>${esc(s.name)}</td><td>${s.config.slots.map((x) => `${label(x.items.M)}${x.items.M !== x.items.F ? "/" + label(x.items.F) : ""} ${x.weight * 100}%`).join("、")}、技能 ${s.config.skill_weight * 100}%、平时 ${s.config.regular_weight * 100}%</td>
      <td>${s.is_current ? '<span class="tag cur">当前学期</span>' : s.entry_open ? '<span class="tag warn">已开放补录</span>' : '<span class="tag">只读</span>'}</td>
      <td>${s.is_current ? "" : `<button class="link" data-cur="${s.id}">设为当前学期</button> <button class="link" data-open="${s.id}" data-v="${s.entry_open ? 0 : 1}">${s.entry_open ? "关闭补录窗口" : "开放补录窗口"}</button>`}</td></tr>`).join("")}</table>`;
  $("#newSemBtn").onclick = async () => { try { await api("/api/admin/semesters", { method: "POST", body: { label: $("#newSem").value } }); await loadSemesters(); renderTop(); adSemesters(el); toast("已新建"); } catch (e) { toast(e.message, true); } };
  $$("[data-cur]", el).forEach((b) => (b.onclick = async () => { await api(`/api/admin/semesters/${b.dataset.cur}`, { method: "PATCH", body: { is_current: true } }); await loadSemesters(); renderTop(); adSemesters(el); }));
  $$("[data-open]", el).forEach((b) => (b.onclick = async () => { await api(`/api/admin/semesters/${b.dataset.open}`, { method: "PATCH", body: { entry_open: b.dataset.v === "1" } }); await loadSemesters(); adSemesters(el); }));
}
async function adImport(el) {
  const sem = curSem();
  if (!sem) { el.innerHTML = `<div class="notice">请先新建学期。</div>`; return; }
  el.innerHTML = `<p>导入到：<b>${esc(sem.name)}</b>（在顶部切换学期）。建议顺序：学生基础数据表 → 课程安排表 → 学生选课表。</p>
    <div class="row"><select id="impKind"><option value="base">学生基础数据表（性别、院系补全）</option><option value="schedule">课程安排表</option><option value="enroll">学生选课表</option></select>
    <input type="file" id="impFile" accept=".xlsx"></div><div id="impStep" style="margin-top:14px"></div>`;
  $("#impFile").onchange = async (e) => {
    const fd = new FormData(); fd.append("kind", $("#impKind").value); fd.append("file", e.target.files[0]);
    $("#impStep").innerHTML = `<p class="muted">正在解析文件…</p>`;
    try { const info = await api("/api/admin/import/upload", { method: "POST", body: fd }); impMapping(info, sem); }
    catch (err) { $("#impStep").innerHTML = `<div class="notice err">${esc(err.message)}</div>`; }
  };
}
function impMapping(info, sem) {
  const el = $("#impStep");
  let sh = info.sheets[0];
  const draw = () => {
    el.innerHTML = `<h3>字段映射</h3><div class="row">工作表 <select id="impSheet">${info.sheets.map((s) => `<option ${s.name === sh.name ? "selected" : ""}>${esc(s.name)}</option>`).join("")}</select>
      表头在第 <input type="number" id="impHr" value="${sh.header_row}" style="width:60px"> 行，约 ${sh.rows} 行数据</div>
      <div class="map-grid" style="margin-top:10px">${info.fields.map((f) => `<div>${esc(f.label)}${f.required ? ' <span style="color:var(--err)">*</span>' : ""}</div>
        <select data-map="${f.code}"><option value="">（不导入）</option>${sh.headers.map((h) => `<option ${sh.mapping[f.code] === h ? "selected" : ""}>${esc(h)}</option>`).join("")}</select>`).join("")}</div>
      ${info.kind !== "base" ? `<h3>导入方式</h3><div class="row"><label><input type="radio" name="mode" value="overwrite" checked> 覆盖（默认，按教学班号更新，可重复执行）</label>
      <label><input type="radio" name="mode" value="new"> 新增（仅首次）</label><label><input type="radio" name="mode" value="append"> 追加（只导入尚未存在的教学班）</label>
      <label><input type="radio" name="mode" value="rebuild"> 重建（以本文件为准，保留已有成绩）</label></div>` : ""}
      <div class="row" style="margin-top:12px"><button class="primary" id="impCheck">生成预检报告</button></div><div id="impReport"></div>`;
    $("#impSheet").onchange = (e) => { sh = info.sheets.find((s) => s.name === e.target.value); draw(); };
    $("#impCheck").onclick = check;
  };
  const args = () => {
    const mapping = {}; $$("[data-map]", el).forEach((s) => { if (s.value) mapping[s.dataset.map] = s.value; });
    const m = $("input[name=mode]:checked", el);
    return { token: info.token, kind: info.kind, sheet: sh.name, header_row: Number($("#impHr").value), mapping, semester_id: sem.id, mode: m ? m.value : "overwrite" };
  };
  const check = async () => {
    const a = args(); $("#impReport").innerHTML = `<p class="muted">预检中…</p>`;
    try {
      const r = await api("/api/admin/import/precheck", { method: "POST", body: a });
      const plan = Object.entries(r.plan).map(([k, v]) => `${({ total: "教学班", new: "新增", update: "更新", absent: "文件中没有的已有教学班", rows: "有效记录", students: "学生", classes: "教学班", skipped_closed: "关闭班记录（不导入）", male: "男", female: "女" })[k] || k} ${v}`).join("，");
      $("#impReport").innerHTML = `<h3>预检报告</h3><p>共读取 ${r.rows} 行。${esc(plan)}</p>
        <ul class="report">${r.items.map((i) => `<li class="lv-${i.level}">【${({ error: "阻断", warn: "提示", info: "说明" })[i.level]}】${esc(i.message)}${i.advice ? `<span class="muted"> — ${esc(i.advice)}</span>` : ""}${i.rows.length ? `<div class="small muted">行号：${i.rows.slice(0, 30).join("、")}${i.row_count > 30 ? " …" : ""}</div>` : ""}</li>`).join("") || "<li>未发现问题</li>"}</ul>
        <div class="row"><button class="primary" id="impGo" ${r.blocking ? "disabled" : ""}>确认导入</button>${r.blocking ? '<span class="muted">请先处理“阻断”项</span>' : ""}</div>`;
      if (!r.blocking) $("#impGo").onclick = async () => {
        if (a.mode === "rebuild" && !confirm("重建将以本文件为准：文件中没有、且尚无成绩的教学班和名单会被删除（已有成绩的保留）。确定继续？")) return;
        a.confirm_rebuild = a.mode === "rebuild";
        try { const res = await api("/api/admin/import/commit", { method: "POST", body: a }); $("#impReport").innerHTML = `<div class="notice info">导入完成：${esc(JSON.stringify(res.result))}</div>`; toast("导入完成"); }
        catch (e) { toast(e.message, true); }
      };
    } catch (e) { $("#impReport").innerHTML = `<div class="notice err">${esc(e.message)}</div>`; }
  };
  draw();
}
async function adPending(el) {
  const sem = curSem(); if (!sem) return;
  const d = await api(`/api/admin/semesters/${sem.id}/pending`);
  el.innerHTML = `<p>${esc(sem.name)} 共 <b>${d.students.length}</b> 名学生待确认：${Object.entries(d.summary).map(([k, v]) => `${esc(k)} ${v}`).join("，") || "无"}</p>
    <div class="row"><input type="text" id="pSearch" placeholder="按学号、姓名或原因筛选" size="30"></div>
    <table class="list" style="margin-top:10px"><thead><tr><th>学号</th><th>姓名</th><th>性别</th><th>暂定标准档</th><th>原因</th><th>选课记录</th><th>操作</th></tr></thead><tbody id="pBody"></tbody></table>
    <h3>教师待补的教学班（${d.teacher_pending.length}）</h3>
    ${d.teacher_pending.length ? `<table class="list"><tr><th>教学班</th><th>当前教师</th><th>指定责任教师</th></tr>${d.teacher_pending.map((c) => `<tr><td>${esc(c.class_name)}（${esc(c.class_no)}）</td><td>${esc(c.teacher_name || "—")}</td>
      <td><input type="text" placeholder="工号" size="10" data-tno="${c.id}"> <input type="text" placeholder="姓名" size="8" data-tname="${c.id}"> <button data-tset="${c.id}">指定</button></td></tr>`).join("")}</table>` : "<p class='muted'>无</p>"}
    <h3>教师标记“信息有误”（${d.info_errors.length}）</h3>
    ${d.info_errors.length ? `<table class="list"><tr><th>学号</th><th>姓名</th><th>教学班</th><th>教师</th><th>说明</th></tr>${d.info_errors.map((x) => `<tr><td>${esc(x.student_no)}</td><td>${esc(x.name)}</td><td>${esc(x.class_name)}</td><td>${esc(x.teacher)}</td><td>${esc(x.note)}</td></tr>`).join("")}</table>` : "<p class='muted'>无</p>"}`;
  const draw = () => {
    const q = $("#pSearch").value.trim();
    $("#pBody").innerHTML = d.students.filter((s) => !q || (s.student_no + s.name + s.reasons.join("")).includes(q)).map((s) => `<tr>
      <td class="nw">${esc(s.student_no)}</td><td class="nw">${esc(s.name)}</td><td class="nw">${esc(s.gender)}</td><td class="nw">${s.grade_num ? `大${s.grade_num}，` : ""}${esc(s.band_label)}<div class="muted small">${esc(s.source)}</div></td>
      <td>${s.reasons.map((r) => `<div>${esc(r)}</div>`).join("")}</td>
      <td>${s.enrollments.map((e) => `<div>${esc(e.class_name)}（${esc(e.teacher)}）上课年级 ${esc(e.grade_field || "空")}${s.enrollments.length > 1 ? (e.active ? ' <span class="tag">有效</span>' : ' <span class="tag err">已停用</span>') + (e.active && s.enrollments.filter((x) => x.active).length > 1 ? ` <button class="link" data-keep="${e.enrollment_id}" data-no="${s.student_no}">只保留这条</button>` : "") : ""}</div>`).join("")}</td>
      <td class="nw"><button class="link" data-fix="${s.student_no}">确认标准档</button></td></tr>`).join("");
    $$("[data-fix]", el).forEach((b) => (b.onclick = () => fixDialog(sem, d.students.find((s) => s.student_no === b.dataset.fix), () => adPending(el))));
    $$("[data-keep]", el).forEach((b) => (b.onclick = async () => { await api(`/api/admin/semesters/${sem.id}/students/${b.dataset.no}/keep`, { method: "POST", body: { enrollment_id: Number(b.dataset.keep) } }); toast("已保留"); adPending(el); }));
  };
  $("#pSearch").oninput = draw; draw();
  $$("[data-tset]", el).forEach((b) => (b.onclick = async () => { try { await api(`/api/admin/classes/${b.dataset.tset}`, { method: "PATCH", body: { staff_no: $(`[data-tno="${b.dataset.tset}"]`).value, teacher_name: $(`[data-tname="${b.dataset.tset}"]`).value } }); toast("已指定"); adPending(el); } catch (e) { toast(e.message, true); } }));
}
function fixDialog(sem, s, done) {
  modal(`<h3>确认标准档：${esc(s.student_no)} ${esc(s.name)}</h3>
    <div class="kv"><div>待确认原因</div><div>${esc(s.reasons.join("；"))}</div><div>判定依据</div><div>${esc(Object.entries(s.detail || {}).map(([k, v]) => `${k}：${typeof v === "object" && v ? JSON.stringify(v) : v ?? "—"}`).join("；"))}</div></div>
    <p class="small muted">确认结果写入年级校正名单（第一优先），此后不再重复判定；该生成绩按新档位重算并留痕。性别或学籍问题请先更正学生基础数据后重新导入。</p>
    <div class="map-grid"><div>方式</div><div><label><input type="radio" name="fx" value="cohort" checked> 实际跟读年级</label> <label><input type="radio" name="fx" value="band"> 直接指定标准档（特殊学籍）</label></div>
    <div>实际跟读年级</div><div><input type="number" id="fxCohort" placeholder="如 2024" value="${esc(s.detail && /^\d{4}$/.test(s.detail["上课年级"]) ? s.detail["上课年级"] : "")}"></div>
    <div>标准档</div><div><select id="fxBand"><option value="12">大一大二</option><option value="34">大三大四</option></select></div>
    <div>异动类型</div><div><select id="fxType">${["体育部确认", "休学", "降级", "跳级", "转专业重读", "提前毕业", "第二学士学位", "留学生", "预科生", "交换生", "港澳台学生"].map((x) => `<option>${x}</option>`).join("")}</select></div>
    <div>依据说明</div><div><input type="text" id="fxNote" style="width:100%" placeholder="必填，例如：教务处学籍异动名单 2026-09"></div></div>
    <div class="row" style="margin-top:12px"><span class="spacer"></span><button class="primary" id="fxGo">确认</button></div>`, (el) => {
    $("#fxGo", el).onclick = async () => {
      const byBand = $("input[name=fx]:checked", el).value === "band";
      try { await api(`/api/admin/semesters/${sem.id}/students/${s.student_no}/standard`, { method: "POST", body: { cohort: byBand ? null : $("#fxCohort", el).value, band: byBand ? $("#fxBand", el).value : null, change_type: $("#fxType", el).value, note: $("#fxNote", el).value } }); closeModal(); toast("已确认"); done(); }
      catch (e) { toast(e.message, true); }
    };
  });
}
async function adUnlock(el) {
  const rs = await api("/api/admin/unlock-requests");
  el.innerHTML = rs.length ? `<table class="list"><tr><th>时间</th><th>教学班</th><th>教师</th><th>原因</th><th>状态</th><th>操作</th></tr>${rs.map((r) => `<tr><td>${esc(r.at)}</td><td><a href="#/class/${r.class_id}">${esc(r.class_name)}</a></td><td>${esc(r.teacher)}</td><td>${esc(r.reason)}</td>
    <td>${esc(r.status)}${r.review_note ? `<div class="muted small">${esc(r.review_note)}</div>` : ""}</td><td>${r.status === "待审核" ? `<button data-ok="${r.id}">同意解锁</button> <button data-no="${r.id}">驳回</button>` : ""}</td></tr>`).join("")}</table>` : `<p class="muted">暂无撤回申请</p>`;
  const act = async (id, approve) => { const note = prompt(approve ? "审核意见（可选）" : "驳回原因") ?? ""; await api(`/api/admin/unlock-requests/${id}`, { method: "POST", body: { approve, note } }); toast("已处理"); adUnlock(el); };
  $$("[data-ok]", el).forEach((b) => (b.onclick = () => act(b.dataset.ok, true)));
  $$("[data-no]", el).forEach((b) => (b.onclick = () => act(b.dataset.no, false)));
}
async function adLogs(el, page = 1) {
  const sem = curSem();
  const action = el.dataset.action || "";
  const d = await api(`/api/admin/logs?semester_id=${sem ? sem.id : ""}&action=${encodeURIComponent(action)}&page=${page}`);
  el.innerHTML = `<div class="row"><select id="lgAct"><option value="">全部操作</option>${["保存", "提交", "撤回申请", "撤回审核", "改档", "导入", "导出", "配置", "回退导入", "指定教师", "学期设置", "登录"].map((a) => `<option ${a === action ? "selected" : ""}>${a}</option>`).join("")}</select><span class="muted">共 ${d.total} 条</span>
    <span class="spacer"></span>${page > 1 ? '<button id="lgPrev">上一页</button>' : ""}${d.total > page * 100 ? '<button id="lgNext">下一页</button>' : ""}</div>
    <table class="list small" style="margin-top:10px"><tr><th>时间</th><th>操作人</th><th>操作</th><th>对象</th><th>详情</th><th>IP</th></tr>
    ${d.logs.map((l) => `<tr><td>${esc(l.at)}</td><td>${esc(l.actor_name)} ${esc(l.actor)}</td><td>${esc(l.action)}</td><td>${esc(l.target)}</td><td style="max-width:520px;word-break:break-all">${esc(JSON.stringify(l.detail)).slice(0, 400)}</td><td>${esc(l.ip)}</td></tr>`).join("")}</table>`;
  $("#lgAct").onchange = (e) => { el.dataset.action = e.target.value; adLogs(el, 1); };
  if ($("#lgPrev")) $("#lgPrev").onclick = () => adLogs(el, page - 1);
  if ($("#lgNext")) $("#lgNext").onclick = () => adLogs(el, page + 1);
}
async function adUsers(el) {
  const us = await api("/api/admin/users");
  const sem = curSem();
  el.innerHTML = `<h3>教师口令（统一认证接入前使用）</h3>
    <p class="muted small">为本学期全部任课教师中还没有口令的账号生成初始口令，并下载成表格分发给教师；教师首次登录须修改。口令只在这份表格里出现一次，请妥善保管。</p>
    <div class="row"><button class="primary" id="pwGen" ${sem ? "" : "disabled"}>为${sem ? esc(sem.name) : "本学期"}任课教师生成口令</button></div>
    <h3>账号</h3>
    <div class="row"><input type="text" id="uNo" placeholder="工号"><button id="uAdd">设为管理员</button></div>
    <table class="list" style="margin-top:10px"><tr><th>工号</th><th>姓名</th><th>角色</th><th>口令</th><th></th></tr>${us.map((u) => `<tr><td>${esc(u.staff_no)}</td><td>${esc(u.name)}</td><td>${u.is_admin ? "体育部管理员" : "教师"}</td>
      <td>${u.has_password ? (u.must_change_password ? "已发放，未修改" : "已设置") : '<span class="muted">未发放</span>'}</td>
      <td>${u.is_admin ? `<button class="link" data-rm="${esc(u.staff_no)}">取消管理员</button>` : `<button class="link" data-add="${esc(u.staff_no)}">设为管理员</button>`}
      <button class="link" data-reset="${esc(u.staff_no)}">${u.has_password ? "重置口令" : "生成口令"}</button></td></tr>`).join("")}</table>`;
  const set = async (no, v) => { try { await api(`/api/admin/users/${encodeURIComponent(no)}`, { method: "POST", body: { is_admin: v } }); adUsers(el); } catch (e) { toast(e.message, true); } };
  $("#uAdd").onclick = () => set($("#uNo").value.trim(), true);
  $$("[data-rm]", el).forEach((b) => (b.onclick = () => set(b.dataset.rm, false)));
  $$("[data-add]", el).forEach((b) => (b.onclick = () => set(b.dataset.add, true)));
  $("#pwGen").onclick = async () => { await download("/api/admin/users/passwords", { semester_id: sem.id }); adUsers(el); };
  $$("[data-reset]", el).forEach((b) => (b.onclick = async () => {
    if (!confirm(`为 ${b.dataset.reset} 生成新的初始口令？原口令将失效。`)) return;
    await download("/api/admin/users/passwords", { staff_nos: [b.dataset.reset] }); adUsers(el);
  }));
}

route();
