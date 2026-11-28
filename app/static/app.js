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
    throw new Error(errText(data && data.detail, r.status));
  }
  return data;
}
/* 后端报错转成一句可读的话：detail 可能是字符串，也可能是 FastAPI 参数校验（422）给的数组或对象 */
function errText(detail, status) {
  if (typeof detail === "string" && detail) return detail;
  if (status === 422 || Array.isArray(detail)) {
    const fields = (Array.isArray(detail) ? detail : [])
      .map((x) => (x && Array.isArray(x.loc) ? x.loc[x.loc.length - 1] : "")).filter((x) => typeof x === "string" && x);
    return `请求参数有误（${status}）${fields.length ? "：" + [...new Set(fields)].join("、") : ""}，请刷新页面重试`;
  }
  if (detail && typeof detail === "object" && typeof detail.msg === "string") return detail.msg;
  return `请求失败（${status}）`;
}

function toast(msg, err = false) {
  const t = $("#toast"); t.textContent = msg; t.className = "toast" + (err ? " err" : ""); t.hidden = false;
  clearTimeout(toast._t); toast._t = setTimeout(() => (t.hidden = true), err ? 5000 : 2200);
}
function modal(html, onMount, onClose) {
  const m = $("#modal"); $(".modal-body", m).innerHTML = html; m.hidden = false;
  modal._onClose = onClose || null;
  if (onMount) onMount($(".modal-body", m));
}
function closeModal() {
  $("#modal").hidden = true;
  const f = modal._onClose; modal._onClose = null; if (f) f();
}
$("#modal .modal-close").onclick = closeModal;
$("#modal").addEventListener("mousedown", (e) => { if (e.target.id === "modal") closeModal(); });

async function download(path, body) {
  try {
    const r = await fetch(path, body === undefined ? { credentials: "same-origin" } :
      { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    if (!r.ok) { const d = await r.json().catch(() => ({})); throw new Error(errText(d.detail, r.status)); }
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
  closeZeroAsk();
  const h = location.hash || "#/";
  if (state.cls && state.cls.flush) await state.cls.flush();
  if (!h.startsWith("#/login")) {
    if (!state.me) { try { state.me = await api("/api/me"); } catch { return; } }
    if (state.me.must_change_password) {
      // 改口令之前后端只放行改口令接口，学期列表取不到；直接渲染改口令页
      if (!h.startsWith("#/password")) { location.hash = "#/password"; return; }
      renderTop(); return viewPassword();
    }
    if (!state.semesters.length) { try { await loadSemesters(); } catch (e) { toast(e.message, true); } }
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
  if (me) $("#logoutBtn").onclick = async () => { await api("/auth/logout", { method: "POST" }); state.me = null; state.semesters = []; location.href = "/#/login"; };
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
  $("#app").innerHTML = `<div class="login"><div class="card"><div class="login-brand"><img src="/static/logo.svg" alt=""><div>体育成绩录入平台<div class="muted small">中国人民大学体育部</div></div></div><h2>登录</h2>${err}
    ${mode.mode === "sso" ? `<p>使用数智人大统一身份认证登录。</p><a class="btn primary" href="/auth/login">统一身份认证登录</a>` : ""}
    ${mode.mode === "dev" ? `<div class="notice info">当前为开发测试模式：输入工号即可登录。正式部署时改为数智人大统一身份认证。</div>
       <label>工号<input type="text" id="devNo" lang="en" inputmode="text" autocomplete="username" autocapitalize="off" autocorrect="off" spellcheck="false" autofocus></label><button class="primary" id="devBtn">登录</button>` : ""}
    <details style="margin-top:18px"><summary class="muted small">体育部公共账号 / 应急管理员登录</summary>
      <label>工号<input type="text" id="locNo" lang="en" inputmode="text" autocomplete="username" autocapitalize="off" autocorrect="off" spellcheck="false"></label><label>口令<input type="password" id="locPw"></label>
      <button id="locBtn">登录</button></details></div></div>`;
  const done = async () => { state.me = null; state.semesters = []; location.hash = "#/"; };
  const devNo = $("#devNo");
  if (devNo) {
    requestAnimationFrame(() => devNo.focus());
    const go = async () => { try { await api("/api/auth/dev-login", { method: "POST", body: { staff_no: devNo.value } }); done(); } catch (e) { toast(e.message, true); } };
    $("#devBtn").onclick = go; devNo.onkeydown = (e) => { if (e.key === "Enter") go(); };
  }
  // 注意：onxxx 属性处理函数返回 false 会取消默认行为，按键就打不进字，所以这里不能写成 `cond && fn()`
  $("#locPw").onkeydown = (e) => { if (e.key === "Enter") $("#locBtn").click(); };
  $("#locBtn").onclick = async () => {
    try { await api("/api/auth/local-login", { method: "POST", body: { staff_no: $("#locNo").value, password: $("#locPw").value } }); done(); }
    catch (e) { toast(e.message, true); }
  };
}
function viewPassword() {
  $("#app").innerHTML = `<div class="login"><div class="card"><h2>修改口令</h2>
   <p class="muted small">公共账号和应急管理员的初始口令须在首次登录时修改。新口令不少于8位，并包含数字、大写字母、小写字母、特殊字符中的至少三类。</p>
   <label>原口令<input type="password" id="pw0"></label><label>新口令<input type="password" id="pw1"></label><label>再输一次<input type="password" id="pw2"></label>
   <button class="primary" id="pwBtn">保存</button></div></div>`;
  $("#pwBtn").onclick = async () => {
    if ($("#pw1").value !== $("#pw2").value) return toast("两次输入的新口令不一致", true);
    try { await api("/api/me/password", { method: "POST", body: { old: $("#pw0").value, new: $("#pw1").value } }); state.me = null; state.semesters = []; toast("口令已修改"); location.hash = "#/"; }
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
  else body = `<table class="list"><thead><tr><th>课程名称</th><th>教学班名称</th><th>上课时间</th><th>上课地点</th><th>校区</th><th>选课人数</th>${all ? "<th>授课教师</th>" : ""}<th>录入进度</th><th title="总评四舍五入后≥90分（即≥89.5分）的人数 ÷ 班级人数">优秀率</th><th>状态</th></tr></thead><tbody>
    ${d.classes.map((c) => `<tr class="clickable" data-id="${c.id}"><td>${esc(c.course_name)}</td><td><a href="#/class/${c.id}">${esc(c.class_name)}</a>${c.pending ? ` <span class="tag warn" title="有学生的年级或性别待体育部确认">待确认 ${c.pending}</span>` : ""}</td>
      <td>${esc(c.weekday)} ${esc(fmtPeriods(c.periods))}<div class="muted small">${esc(c.weeks)}</div></td><td>${esc(c.location)}</td><td>${esc(c.campus)}</td><td>${c.roster}</td>
      ${all ? `<td>${esc(c.teacher_name)}${c.teacher_pending ? ' <span class="tag err">教师待补</span>' : ""}</td>` : ""}
      <td>${c.entered}/${c.roster}</td><td>${excellentCell(c.excellent, c.roster)}</td><td><span class="tag s-${c.status}">${statusText(c.status)}</span></td></tr>`).join("")}</tbody></table>`;
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
const EXCELLENT_LINE = 89.5;   // 总评四舍五入后≥90分即为优秀
const EXCELLENT_WARN = 0.3;    // 优秀率超过30%标红提醒
function excellentCell(n, total) {
  if (!total) return "—";
  const r = n / total;
  return `<span style="${r > EXCELLENT_WARN ? "color:var(--err);font-weight:600" : ""}">${(r * 100).toFixed(1)}%</span><div class="muted small">${n}/${total}</div>`;
}
function updateExcellence(C) {
  const el = $("#exRate"); if (!el) return;
  const n = C.rows.length;
  const ex = C.rows.filter((r) => r.total !== null && r.total >= EXCELLENT_LINE).length;
  const done = C.rows.filter((r) => r.complete).length;
  const rate = n ? ex / n : 0;
  const over = rate > EXCELLENT_WARN;
  el.className = "notice " + (over ? "err" : "info");
  el.innerHTML = `<b>优秀率 ${(rate * 100).toFixed(1)}%</b>　优秀（总评≥89.5分）${ex} 人 / 全班 ${n} 人，已录完 ${done} 人${over ? "　<b>已超过30%，请注意控制优秀比例</b>" : ""}`;
}
/* 只有体测需要提交：班级状态“已提交”指体测已提交 */
function statusText(s) { return s === "已提交" ? "体测成绩已提交" : s; }
function fmtPeriods(p) { const m = String(p || "").match(/\d{2}/g); return m ? `第${m.map(Number).join("-")}节` : p || ""; }

/* ------------------------------------------------------------ 录入界面 */
const SAVE_EVERY = 60000;
const SORTS = { no: "学号", name: "姓名", total: "总成绩（高→低）", missing: "未录入的在前" };
async function viewClass(id) {
  const d = await api(`/api/classes/${id}`);
  const C = { id: Number(id), d, rows: d.rows, byId: new Map(d.rows.map((r) => [r.enrollment_id, r])), queue: new Map(), undo: [], sel: new Set(), filter: "", errors: new Map(), savedAt: null };
  state.cls = C;
  C.flush = () => flushQueue(C);
  C.nextSave = Date.now() + SAVE_EVERY;
  restoreDraft(C);
  renderClass(C);
  // 固定每分钟自动保存一次，右上角倒计时；离开本班时立即存掉剩下的修改
  clearInterval(state.autosave);
  state.autosave = setInterval(() => {
    if (state.cls !== C) { clearInterval(state.autosave); flushQueue(C); return; }
    if (Date.now() >= C.nextSave) { C.nextSave = Date.now() + SAVE_EVERY; flushQueue(C); }
    paintSaveState(C);
  }, 1000);
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

/* ---- 填空式输入：每种项目一套空格，填满一个空自动跳到下一个空 ---- */
const digitsOnly = (s) => String(s).replace(/[^\d]/g, "");
function segSpec(C, item, field) {
  if (field === "skill" || field === "regular") {
    const max = Math.round((field === "skill" ? C.d.config.skill_weight : C.d.config.regular_weight) * 100);
    const n = String(max).length;
    return { segs: [{ n, max: n + 2, dec: true, after: `/${max}`, full: () => false }],
      join: (a) => a[0], split: (t) => [String(t ?? "")] };
  }
  if (item === "run1000" || item === "run800") {
    return { segs: [{ n: 1, after: "'" }, { n: 2, after: "\"", full: () => false }],
      join: (a) => (a[0] === "" && a[1] === "" ? "" : `${a[0] || 0}'${(a[1] || "0").padStart(2, "0")}"`),
      split: (t) => {
        const s = String(t ?? "").trim(); if (!s) return ["", ""];
        let m = s.match(/^(\d+)\s*(?:'|:|：|分|’|′)\s*(\d+(?:\.\d+)?)/); if (m) return [m[1], m[2]];
        m = s.match(/^(\d)(\d{2})$/); if (m) return [m[1], m[2]];
        return [s, ""];
      } };
  }
  if (item === "run50") {
    return { segs: [{ n: 2, after: ".", full: () => false }, { n: 1, after: "s", full: () => false }],
      join: (a) => (a[0] === "" && a[1] === "" ? "" : a[1] === "" ? a[0] : `${a[0] || 0}.${a[1]}`),
      split: (t) => { const s = String(t ?? "").replace(/[秒sS"]/g, "").trim(); if (!s) return ["", ""]; const m = s.match(/^(\d+)(?:\.(\d))?/); return m ? [m[1], m[2] || ""] : [s, ""]; } };
  }
  const simple = { long_jump: [3, "cm"], pull_up: [2, "个"], sit_up: [2, "个"], vital_capacity: [4, "ml"], sit_reach: [5, "cm", true] }[item] || [8, ""];
  return { segs: [{ n: simple[0], after: simple[1], dec: !!simple[2], neg: !!simple[2], full: () => false }],
    join: (a) => a[0], split: (t) => [String(t ?? "").replace(/(cm|厘米|个|次|ml|毫升)$/i, "").trim()] };
}
function segFull(sp, i, v) { const s = sp.segs[i]; return s.full ? s.full(v) : v.length >= s.n; }
function segClean(sp, i, v) {
  const s = sp.segs[i];
  let out = String(v).replace(s.neg ? /[^\d.\-]/g : s.dec ? /[^\d.]/g : /[^\d]/g, "");
  return out.slice(0, s.max || s.n);
}
function segFieldItem(r, field) { return field.endsWith("_raw") ? r.display[field[0]].item : null; }
function segOf(el) { return el && el.closest ? el.closest(".seg") : null; }
function segValue(C, seg) {
  const r = C.byId.get(Number(seg.dataset.e)); const sp = segSpec(C, segFieldItem(r, seg.dataset.f), seg.dataset.f);
  return sp.join($$("input.sg", seg).map((x) => x.value.trim()));
}
function segSet(C, seg, text) {
  const r = C.byId.get(Number(seg.dataset.e)); const sp = segSpec(C, segFieldItem(r, seg.dataset.f), seg.dataset.f);
  const parts = sp.split(text); $$("input.sg", seg).forEach((x, i) => (x.value = parts[i] ?? ""));
}

function setSaveState(t) { const el = $("#saveState"); if (el) el.firstElementChild.textContent = t; }
function paintSaveState(C) {
  if (C._busy) return setSaveState("保存中…");
  if (C._fail) return setSaveState(`保存失败，修改暂存在本机，${Math.max(0, Math.ceil((C.nextSave - Date.now()) / 1000))} 秒后重试`);
  if (C.queue.size) return setSaveState(`${C.queue.size} 处修改未保存 · ${Math.max(0, Math.ceil((C.nextSave - Date.now()) / 1000))} 秒后自动保存`);
  if (C.errors.size) return setSaveState(`有 ${C.errors.size} 处输入没通过检查（标红）`);
  setSaveState(saveIdleText(C));
}
function commitActive(C) { const s = segOf(document.activeElement); if (s) commitSeg(C, s); }
function saveIdleText(C) { return C.savedAt ? `已保存 ${C.savedAt}` : "所有修改都已保存"; }
/* 体测锁定：教师提交后本班锁定；体育部也可按学期一键锁定全部班级 */
function lockText(C) { return C.d.sem_locked ? "体测成绩已由体育部锁定" : C.d.class.status === "已提交" ? "体测成绩已提交（已锁定）" : "体测成绩未提交"; }
function paintLockState(C) { const el = $("#saveState"); if (!el) return; el.lastElementChild.textContent = " · " + lockText(C); el.classList.toggle("done", !!C.d.pe_locked); }

function renderClass(C) {
  closeZeroAsk();
  const { d } = C; const c = d.class; const cfg = d.config; const sem = d.semester;
  const itemName = (k) => { const l = d.config_labels[k]; return l.M === l.F ? l.M : `${l.M}/${l.F}`; };
  const wt = (k) => (cfg.slots.find((s) => s.key === k).weight * 100).toFixed(0);
  const pendingN = C.rows.filter((r) => r.pending || !r.gender_code).length;
  const sortBy = localStorage.getItem("classSort") || "no";
  $("#app").innerHTML = `
  <div class="card">
    <div class="row"><a href="#/">← 返回教学班列表</a><span class="spacer"></span>
      <span class="save-pill" id="saveState"><span></span><span></span></span></div>
    <h2 style="margin-top:8px">${esc(c.class_name)}</h2>
    <div class="row muted">${esc(c.course_name)} · ${esc(c.weekday)} ${esc(fmtPeriods(c.periods))} · ${esc(c.location)} · ${C.rows.length} 人
      <details class="more-info"><summary>班级信息</summary><div class="class-head">
        <div><span>学年学期</span>${esc(sem.name)}</div><div><span>教学班号</span>${esc(c.class_no)}</div><div><span>授课教师</span>${esc(c.teacher_name)}</div>
        <div><span>周次</span>${esc(c.weeks)}</div><div><span>评分表</span>${esc(d.standard_version)}，按学生年级逐人匹配</div>
        <div><span>权重</span>${esc(itemName("a"))} ${wt("a")}%、${esc(itemName("b"))} ${wt("b")}%、技能 ${(cfg.skill_weight * 100).toFixed(0)}%、平时 ${(cfg.regular_weight * 100).toFixed(0)}% ${d.editable ? '<button class="link" id="cfgBtn">修改</button>' : ""}</div>
      </div></details></div>
    ${!d.editable || d.pe_locked ? `<div class="notice info" style="margin-top:10px">${esc(d.readonly_reason)}</div>` : ""}
    <div id="exRate" class="notice info" style="margin-top:10px"></div>
    <div id="zeroNote"></div>
    ${pendingN ? `<div class="notice" style="margin-top:10px">本班有 ${pendingN} 名学生的年级或性别待确认（黄色行），确认前按暂定标准档计算，体测不能提交。核对后可在该生的“更多”里直接改性别或标准档。</div>` : ""}
    <div class="toolbar">
      <label>排序 <select id="sortBy">${Object.entries(SORTS).map(([k, v]) => `<option value="${k}" ${k === sortBy ? "selected" : ""}>${v}</option>`).join("")}</select></label>
      <label><input type="checkbox" id="fMissing"> 只看未录入</label>
      <label><input type="checkbox" id="fIssue"> 只看有问题的学生</label>
      <span class="spacer"></span>
      <button id="exportBtn">导出 Excel</button>
      ${d.editable ? `<button id="tplBtn">下载录入模板</button><button id="uploadBtn">用模板整班上传</button>` : ""}
      ${d.editable ? `<button id="saveBtn" title="平时会自动保存，这里可以立即保存一次">保存</button>` : ""}
      ${d.editable && c.status !== "已提交" ? `<button class="primary" id="submitBtn" title="提交后两项体测成绩锁定；运动技能、平时成绩仍可修改">提交体测成绩</button>` : ""}
      ${d.editable && c.status === "已提交" && !d.sem_locked ? `<button id="withdrawBtn" title="直接撤回，不用体育部审核">撤回提交</button>` : ""}
    </div>
    <div class="muted small">填完一格按回车，先到右边一格，一行填完再到下一位学生；↑↓ 上下换格。耐力跑填完“分”会自动跳到“秒”。修改会自动保存。只有体测需要提交，体测录完后点“提交体测成绩”。体测填 0 时会问一句学生是不是没来测，要标特殊情况就点框里的“标记特殊情况”。免测、课上未测、缓考、退课、休学、选课未修等特殊情况和学生信息修改点每行的“更多”。</div>
  </div>
  <div class="grid-wrap"><table class="grid" id="grid">
    <thead><tr>
      <th rowspan="2" class="sticky">序号</th>
      <th rowspan="2" class="sticky2">学号</th><th rowspan="2">性别</th><th rowspan="2" class="opt">学院</th><th rowspan="2" class="opt">年级/标准档</th>
      <th rowspan="2">姓名</th><th rowspan="2">总成绩</th>
      ${["a", "b"].map((k) => `<th colspan="2">${esc(itemName(k))}<span class="qm" data-std="${k}" title="查看评分表与加分规则">?</span></th>`).join("")}
      <th rowspan="2">体测小计</th><th rowspan="2">姓名</th><th rowspan="2">运动技能</th><th rowspan="2">平时成绩</th><th rowspan="2"></th>
    </tr><tr>${["a", "b"].map(() => `<th>成绩</th><th>得分</th>`).join("")}</tr></thead>
    <tbody></tbody></table></div>`;
  paintLockState(C); paintSaveState(C);
  renderRows(C);
  paintZeros(C);
  bindClass(C);
  stickGrid();
}
// 固定列和表头第二行的位置按实际尺寸算：学号列紧贴序号列右边，「成绩/得分」行紧贴第一行下边。
// 列宽、行高随字体、缩放、数据变化，用 ResizeObserver 跟着重算，不写死像素。
function stickGrid() {
  const grid = $("#grid"); if (!grid) return;
  const first = $("thead th.sticky", grid); const head1 = $("thead tr", grid);
  const apply = () => {
    if (!grid.isConnected) { ro.disconnect(); return; }
    grid.style.setProperty("--stick2-left", `${first.getBoundingClientRect().width}px`);
    grid.style.setProperty("--head2-top", `${head1.getBoundingClientRect().height}px`);
  };
  const ro = new ResizeObserver(apply);
  ro.observe(first); ro.observe(head1);
  apply();
}

function isMissing(r) {
  const v = r.values; const miss = (k) => v[`${k}_status`] === "正常" && v[`${k}_raw`] === null;
  if (r.course_status && r.course_status !== "正常") return miss("a") || miss("b");
  return miss("a") || miss("b") || v.skill === null || v.regular === null;
}
function rowVisible(C, r) {
  if ($("#fMissing") && $("#fMissing").checked && !isMissing(r)) return false;
  if ($("#fIssue") && $("#fIssue").checked) {
    if (!(r.pending || !r.gender_code || r.issues.length || r.sudden.length || (r.info_error && !r.info_error_resolved) || [...C.errors.keys()].some((k) => k.startsWith(r.enrollment_id + ":")))) return false;
  }
  return true;
}
function sortedRows(C) {
  const by = ($("#sortBy") && $("#sortBy").value) || "no";
  const no = (a, b) => String(a.student_no).localeCompare(String(b.student_no), "zh", { numeric: true });
  const cmp = {
    no,
    name: (a, b) => String(a.name).localeCompare(String(b.name), "zh") || no(a, b),
    total: (a, b) => (b.total ?? -1) - (a.total ?? -1) || no(a, b),
    missing: (a, b) => Number(isMissing(b)) - Number(isMissing(a)) || no(a, b),
  }[by] || no;
  return [...C.rows].sort(cmp);
}
function renderRows(C) {
  const tb = $("#grid tbody");
  updateExcellence(C);
  tb.innerHTML = sortedRows(C).filter((r) => rowVisible(C, r)).map((r, i) => rowHtml(C, r, i + 1)).join("") || `<tr><td colspan="24" class="muted" style="padding:20px">没有符合条件的学生</td></tr>`;
  stickGridHead();
}
function rowHtml(C, r, no) {
  const ed = C.d.editable;
  const v = r.values;
  const cell = (field) => {
    const k = field[0]; const slot = field.endsWith("_raw") ? r.display[k] : null;
    const key = `${r.enrollment_id}:${field}`; const err = C.errors.get(key);
    if (slot && v[`${k}_status`] !== "正常") {
      return `<td class="in ro"><div class="seg st-only" data-e="${r.enrollment_id}" data-f="${field}"><button class="link stTag" data-e="${r.enrollment_id}" title="点击修改特殊情况">${esc(v[`${k}_status`])}</button></div></td>`;
    }
    // 课程成绩标了缓考、缺考、退课、休学、选课未修：技能和平时两格不再录入，显示状态，点开「更多」
    if (!slot && r.course_status && r.course_status !== "正常") {
      return `<td class="in ro"><div class="seg st-only" data-e="${r.enrollment_id}" data-f="${field}"><button class="link stTag" data-e="${r.enrollment_id}" title="课程成绩${esc(r.course_status)}，点击修改">${esc(r.course_status)}</button></div></td>`;
    }
    const ro = !ed || (slot && (!r.gender_code || C.d.pe_locked));
    const pend = C.queue.get(key);
    const val = pend ? pend.value : slot ? slot.raw_text : v[field] ?? "";
    const sp = segSpec(C, slot ? slot.item : null, field); const parts = sp.split(val);
    // 不在格子下面加说明，免得行高跟着变；错误原因放在鼠标悬停提示里
    const tip = err || (slot && !r.gender_code ? "性别待确认，暂不能录入" : (slot && slot.item_label) || "");
    const inputs = sp.segs.map((s, i) => `<input class="sg" data-i="${i}" value="${esc(parts[i] ?? "")}" style="width:${(s.max || s.n) > s.n && !s.neg && s.after.startsWith("/") ? s.n + 3 : Math.max(s.n, 1) + 0.9}ch" maxlength="${s.max || s.n}" inputmode="${s.dec ? "decimal" : "numeric"}" ${ro ? "readonly" : ""} autocomplete="off"><span class="sx">${esc(s.after)}</span>`).join("");
    return `<td class="in ${err ? "bad" : ""} ${ro ? "ro" : ""}"><div class="seg" data-e="${r.enrollment_id}" data-f="${field}" title="${esc(tip)}">${inputs}</div></td>`;
  };
  const scoreCell = (k) => {
    const s = r.display[k];
    const sc = s.score === null ? "" : `${fmt(s.score, s.score % 1 ? 1 : 0)}${s.bonus ? `<span class="bonus" title="含加分">+${s.bonus}</span>` : ""}`;
    return `<td class="lock num score-cell" data-ex="${r.enrollment_id}:${k}" title="${s.folded !== null && s.folded !== undefined ? `折算分 ${fmt(s.folded, 2)}，点击看计算过程` : "点击看计算过程"}">${sc}</td>`;
  };
  const flags = [];
  if (r.pending) flags.push(`<span class="tag warn" title="${esc(r.pending_reasons.join("；"))}">待确认</span>`);
  if (r.flags.includes("band_changed")) flags.push(`<span class="tag warn" title="体育部已变更该生标准档">档已变更</span>`);
  if (r.info_error && !r.info_error_resolved) flags.push(`<span class="tag err" title="${esc(r.info_error)}">其他问题</span>`);
  if (r.sudden.length) flags.push(`<span class="tag warn" title="${esc(r.sudden.join("；"))}">突变</span>`);
  return `<tr data-e="${r.enrollment_id}" data-no="${no}" class="${r.pending || !r.gender_code ? "pending" : ""}">
    <td class="sticky lock num">${no}</td>
    <td class="sticky2 lock">${esc(r.student_no)}</td><td class="lock">${esc(r.gender)}</td>
    <td class="lock trunc opt" title="${esc(r.college)}">${esc(r.college)}</td>
    <td class="lock opt">${r.grade_num ? `大${r.grade_num}` : ""} ${esc(r.band_label)}</td>
    <td class="lock">${esc(r.name)} ${flags.join(" ")}</td><td class="lock num"><b>${r.course_status && r.course_status !== "正常" ? esc(r.course_status) : fmt(r.total, 1)}</b></td>
    ${cell("a_raw")}${scoreCell("a")}${cell("b_raw")}${scoreCell("b")}
    <td class="lock num"><b>${fmt(r.subtotal, 2)}</b></td><td class="lock">${esc(r.name)}</td>${cell("skill")}${cell("regular")}
    <td class="lock"><button class="link rowMenu" data-e="${r.enrollment_id}">更多</button></td></tr>`;
}

function enqueue(C, eid, field, value, { undo = true } = {}) {
  const r = C.byId.get(eid);
  const key = `${eid}:${field}`;
  const old = field.endsWith("_raw") ? r.display[field[0]].raw_text : r.values[field] ?? "";
  if (String(old) === String(value) && !C.queue.has(key)) return;
  if (undo) C.undo.push({ eid, field, value: old });
  C.queue.set(key, { enrollment_id: eid, field, value });
  saveDraft(C);
  paintSaveState(C);
  schedulePreview(C, eid);
}
// 保存仍按每分钟一次；但离开单元格就向服务器要一次“只算不存”的换算，得分和总成绩立刻更新
function schedulePreview(C, eid) {
  (C._pv || (C._pv = new Set())).add(eid);
  clearTimeout(C._pvT); C._pvT = setTimeout(() => runPreview(C), 120);
}
async function runPreview(C) {
  const ids = new Set(C._pv); C._pv.clear();
  const changes = [...C.queue.values()].filter((x) => ids.has(x.enrollment_id));
  if (!changes.length) return;
  try {
    const res = await api(`/api/classes/${C.id}/preview`, { method: "POST", body: { changes } });
    if (state.cls !== C) return;
    const touched = new Set();
    for (const r of res.rows) {
      if (![...C.queue.values()].some((x) => x.enrollment_id === r.enrollment_id)) continue;  // 期间已保存，以保存结果为准
      const i = C.rows.findIndex((x) => x.enrollment_id === r.enrollment_id); if (i < 0) continue;
      r.seq = C.rows[i].seq; r.sudden = C.rows[i].sudden; C.rows[i] = r; C.byId.set(r.enrollment_id, r); touched.add(r.enrollment_id);
    }
    for (const ch of changes) C.errors.delete(`${ch.enrollment_id}:${ch.field}`);
    for (const er of res.errors) C.errors.set(`${er.enrollment_id}:${er.field}`, er.error);
    if (touched.size) refreshRows(C, touched);
  } catch (_) { /* 换算预览失败不影响保存，等保存时再算 */ }
}
async function flushQueue(C) {
  if (!C.queue.size || C._busy) return;
  C._busy = true;
  const items = [...C.queue.values()]; C.queue.clear();
  paintSaveState(C);
  try {
    const res = await api(`/api/classes/${C.id}/cells`, { method: "POST", body: { changes: items } });
    const warns = [];
    for (const it of res.results) {
      const key = `${it.enrollment_id}:${it.field}`;
      if (it.ok) { C.errors.delete(key); (it.warnings || []).forEach((w) => warns.push(`${C.byId.get(it.enrollment_id).name}：${w}`)); }
      else C.errors.set(key, it.error);
    }
    for (const r of res.rows) { const i = C.rows.findIndex((x) => x.enrollment_id === r.enrollment_id); if (i >= 0) { r.seq = C.rows[i].seq; C.rows[i] = r; C.byId.set(r.enrollment_id, r); } }
    if (C.d.class.status !== res.status) { C.d.class.status = res.status; paintLockState(C); }
    if (res.pe_zeros) { C.d.pe_zeros = res.pe_zeros; paintZeros(C); }
    saveDraft(C);
    refreshRows(C, new Set(items.map((i) => i.enrollment_id)));
    const nErr = res.results.filter((x) => !x.ok).length;
    C.savedAt = new Date().toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", hour12: false });
    C._fail = false;
    const firstErr = res.results.find((x) => !x.ok);
    if (firstErr) toast(`${C.byId.get(firstErr.enrollment_id).name}：${firstErr.error}`, true);
    else if (warns.length) toast(warns.slice(0, 3).join("；"));
  } catch (e) {
    for (const it of items) if (!C.queue.has(`${it.enrollment_id}:${it.field}`)) C.queue.set(`${it.enrollment_id}:${it.field}`, it);
    saveDraft(C);
    C._fail = true;
    toast(e.message, true);
  } finally { C._busy = false; paintSaveState(C); }
}
function refreshRows(C, ids) {
  updateExcellence(C);
  // 重画行时保住正在输入的那一格：焦点、已经打进去还没离开的数字
  const active = document.activeElement; const aseg = segOf(active);
  const keep = aseg ? { e: aseg.dataset.e, f: aseg.dataset.f, i: active.dataset.i, vals: $$("input.sg", aseg).map((x) => x.value), orig: aseg.dataset.orig, pos: active.selectionStart } : null;
  for (const id of ids) {
    const tr = $(`#grid tr[data-e="${id}"]`); if (!tr) continue;
    const t = document.createElement("tbody"); t.innerHTML = rowHtml(C, C.byId.get(id), Number(tr.dataset.no)); tr.replaceWith(t.firstElementChild);
  }
  if (keep && ids.has(Number(keep.e))) {
    const seg = $(`#grid .seg[data-e="${keep.e}"][data-f="${keep.f}"]`); if (!seg) return;
    const ins = $$("input.sg", seg); if (!ins.length) return;
    if (seg.dataset.orig === undefined) seg.dataset.orig = keep.orig ?? "";
    if (document.activeElement !== active) {
      ins.forEach((x, i) => (x.value = keep.vals[i] ?? x.value));
      const el = ins[Number(keep.i) || 0] || ins[0]; seg._refocus = true; el.focus(); seg._refocus = false;
      try { el.setSelectionRange(keep.pos, keep.pos); } catch (_) { /* 不支持的输入类型 */ }
    }
  }
}
function focusSeg(seg, last = false) {
  const ins = $$("input.sg", seg); const el = last ? ins[ins.length - 1] : ins[0];
  if (el) { el.focus(); el.select(); return true; } return false;
}
function moveFocus(seg, dRow) {
  const segs = $$(`#grid .seg[data-f="${seg.dataset.f}"]`);
  for (let i = segs.indexOf(seg) + dRow; i >= 0 && i < segs.length; i += dRow) if (focusSeg(segs[i])) return;
}
// 回车顺序：体测两项一组、技能和平时一组，先向右，一行填完再到下一行
const ENTER_GROUPS = [["a_raw", "b_raw"], ["skill", "regular"]];
function nextInRow(seg, d) {
  const g = ENTER_GROUPS.find((x) => x.includes(seg.dataset.f)) || [seg.dataset.f];
  const segs = $$("#grid .seg").filter((s) => g.includes(s.dataset.f));
  for (let i = segs.indexOf(seg) + d; i >= 0 && i < segs.length; i += d) if (focusSeg(segs[i])) return;
}
function commitSeg(C, seg, opts = {}) {
  if (!seg || !C.d.editable || !$("input.sg", seg) || $("input.sg", seg).readOnly) return;
  const val = segValue(C, seg);
  const changed = val !== (seg.dataset.orig ?? "");
  if (changed) enqueue(C, Number(seg.dataset.e), seg.dataset.f, val);
  seg.dataset.orig = val;
  if (changed && !opts.noAsk && isZeroText(seg.dataset.f, val) && askZero(C, seg)) return "ask";
  if (changed) closeZeroAsk();
}

/* ---- 体测填了 0：格子旁弹小框问是不是没来测 ----
   回车（或 Tab、离开这格）后弹出：这一格退出编辑状态，键盘不起作用，页面别处也点不了，
   只能用鼠标点框里的【标记特殊情况】或【确实是 0】。点【确实是 0】光标到下一格接着录；
   点【标记特殊情况】打开这名学生的“更多”，突出这一项的体测状态，老师自己选；
   不选直接关掉就保留 0，关掉后光标回到下一格。
   体测已锁定、粘贴、整班上传不弹，靠录入页上方的常驻提示兜底。 */
function isZeroText(field, val) { return /^[ab]_raw$/.test(field) && /^0+$/.test(String(val).replace(/[^\d]/g, "")); }
let zeroCtx = null;
function closeZeroAsk() {
  for (const id of ["#zeroAsk", "#zeroMask"]) { const el = $(id); if (el) el.remove(); }
  zeroCtx = null; document.removeEventListener("keydown", zeroAskKey, true);
}
function zeroAskKey(e) { if (zeroCtx) { e.preventDefault(); e.stopPropagation(); } }  // 等鼠标点，按键一律不起作用
function zeroLiveSeg() {  // 自动保存可能重画了这一行，按位置重新找到这一格
  const { seg } = zeroCtx;
  return $(`#grid .seg[data-e="${seg.dataset.e}"][data-f="${seg.dataset.f}"]`) || seg;
}
function askZero(C, seg) {
  closeZeroAsk();
  if (C.d.pe_locked || !C.d.editable) return false;
  zeroCtx = { C, seg };
  if (document.activeElement && seg.contains(document.activeElement)) document.activeElement.blur();
  const mask = document.createElement("div");
  mask.id = "zeroMask"; mask.className = "zero-mask";
  const box = document.createElement("div");
  box.id = "zeroAsk"; box.className = "zero-ask";
  box.innerHTML = `<span>学生没来测？</span><button class="primary" data-z="mark">标记特殊情况</button><button data-z="keep">确实是 0</button>`;
  document.body.append(mask, box);
  const rc = seg.getBoundingClientRect();
  box.style.left = `${Math.min(window.scrollX + rc.left, window.scrollX + document.documentElement.clientWidth - box.offsetWidth - 8)}px`;
  box.style.top = `${window.scrollY + rc.bottom + 4}px`;
  box.onclick = (e) => {
    const b = e.target.closest("[data-z]"); if (!b) return;
    const live = zeroLiveSeg(); const mark = b.dataset.z === "mark";
    closeZeroAsk();
    if (!mark) return nextInRow(live, 1);
    const eid = Number(live.dataset.e), f = live.dataset.f;
    markSpecial(C, eid, f[0], () => {  // 关掉“更多”后回到下一格；行可能已重画，按位置重新找
      const cur = $(`#grid .seg[data-e="${eid}"][data-f="${f}"]`);
      if (cur) nextInRow(cur, 1);
    });
  };
  document.addEventListener("keydown", zeroAskKey, true);
  return true;
}
/* 标记特殊情况：打开这名学生的“更多”，定位到填 0 那一项的体测状态。
   选了特殊情况按“更多”里的逻辑清成绩、留痕、重算；不选关掉就保留 0。 */
function markSpecial(C, eid, k, onClose) {
  rowMenu(C, eid, { focusSt: `${k}_status`, onClose }).catch((e) => { toast(e.message, true); if (onClose) onClose(); });
}
function paintZeros(C) {
  const el = $("#zeroNote"); if (!el) return;
  const zs = C.d.pe_zeros || [];
  if (!zs.length) { el.innerHTML = ""; return; }
  const can = C.d.editable && !C.d.pe_locked;
  el.innerHTML = `<div class="notice" style="margin-top:10px"><details><summary>本班有 <b>${zs.length}</b> 名学生体测填了 0，没来测或有其他特殊情况的请标记。点这里看是谁</summary>
    <ul class="zero-list">${zs.map((z) => `<li>${z.seq}. ${esc(z.student_no)} ${esc(z.name)}：${z.items.map((it) => `${esc(it.label)} 0 ${can ? `<button class="link" data-zm="${z.enrollment_id}:${it.key}">标记特殊情况</button>` : ""}`).join("；")}</li>`).join("")}</ul></details></div>`;
  $$("[data-zm]", el).forEach((b) => (b.onclick = () => { const [eid, k] = b.dataset.zm.split(":"); markSpecial(C, Number(eid), k); }));
}

/* 录入表随整页滚动；外框只管横向滚动，表头靠位移贴在顶栏下方 */
function stickGridHead() {
  const wrap = $(".grid-wrap"); const head = wrap && $("#grid thead");
  if (!head) return;
  const bar = $(".topbar"); const top = bar ? bar.getBoundingClientRect().bottom : 0;
  const box = wrap.getBoundingClientRect();
  const y = Math.max(0, Math.min(top - box.top, box.height - head.offsetHeight));
  wrap.style.setProperty("--head-y", `${y}px`);
}
window.addEventListener("scroll", stickGridHead, { passive: true });
window.addEventListener("resize", stickGridHead);

function bindClass(C) {
  const grid = $("#grid");
  grid.addEventListener("focusin", (e) => {
    const seg = segOf(e.target); if (!seg || !e.target.classList.contains("sg")) return;
    if (!seg.contains(e.relatedTarget) && !seg._refocus) seg.dataset.orig = segValue(C, seg);
    e.target.select();
  });
  grid.addEventListener("focusout", (e) => {
    const seg = segOf(e.target); if (!seg || !seg.isConnected || seg.contains(e.relatedTarget)) return;
    commitSeg(C, seg);
  });
  grid.addEventListener("input", (e) => {
    const t = e.target; const seg = segOf(t); if (!seg || !t.classList.contains("sg")) return;
    const r = C.byId.get(Number(seg.dataset.e)); const sp = segSpec(C, segFieldItem(r, seg.dataset.f), seg.dataset.f);
    const i = Number(t.dataset.i); const raw = t.value;
    // 打了分隔符（' : . 空格）就跳到下一个空
    const sep = /[^\d]$/.test(raw) && !(sp.segs[i].dec && raw.endsWith(".") && i === sp.segs.length - 1) && !(sp.segs[i].neg && raw === "-");
    t.value = segClean(sp, i, sep ? raw.slice(0, -1) : raw);
    const ins = $$("input.sg", seg);
    if (sep || segFull(sp, i, t.value)) {
      if (i < ins.length - 1) { ins[i + 1].focus(); ins[i + 1].select(); }
    }
  });
  grid.addEventListener("keydown", (e) => {
    const t = e.target; const seg = segOf(t); if (!seg || !t.classList.contains("sg")) return;
    const ins = $$("input.sg", seg); const i = Number(t.dataset.i);
    const atStart = t.selectionStart === 0 && t.selectionEnd === 0; const atEnd = t.selectionStart === t.value.length;
    if (e.key === "Enter" || e.key === "Tab") { e.preventDefault(); if (commitSeg(C, seg) !== "ask") nextInRow(seg, e.shiftKey ? -1 : 1); }
    else if (e.key === "ArrowDown") { e.preventDefault(); moveFocus(seg, 1); }
    else if (e.key === "ArrowUp") { e.preventDefault(); moveFocus(seg, -1); }
    else if (e.key === "ArrowLeft" && atStart && i > 0) { e.preventDefault(); ins[i - 1].focus(); ins[i - 1].select(); }
    else if (e.key === "ArrowRight" && atEnd && i < ins.length - 1) { e.preventDefault(); ins[i + 1].focus(); ins[i + 1].select(); }
    else if (e.key === "Backspace" && t.value === "" && i > 0) { e.preventDefault(); ins[i - 1].focus(); ins[i - 1].value = ins[i - 1].value.slice(0, -1); }
    else if (e.key === "Escape") { segSet(C, seg, seg.dataset.orig ?? ""); t.blur(); }
  });
  grid.addEventListener("paste", (e) => {
    const t = e.target; const seg = segOf(t); if (!seg || !t.classList.contains("sg") || !C.d.editable || t.readOnly) return;
    const text = (e.clipboardData || window.clipboardData).getData("text");
    const lines = text.replace(/\r/g, "").split("\n"); if (lines.length && lines[lines.length - 1] === "") lines.pop();
    e.preventDefault();
    if (lines.length <= 1) { segSet(C, seg, (lines[0] || "").split("\t")[0].trim()); commitSeg(C, seg, { noAsk: true }); return; }
    const segs = $$(`#grid .seg[data-f="${seg.dataset.f}"]`); const start = segs.indexOf(seg);
    let n = 0;
    lines.forEach((line, i) => {
      const s = segs[start + i]; const first = s && $("input.sg", s); if (!first || first.readOnly) return;
      const v = line.split("\t")[0].trim(); segSet(C, s, v); s.dataset.orig = v; enqueue(C, Number(s.dataset.e), s.dataset.f, v); n++;
    });
    toast(`已粘贴 ${n} 行，逐行检查中`);
  });
  grid.addEventListener("click", (e) => {
    const t = e.target;
    const ex = t.closest("[data-ex]"); if (ex) return showExplain(C, ...ex.dataset.ex.split(":"));
    if (t.dataset.std) return showStandard(C, t.dataset.std);
    if (t.classList.contains("rowMenu") || t.classList.contains("stTag")) return rowMenu(C, Number(t.dataset.e));
  });
  document.onkeydown = (e) => {
    if (state.cls !== C) return;
    const aseg = segOf(document.activeElement);
    const typing = aseg && segValue(C, aseg) !== (aseg.dataset.orig ?? "");
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "z" && !typing) {
      const u = C.undo.pop(); if (!u) return toast("没有可撤销的修改");
      e.preventDefault(); enqueue(C, u.eid, u.field, u.value, { undo: false });
      const s = $(`#grid .seg[data-e="${u.eid}"][data-f="${u.field}"]`); if (s && $("input.sg", s)) { segSet(C, s, u.value); s.dataset.orig = u.value; }
      toast("已撤销上一次修改");
    }
  };
  $("#sortBy").onchange = (e) => { localStorage.setItem("classSort", e.target.value); renderRows(C); };
  $("#fMissing").onchange = () => renderRows(C); $("#fIssue").onchange = () => renderRows(C);
  const on = (id, fn) => { const el = $(id); if (el) el.onclick = fn; };
  on("#saveBtn", async () => { commitActive(C); C.nextSave = Date.now() + SAVE_EVERY; await flushQueue(C); if (!C.queue.size && !C.errors.size) toast("已保存（还没有提交）"); });
  on("#exportBtn", async () => { commitActive(C); await flushQueue(C); download(`/api/classes/${C.id}/export?sort=${encodeURIComponent(($("#sortBy") && $("#sortBy").value) || "no")}`); });
  on("#tplBtn", () => download(`/api/classes/${C.id}/template`));
  on("#uploadBtn", () => uploadDialog(C));
  on("#submitBtn", () => submitDialog(C));
  on("#withdrawBtn", () => withdrawDialog(C));
  on("#cfgBtn", () => configDialog(C));
  // 关页面时修改已暂存在本机，下次打开自动补存；这里再提醒一次
  window.onbeforeunload = () => { commitActive(C); return C.queue.size ? "还有未保存的修改" : undefined; };
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

/* 「更多」弹窗，从上到下：特殊情况｜学生信息｜备注｜其他问题｜修改记录（收起）
   focusSt：从填 0 小框或 0 分清单打开时，那一项体测的状态框用和“待确认”一样的红框突出 */
async function rowMenu(C, eid, { focusSt = null, onClose = null } = {}) {
  const r = C.byId.get(eid);
  const h = await api(`/api/classes/${C.id}/history/${eid}`);
  const st = h.standard || {};
  const stu = h.student || {};
  const issue = h.issue || {};
  const fieldName = { a_raw: "项目一原始成绩", a_status: "项目一状态", b_raw: "项目二原始成绩", b_status: "项目二状态", skill: "运动技能评定", regular: "平时成绩", note: "备注", course_status: "课程成绩特殊情况" };
  const slots = C.d.config.slots;
  const ed = C.d.editable, peLock = C.d.pe_locked;
  const stSel = (k) => { const f = `${k}_status`; return `<label${focusSt === f ? ' class="pending-bad"' : ""}>${esc(r.display[k].item_label || slots.find((s) => s.key === k).label)} <select data-st="${f}" ${ed && !peLock ? "" : "disabled"}>${C.d.statuses.map((s) => `<option value="${s}" ${r.values[f] === s ? "selected" : ""}>${s === "正常" ? "正常参加" : s}</option>`).join("")}</select></label>`; };
  const courseSel = `<label>课程成绩 <select data-st="course_status" ${ed ? "" : "disabled"}>${(C.d.course_statuses || ["正常"]).map((s) => `<option value="${s}" ${(r.course_status || "正常") === s ? "selected" : ""}>${s}</option>`).join("")}</select></label>`;
  const lockInfo = peLock ? (C.d.sem_locked ? "体测成绩已由体育部锁定，性别和标准档不能再改" : "体测成绩已提交，性别和标准档不能再改") : "";
  // 待确认的项标红：按原因分到性别或标准档，分不出的两项都标
  const reasons = st.reasons || [];
  const isGenderReason = (x) => x.startsWith("学生基础数据") || x.startsWith("无对应学籍");
  const isBandReason = (x) => /学号|年级|特殊学籍/.test(x);
  const unknownReason = st.pending && (!reasons.length || reasons.some((x) => !isGenderReason(x) && !isBandReason(x)));
  const genderBad = !stu.gender || (st.pending && (reasons.some(isGenderReason) || unknownReason));
  const bandBad = !st.band || (st.pending && (reasons.some(isBandReason) || unknownReason));
  const badWhat = [genderBad && "性别", bandBad && "标准档"].filter(Boolean).join("和");
  const pendingLine = genderBad || bandBad ? `<div class="notice err" style="margin:6px 0">这名学生的${badWhat}待确认${reasons.length ? `：${esc(reasons.join("；"))}` : ""}。${peLock ? (C.d.sem_locked ? "体测成绩已由体育部锁定，要改须联系体育部解锁后再在下面改好标红的项并保存。" : "体测成绩已提交，要改须先撤回提交，再在下面改好标红的项并保存。") : "核对后在下面改好标红的项并保存，体测即可提交。"}</div>` : "";
  const bad = (b) => (b ? ' class="pending-bad"' : "");
  const hist = h.history || [];
  modal(`<h3>${esc(r.student_no)} ${esc(r.name)}</h3>
    <h4>特殊情况</h4><div class="row">${stSel("a")}${stSel("b")}${courseSel}</div>
    <p class="muted small">体测：免测不计入成绩；缓测记为缺项；课上未测（没来参加课上的测试）、违纪记 0 分。课程成绩选缓考、缺考、退课、休学、选课未修时总成绩不算，显示状态，运动技能和平时成绩可以不填。${peLock ? "体测成绩已锁定，两项体测状态不能再改。" : ""}</p>
    <h4>学生信息</h4>${pendingLine}
    <div class="map-grid" style="max-width:none;grid-template-columns:70px 1fr 70px 1fr">
      <div>姓名</div><div><input type="text" id="siName" value="${esc(stu.name || r.name)}" ${ed ? "" : "disabled"}></div>
      <div${bad(genderBad)}>性别</div><div${bad(genderBad)}><select id="siGender" ${ed && !peLock ? "" : "disabled"}><option value="">待确认</option><option value="M" ${stu.gender === "M" ? "selected" : ""}>男</option><option value="F" ${stu.gender === "F" ? "selected" : ""}>女</option></select></div>
      <div>学院</div><div><input type="text" id="siCollege" value="${esc(stu.college || r.college || "")}" ${ed ? "" : "disabled"}></div>
      <div${bad(bandBad)}>标准档</div><div${bad(bandBad)}><select id="siBand" ${ed && !peLock ? "" : "disabled"}>${st.band ? "" : '<option value="">待确认</option>'}<option value="12" ${st.band === "12" ? "selected" : ""}>大一大二</option><option value="34" ${st.band === "34" ? "selected" : ""}>大三大四</option></select></div></div>
    ${ed ? `<div class="row" style="margin-top:6px"><span class="muted small">保存即生效。姓名、性别、学院是全校共用的学生信息，别的班也会跟着变；改性别或标准档会立即重算体测得分。${lockInfo}</span><span class="spacer"></span><button id="siSave">保存学生信息</button></div>` : ""}
    <h4>备注</h4>
    <div class="row"><textarea id="noteIn" rows="2" style="flex:1" placeholder="随便记几句，自己看的，不会通知体育部。违纪时请写明原因" ${ed ? "" : "disabled"}>${esc(r.values.note || "")}</textarea></div>
    ${ed ? '<div class="row" style="margin-top:6px"><span class="muted small">体育部导出“本学期全部成绩”时会带上备注。</span><span class="spacer"></span><button id="noteBtn">保存</button></div>' : ""}
    <h4>其他问题</h4>
    ${issue.note ? `<p class="small">已提交：${esc(issue.note)} <span class="tag ${issue.resolved ? "s-已提交" : "warn"}">${issue.resolved ? `体育部已处理${issue.resolved_at ? " " + esc(issue.resolved_at) : ""}` : "待体育部处理"}</span></p>` : ""}
    <div class="row"><textarea id="ieNote" rows="2" style="flex:1" placeholder="上面改不了的问题写在这里，提交给体育部处理">${esc(issue.resolved ? "" : issue.note || "")}</textarea></div>
    <div class="row" style="margin-top:6px"><span class="spacer"></span>${issue.note && !issue.resolved ? '<button id="ieClr">撤回问题</button>' : ""}<button id="ieBtn">${issue.note && !issue.resolved ? "更新问题" : "提交给体育部"}</button></div>
    ${hist.length ? `<details style="margin-top:12px"><summary><b>修改记录（${hist.length} 条）</b></summary><table class="list small"><tr><th>时间</th><th>字段</th><th>原值</th><th>新值</th><th>换算</th><th>版本</th><th>操作人</th></tr>
      ${hist.map((x) => `<tr><td>${esc(x.at)}</td><td>${esc(fieldName[x.field] || x.field)}</td><td>${esc(x.old ?? "")}</td><td>${esc(x.new ?? "")}</td>
      <td>${x.computed && x.computed.slot && x.computed.slot.score !== null ? `${x.computed.slot.level ?? ""}分档→${x.computed.slot.score}分→折算${x.computed.slot.folded}` : esc((x.computed && x.computed.reason) || "")}${x.computed && x.computed.total !== undefined && x.computed.total !== null ? `；总${x.computed.total}` : ""}</td>
      <td>${esc(x.version)}</td><td>${esc(x.operator)}</td></tr>`).join("")}</table></details>` : ""}`, (el) => {
    const mark = async (note) => { try { await api(`/api/classes/${C.id}/info-error`, { method: "POST", body: { enrollment_id: eid, note } }); r.info_error = note || null; r.info_error_resolved = false; refreshRows(C, new Set([eid])); closeModal(); toast(note ? "已提交给体育部" : "已撤回"); } catch (e) { toast(e.message, true); } };
    $("#ieBtn", el).onclick = () => { const v = $("#ieNote", el).value.trim(); if (!v) return toast("请写明是什么问题", true); mark(v); };
    if ($("#ieClr", el)) $("#ieClr", el).onclick = () => mark("");
    $$("[data-st]", el).forEach((x) => (x.onchange = () => {
      // 体测标了特殊情况：还没存上的这格成绩（比如刚填的 0）不再提交，由服务器清掉原成绩并留痕
      if (/^[ab]_status$/.test(x.dataset.st) && x.value !== "正常") C.queue.delete(`${eid}:${x.dataset.st[0]}_raw`);
      enqueue(C, eid, x.dataset.st, x.value); flushQueue(C); toast(`已设为“${x.value === "正常" ? (x.dataset.st === "course_status" ? "正常" : "正常参加") : x.value}”`); }));
    if ($("#noteBtn", el)) $("#noteBtn", el).onclick = async () => { enqueue(C, eid, "note", $("#noteIn", el).value.trim()); while (C._busy) await new Promise((ok) => setTimeout(ok, 100)); await flushQueue(C); toast(C.queue.size ? "备注没存上，稍后自动重试" : "备注已保存"); };
    if ($("#siSave", el)) $("#siSave", el).onclick = async () => {
      const body = { name: $("#siName", el).value.trim(), college: $("#siCollege", el).value.trim() };
      if (!peLock) { if ($("#siGender", el).value) body.gender = $("#siGender", el).value; if ($("#siBand", el).value) body.band = $("#siBand", el).value; }
      if (!body.name) return toast("姓名不能为空", true);
      try {
        commitActive(C); await flushQueue(C);
        const res = await api(`/api/classes/${C.id}/students/${eid}`, { method: "POST", body });
        closeModal();
        if (!res.changed.length) return toast("没有改动");
        await reloadClass(C); toast("已保存，得分已重算");
      } catch (e) { toast(e.message, true); }
    };
    const fs = focusSt && $(`select[data-st="${focusSt}"]`, el);
    if (fs && !fs.disabled) { fs.scrollIntoView({ block: "nearest" }); fs.focus(); }
  }, onClose);
}
/* 改了学生信息或标准档后重新取一遍本班数据，保住滚动位置 */
async function reloadClass(C) {
  const wrap = $(".grid-wrap"); const pos = wrap ? [wrap.scrollLeft, window.scrollY] : null;
  const keep = { m: $("#fMissing") && $("#fMissing").checked, i: $("#fIssue") && $("#fIssue").checked };
  const d = await api(`/api/classes/${C.id}`);
  if (state.cls !== C) return;
  C.d = d; C.rows = d.rows; C.byId = new Map(d.rows.map((r) => [r.enrollment_id, r]));
  renderClass(C);
  if ($("#fMissing")) $("#fMissing").checked = !!keep.m;
  if ($("#fIssue")) $("#fIssue").checked = !!keep.i;
  if (keep.m || keep.i) renderRows(C);
  if (pos) { const w = $(".grid-wrap"); if (w) w.scrollLeft = pos[0]; window.scrollTo(0, pos[1]); }
}

async function submitDialog(C) {
  commitActive(C);
  while (C._busy) await new Promise((ok) => setTimeout(ok, 100));
  await flushQueue(C);
  const ck = await api(`/api/classes/${C.id}/check`);
  const sec = (title, list, tip) => `<h4>${title}（${list.length}）</h4>${list.length ? `<ul>${list.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : `<p class="muted small">无</p>`}${tip && list.length ? `<p class="small muted">${tip}</p>` : ""}`;
  modal(`<h3>提交体测成绩前校验</h3><div class="checklist">
    ${sec("体测未录入项", ck.missing, "每名学生的两项体测都要填成绩或标特殊情况（免测、缓测、课上未测等）才能提交。")}
    ${sec("年级/性别待确认项", ck.pending, "核对后可在该生的“更多”里直接修改，或等体育部确认。")}
    ${sec("加分超限项", ck.bonus)}
    ${sec("成绩突变项（与上学期同项目偏差超过25%）", ck.sudden)}</div>
    <p>提交后本班两项体测的成绩和特殊状态锁定；发现录错了可以自己点“撤回提交”接着改（体育部统一锁定后就不能撤回了）。运动技能、平时成绩、备注和课程成绩特殊情况提交后仍可修改。</p>
    <div class="row"><span class="spacer"></span><button id="cancelSub">返回修改</button><button class="primary" id="doSub" ${ck.blocking ? "disabled" : ""}>确认提交</button></div>
    ${ck.blocking ? `<div class="notice err" style="margin-top:10px">${ck.pending.length ? "本班存在年级或性别待确认的学生" : "本班还有体测没录完的学生"}，暂不能提交。</div>` : ""}`, (el) => {
    $("#cancelSub", el).onclick = closeModal;
    $("#doSub", el).onclick = async () => { try { await api(`/api/classes/${C.id}/submit`, { method: "POST" }); closeModal(); toast("体测成绩已提交"); viewClass(C.id); } catch (e) { toast(e.message, true); } };
  });
}
function withdrawDialog(C) {
  modal(`<h3>撤回体测成绩提交</h3><p>撤回后本班两项体测解锁，可以接着修改，改完再重新提交。</p>
    <div class="row" style="margin-top:10px"><span class="spacer"></span><button id="wdCancel">取消</button><button class="primary" id="wdBtn">撤回提交</button></div>`, (el) => {
    $("#wdCancel", el).onclick = closeModal;
    $("#wdBtn", el).onclick = async () => { try { await api(`/api/classes/${C.id}/withdraw`, { method: "POST" }); closeModal(); toast("已撤回提交，体测可以修改了"); viewClass(C.id); } catch (e) { toast(e.message, true); } };
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
  modal(`<h3>整班上传成绩</h3><p class="muted small">先“下载录入模板”，线下填好后上传。系统按学号匹配、逐行校验；空单元格不修改原值。成绩和系统已有的比较取更好的，想改低请在网页里改；状态列填了就覆盖。导入后可回退。</p>
    <input type="file" id="upFile" accept=".xlsx"><div id="upRes" style="margin-top:12px"></div>`, (el) => {
    $("#upFile", el).onchange = async (e) => {
      const fd = new FormData(); fd.append("file", e.target.files[0]);
      try {
        const r = await api(`/api/classes/${C.id}/upload`, { method: "POST", body: fd });
        const other = r.skipped.length - r.better_kept;
        const upTable = r.updates.length ? `<details><summary>将更新的格子（${r.updates.length}）</summary><table class="list" style="margin-top:6px"><thead><tr><th>行</th><th>学号</th><th>姓名</th><th>列</th><th>系统里</th><th>改成</th></tr></thead><tbody>
          ${r.updates.slice(0, 500).map((x) => `<tr><td>${x.row}</td><td>${esc(x.student_no)}</td><td>${esc(x.name)}</td><td>${esc(x.label)}</td><td>${esc(x.old) || '<span class="muted">空</span>'}</td><td><b>${esc(x.new)}</b></td></tr>`).join("")}</tbody></table></details>` : "";
        const skTable = r.skipped.length ? `<details><summary>不改的格子（${r.skipped.length}）</summary><table class="list" style="margin-top:6px"><thead><tr><th>行</th><th>学号</th><th>姓名</th><th>列</th><th>表里</th><th>系统里</th><th>原因</th></tr></thead><tbody>
          ${r.skipped.slice(0, 500).map((x) => `<tr><td>${x.row}</td><td>${esc(x.student_no)}</td><td>${esc(x.name)}</td><td>${esc(x.label)}</td><td>${esc(x.value)}</td><td>${esc(x.current) || '<span class="muted">空</span>'}</td><td>${esc(x.reason)}</td></tr>`).join("")}</tbody></table></details>` : "";
        $("#upRes", el).innerHTML = `<p>本班 ${r.roster} 人。将更新 <b>${r.cells}</b> 格（${r.students} 人）${r.better_kept ? `，<b>${r.better_kept}</b> 格因系统已有更好成绩不改` : ""}${other ? `，${other} 格因该项标了特殊情况不写入` : ""}。</p>
          ${upTable}${skTable}
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
  const tabs = [["overview", "概览"], ["semesters", "学期管理"], ["import", "信息导入"], ["pending", "待确认清单"], ["students", "学生信息"], ["logs", "操作日志"], ["users", "账号"]];
  $("#app").innerHTML = `<div class="card"><div class="tabs">${tabs.map(([k, l]) => `<a href="#/admin/${k}" class="${k === tab ? "on" : ""}">${l}</a>`).join("")}</div><div id="adminBody"></div></div>`;
  const fn = { overview: adOverview, semesters: adSemesters, import: adImport, pending: adPending, students: adStudents, logs: adLogs, users: adUsers }[tab];
  try { await fn($("#adminBody")); } catch (e) { $("#adminBody").innerHTML = `<div class="notice err">${esc(e.message)}</div>`; }
}
async function adOverview(el) {
  const sem = curSem();
  if (!sem) { el.innerHTML = `<div class="notice">还没有学期，请先到“学期管理”新建。</div>`; return; }
  const o = await api(`/api/admin/semesters/${sem.id}/overview`);
  el.innerHTML = `<h2>${esc(sem.name)}</h2>
   ${!o.classes ? `<div class="notice">本学期课程信息尚未导入。请到“信息导入”依次导入学生基础数据表、课程安排表、学生选课表。</div>` : ""}
   <div class="kv"><div>教学班</div><div>${o.classes}（未开始 ${o.status["未开始"] || 0}／录入中 ${o.status["录入中"] || 0}／体测成绩已提交 ${o.status["已提交"] || 0}）</div>
   <div>体测成绩已提交</div><div><b>${o.pe_submitted}</b> / ${o.classes} 个班</div>
   <div>体测成绩</div><div>${o.semester.pe_locked ? `<span class="tag s-已提交">已锁定</span> <span class="muted small">${esc(o.pe_locked_by_name || o.semester.pe_locked_by || "")} ${esc(o.semester.pe_locked_at || "")}</span> <button id="peUnlock">解锁</button>` : `<span class="tag">未锁定</span> <button class="primary" id="peLock">锁定全部班级体测成绩</button>`}</div>
   <div>有效选课记录</div><div>${o.enrollments}</div><div>待确认学生</div><div>${o.pending} <a href="#/admin/pending">去处理</a></div>
   <div>教师提交的其他问题</div><div>${o.info_errors ? `<b style="color:var(--err)">${o.info_errors}</b> 条待处理 <a href="#/admin/pending">去处理</a>` : 0}</div>
   <div>教师改过的学生信息</div><div>${o.teacher_edits} 次 ${o.teacher_edits ? '<a href="#/admin/pending">去抽查</a>' : ""}</div>
   <div>教师待补</div><div>${o.teacher_pending}</div></div>
   <div class="row" style="margin-top:14px"><button class="primary" id="expPe">导出全校体测成绩表</button><button id="expAll">导出本学期全部成绩</button><a class="btn" href="#/">查看全部教学班</a></div>
   ${o.class_list.length ? `<h3>各班体测成绩提交情况</h3><div class="row"><label><input type="checkbox" id="ovOnly"> 只看未提交</label><label><input type="checkbox" id="ovZero"> 只看有体测填 0 的班</label></div>
   <table class="list" style="margin-top:8px"><thead><tr><th>教学班号</th><th>课程</th><th>教学班</th><th>教师</th><th>体测</th><th title="体测成绩填了 0（没标特殊情况）的学生人数，多半是没来测">体测填 0</th></tr></thead><tbody id="ovBody"></tbody></table>` : ""}`;
  const setLock = async (locked) => {
    const unsub = o.classes - o.pe_submitted;
    const msg = locked
      ? `锁定${sem.name}全部班级的体测成绩？\n\n${unsub > 0 ? `还有 ${unsub} 个班没有提交体测成绩，锁定后这些班也不能再改体测。\n\n` : "全部班级都已提交。\n\n"}锁定后教师不能撤回提交，不能再改两项体测的成绩和特殊状态，也不能改学生性别和标准档；体测表格导入和模板上传也不会改体测。运动技能、平时成绩、备注和课程成绩特殊情况照常能改。之后可以随时解锁。`
      : `解锁${sem.name}全部班级的体测成绩？\n\n解锁后没提交的班又能修改体测；已提交的班仍锁着，教师可以自己撤回提交。`;
    if (!confirm(msg)) return;
    try { await api(`/api/admin/semesters/${sem.id}/pe-lock`, { method: "POST", body: { locked } }); await loadSemesters(); toast(locked ? "已锁定全部班级体测成绩" : "已解锁体测成绩"); adOverview(el); }
    catch (e) { toast(e.message, true); }
  };
  if ($("#peLock")) $("#peLock").onclick = () => setLock(true);
  if ($("#peUnlock")) $("#peUnlock").onclick = () => setLock(false);
  $("#expPe").onclick = () => download(`/api/admin/semesters/${sem.id}/export-pe`);
  $("#expAll").onclick = () => download(`/api/admin/semesters/${sem.id}/export`);
  const drawOv = () => {
    const only = $("#ovOnly") && $("#ovOnly").checked, zeroOnly = $("#ovZero") && $("#ovZero").checked;
    $("#ovBody").innerHTML = o.class_list.filter((c) => (!only || !c.submitted) && (!zeroOnly || c.zeros)).map((c) => `<tr><td>${esc(c.class_no)}</td><td>${esc(c.course_name)}</td><td><a href="#/class/${c.id}">${esc(c.class_name)}</a></td><td>${esc(c.teacher_name || "教师待补")} <button class="link" data-assign="${c.id}" title="把这个班指派给某位教师或公共账号">指定教师</button></td>
      <td>${c.submitted ? `<span class="tag s-已提交">已提交</span> <span class="muted small">${esc(c.submitted_at || "")}</span>` : '<span class="tag">未提交</span>'}</td>
      <td>${c.zeros ? `<b style="color:var(--err)">${c.zeros}</b> 人` : 0}</td></tr>`).join("") || `<tr><td colspan="6" class="muted">没有符合条件的班</td></tr>`;
  };
  if ($("#ovBody")) {
    drawOv(); $("#ovOnly").onchange = drawOv; $("#ovZero").onchange = drawOv;
    $("#ovBody").onclick = (e) => {
      const b = e.target.closest("[data-assign]"); if (!b) return;
      const c = o.class_list.find((x) => String(x.id) === b.dataset.assign);
      assignTeacher(c, () => adOverview(el));
    };
  }
  sessionStorage.setItem("classFilter", JSON.stringify({ scope: "all" }));
}
async function adSemesters(el) {
  const sems = await api("/api/semesters");
  const label = (it) => ({ run1000: "1000米跑", run800: "800米跑", run50: "50米跑", long_jump: "立定跳远", pull_up: "引体向上", sit_up: "仰卧起坐" }[it] || it);
  // 默认带出下一个要建的学期：8 月起是当年秋季，1–7 月是上一学年的春季
  const now = new Date(), defYear = now.getMonth() >= 7 ? now.getFullYear() : now.getFullYear() - 1, defSeason = now.getMonth() >= 7 ? "fall" : "spring";
  el.innerHTML = `<div class="row">学年 <input type="number" id="newSemYear" min="2020" max="2100" step="1" value="${defYear}" style="width:6em"> – <span id="newSemYear2">${defYear + 1}</span> 学年
    <select id="newSemSeason"><option value="fall" ${defSeason === "fall" ? "selected" : ""}>秋季学期</option><option value="spring" ${defSeason === "spring" ? "selected" : ""}>春季学期</option></select>
    <button class="primary" id="newSemBtn">新建学期</button> <span id="newSemHint" class="small"></span></div>
    <div class="row">
    <span class="muted small">新建学期时自动带入默认体测项目配置：秋季耐力跑20%、立定跳远10%；春季50米跑20%、引体向上/仰卧起坐10%。</span></div>
    <table class="list" style="margin-top:12px"><tr><th>学期</th><th>体测项目配置（默认）</th><th>状态</th><th>操作</th></tr>
    ${sems.map((s) => `<tr><td>${esc(s.name)}</td><td>${s.config.slots.map((x) => `${label(x.items.M)}${x.items.M !== x.items.F ? "/" + label(x.items.F) : ""} ${x.weight * 100}%`).join("、")}、技能 ${s.config.skill_weight * 100}%、平时 ${s.config.regular_weight * 100}%</td>
      <td>${s.is_current ? '<span class="tag cur">当前学期</span>' : s.entry_open ? '<span class="tag warn">已开放补录</span>' : '<span class="tag">只读</span>'}</td>
      <td>${s.is_current ? "" : `<button class="link" data-cur="${s.id}">设为当前学期</button> <button class="link" data-open="${s.id}" data-v="${s.entry_open ? 0 : 1}">${s.entry_open ? "关闭补录窗口" : "开放补录窗口"}</button>`}</td></tr>`).join("")}</table>`;
  const semPick = () => {
    const v = $("#newSemYear").value.trim(), y = Number(v), season = $("#newSemSeason").value;
    if (!/^\d{4}$/.test(v) || y < 2020 || y > 2100) return { err: "学年起始年份请填 2020–2100 之间的四位年份" };
    const code = `${y}-${y + 1}-${season === "fall" ? 1 : 2}`, name = `${y}-${y + 1}学年${season === "fall" ? "秋季" : "春季"}学期`;
    return sems.some((s) => s.code === code) ? { err: `${name}已存在，不能重复新建` } : { y, season, name };
  };
  const semHint = () => {
    const p = semPick(), ok = !p.err;
    $("#newSemYear2").textContent = /^\d{4}$/.test($("#newSemYear").value.trim()) ? Number($("#newSemYear").value) + 1 : "____";
    $("#newSemHint").textContent = ok ? `将新建：${p.name}` : p.err;
    $("#newSemHint").className = ok ? "small muted" : "small"; $("#newSemHint").style.color = ok ? "" : "var(--err)";
    $("#newSemBtn").disabled = !ok;
  };
  $("#newSemYear").oninput = semHint;
  $("#newSemSeason").onchange = semHint;
  semHint();
  $("#newSemBtn").onclick = async () => {
    const p = semPick();
    if (p.err) return toast(p.err, true);
    try { await api("/api/admin/semesters", { method: "POST", body: { start_year: p.y, season: p.season } }); await loadSemesters(); renderTop(); adSemesters(el); toast(`已新建${p.name}`); } catch (e) { toast(e.message, true); }
  };
  $$("[data-cur]", el).forEach((b) => (b.onclick = async () => { await api(`/api/admin/semesters/${b.dataset.cur}`, { method: "PATCH", body: { is_current: true } }); await loadSemesters(); renderTop(); adSemesters(el); }));
  $$("[data-open]", el).forEach((b) => (b.onclick = async () => { await api(`/api/admin/semesters/${b.dataset.open}`, { method: "PATCH", body: { entry_open: b.dataset.v === "1" } }); await loadSemesters(); adSemesters(el); }));
}
async function adImport(el) {
  const sem = curSem();
  if (!sem) { el.innerHTML = `<div class="notice">请先新建学期。</div>`; return; }
  el.innerHTML = `<p>导入到：<b>${esc(sem.name)}</b>（在顶部切换学期）。建议顺序：学生基础数据表 → 课程安排表 → 学生选课表。</p>
    <div class="row"><select id="impKind"><option value="base">学生基础数据表（性别、院系补全）</option><option value="schedule">课程安排表</option><option value="enroll">学生选课表</option><option value="scores">体测成绩（同一项目取最优）</option></select>
    <input type="file" id="impFile" accept=".xlsx"></div><p id="impHint" class="muted small" hidden>每个工作表一个项目，表名写项目（如“立定跳远”“1000米”）；表名不是项目时按列名认。耐力跑写“分.秒”（3.24 即 3分24秒）。只写入学生本学期要测的项目；同一学生同一项目，表里多条和系统里已有的成绩一起比，取最好的，不会用更差的成绩盖掉已有成绩。体测已提交的班不改。</p><div id="impStep" style="margin-top:14px"></div>`;
  $("#impKind").onchange = () => { $("#impHint").hidden = $("#impKind").value !== "scores"; $("#impFile").value = ""; $("#impStep").innerHTML = ""; };
  $("#impFile").onchange = async (e) => {
    if ($("#impKind").value === "scores") return scoreImport(e.target.files[0], sem);
    const fd = new FormData(); fd.append("kind", $("#impKind").value); fd.append("file", e.target.files[0]);
    $("#impStep").innerHTML = `<p class="muted">正在解析文件…</p>`;
    try { const info = await api("/api/admin/import/upload", { method: "POST", body: fd }); impMapping(info, sem); }
    catch (err) { $("#impStep").innerHTML = `<div class="notice err">${esc(err.message)}</div>`; }
  };
}
async function scoreImport(file, sem) {
  const box = $("#impStep");
  if (!file) return;
  const fd = new FormData(); fd.append("semester_id", sem.id); fd.append("file", file);
  box.innerHTML = `<p class="muted">正在解析文件…</p>`;
  let r;
  try { r = await api("/api/admin/score-import/upload", { method: "POST", body: fd }); }
  catch (err) { box.innerHTML = `<div class="notice err">${esc(err.message)}</div>`; return; }
  const reasons = Object.entries(r.reason_counts).map(([k, v]) => `${esc(k)} ${v} 条`).join("；");
  const up = r.updates, sk = r.skips;
  box.innerHTML = `<h3>预检结果</h3>
    ${r.notes.length ? `<div class="notice">${r.notes.map(esc).join("<br>")}</div>` : ""}
    <p>表里共 ${r.entries} 个成绩。将更新 <b>${up.length}</b> 项（${r.students} 名学生），跳过 <b>${sk.length}</b> 项${reasons ? `：${reasons}` : ""}。</p>
    ${up.length ? `<details open><summary>将更新的成绩（${up.length}）</summary><table class="list" style="margin-top:6px"><thead><tr><th>教学班</th><th>学号</th><th>姓名</th><th>项目</th><th>原成绩</th><th>新成绩</th><th>来源</th></tr></thead><tbody>
      ${up.slice(0, 500).map((u) => `<tr><td>${esc(u.class)}</td><td>${esc(u.student_no)}</td><td>${esc(u.name)}</td><td>${esc(u.item_label)}</td><td>${esc(u.old) || '<span class="muted">空</span>'}</td><td><b>${esc(u.new)}</b></td><td class="small muted">${esc(u.sheet)} 第${u.row}行</td></tr>`).join("")}</tbody></table>${up.length > 500 ? `<p class="muted small">只列前 500 项</p>` : ""}</details>` : ""}
    ${sk.length ? `<details ${up.length ? "" : "open"}><summary>跳过的成绩（${sk.length}）</summary><table class="list" style="margin-top:6px"><thead><tr><th>来源</th><th>学号</th><th>姓名</th><th>项目</th><th>表里的值</th><th>原因</th></tr></thead><tbody>
      ${sk.slice(0, 500).map((x) => `<tr><td class="small">${esc(x.sheet)} 第${x.row}行</td><td>${esc(x.student_no)}</td><td>${esc(x.name)}</td><td>${esc(x.item_label)}</td><td>${esc(x.value)}</td><td>${esc(x.reason)}${x.class ? `<span class="muted small">（${esc(x.class)}）</span>` : ""}</td></tr>`).join("")}</tbody></table>${sk.length > 500 ? `<p class="muted small">只列前 500 项</p>` : ""}</details>` : ""}
    <div class="row" style="margin-top:12px"><button class="primary" id="scGo" ${up.length ? "" : "disabled"}>确认写入 ${up.length} 项</button></div><div id="scRes"></div>`;
  if (!up.length) return;
  $("#scGo").onclick = async () => {
    $("#scGo").disabled = true;
    try {
      const res = await api("/api/admin/score-import/commit", { method: "POST", body: { semester_id: sem.id, token: r.token } });
      $("#scRes").innerHTML = `<div class="notice info">已写入 ${res.saved} 项成绩（${res.students} 名学生），跳过 ${res.skipped} 项。修改记录里能看到是这次导入写入的。 <button id="scUndo">回退这次导入</button></div>`;
      toast("导入完成");
      $("#scUndo").onclick = async () => {
        if (!confirm("把这次导入写入的成绩改回导入前的值？导入后又被改过的成绩不动。")) return;
        try { const rv = await api(`/api/admin/score-import/${res.batch}/revert`, { method: "POST", body: { semester_id: sem.id } }); toast(`已回退 ${rv.reverted} 项`); $("#scUndo").disabled = true; }
        catch (e) { toast(e.message, true); }
      };
    } catch (e) { $("#scGo").disabled = false; toast(e.message, true); }
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
      const impact = r.plan.scored_impact;
      const plan = Object.entries(r.plan).filter(([k]) => k !== "scored_impact").map(([k, v]) => `${({ total: "教学班", new: "新增", update: "更新", absent: "文件中没有的已有教学班", rows: "有效记录", students: "学生", classes: "教学班", skipped_closed: "关闭班记录（不导入）", male: "男", female: "女" })[k] || k} ${v}`).join("，");
      $("#impReport").innerHTML = `<h3>预检报告</h3><p>共读取 ${r.rows} 行。${esc(plan)}</p>
        <ul class="report">${r.items.map((i) => `<li class="lv-${i.level}">【${({ error: "阻断", warn: "提示", info: "说明" })[i.level]}】${esc(i.message)}${i.advice ? `<span class="muted"> — ${esc(i.advice)}</span>` : ""}${i.rows.length ? `<div class="small muted">行号：${i.rows.slice(0, 30).join("、")}${i.row_count > 30 ? " …" : ""}</div>` : ""}</li>`).join("") || "<li>未发现问题</li>"}</ul>
        <div class="row"><button class="primary" id="impGo" ${r.blocking ? "disabled" : ""}>确认导入</button>${r.blocking ? '<span class="muted">请先处理“阻断”项</span>' : ""}</div>`;
      if (!r.blocking) $("#impGo").onclick = async () => {
        const msgs = [];
        if (a.mode === "rebuild") msgs.push("重建将以本文件为准：文件中没有、且尚无成绩的教学班和名单会被删除（已有成绩的保留）。");
        if (impact) msgs.push(`${impact.message}${impact.class_nos && impact.class_nos.length ? `\n涉及教学班号：${impact.class_nos.slice(0, 10).join("、")}${impact.classes > 10 ? " 等" : ""}` : ""}`);
        if (msgs.length && !confirm(msgs.join("\n\n") + "\n\n确定继续导入？")) return;
        a.confirm_rebuild = a.mode === "rebuild";
        a.confirm_scored = !!impact;
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
  const openIssues = d.info_errors.filter((x) => !x.resolved);
  el.innerHTML = `<div class="${openIssues.length ? "notice err" : "notice info"}" style="margin-bottom:16px">
    <h3 style="margin-top:0">教师提交的其他问题${openIssues.length ? `：<b>${openIssues.length}</b> 条待处理` : "：暂无待处理"}</h3>
    ${d.info_errors.length ? `<table class="list"><tr><th>学号</th><th>姓名</th><th>教学班</th><th>教师</th><th>问题</th><th>状态</th><th></th></tr>${d.info_errors.map((x) => `<tr style="${x.resolved ? "opacity:.6" : ""}"><td>${esc(x.student_no)}</td><td>${esc(x.name)}</td><td>${esc(x.class_name)}</td><td>${esc(x.teacher)}</td><td>${esc(x.note)}</td><td>${x.resolved ? "已处理" : "待处理"}</td><td class="nw"><button class="link" data-edit2="${esc(x.student_no)}">去修改</button> <button class="link" data-resolve="${x.enrollment_id}" data-v="${x.resolved ? 0 : 1}">${x.resolved ? "改回待处理" : "标记已处理"}</button></td></tr>`).join("")}</table>` : `<p class="muted small" style="margin:0">教师在录入页“更多”里提交的问题会出现在这里，处理后教师那边显示“已处理”。</p>`}</div>
    <details style="margin-bottom:16px" ${d.teacher_edits.length ? "" : "hidden"}><summary><b>教师改过的学生信息（${d.teacher_edits.length} 次）</b> <span class="muted small">教师在录入页直接改的姓名、性别、学院、标准档，供抽查</span></summary>
      <table class="list small" style="margin-top:8px"><tr><th>时间</th><th>教师</th><th>学号</th><th>姓名</th><th>教学班</th><th>改动</th><th></th></tr>${d.teacher_edits.map((x) => `<tr><td class="nw">${esc(x.at)}</td><td>${esc(x.teacher)}</td><td>${esc(x.student_no)}</td><td>${esc(x.name)}</td><td>${esc(x.class_name)}</td><td>${x.changes.map((c) => `<div>${esc(c)}</div>`).join("")}</td><td><button class="link" data-edit2="${esc(x.student_no)}">查看</button></td></tr>`).join("")}</table></details>
    <p>${esc(sem.name)} 共 <b>${d.students.length}</b> 名学生待确认：${Object.entries(d.summary).map(([k, v]) => `${esc(k)} ${v}`).join("，") || "无"}</p>
    <div class="row"><input type="text" id="pSearch" placeholder="按学号、姓名或原因筛选" size="30"></div>
    <table class="list" style="margin-top:10px"><thead><tr><th>学号</th><th>姓名</th><th>性别</th><th>暂定标准档</th><th>原因</th><th>选课记录</th><th>操作</th></tr></thead><tbody id="pBody"></tbody></table>
    <h3>教师待补的教学班（${d.teacher_pending.length}）</h3>
    ${d.teacher_pending.length ? `<table class="list"><tr><th>教学班</th><th>当前教师</th><th>指定责任教师</th></tr>${d.teacher_pending.map((c) => `<tr><td>${esc(c.class_name)}（${esc(c.class_no)}）</td><td>${esc(c.teacher_name || "—")}</td>
      <td><button data-tset="${c.id}">选择教师</button></td></tr>`).join("")}</table>` : "<p class='muted'>无</p>"}`;
  const draw = () => {
    const q = $("#pSearch").value.trim();
    $("#pBody").innerHTML = d.students.filter((s) => !q || (s.student_no + s.name + s.reasons.join("")).includes(q)).map((s) => `<tr>
      <td class="nw">${esc(s.student_no)}</td><td class="nw">${esc(s.name)}</td><td class="nw">${esc(s.gender)}</td><td class="nw">${s.grade_num ? `大${s.grade_num}，` : ""}${esc(s.band_label)}<div class="muted small">${esc(s.source)}</div></td>
      <td>${s.reasons.map((r) => `<div>${esc(r)}</div>`).join("")}</td>
      <td>${s.enrollments.map((e) => `<div>${esc(e.class_name)}（${esc(e.teacher)}）上课年级 ${esc(e.grade_field || "空")}</div>`).join("")}</td>
      <td class="nw"><button class="link" data-edit="${s.student_no}">补全/修改信息</button><br><button class="link" data-fix="${s.student_no}">确认标准档</button></td></tr>`).join("");
    $$("[data-fix]", el).forEach((b) => (b.onclick = () => fixDialog(sem, d.students.find((s) => s.student_no === b.dataset.fix), () => adPending(el))));
    $$("[data-edit]", el).forEach((b) => (b.onclick = () => studentEditor(sem, b.dataset.edit, () => adPending(el))));
  };
  $("#pSearch").oninput = draw; draw();
  $$("[data-edit2]", el).forEach((b) => (b.onclick = () => studentEditor(sem, b.dataset.edit2, () => adPending(el))));
  $$("[data-resolve]", el).forEach((b) => (b.onclick = async () => { try { await api(`/api/admin/enrollments/${b.dataset.resolve}/issue-resolved`, { method: "POST", body: { resolved: b.dataset.v === "1" } }); adPending(el); } catch (e) { toast(e.message, true); } }));
  $$("[data-tset]", el).forEach((b) => (b.onclick = () => assignTeacher(d.teacher_pending.find((c) => String(c.id) === b.dataset.tset), () => adPending(el))));
}
// 指定教师：从平台已有账号（教师和公共账号）里选。学号（10位）的学生管理员不列出。
async function assignTeacher(c, done) {
  let us;
  try { us = (await api("/api/admin/users")).filter((u) => !/^\d{10}$/.test(u.staff_no)); } catch (e) { toast(e.message, true); return; }
  modal(`<h3>指定教师：${esc(c.class_name)}（${esc(c.class_no)}）</h3>
    <p class="muted small">当前：${esc(c.teacher_name || "教师待补")}${c.staff_no ? `（${esc(c.staff_no)}）` : ""}。课程安排表里出现过的教师都在列表里，没有数智人大身份的老师请先在“账号”页新建公共账号。</p>
    <div class="row"><input type="text" id="atQ" placeholder="按工号或姓名搜索" size="24"></div>
    <div style="max-height:50vh;overflow:auto;margin-top:8px"><table class="list"><thead><tr><th>工号</th><th>姓名</th><th>类型</th><th></th></tr></thead><tbody id="atBody"></tbody></table></div>`, (m) => {
    const draw = () => {
      const q = $("#atQ", m).value.trim().toLowerCase();
      const hit = us.filter((u) => !q || (u.staff_no + " " + (u.name || "")).toLowerCase().includes(q));
      $("#atBody", m).innerHTML = hit.slice(0, 200).map((u) => `<tr><td>${esc(u.staff_no)}</td><td>${esc(u.name || "")}</td><td>${u.is_public ? "公共账号" : u.is_admin ? "体育部管理员" : "教师"}</td>
        <td>${u.staff_no === c.staff_no ? '<span class="muted small">当前</span>' : `<button class="link" data-pick="${esc(u.staff_no)}">选这位</button>`}</td></tr>`).join("")
        || `<tr><td colspan="4" class="muted">没有匹配的账号</td></tr>`;
    };
    $("#atQ", m).oninput = draw; draw(); $("#atQ", m).focus();
    $("#atBody", m).onclick = async (e) => {
      const b = e.target.closest("[data-pick]"); if (!b) return;
      try { await api(`/api/admin/classes/${c.id}`, { method: "PATCH", body: { staff_no: b.dataset.pick } }); closeModal(); toast("已指定"); done(); }
      catch (err) { toast(err.message, true); }
    };
  });
}
async function adStudents(el) {
  const sem = curSem(); if (!sem) return;
  el.innerHTML = `<p class="muted small">按学号或姓名查找学生，补录或修改性别等基础信息，修改选课信息、调班、移出或加入教学班。所有修改都留痕；手动改过的信息之后重新导入表格时不会被覆盖。</p>
    <div class="row"><input type="text" id="stQ" placeholder="学号或姓名（至少2个字）" size="28"><button id="stGo">查找</button><span class="spacer"></span><button id="stNew">按学号新增/打开</button></div>
    <div id="stList" style="margin-top:10px"></div>`;
  const go = async () => {
    const q = $("#stQ").value.trim(); if (q.length < 2) return toast("至少输入2个字", true);
    const d = await api(`/api/admin/semesters/${sem.id}/students?q=${encodeURIComponent(q)}`);
    $("#stList").innerHTML = d.students.length ? `<table class="list"><tr><th>学号</th><th>姓名</th><th>性别</th><th>本学期教学班</th><th></th></tr>${d.students.map((x) => `<tr><td>${esc(x.student_no)}</td><td>${esc(x.name)}</td><td>${esc(x.gender)}</td><td>${esc(x.classes.join("、") || "—")}</td><td><button class="link" data-open="${esc(x.student_no)}">查看/修改</button></td></tr>`).join("")}</table>` : `<p class="muted">没有找到</p>`;
    $$("[data-open]", el).forEach((b) => (b.onclick = () => studentEditor(sem, b.dataset.open, go)));
  };
  $("#stGo").onclick = go; $("#stQ").onkeydown = (e) => { if (e.key === "Enter") go(); };
  $("#stNew").onclick = () => { const no = prompt("学号"); if (no && no.trim()) studentEditor(sem, no.trim(), () => {}); };
}
async function studentEditor(sem, no, done) {
  let d;
  try { d = await api(`/api/admin/semesters/${sem.id}/student/${encodeURIComponent(no)}`); } catch (e) { return toast(e.message, true); }
  const st = d.student || {}; const sd = d.standard;
  const classOpts = (sel) => d.classes.map((c) => `<option value="${c.id}" ${c.id === sel ? "selected" : ""}>${esc(c.label)}</option>`).join("");
  const enrollRow = (e) => `<div class="card" style="padding:10px;margin:8px 0;${e.active ? "" : "opacity:.6"}" data-eid="${e.enrollment_id}">
      <div class="row"><b>${esc(e.course_name)} · ${esc(e.class_name)}</b><span class="muted small">${esc(e.teacher || "教师待补")} · ${esc(e.class_status)}</span>
        ${e.active ? "" : '<span class="tag err">已移出</span>'}${e.manual ? '<span class="tag">手动维护</span>' : ""}${e.info_error && !e.info_error_resolved ? `<span class="tag err" title="${esc(e.info_error)}">教师提了问题：${esc(e.info_error)}</span>` : ""}</div>
      ${e.active ? `<div class="map-grid" style="margin-top:8px;max-width:none;grid-template-columns:90px 1fr 90px 1fr">
        <div>教学班</div><div style="grid-column:span 3"><select data-k="class_id" style="max-width:100%">${classOpts(e.class_id)}</select></div>
        <div>姓名</div><div><input type="text" data-k="name" value="${esc(e.name)}"></div><div>上课班级</div><div><input type="text" data-k="class_label" value="${esc(e.class_label)}"></div>
        <div>上课专业</div><div><input type="text" data-k="major" value="${esc(e.major)}"></div><div>上课年级</div><div><input type="text" data-k="grade_field" value="${esc(e.grade_field)}" placeholder="如 2025"></div>
        <div>上课院系</div><div><input type="text" data-k="college" value="${esc(e.college)}"></div><div>上课校区</div><div><select data-k="campus">${["", "中关村校区", "通州校区"].map((x) => `<option ${x === e.campus ? "selected" : ""}>${x}</option>`).join("")}</select></div></div>
        <div class="row" style="margin-top:8px"><span class="spacer"></span><button class="link" data-rm>移出该班</button><button data-save>保存这条选课</button></div>`
      : `<div class="row" style="margin-top:6px"><span class="spacer"></span><button data-restore>恢复到该班</button></div>`}</div>`;
  modal(`<h3>${esc(no)} ${esc(st.name || (d.enrollments[0] && d.enrollments[0].name) || "")}</h3>
    ${sd ? `<p class="small">标准档：${sd.grade_num ? `大${sd.grade_num}，` : ""}${esc(sd.band_label)}（${esc(sd.source || "—")}）${sd.pending ? ` <span class="tag warn">待确认：${esc(sd.reasons.join("；"))}</span>` : ""}</p>` : ""}
    <h4>学生基础信息${st.manual ? ' <span class="tag">手动维护</span>' : ""}${d.student ? "" : ' <span class="tag warn">学生基础数据中没有，保存后补录</span>'}</h4>
    <div class="map-grid" style="max-width:none;grid-template-columns:90px 1fr 90px 1fr">
      <div>性别 <span style="color:var(--err)">*</span></div><div><select id="sxG"><option value="">请选择</option><option value="M" ${st.gender === "M" ? "selected" : ""}>男</option><option value="F" ${st.gender === "F" ? "selected" : ""}>女</option></select></div>
      <div>姓名</div><div><input type="text" id="sxN" value="${esc(st.name || (d.enrollments[0] && d.enrollments[0].name) || "")}"></div>
      <div>学院</div><div><input type="text" id="sxC" value="${esc(st.college || (d.enrollments[0] && d.enrollments[0].college) || "")}"></div>
</div>
    <p class="muted small">上课班级、专业、年级以选课记录为准，在下方各条选课里修改。</p>
    <div class="row" style="margin-top:8px"><span class="spacer"></span><button class="primary" id="sxSave">保存基础信息</button></div>
    <h4>本学期选课（${d.enrollments.filter((e) => e.active).length}）</h4>
    ${d.enrollments.map(enrollRow).join("") || '<p class="muted">本学期没有选课记录</p>'}
    <h4>加入教学班</h4><div class="row"><select id="sxAdd" style="max-width:520px"><option value="">选择教学班</option>${classOpts(null)}</select><button id="sxAddBtn">加入</button></div>
    <p class="muted small">调班时已录的成绩跟着学生转到新班，原班留一条“已移出”记录，避免以后重新导入时又被加回原班。</p>`, (el) => {
    const reload = () => { done && done(); studentEditor(sem, no, done); };
    $("#sxSave", el).onclick = async () => {
      try { await api(`/api/admin/semesters/${sem.id}/student/${encodeURIComponent(no)}`, { method: "PUT", body: { gender: $("#sxG", el).value, name: $("#sxN", el).value, college: $("#sxC", el).value } }); toast("已保存，标准档与成绩已重算"); reload(); }
      catch (e) { toast(e.message, true); }
    };
    $$("[data-eid]", el).forEach((box) => {
      const eid = box.dataset.eid;
      const sv = $("[data-save]", box), rm = $("[data-rm]", box), rs = $("[data-restore]", box);
      if (sv) sv.onclick = async () => {
        const body = {}; $$("[data-k]", box).forEach((x) => (body[x.dataset.k] = x.value)); body.class_id = Number(body.class_id);
        try { await api(`/api/admin/enrollments/${eid}`, { method: "PATCH", body }); toast("已保存"); reload(); } catch (e) { toast(e.message, true); }
      };
      if (rm) rm.onclick = async () => { if (!confirm("把该生移出这个教学班？已录成绩会保留，可以再恢复。")) return; try { await api(`/api/admin/enrollments/${eid}`, { method: "DELETE" }); toast("已移出"); reload(); } catch (e) { toast(e.message, true); } };
      if (rs) rs.onclick = async () => { try { await api(`/api/admin/enrollments/${eid}/restore`, { method: "POST" }); toast("已恢复"); reload(); } catch (e) { toast(e.message, true); } };
    });
    $("#sxAddBtn", el).onclick = async () => {
      const cid = Number($("#sxAdd", el).value); if (!cid) return toast("请选择教学班", true);
      try { await api(`/api/admin/semesters/${sem.id}/enrollments`, { method: "POST", body: { student_no: no, class_id: cid, name: $("#sxN", el).value, college: $("#sxC", el).value } }); toast("已加入"); reload(); }
      catch (e) { toast(e.message, true); }
    };
  });
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
async function adLogs(el, page = 1) {
  const sem = curSem();
  const action = el.dataset.action || "";
  const onlySem = !!sem && el.dataset.scope === "sem";
  const qs = new URLSearchParams({ action, page });
  if (onlySem) qs.set("semester_id", sem.id);
  const d = await api(`/api/admin/logs?${qs}`);
  el.innerHTML = `<div class="row"><select id="lgScope"><option value="">全部学期</option>${sem ? `<option value="sem" ${onlySem ? "selected" : ""}>${esc(sem.name)}（含登录等通用记录）</option>` : ""}</select><select id="lgAct"><option value="">全部操作</option>${["保存", "提交", "撤回申请", "撤回审核", "改档", "教师修改学生信息", "提交其他问题", "处理其他问题", "导入", "导出", "配置", "回退导入", "指定教师", "学期设置", "公共账号", "登录"].map((a) => `<option ${a === action ? "selected" : ""}>${a}</option>`).join("")}</select><span class="muted">共 ${d.total} 条</span>
    <span class="spacer"></span>${page > 1 ? '<button id="lgPrev">上一页</button>' : ""}${d.total > page * 100 ? '<button id="lgNext">下一页</button>' : ""}</div>
    <table class="list small" style="margin-top:10px"><tr><th>时间</th><th>操作人</th><th>操作</th><th>对象</th><th>详情</th><th>IP</th></tr>
    ${d.logs.map((l) => `<tr><td>${esc(l.at)}</td><td>${esc(l.actor_name)} ${esc(l.actor)}</td><td>${esc(l.action)}</td><td>${esc(l.target)}</td><td style="max-width:520px;word-break:break-all">${esc(JSON.stringify(l.detail)).slice(0, 400)}</td><td>${esc(l.ip)}</td></tr>`).join("")}</table>`;
  $("#lgAct").onchange = (e) => { el.dataset.action = e.target.value; adLogs(el, 1); };
  $("#lgScope").onchange = (e) => { el.dataset.scope = e.target.value; adLogs(el, 1); };
  if ($("#lgPrev")) $("#lgPrev").onclick = () => adLogs(el, page - 1);
  if ($("#lgNext")) $("#lgNext").onclick = () => adLogs(el, page + 1);
}
async function adUsers(el) {
  const us = await api("/api/admin/users");
  const pubs = us.filter((u) => u.is_public), others = us.filter((u) => !u.is_public);
  el.innerHTML = `<h3>体育部公共账号</h3>
    <p class="muted small">给没有数智人大身份的老师用：用登录页的工号＋口令登录，不是管理员，只能看、录被“指定教师”指派给它的教学班。可以建多个。多人共用一个账号时，操作日志只记到这个账号。</p>
    <div class="row"><input type="text" id="pName" placeholder="账号名称，如 体育部公共"><input type="text" id="pNo" placeholder="工号，如 tyb01" lang="en" autocapitalize="off" spellcheck="false"><button class="primary" id="pAdd">新建公共账号</button></div>
    ${pubs.length ? `<table class="list" style="margin-top:10px"><tr><th>工号</th><th>名称</th><th>口令</th><th></th></tr>${pubs.map((u) => `<tr><td>${esc(u.staff_no)}</td><td>${esc(u.name)}</td><td>${u.must_change_password ? '<span class="tag warn">初始口令，未改</span>' : "已设置"}</td>
      <td><button class="link" data-reset="${esc(u.staff_no)}">重置口令</button></td></tr>`).join("")}</table>` : `<p class="muted small">还没有公共账号。</p>`}
    <h3>账号</h3>
    <p class="muted small">教师用数智人大统一身份认证登录；导入课程安排表时，表里的教师也会出现在这里。</p>
    <h3>添加学生管理员</h3>
    <p class="muted small">输入学生的10位学号，设为体育部管理员。学生管理员可以用数智人大统一身份认证登录（普通学生不能登录）。也可以输入平台里已有的8位教师工号，把这位老师设为管理员。</p>
    <div class="row"><input type="text" id="uNo" placeholder="10位学号" inputmode="numeric" maxlength="10"><button id="uAdd">添加学生管理员</button></div>
    <table class="list" style="margin-top:10px"><tr><th>工号</th><th>姓名</th><th>角色</th><th></th></tr>${others.map((u) => `<tr><td>${esc(u.staff_no)}</td><td>${esc(u.name)}</td><td>${u.is_admin ? (/^\d{10}$/.test(u.staff_no) ? "学生管理员" : "体育部管理员") : "教师"}</td>
      <td>${u.is_admin ? `<button class="link" data-rm="${esc(u.staff_no)}">取消管理员</button>` : `<button class="link" data-add="${esc(u.staff_no)}">设为管理员</button>`}</td></tr>`).join("")}</table>`;
  const set = async (no, v) => { try { await api(`/api/admin/users/${encodeURIComponent(no)}`, { method: "POST", body: { is_admin: v } }); adUsers(el); } catch (e) { toast(e.message, true); } };
  const showPw = (r, title) => modal(`<h3>${esc(title)}</h3><div class="kv"><div>工号</div><div>${esc(r.staff_no)}</div><div>初始口令</div><div><b style="font-family:monospace;font-size:18px">${esc(r.password)}</b></div></div>
    <p class="small">口令只显示这一次，请抄下交给使用的老师。首次登录时须修改口令。</p>`);
  $("#pAdd").onclick = async () => {
    try { const r = await api("/api/admin/public-accounts", { method: "POST", body: { name: $("#pName").value, staff_no: $("#pNo").value } }); await adUsers(el); showPw(r, `已新建公共账号：${r.name}`); }
    catch (e) { toast(e.message, true); }
  };
  $$("[data-reset]", el).forEach((b) => (b.onclick = async () => {
    if (!confirm(`重置 ${b.dataset.reset} 的口令？原口令立即失效。`)) return;
    try { const r = await api(`/api/admin/public-accounts/${encodeURIComponent(b.dataset.reset)}/reset`, { method: "POST" }); await adUsers(el); showPw(r, "口令已重置"); }
    catch (e) { toast(e.message, true); }
  }));
  $("#uAdd").onclick = () => {
    const no = $("#uNo").value.trim(), have = us.find((u) => u.staff_no === no);
    if (/^\d{8}$/.test(no) && !have) { toast(`平台里没有工号为 ${no} 的教师账号，不能设为管理员`, true); return; }
    if (!have && !/^\d{10}$/.test(no)) { toast("请输入10位学号，或平台里已有的8位教师工号", true); return; }
    if (have && have.is_public) { toast("公共账号不能设为管理员", true); return; }
    set(no, true);
  };
  $$("[data-rm]", el).forEach((b) => (b.onclick = () => set(b.dataset.rm, false)));
  $$("[data-add]", el).forEach((b) => (b.onclick = () => set(b.dataset.add, true)));
}

route();
