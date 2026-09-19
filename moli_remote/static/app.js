/* 魔力局域网远程助手 主控端逻辑(原生 JS,无依赖) */
"use strict";

const $ = (s) => document.querySelector(s);
const clamp = (v, a, b) => Math.min(b, Math.max(a, v));

const els = {
  landing: $("#landing"), viewer: $("#viewer"),
  devInfo: $("#devInfo"), code: $("#codeInput"),
  connect: $("#btnConnect"), landingMsg: $("#landingMsg"), quick: $("#quickReconnect"),
  stage: $("#stage"), wrap: $("#canvasWrap"), canvas: $("#screen"),
  cursor: $("#cursor"), hint: $("#hint"),
  toolbar: $("#toolbar"), status: $("#status"),
  overlay: $("#overlay"), overlayMsg: $("#overlayMsg"), btnBack: $("#btnBack"),
  textBar: $("#textBar"), textInput: $("#textInput"), btnTextOff: $("#btnTextOff"),
  clipModal: $("#clipModal"), clipTitle: $("#clipTitle"), clipText: $("#clipText"),
  clipCopy: $("#clipCopy"), clipSend: $("#clipSend"), clipClose: $("#clipClose"),
  quality: $("#qualitySelect"), monitor: $("#monSelect"), controlToggle: $("#controlToggle"),
  toast: $("#toast"),
};

const state = {
  ws: null, authed: false, control: false, controlEnabled: true, held: false,
  closedByUser: false, reconnectTries: 0, reconnectTimer: null,
  screen: { w: 0, h: 0 }, settings: { preset: "hd", monitor: 1 },
  frames: 0, fps: 0, rtt: 0,
  drawChain: Promise.resolve(),
  mouseThrottle: 0,
  textMode: false, lastSent: "",
  toolbarTimer: null, toastTimer: null, pingTimer: null, statTimer: null,
};

const tokenKey = () => "moli-remote:" + location.host;
const getToken = () => sessionStorage.getItem(tokenKey());
const setToken = (t) => t ? sessionStorage.setItem(tokenKey(), t) : sessionStorage.removeItem(tokenKey());

/* ============================ 连接管理 ============================ */

function openWs(authExtra) {
  closeWs();
  state.closedByUser = false;
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws`);
  ws.binaryType = "arraybuffer";
  state.ws = ws;
  ws.onopen = () => {
    ws.send(JSON.stringify(Object.assign({
      t: "auth",
      name: /Mobi|Android|iPhone|iPad/i.test(navigator.userAgent) ? "移动端浏览器" : "浏览器",
    }, authExtra)));
  };
  ws.onmessage = onMessage;
  ws.onclose = onClose;
  ws.onerror = () => {};
}

function closeWs() {
  if (state.ws) {
    state.ws.onclose = null;
    state.ws.onmessage = null;
    try { state.ws.close(); } catch (e) { /* ignore */ }
    state.ws = null;
  }
  if (state.reconnectTimer) { clearTimeout(state.reconnectTimer); state.reconnectTimer = null; }
}

function onMessage(ev) {
  if (ev.data instanceof ArrayBuffer) { onFrame(ev.data); return; }
  let m;
  try { m = JSON.parse(ev.data); } catch (e) { return; }
  switch (m.t) {
    case "auth_ok": {
      state.authed = true;
      state.reconnectTries = 0;
      setToken(m.token);
      state.control = m.control;
      state.held = m.control || state.held;
      if (m.control) {
        state.controlEnabled = true;
        els.controlToggle.checked = true;
        els.controlToggle.disabled = false;
      } else {
        state.controlEnabled = false;
        els.controlToggle.checked = false;
        els.controlToggle.disabled = true;
      }
      state.screen = m.screen;
      state.settings = m.settings;
      fillMonitorSelect(m.monitors);
      syncToolbar();
      showViewer();
      hideOverlay();
      startTimers();
      break;
    }
    case "auth_fail":
      state.authed = false;
      if (m.expired) { setToken(null); showLanding("会话已过期,请重新输入验证码"); }
      else {
        els.landingMsg.textContent = m.reason || "验证码错误";
        els.landingMsg.classList.add("err");
        els.connect.disabled = false;
        closeWs();
      }
      break;
    case "settings":
      state.settings = Object.assign({}, state.settings, m.settings);
      syncToolbar();
      break;
    case "control_state":
      state.control = !!m.you;
      state.held = !!m.held;
      if (state.control) {
        state.controlEnabled = true;
        els.controlToggle.checked = true;
        els.controlToggle.disabled = false;
      } else {
        els.controlToggle.checked = false;
        els.controlToggle.disabled = !!m.held;
      }
      updateStatusText();
      if (!m.you && m.held) toast(`当前处于观看模式(被「${m.by || "其他设备"}」控制)`);
      else if (!m.you && !m.held) toast("可勾选「控制模式」取得控制权");
      break;
    case "kicked":
      state.closedByUser = true;
      closeWs();
      showOverlay(`设备已被「${m.by}」接管`, true);
      break;
    case "pong":
      state.rtt = Math.max(0, Math.round(performance.now() - m.ts));
      break;
    case "clip":
      openClipModal("远程剪贴板", m.text || "", false);
      break;
    case "clip_ok":
      toast("已写入远程剪贴板,可在远程电脑上直接 Ctrl+V");
      break;
    case "warn":
      toast(m.msg || "");
      break;
  }
}

function onClose() {
  state.authed = false;
  stopTimers();
  if (state.closedByUser) return;
  state.reconnectTries++;
  if (state.reconnectTries > 8) {
    showOverlay("连接已断开,设备无响应", true);
    return;
  }
  showOverlay(`连接断开,正在重连(第 ${state.reconnectTries} 次)…`);
  state.reconnectTimer = setTimeout(() => {
    openWs({ token: getToken() });
  }, Math.min(3000, 600 * state.reconnectTries));
}

/* ============================ 画面渲染 ============================ */

function onFrame(buf) {
  if (buf.byteLength < 10) return;
  const dv = new DataView(buf, 0, 9);
  const w = dv.getUint16(0), h = dv.getUint16(2);
  const cx = dv.getUint16(4), cy = dv.getUint16(6);
  const flags = dv.getUint8(8);
  if (!w || !h) return;
  const jpeg = buf.slice(9);

  if (els.canvas.width !== w || els.canvas.height !== h) {
    els.canvas.width = w;
    els.canvas.height = h;
  }
  if (state.screen.w !== w || state.screen.h !== h) {
    state.screen = { w, h };
    updateStatusText();
  }
  els.hint.classList.add("hidden");
  state.frames++;
  state.lastFrameAt = performance.now();

  state.drawChain = state.drawChain
    .then(() => createImageBitmap(new Blob([jpeg], { type: "image/jpeg" })))
    .then((bmp) => {
      const ctx = els.canvas.getContext("2d");
      ctx.drawImage(bmp, 0, 0, els.canvas.width, els.canvas.height);
      bmp.close();
      placeCursor(cx, cy, !!(flags & 1));
    })
    .catch(() => {});
}

function placeCursor(cx, cy, visible) {
  if (!visible) { els.cursor.style.display = "none"; return; }
  const rect = els.canvas.getBoundingClientRect();
  if (!rect.width) return;
  els.cursor.style.display = "block";
  els.cursor.style.transform =
    `translate(${(cx * rect.width / state.screen.w).toFixed(1)}px,` +
    ` ${(cy * rect.height / state.screen.h).toFixed(1)}px)`;
}

/* ============================ 输入转发 ============================ */

function sendInput(obj) {
  if (state.authed && state.control && state.controlEnabled &&
      state.ws && state.ws.readyState === 1) {
    state.ws.send(JSON.stringify(obj));
  }
}

function canvasPos(e) {
  const rect = els.canvas.getBoundingClientRect();
  return {
    x: clamp((e.clientX - rect.left) / rect.width, 0, 1),
    y: clamp((e.clientY - rect.top) / rect.height, 0, 1),
  };
}

function sendMove(e, force) {
  const now = performance.now();
  if (!force && now - state.mouseThrottle < 30) return;
  state.mouseThrottle = now;
  const p = canvasPos(e);
  sendInput({ t: "in", k: "move", x: p.x, y: p.y });
}

const btnName = (b) => (b === 2 ? "right" : b === 1 ? "middle" : "left");

function bindCanvasInput() {
  els.canvas.addEventListener("pointermove", (e) => sendMove(e, false));
  els.canvas.addEventListener("pointerdown", (e) => {
    try { els.canvas.setPointerCapture(e.pointerId); } catch (err) { /* ignore */ }
    sendMove(e, true);
    sendInput({ t: "in", k: "down", btn: btnName(e.button) });
    e.preventDefault();
  });
  els.canvas.addEventListener("pointerup", (e) => {
    sendMove(e, true);
    sendInput({ t: "in", k: "up", btn: btnName(e.button) });
  });
  els.canvas.addEventListener("wheel", (e) => {
    e.preventDefault();
    sendMove(e, true);
    const line = e.deltaMode === 1; // Firefox 行模式
    sendInput({
      t: "in", k: "wheel",
      dx: line ? -e.deltaX : -e.deltaX / 100,
      dy: line ? -e.deltaY : -e.deltaY / 100,
    });
  }, { passive: false });
  els.canvas.addEventListener("contextmenu", (e) => e.preventDefault());
}

function isTypingTarget(e) {
  const t = e.target;
  if (!t) return false;
  const tag = t.tagName;
  return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || t.isContentEditable;
}

function bindKeyboard() {
  window.addEventListener("keydown", (e) => {
    if (!state.authed || !state.controlEnabled || state.textMode) return;
    if (isTypingTarget(e)) return;
    if (els.clipModal && !els.clipModal.classList.contains("hidden")) return;
    sendInput({ t: "key", code: e.code, key: e.key, kc: e.keyCode, down: true });
    e.preventDefault();
  });
  window.addEventListener("keyup", (e) => {
    if (!state.authed || !state.controlEnabled || state.textMode) return;
    if (isTypingTarget(e)) return;
    sendInput({ t: "key", code: e.code, key: e.key, kc: e.keyCode, down: false });
    e.preventDefault();
  });
}

/* ---------- 文字输入(IME 透传) ---------- */

function setTextMode(on) {
  state.textMode = on;
  els.textBar.classList.toggle("hidden", !on);
  $("#btnText").classList.toggle("active", on);
  if (on) {
    els.textInput.focus();
    toast("文字输入已开启,支持中文输入法;关闭前键盘不转发到远程");
  } else {
    els.textInput.blur();
    els.textInput.value = "";
    state.lastSent = "";
  }
}

function handleTextInput() {
  const ta = els.textInput;
  const v = ta.value;
  if (v === state.lastSent) return;
  const composing = ta.dataset.composing === "1";
  if (v.startsWith(state.lastSent)) {
    sendInput({ t: "text", text: v.slice(state.lastSent.length), bs: 0 });
  } else {
    sendInput({ t: "text", text: v, bs: state.lastSent.length });
  }
  state.lastSent = v;
  if (!composing) {
    ta.value = "";
    state.lastSent = "";
    ta.style.height = "auto";
  }
}

function bindTextBar() {
  const ta = els.textInput;
  ta.addEventListener("compositionstart", () => { ta.dataset.composing = "1"; });
  ta.addEventListener("compositionend", () => {
    delete ta.dataset.composing;
    handleTextInput();
  });
  // 组合被中断(如失焦)时兜底发送,避免文字滞留
  ta.addEventListener("blur", () => {
    if (ta.dataset.composing === "1") {
      delete ta.dataset.composing;
      handleTextInput();
    }
  });
  ta.addEventListener("input", () => {
    if (ta.dataset.composing !== "1") handleTextInput();
    ta.style.height = "auto";
    ta.style.height = Math.min(90, ta.scrollHeight) + "px";
  });
  els.btnTextOff.addEventListener("click", () => setTextMode(false));
}

/* ============================ 工具栏 / 功能 ============================ */

function fillMonitorSelect(monitors) {
  els.monitor.innerHTML = "";
  (monitors || []).forEach((m) => {
    const opt = document.createElement("option");
    opt.value = m.i;
    opt.textContent = m.i === 0 ? `全部屏幕 (${m.w}×${m.h})` : `显示器 ${m.i} (${m.w}×${m.h})`;
    els.monitor.appendChild(opt);
  });
  els.monitor.value = String(state.settings.monitor);
}

function syncToolbar() {
  if (state.settings.preset) els.quality.value = state.settings.preset;
  if (els.monitor.querySelector(`option[value="${state.settings.monitor}"]`)) {
    els.monitor.value = String(state.settings.monitor);
  }
  updateStatusText();
}

function updateStatusText() {
  const s = state.screen;
  const watch = state.authed && !state.control;
  els.status.classList.toggle("hidden", !state.authed);
  els.status.classList.toggle("watch", watch);
  els.status.textContent =
    (watch ? "观看模式 · " : "") +
    `${s.w}×${s.h} · ${state.fps} fps · ${state.rtt} ms`;
}

function bindToolbar() {
  els.quality.addEventListener("change", () => {
    sendInput({ t: "cfg", preset: els.quality.value });
  });
  els.monitor.addEventListener("change", () => {
    sendInput({ t: "cfg", monitor: parseInt(els.monitor.value, 10) });
  });
  els.controlToggle.addEventListener("change", () => {
    state.controlEnabled = els.controlToggle.checked;
    if (state.controlEnabled && !state.control) {
      state.ws && state.ws.readyState === 1 && state.ws.send(JSON.stringify({ t: "take" }));
      toast("正在请求控制权…");
    }
    updateStatusText();
  });
  $("#btnText").addEventListener("click", () => setTextMode(!state.textMode));
  $("#btnClipGet").addEventListener("click", () => {
    sendInput({ t: "clip_get" });
    toast("正在读取远程剪贴板…");
  });
  $("#btnClipSet").addEventListener("click", async () => {
    let text = "";
    try { text = await navigator.clipboard.readText(); } catch (e) { /* http 下不可用 */ }
    openClipModal("发送到远程剪贴板", text, true);
  });
  $("#btnShot").addEventListener("click", () => {
    els.canvas.toBlob((blob) => {
      if (!blob) return;
      const a = document.createElement("a");
      const ts = new Date().toISOString().replace(/[-:T]/g, "").slice(0, 15);
      a.href = URL.createObjectURL(blob);
      a.download = `romoter-${ts}.png`;
      a.click();
      setTimeout(() => URL.revokeObjectURL(a.href), 3000);
      toast("截图已保存");
    }, "image/png");
  });
  $("#btnFull").addEventListener("click", () => {
    if (document.fullscreenElement) document.exitFullscreen();
    else els.stage.requestFullscreen && els.stage.requestFullscreen().catch(() => toast("当前浏览器不支持全屏"));
  });
  $("#btnLeave").addEventListener("click", () => {
    state.closedByUser = true;
    closeWs();
    stopTimers();
    showLanding("已断开连接");
  });
}

/* ---------- 剪贴板弹窗 ---------- */

function openClipModal(title, text, sendMode) {
  els.clipTitle.textContent = title;
  els.clipText.value = text || "";
  els.clipSend.classList.toggle("hidden", !sendMode);
  els.clipCopy.classList.toggle("hidden", sendMode);
  els.clipModal.classList.remove("hidden");
  els.clipText.focus();
}

function bindClipModal() {
  els.clipClose.addEventListener("click", () => els.clipModal.classList.add("hidden"));
  els.clipModal.addEventListener("click", (e) => {
    if (e.target === els.clipModal) els.clipModal.classList.add("hidden");
  });
  els.clipCopy.addEventListener("click", async () => {
    const text = els.clipText.value;
    try {
      await navigator.clipboard.writeText(text);
      toast("已复制到本机剪贴板");
    } catch (e) {
      els.clipText.select();
      document.execCommand("copy");
      toast("已复制到本机剪贴板");
    }
  });
  els.clipSend.addEventListener("click", () => {
    sendInput({ t: "clip_set", text: els.clipText.value });
    els.clipModal.classList.add("hidden");
  });
}

/* ---------- 工具栏自动隐藏 ---------- */

function wakeToolbar() {
  els.toolbar.classList.remove("hide");
  els.status.style.opacity = "1";
  clearTimeout(state.toolbarTimer);
  state.toolbarTimer = setTimeout(() => {
    els.toolbar.classList.add("hide");
    els.status.style.opacity = "0";
  }, 2600);
}

/* ============================ 页面切换 / 遮罩 ============================ */

function showLanding(msg) {
  msg = msg || "";
  els.landing.classList.remove("hidden");
  els.viewer.classList.add("hidden");
  els.overlay.classList.add("hidden");
  els.connect.disabled = false;
  els.landingMsg.textContent = msg;
  els.landingMsg.classList.toggle("err", msg.includes("错误") || msg.includes("过期"));
  els.hint.classList.remove("hidden");
  state.authed = false;
  state.control = false;
  state.fps = 0;
  state.reconnectTries = 0;
  updateQuickLink();
  fetchInfo();
}

function showViewer() {
  els.landing.classList.add("hidden");
  els.viewer.classList.remove("hidden");
  wakeToolbar();
}

function showOverlay(msg, failed) {
  els.overlay.classList.remove("hidden");
  els.overlayMsg.textContent = msg;
  els.overlay.querySelector(".spin").style.display = failed ? "none" : "block";
  els.btnBack.classList.toggle("hidden", !failed);
}

function hideOverlay() { els.overlay.classList.add("hidden"); }

function toast(text) {
  els.toast.textContent = text;
  els.toast.classList.add("show");
  clearTimeout(state.toastTimer);
  state.toastTimer = setTimeout(() => els.toast.classList.remove("show"), 2200);
}

function updateQuickLink() {
  els.quick.classList.toggle("hidden", !getToken());
}

/* ============================ 定时器 ============================ */

function startTimers() {
  stopTimers();
  state.pingTimer = setInterval(() => {
    if (state.ws && state.ws.readyState === 1) {
      state.ws.send(JSON.stringify({ t: "ping", ts: performance.now() }));
    }
  }, 2000);
  state.statTimer = setInterval(() => {
    state.fps = state.frames;
    state.frames = 0;
    updateStatusText();
  }, 1000);
}

function stopTimers() {
  clearInterval(state.pingTimer);
  clearInterval(state.statTimer);
  state.pingTimer = state.statTimer = null;
}

/* ============================ 初始化 ============================ */

function fetchInfo() {
  fetch("/api/info").then((r) => r.json()).then((info) => {
    els.devInfo.innerHTML = "";
    const b = document.createElement("b");
    b.textContent = info.name;
    els.devInfo.appendChild(b);
    els.devInfo.appendChild(document.createTextNode(` · ${info.os} · 魔力远程助手 v${info.version}`));
    if (!info.input) els.devInfo.appendChild(document.createTextNode(" · ⚠ 输入控制不可用,仅观看"));
  }).catch(() => {
    els.devInfo.textContent = "获取设备信息失败,请确认地址正确且被控端已启动";
  });
}

function bindLanding() {
  els.code.addEventListener("input", () => {
    els.code.value = els.code.value.replace(/\D/g, "").slice(0, 6);
  });
  const doConnect = () => {
    const code = els.code.value.trim();
    if (code.length !== 6) {
      els.landingMsg.textContent = "请输入 6 位数字验证码";
      els.landingMsg.classList.add("err");
      return;
    }
    els.connect.disabled = true;
    els.landingMsg.textContent = "";
    showOverlay("正在连接设备…");
    openWs({ code });
  };
  els.connect.addEventListener("click", doConnect);
  els.code.addEventListener("keydown", (e) => { if (e.key === "Enter") doConnect(); });
  els.quick.addEventListener("click", () => {
    els.connect.disabled = true;
    showOverlay("正在快速重连…");
    openWs({ token: getToken() });
  });
  els.btnBack.addEventListener("click", () => {
    state.closedByUser = true;
    closeWs();
    showLanding("");
  });
}

function bindWake() {
  ["pointermove", "pointerdown", "touchstart"].forEach((evt) =>
    els.viewer.addEventListener(evt, wakeToolbar, { passive: true }));
}

function init() {
  bindLanding();
  bindToolbar();
  bindCanvasInput();
  bindKeyboard();
  bindTextBar();
  bindClipModal();
  bindWake();
  window.addEventListener("beforeunload", closeWs);
  showLanding("");
}

init();
