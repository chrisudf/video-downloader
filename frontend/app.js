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
    // Pick the first media URL — usually the master playlist
    const first = data.media[0];
    inspectStatus.textContent = `抓到 ${data.media.length} 个 m3u8，正在解析变体…`;
    const probe = await api("/api/inspect", {
      method: "POST",
      body: JSON.stringify({
        url: first.url,
        referer: first.referer || url,
      }),
    });
    currentInspection = probe;
    renderInspection(probe);
    inspectStatus.textContent = `✓ 从浏览器抓到 ${probe.formats.length} 个格式`;
    inspectStatus.className = "status ok";
    sniffActions.classList.add("hidden");
  } catch (e) {
    inspectStatus.textContent = `× sniff 失败: ${e.message}`;
    inspectStatus.className = "status error";
  } finally {
    sniffBtn.disabled = false;
  }
});

urlInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter") inspectBtn.click();
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
}

// ===== Download =====
downloadBtn.addEventListener("click", async () => {
  if (!currentInspection) return;
  const fid = formatSelect.value;
  const data = currentInspection;
  downloadBtn.disabled = true;
  try {
    const job = await api("/api/download", {
      method: "POST",
      body: JSON.stringify({
        url: data.resolved_url || data.source_url,
        format_id: fid,
        downloader: data.downloader,
        title: data.title,
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
        title,
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
$("settings-btn").addEventListener("click", async () => {
  const cfg = await api("/api/config");
  for (const [k, v] of Object.entries(cfg)) {
    const el = settingsForm.elements.namedItem(k);
    if (el) el.value = v;
  }
  saveDirInput.value = cfg.save_dir || "";
  settingsDialog.showModal();
});

$("settings-cancel").addEventListener("click", () => settingsDialog.close());

settingsForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const fd = new FormData(settingsForm);
  const patch = {};
  for (const [k, v] of fd.entries()) {
    patch[k] = k === "max_concurrent_downloads" ? Number(v) : v;
  }
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

// ===== Init =====
(async function init() {
  const cfg = await api("/api/config").catch(() => null);
  if (cfg) saveDirInput.value = cfg.save_dir || "";
  await refreshJobs();
})();
