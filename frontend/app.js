// ===== State =====
let currentInspection = null;
const liveSockets = new Map();   // job_id -> WebSocket

// ===== DOM =====
const $ = (id) => document.getElementById(id);
const urlInput = $("url-input");
const refererInput = $("referer-input");
const inspectBtn = $("inspect-btn");
const inspectStatus = $("inspect-status");
const resultEl = $("result");
const thumbEl = $("thumb");
const titleEl = $("title");
const metaLineEl = $("meta-line");
const downloaderLineEl = $("downloader-line");
const formatSelect = $("format-select");
const saveDirInput = $("save-dir-input");
const downloadBtn = $("download-btn");
const jobsList = $("jobs-list");
const settingsDialog = $("settings-dialog");
const settingsForm = $("settings-form");

// ===== API =====
async function api(path, opts = {}) {
  const r = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  if (!r.ok) {
    const detail = await r.json().catch(() => ({ detail: r.statusText }));
    throw new Error(detail.detail || `HTTP ${r.status}`);
  }
  return r.json();
}

// ===== Inspect =====
const sniffActions = $("sniff-actions");
const sniffBtn = $("sniff-btn");

inspectBtn.addEventListener("click", async () => {
  const url = urlInput.value.trim();
  if (!url) return;
  inspectBtn.disabled = true;
  inspectStatus.textContent = "正在抓取（可能要等一下，第一次会自动跑 headless Chromium）…";
  inspectStatus.className = "status";
  resultEl.classList.add("hidden");
  sniffActions.classList.add("hidden");
  try {
    const data = await api("/api/inspect", {
      method: "POST",
      body: JSON.stringify({
        url,
        referer: refererInput.value.trim() || null,
      }),
    });
    currentInspection = data;
    renderInspection(data);
    inspectStatus.textContent = `✓ 识别为 ${data.downloader}，找到 ${data.formats.length} 个格式`;
    inspectStatus.className = "status ok";
  } catch (e) {
    inspectStatus.textContent = `× ${e.message}`;
    inspectStatus.className = "status error";
    sniffActions.classList.remove("hidden");
  } finally {
    inspectBtn.disabled = false;
  }
});

sniffBtn.addEventListener("click", async () => {
  const url = urlInput.value.trim();
  if (!url) return;
  sniffBtn.disabled = true;
  inspectStatus.textContent = "已打开 Chromium 窗口 — 点击页面里的播放按钮，关掉广告，等 m3u8 出现…";
  inspectStatus.className = "status";
  try {
    const data = await api("/api/sniff", {
      method: "POST",
      body: JSON.stringify({
        url,
        mode: "headed",
        timeout_seconds: 120,
        referer: refererInput.value.trim() || null,
      }),
    });
    if (!data.media || data.media.length === 0) {
      inspectStatus.textContent = `× 没抓到 m3u8（${data.error || "timeout"}）。可能这个站用了不同的格式 — 试着在 DevTools Network 里手动找 m3u8 URL 粘贴。`;
      inspectStatus.className = "status error";
      return;
    }
    // Preserve all captured URLs so the user can switch source if the first isn't the right one.
    await probeSniffedSource(data.media[0], data.media, url);
    inspectStatus.textContent = data.media.length > 1
      ? `✓ 抓到 ${data.media.length} 个 m3u8（默认用第一个，可下方切换）`
      : `✓ 从浏览器抓到 m3u8`;
    inspectStatus.className = "status ok";
    sniffActions.classList.add("hidden");
  } catch (e) {
    inspectStatus.textContent = `× sniff 失败: ${e.message}`;
    inspectStatus.className = "status error";
  } finally {
    sniffBtn.disabled = false;
  }
});

async function probeSniffedSource(chosen, allCandidates, originalPageUrl) {
  const probe = await api("/api/inspect", {
    method: "POST",
    body: JSON.stringify({
      url: chosen.url,
      referer: chosen.referer || originalPageUrl,
    }),
  });
  probe.sniff_candidates = allCandidates;
  probe.sniff_page_url = originalPageUrl;
  probe.sniff_selected_index = allCandidates.findIndex(c => c.url === chosen.url);
  currentInspection = probe;
  renderInspection(probe);
}

const sniffSourceSelect = $("sniff-source");

sniffSourceSelect.addEventListener("change", async () => {
  if (!currentInspection || !currentInspection.sniff_candidates) return;
  const idx = parseInt(sniffSourceSelect.value, 10);
  const cand = currentInspection.sniff_candidates[idx];
  if (!cand) return;
  $("sniff-open-external").href = cand.url;
  inspectStatus.textContent = "切换源，正在重新解析…";
  inspectStatus.className = "status";
  try {
    await probeSniffedSource(cand, currentInspection.sniff_candidates, currentInspection.sniff_page_url);
    inspectStatus.textContent = "✓ 已切换";
    inspectStatus.className = "status ok";
  } catch (e) {
    inspectStatus.textContent = `× 切换失败: ${e.message}`;
    inspectStatus.className = "status error";
  }
});

urlInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter") inspectBtn.click();
});

$("sniff-copy").addEventListener("click", async () => {
  if (!currentInspection || !currentInspection.sniff_candidates) return;
  const idx = parseInt(sniffSourceSelect.value, 10) || 0;
  const cand = currentInspection.sniff_candidates[idx];
  if (!cand) return;
  try {
    await navigator.clipboard.writeText(cand.url);
    const btn = $("sniff-copy");
    const orig = btn.textContent;
    btn.textContent = "已复制 ✓";
    setTimeout(() => { btn.textContent = orig; }, 1500);
  } catch (e) {
    alert("复制失败：" + e.message + "\n\n" + cand.url);
  }
});

function renderInspection(data) {
  resultEl.classList.remove("hidden");
  thumbEl.src = data.thumbnail || "";
  thumbEl.style.display = data.thumbnail ? "block" : "none";
  titleEl.textContent = data.title || "(no title)";

  const metaParts = [];
  if (data.uploader) metaParts.push(data.uploader);
  if (data.duration) metaParts.push(formatDuration(data.duration));
  if (data.resolved_url && data.resolved_url !== data.source_url) {
    metaParts.push(truncate(data.resolved_url, 60));
  }
  metaLineEl.textContent = metaParts.join("  ·  ");
  downloaderLineEl.textContent = `downloader: ${data.downloader} · type: ${data.type}`;

  formatSelect.innerHTML = "";
  for (const f of data.formats) {
    const opt = document.createElement("option");
    opt.value = f.id;
    let label = f.label;
    if (f.note && !label.includes(f.note)) label += ` — ${f.note}`;
    if (f.filesize) label += ` (${formatBytes(f.filesize)})`;
    opt.textContent = label;
    formatSelect.appendChild(opt);
  }

  // Show the sniffed m3u8 URL(s) whenever the source came from browser sniff,
  // even if only one was captured — so the user can see & verify it.
  const cands = data.sniff_candidates || [];
  const sniffInfoEl = $("sniff-source-info");
  if (cands.length >= 1) {
    sniffInfoEl.classList.remove("hidden");
    sniffSourceSelect.innerHTML = "";
    cands.forEach((c, i) => {
      const opt = document.createElement("option");
      opt.value = i;
      const u = new URL(c.url);
      const tail = u.pathname.split("/").pop() || u.pathname;
      opt.textContent = cands.length > 1
        ? `[${i + 1}] ${u.host}  …/${truncate(tail, 44)}`
        : `${u.host}  …/${truncate(tail, 60)}`;
      opt.title = c.url;   // hover shows full URL
      sniffSourceSelect.appendChild(opt);
    });
    sniffSourceSelect.value = String(data.sniff_selected_index ?? 0);
    // Adjust label wording based on count
    $("sniff-source-label").textContent = cands.length > 1
      ? `从浏览器抓到 ${cands.length} 个 m3u8，可切换：`
      : "从浏览器抓到的 m3u8:";
    // Single-URL select loses "click to open" affordance; disable interaction if only 1
    sniffSourceSelect.disabled = (cands.length <= 1);
    // Wire the external-open link to the currently selected URL
    $("sniff-open-external").href = cands[data.sniff_selected_index ?? 0].url;
  } else {
    sniffInfoEl.classList.add("hidden");
  }

  // Reset filename input — user can override the derived title.
  // Keep the "no extension" hint from the base HTML placeholder even when
  // we augment it with the derived title as a preview.
  $("filename-input").value = "";
  $("filename-input").placeholder =
    `不含扩展名；留空则用 "${(data.title || "video").slice(0, 40)}"`;
}

// ===== Download =====
downloadBtn.addEventListener("click", async () => {
  if (!currentInspection) return;
  const fid = formatSelect.value;
  const data = currentInspection;
  downloadBtn.disabled = true;
  try {
    const customName = $("filename-input").value.trim();
    const job = await api("/api/download", {
      method: "POST",
      body: JSON.stringify({
        url: data.resolved_url || data.source_url,
        format_id: fid,
        downloader: data.downloader,
        title: data.title,
        filename_override: customName || null,
        save_dir: saveDirInput.value.trim() || null,
        headers: data.headers || {},
      }),
    });
    addJob(job);
    openJobSocket(job.job_id);
  } catch (e) {
    alert("启动下载失败：" + e.message);
  } finally {
    downloadBtn.disabled = false;
  }
});

// ===== Direct m3u8 =====
$("direct-m3u8-btn").addEventListener("click", async () => {
  const url = $("direct-m3u8-url").value.trim();
  if (!url) return;
  const referer = $("direct-m3u8-referer").value.trim();
  const title = $("direct-m3u8-title").value.trim() || null;
  const fid = $("direct-m3u8-quality").value.trim() || "best";
  const saveDir = $("direct-m3u8-save").value.trim() || saveDirInput.value.trim() || null;
  const statusEl = $("direct-m3u8-status");
  const btn = $("direct-m3u8-btn");
  btn.disabled = true;
  statusEl.textContent = "提交任务…";
  statusEl.className = "status";
  try {
    const headers = {};
    if (referer) headers["Referer"] = referer;
    const job = await api("/api/download", {
      method: "POST",
      body: JSON.stringify({
        url,
        format_id: fid,
        downloader: "m3u8",
        // Send `title` too — the backend job list surfaces `request.title`
        // as the display label, so leaving it blank shows the raw URL.
        title,
        filename_override: title,
        save_dir: saveDir,
        headers,
      }),
    });
    statusEl.textContent = `✓ 已开始下载 — 见下方任务列表`;
    statusEl.className = "status ok";
    addJob(job);
    openJobSocket(job.job_id);
  } catch (e) {
    statusEl.textContent = `× ${e.message}`;
    statusEl.className = "status error";
  } finally {
    btn.disabled = false;
  }
});

// ===== Jobs =====
async function refreshJobs() {
  try {
    const jobs = await api("/api/jobs");
    jobsList.innerHTML = "";
    for (const j of jobs) {
      addJob(j);
      if (j.status === "running" || j.status === "queued") {
        openJobSocket(j.job_id);
      }
    }
  } catch (e) {
    console.warn("refreshJobs failed", e);
  }
}

$("refresh-jobs").addEventListener("click", refreshJobs);

$("clear-finished").addEventListener("click", async () => {
  try {
    await api("/api/jobs/clear_finished", { method: "POST" });
    await refreshJobs();
  } catch (e) {
    alert("清除失败：" + e.message);
  }
});

function addJob(job) {
  const existing = document.getElementById(`job-${job.job_id}`);
  if (existing) {
    renderJob(existing, job);
    return;
  }
  const li = document.createElement("li");
  li.id = `job-${job.job_id}`;
  li.className = "job";
  li.innerHTML = `
    <div class="job-head">
      <div>
        <div class="job-title"></div>
        <div class="job-meta"></div>
      </div>
      <span class="job-status"></span>
    </div>
    <div class="progress-bar"><div class="progress-fill"></div></div>
    <div class="job-log"></div>
    <div class="job-actions">
      <button class="link cancel-btn">取消</button>
      <button class="link open-btn hidden">打开文件夹</button>
      <button class="link remove-btn hidden danger">移除</button>
    </div>
  `;
  jobsList.prepend(li);
  renderJob(li, job);

  li.querySelector(".cancel-btn").addEventListener("click", async () => {
    try {
      await api(`/api/jobs/${job.job_id}/cancel`, { method: "POST" });
    } catch (e) { /* ignore */ }
  });
  li.querySelector(".open-btn").addEventListener("click", async () => {
    try {
      await api(`/api/jobs/${job.job_id}/reveal`, { method: "POST" });
    } catch (e) {
      // Fallback: copy path to clipboard
      const path = li.dataset.outputFile;
      if (path) {
        try { await navigator.clipboard.writeText(path); } catch {}
        alert("打不开文件夹（" + e.message + "）。路径已复制：\n" + path);
      }
    }
  });
  li.querySelector(".remove-btn").addEventListener("click", async () => {
    try {
      await api(`/api/jobs/${job.job_id}`, { method: "DELETE" });
      li.remove();
    } catch (e) {
      alert("移除失败：" + e.message);
    }
  });
}

function renderJob(li, job) {
  li.querySelector(".job-title").textContent = job.title || job.url;
  const metaParts = [
    job.downloader,
    job.format_id,
    job.save_dir,
  ];
  if (job.speed) metaParts.push(job.speed);
  if (job.eta) metaParts.push("ETA " + job.eta);
  li.querySelector(".job-meta").textContent = metaParts.filter(Boolean).join("  ·  ");

  const statusEl = li.querySelector(".job-status");
  statusEl.textContent = job.status;
  statusEl.className = "job-status " + job.status;

  const fill = li.querySelector(".progress-fill");
  fill.style.width = (job.percent || 0).toFixed(1) + "%";
  fill.className = "progress-fill";
  if (job.status === "done") fill.classList.add("done");
  if (job.status === "error" || job.status === "cancelled") fill.classList.add("error");

  const log = li.querySelector(".job-log");
  if (job.error) {
    log.textContent = "ERROR: " + job.error;
  } else if (job.log_tail && job.log_tail.length) {
    log.textContent = job.log_tail[job.log_tail.length - 1];
  }

  const finished = ["done", "error", "cancelled"].includes(job.status);
  li.querySelector(".cancel-btn").classList.toggle("hidden", finished);
  li.querySelector(".remove-btn").classList.toggle("hidden", !finished);

  const openBtn = li.querySelector(".open-btn");
  if (job.output_file) {
    li.dataset.outputFile = job.output_file;
    openBtn.classList.remove("hidden");
  }
}

function openJobSocket(jobId) {
  if (liveSockets.has(jobId)) return;
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws/progress/${jobId}`);
  liveSockets.set(jobId, ws);
  ws.onmessage = (evt) => {
    const data = JSON.parse(evt.data);
    const li = document.getElementById(`job-${jobId}`);
    if (li) renderJob(li, data);
    if (data.__closed__) {
      ws.close();
      liveSockets.delete(jobId);
    }
  };
  ws.onclose = () => liveSockets.delete(jobId);
  ws.onerror = () => liveSockets.delete(jobId);
}

// ===== Settings =====
// custom_headers is the only object-valued setting; the generic loop below
// would render it as "[object Object]", so it round-trips through a
// "Name: Value" per line textarea instead.
const HEADER_NAME_RE = /^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$/;

function headersToText(obj) {
  if (!obj || typeof obj !== "object") return "";
  return Object.entries(obj).map(([k, v]) => `${k}: ${v}`).join("\n");
}

/** Parse the textarea into {headers, errors}. Splits on the FIRST colon so
 *  values containing colons (URLs!) survive. */
function parseHeadersText(text) {
  const headers = {};
  const errors = [];
  for (const [i, rawLine] of String(text || "").split("\n").entries()) {
    const line = rawLine.trim();
    if (!line || line.startsWith("#")) continue;
    const colon = line.indexOf(":");
    if (colon < 1) {
      errors.push(`第 ${i + 1} 行缺少 "名称: 值" 中的冒号`);
      continue;
    }
    const name = line.slice(0, colon).trim();
    const value = line.slice(colon + 1).trim();
    if (!HEADER_NAME_RE.test(name)) {
      errors.push(`第 ${i + 1} 行的头名称无效：${name}`);
      continue;
    }
    if (!value) {
      errors.push(`第 ${i + 1} 行的 ${name} 没有值`);
      continue;
    }
    headers[name] = value;
  }
  return { headers, errors };
}

$("settings-btn").addEventListener("click", async () => {
  const cfg = await api("/api/config");
  for (const [k, v] of Object.entries(cfg)) {
    const el = settingsForm.elements.namedItem(k);
    if (el) el.value = k === "custom_headers" ? headersToText(v) : v;
  }
  saveDirInput.value = cfg.save_dir || "";
  $("custom-headers-error").classList.add("hidden");
  settingsDialog.showModal();
  loadToolVersions();
});

$("settings-cancel").addEventListener("click", () => settingsDialog.close());

// ===== Tool versions =====
let lastToolVersions = null;

async function loadToolVersions() {
  const el = $("tool-versions-list");
  el.textContent = "加载中…";
  try {
    const v = await api("/api/tools/versions");
    lastToolVersions = v;
    renderToolVersions(v);
  } catch (e) {
    el.textContent = "× " + e.message;
  }
}

function ageClass(days) {
  if (days == null) return "";
  if (days > 60) return "tool-age-verystale";
  if (days > 30) return "tool-age-stale";
  return "tool-age-fresh";
}

function renderToolVersions(v) {
  const el = $("tool-versions-list");
  el.innerHTML = "";
  const rows = [
    { key: "ytdlp",  label: "yt-dlp" },
    { key: "m3u8dl", label: "N_m3u8DL-RE" },
    { key: "ffmpeg", label: "ffmpeg" },
  ];
  for (const row of rows) {
    const info = v[row.key];
    if (!info) continue;
    const div = document.createElement("div");
    div.className = "tool-line";
    const name = document.createElement("span");
    name.className = "tool-name";
    name.textContent = row.label;
    const right = document.createElement("span");
    if (!info.available) {
      right.textContent = "× 找不到 (" + info.path + ")";
      right.className = "tool-age-verystale";
    } else {
      let text = info.version || "未知";
      if (info.age_days != null) text += ` · ${info.age_days} 天前`;
      right.textContent = text;
      right.className = ageClass(info.age_days);
    }
    div.append(name, right);
    el.append(div);
  }
}

$("refresh-versions-btn").addEventListener("click", loadToolVersions);

const UPDATE_BTN_LABEL = "更新 yt-dlp";

async function runYtdlpUpdate() {
  const btn = $("update-ytdlp-btn");
  const logEl = $("update-log");
  const fixEl = $("relocate-hint");
  btn.disabled = true;
  btn.textContent = "更新中…";
  logEl.classList.remove("hidden");
  logEl.textContent = "运行 yt-dlp -U，请稍候（可能几十秒）…";
  fixEl.classList.add("hidden");
  try {
    const r = await api("/api/tools/update_ytdlp", { method: "POST" });
    logEl.textContent = r.log || "(无输出)";
    if (r.ok) {
      const after = r.version_after ? ` → ${r.version_after}` : "";
      btn.textContent = "更新完成 ✓" + after;
      setTimeout(() => { btn.textContent = UPDATE_BTN_LABEL; }, 3000);
      await loadToolVersions();
    } else if (r.permission_error) {
      // Show relocation offer
      btn.textContent = "更新失败（权限不足）";
      const target = r.suggested_relocation || "用户可写目录";
      fixEl.classList.remove("hidden");
      fixEl.innerHTML = "";
      const msg = document.createElement("div");
      msg.className = "muted small";
      const currentPath = (lastToolVersions && lastToolVersions.ytdlp && lastToolVersions.ytdlp.path) || "当前路径";
      msg.textContent = `写不进 ${currentPath} — 通常是因为它在需要管理员权限的目录（比如 C:\\ 根目录）。挪到用户目录就能自动更新：`;
      const dest = document.createElement("code");
      dest.textContent = target;
      dest.style.cssText = "display:block;margin:4px 0;padding:4px 6px;background:var(--panel);border-radius:3px;font-size:11px;word-break:break-all";
      const relocateBtn = document.createElement("button");
      relocateBtn.type = "button";
      relocateBtn.textContent = "移到用户目录并重试更新";
      relocateBtn.className = "primary";
      relocateBtn.style.marginTop = "6px";
      relocateBtn.addEventListener("click", async () => {
        relocateBtn.disabled = true;
        relocateBtn.textContent = "移动中…";
        try {
          const mv = await api("/api/tools/relocate_ytdlp", { method: "POST", body: JSON.stringify({}) });
          if (!mv.ok) {
            logEl.textContent = "× 移动失败: " + (mv.error || "unknown");
            relocateBtn.textContent = "移动失败";
            return;
          }
          logEl.textContent = `已复制到 ${mv.destination}\n(原文件 ${mv.source} 仍保留)\n\n准备重新更新…`;
          // Sync the settings form field so a subsequent "Save" doesn't clobber
          // the new path with the old cached value.
          const pathInput = settingsForm.elements.namedItem("ytdlp_path");
          if (pathInput) pathInput.value = mv.destination;
          fixEl.classList.add("hidden");
          setTimeout(runYtdlpUpdate, 500);
        } catch (e) {
          logEl.textContent = "× " + e.message;
          relocateBtn.textContent = "移动失败";
        } finally {
          relocateBtn.disabled = false;
        }
      });
      fixEl.append(msg, dest, relocateBtn);
    } else {
      btn.textContent = "更新失败";
      setTimeout(() => { btn.textContent = UPDATE_BTN_LABEL; }, 3000);
    }
  } catch (e) {
    logEl.textContent = "× " + e.message;
    btn.textContent = "更新失败";
    // Was `origLabel` — a leftover from an earlier revision where the label
    // was captured per-call. That variable no longer exists, so the reset
    // now uses the shared constant to match the sibling branches above.
    setTimeout(() => { btn.textContent = UPDATE_BTN_LABEL; }, 3000);
  } finally {
    btn.disabled = false;
  }
}

$("update-ytdlp-btn").addEventListener("click", runYtdlpUpdate);

settingsForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const errEl = $("custom-headers-error");
  const { headers, errors } = parseHeadersText($("custom-headers").value);
  if (errors.length) {
    // Don't silently drop malformed lines — a typo'd header is exactly the
    // kind of thing the user would otherwise spend an hour debugging.
    errEl.textContent = "请求头有问题：" + errors.join("；");
    errEl.classList.remove("hidden");
    return;
  }
  errEl.classList.add("hidden");

  const fd = new FormData(settingsForm);
  const patch = {};
  for (const [k, v] of fd.entries()) {
    if (k === "custom_headers") continue;   // object-valued, set below
    patch[k] = k === "max_concurrent_downloads" ? Number(v) : v;
  }
  patch.custom_headers = headers;
  await api("/api/config", { method: "POST", body: JSON.stringify(patch) });
  settingsDialog.close();
});

// ===== Utils =====
function formatDuration(s) {
  s = Math.round(s);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  return h ? `${h}:${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}`
           : `${m}:${String(sec).padStart(2, "0")}`;
}
function formatBytes(b) {
  if (b < 1024) return b + " B";
  if (b < 1024 * 1024) return (b / 1024).toFixed(1) + " KB";
  if (b < 1024 * 1024 * 1024) return (b / 1024 / 1024).toFixed(1) + " MB";
  return (b / 1024 / 1024 / 1024).toFixed(2) + " GB";
}
function truncate(s, n) {
  return s.length > n ? s.slice(0, n - 1) + "…" : s;
}

// ===== First-run tool bootstrap =====
const bootstrapBanner = $("bootstrap-banner");
const bootstrapMessage = $("bootstrap-message");
const bootstrapSteps = $("bootstrap-steps");
const bootstrapBtn = $("bootstrap-btn");
let bootstrapTimer = null;

const TOOL_LABELS = {
  ytdlp: "yt-dlp",
  m3u8dl: "N_m3u8DL-RE",
  ffmpeg: "ffmpeg",
  jsruntime: "deno (YouTube 所需)",
};

function renderBootstrap(st) {
  const missing = st.missing || [];
  const running = st.state === "running";

  if (!running && missing.length === 0) {
    // Everything present: hide (briefly show success if we were downloading).
    if (st.state === "done" && !bootstrapBanner.classList.contains("hidden")) {
      bootstrapMessage.textContent = "✓ 组件就绪，可以开始下载了";
      bootstrapBtn.classList.add("hidden");
      setTimeout(() => bootstrapBanner.classList.add("hidden"), 4000);
    } else {
      bootstrapBanner.classList.add("hidden");
    }
    return;
  }

  bootstrapBanner.classList.remove("hidden");
  if (running) {
    bootstrapMessage.textContent = "首次启动：正在自动下载所需组件（yt-dlp / N_m3u8DL-RE / ffmpeg / deno）…";
    bootstrapBtn.classList.add("hidden");
  } else if (st.state === "error") {
    bootstrapMessage.textContent = "✗ 部分组件下载失败（可重试，或在 ⚙ 设置里手动填工具路径）";
    bootstrapBtn.textContent = "重试下载";
    bootstrapBtn.classList.remove("hidden");
  } else {
    bootstrapMessage.textContent =
      "缺少组件：" + missing.map((k) => TOOL_LABELS[k] || k).join("、") + " — 无法下载视频";
    bootstrapBtn.textContent = "一键下载缺失组件";
    bootstrapBtn.classList.remove("hidden");
  }

  bootstrapSteps.innerHTML = "";
  for (const [key, step] of Object.entries(st.steps || {})) {
    const line = document.createElement("div");
    line.className = "bootstrap-step";
    const pct = step.percent != null ? ` ${step.percent}%` : "";
    const mark = step.state === "done" ? "✓" : step.state === "error" ? "✗" : "↓";
    line.textContent = `${mark} ${TOOL_LABELS[key] || key}${pct} — ${step.message || step.state}`;
    if (step.state === "error") line.classList.add("bootstrap-step-error");
    if (step.state === "done") line.classList.add("bootstrap-step-done");
    bootstrapSteps.append(line);
  }
}

let bootstrapPollSeq = 0;

async function pollBootstrap() {
  // Sequence guard: a manual retry can start a new loop while a previous
  // poll is awaiting its fetch — without this, both would keep scheduling
  // and the page ends up with parallel polling loops racing renders.
  const seq = ++bootstrapPollSeq;
  if (bootstrapTimer) {
    clearTimeout(bootstrapTimer);
    bootstrapTimer = null;
  }
  let st;
  try {
    st = await api("/api/tools/bootstrap/status");
  } catch {
    return; // server not ready yet; init retry loop handles it
  }
  if (seq !== bootstrapPollSeq) return; // superseded by a newer loop
  renderBootstrap(st);
  if (st.state === "running") {
    bootstrapTimer = setTimeout(pollBootstrap, 1500);
  } else if ((st.missing || []).length > 0) {
    // Missing but idle/error — re-check occasionally in case the user fixes
    // paths in Settings.
    bootstrapTimer = setTimeout(pollBootstrap, 8000);
  } else {
    bootstrapTimer = null;
  }
}

bootstrapBtn.addEventListener("click", async () => {
  bootstrapBtn.classList.add("hidden");
  try {
    renderBootstrap(await api("/api/tools/bootstrap", { method: "POST" }));
  } catch (e) {
    bootstrapMessage.textContent = "✗ " + e.message;
  }
  if (bootstrapTimer) clearTimeout(bootstrapTimer);
  bootstrapTimer = setTimeout(pollBootstrap, 800);
});

// ===== Init =====
(async function init() {
  const cfg = await api("/api/config").catch(() => null);
  if (cfg) saveDirInput.value = cfg.save_dir || "";
  await refreshJobs();
  pollBootstrap();
})();
