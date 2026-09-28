"use strict";

// 화면 문구는 모두 textContent 로만 넣는다(HTML 로 해석하지 않음).
// 아래 클라이언트 검사 문구는 서버 오류 카탈로그(app/errors.py)와 같은 문장이며 테스트로 일치를 확인한다.
const MAX_BYTES = 50 * 1024 * 1024;
const CLIENT_ERRORS = {
  "E-UP-001": "확장자가 CSV가 아닌 파일이라 올릴 수 없으니, 엑셀에서 'CSV로 저장'한 파일을 선택해 주세요.",
  "E-UP-002": "파일이 50MB를 넘어 올릴 수 없으니, 50MB 이하로 줄여서 다시 올려 주세요.",
  "E-UP-010": "선택된 파일이 없으니, CSV 파일을 선택한 뒤 다시 올려 주세요.",
  "E-SY-002": "일시적인 문제가 발생했으니, 잠시 후 다시 시도하고 계속되면 요청 번호로 로그를 확인해 주세요.",
};

// 잡 상태 한글 라벨과 상태 배지 CSS 클래스(app/jobs_worker.py 의 _STATUS_LABELS 와 맞춘다).
const STATUS_LABELS = {
  queued: "대기 중",
  running: "학습 중",
  completed: "완료",
  failed: "실패",
  cancelled: "취소됨",
  timeout: "시간 초과",
  interrupted: "중단됨",
};
const TERMINAL_JOB_STATUSES = new Set(["completed", "failed", "cancelled", "timeout", "interrupted"]);
const DEVICE_LABELS = { cpu: "CPU", cuda: "GPU (CUDA)" };

// job.metrics 하위 지표 키 -> 한글 라벨(app/ml/metrics.py 의 classification_metrics/regression_metrics 와 맞춘다).
const METRIC_LABELS = {
  accuracy: "정확도",
  macro_f1: "매크로 F1",
  roc_auc: "ROC-AUC",
  mae: "MAE",
  rmse: "RMSE",
  r2: "R²",
};
const SPLIT_SIZE_LABELS = { train: "학습(train)", validation: "검증(validation)", test: "평가(test)" };

const $ = (id) => document.getElementById(id);

function el(tag, options = {}, children = []) {
  const node = document.createElement(tag);
  if (options.className) node.className = options.className;
  if (options.text !== undefined) node.textContent = options.text;
  for (const [key, value] of Object.entries(options.attrs || {})) node.setAttribute(key, value);
  for (const child of children) node.append(child);
  return node;
}

function formatBytes(n) {
  if (n < 1024) return n + " B";
  if (n < 1024 * 1024) return (n / 1024).toFixed(1) + " KB";
  return (n / 1024 / 1024).toFixed(1) + " MB";
}

const kstFormatter = new Intl.DateTimeFormat("ko-KR", {
  timeZone: "Asia/Seoul",
  year: "numeric", month: "2-digit", day: "2-digit",
  hour: "2-digit", minute: "2-digit", hour12: false,
});
function formatKst(iso) {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "" : kstFormatter.format(d) + " (KST)";
}

function formatSeconds(s) {
  if (s === null || s === undefined || Number.isNaN(s)) return "-";
  const total = Math.max(0, Math.round(s));
  if (total < 60) return `${total}초`;
  return `${Math.floor(total / 60)}분 ${total % 60}초`;
}

// ---- 메시지 ----
// message/train-message/job-result 세 상자 모두 같은 모양(성공·오류 배경, 오류 코드 줄)을 쓴다.
function showMessageIn(boxId, kind, text, code, requestId) {
  const box = $(boxId);
  box.className = kind;
  box.replaceChildren(el("span", { text }));
  if (code) {
    const label = requestId ? `오류 코드 ${code} · 요청 번호 ${requestId}` : `오류 코드 ${code}`;
    box.append(el("span", { className: "code", text: label }));
  }
  box.hidden = false;
}
function clearMessageIn(boxId) {
  $(boxId).hidden = true;
  $(boxId).replaceChildren();
}
function showMessage(kind, text, code, requestId) {
  showMessageIn("message", kind, text, code, requestId);
}
function clearMessage() {
  clearMessageIn("message");
}
function showClientError(code) {
  showMessage("error", CLIENT_ERRORS[code], code);
}
function genericErrorMessage(code) {
  return CLIENT_ERRORS[code] || "요청을 처리하는 중 문제가 발생했습니다.";
}

// ---- API ----
function parseError(xhr) {
  try {
    const body = JSON.parse(xhr.responseText);
    if (body && body.error && body.error.message) return body.error;
  } catch (_) { /* 본문이 JSON 이 아니면 아래 기본 안내 사용 */ }
  return { code: "E-SY-002", message: CLIENT_ERRORS["E-SY-002"], request_id: "" };
}

async function api(method, url, body) {
  let response;
  const init = { method };
  if (body !== undefined) {
    init.headers = { "Content-Type": "application/json" };
    init.body = JSON.stringify(body);
  }
  try {
    response = await fetch(url, init);
  } catch (_) {
    return { ok: false, error: { code: "E-SY-002", message: CLIENT_ERRORS["E-SY-002"], request_id: "" } };
  }
  let payload = null;
  try { payload = await response.json(); } catch (_) { /* 무시 */ }
  if (!response.ok) {
    const err = payload && payload.error ? payload.error : { code: "E-SY-002", message: CLIENT_ERRORS["E-SY-002"], request_id: "" };
    return { ok: false, error: err };
  }
  return { ok: true, body: payload };
}

// ---- 상세 ----
let activeDataset = null;
let currentJobId = null;
let jobEventSource = null;

function statItem(label, value) {
  return el("div", {}, [el("dt", { text: label }), el("dd", { text: value })]);
}

// ---- 학습 결과 지표 표시 ----
// job.metrics 는 validation/test(accuracy 등의 평면 객체), baselines(모델별 validation/test 를 담은 배열),
// split_sizes 같은 중첩 구조라서, 단순 문자열 이어붙이기로는 "[object Object]" 가 나온다.
// 아래 헬퍼들은 그 구조를 그대로 펼쳐서 표(.stats)로 보여준다.
function formatMetricValue(v) {
  if (v === null || v === undefined) return "-";
  if (typeof v === "number") return v.toFixed(4);
  return String(v);
}
function metricsDl(metrics) {
  const entries = Object.entries(metrics || {});
  return el("dl", { className: "stats" }, entries.map(([k, v]) =>
    statItem(METRIC_LABELS[k] || k, formatMetricValue(v))));
}
function metricsBlock(title, metrics) {
  return el("div", { className: "metrics-block" }, [
    el("h4", { text: title }),
    metricsDl(metrics),
  ]);
}
function splitSizesBlock(splitSizes) {
  const entries = Object.entries(splitSizes || {});
  return el("div", { className: "metrics-block" }, [
    el("h4", { text: "Split Sizes" }),
    el("dl", { className: "stats" }, entries.map(([k, v]) =>
      statItem(SPLIT_SIZE_LABELS[k] || k, Number(v).toLocaleString("ko-KR")))),
  ]);
}
function baselinesBlock(baselines) {
  if (!baselines || baselines.length === 0) return null;
  const items = baselines.map((b) => el("div", { className: "baseline-item" }, [
    el("p", { className: "baseline-name", text: b.label || b.kind }),
    metricsBlock("Validation", b.validation),
    metricsBlock("Test", b.test),
  ]));
  return el("div", { className: "metrics-block" }, [el("h4", { text: "Baselines" }), ...items]);
}
function renderCompletedResult(metrics) {
  const box = $("job-result");
  box.className = "ok";
  const children = [el("p", { className: "result-lead", text: "학습을 완료했습니다." })];
  if (metrics) {
    if (metrics.validation) children.push(metricsBlock("Validation", metrics.validation));
    if (metrics.test) children.push(metricsBlock("Test", metrics.test));
    const baselinesEl = baselinesBlock(metrics.baselines);
    if (baselinesEl) children.push(baselinesEl);
    if (metrics.split_sizes) children.push(splitSizesBlock(metrics.split_sizes));
  }
  box.replaceChildren(...children);
  box.hidden = false;
}

function renderDetail(d) {
  activeDataset = d;
  resetTrainingUI();

  $("detail").hidden = false;
  $("detail-name").textContent = d.original_name;

  const banner = $("banner");
  if (d.banner) {
    banner.textContent = d.banner;
    banner.hidden = false;
  } else {
    banner.hidden = true;
    banner.textContent = "";
  }

  const warnings = $("warnings");
  warnings.replaceChildren(...d.warnings.map((w) => el("li", { text: w })));
  warnings.hidden = d.warnings.length === 0;

  $("stats").replaceChildren(
    statItem("전체 행", d.total_rows.toLocaleString("ko-KR")),
    statItem("사용 행", d.used_rows.toLocaleString("ko-KR")),
    statItem("열 수", d.n_columns.toLocaleString("ko-KR")),
    statItem("파일 크기", formatBytes(d.size_bytes)),
    statItem("인코딩", d.encoding_label),
    statItem("올린 시각", formatKst(d.created_at)),
  );

  const colBody = $("columns-table").tBodies[0];
  colBody.replaceChildren(...d.columns.map((c) => el("tr", {}, [
    el("td", { text: c.name }),
    el("td", { text: c.type_label }),
    el("td", { className: "num", text: `${c.missing.toLocaleString("ko-KR")} (${c.missing_pct}%)` }),
    el("td", { className: "num", text: c.unique.toLocaleString("ko-KR") }),
  ])));

  const head = $("preview-table").tHead;
  head.replaceChildren(el("tr", {}, d.preview.columns.map((name) => el("th", { text: name }))));
  $("preview-table").tBodies[0].replaceChildren(...d.preview.rows.map((row) => el("tr", {},
    row.map((cell) => (cell === null
      ? el("td", { className: "null", text: "(결측)" })
      : el("td", { text: cell }))))));
}

// ---- 목록 ----
function renderList(items) {
  $("list-empty").hidden = items.length !== 0;
  $("dataset-list").replaceChildren(...items.map((d) => {
    const title = el("div", { className: "title", text: d.original_name });
    if (d.sampled) title.append(el("span", { className: "tag", text: "샘플링" }));
    const rows = d.sampled
      ? `사용 ${d.used_rows.toLocaleString("ko-KR")} / 전체 ${d.total_rows.toLocaleString("ko-KR")}행`
      : `${d.total_rows.toLocaleString("ko-KR")}행`;
    const meta = el("div", {
      className: "meta",
      text: `${rows} · ${d.n_columns}열 · ${formatBytes(d.size_bytes)} · ${formatKst(d.created_at)}`,
    });
    const viewBtn = el("button", { className: "secondary", text: "보기", attrs: { type: "button" } });
    viewBtn.addEventListener("click", () => openDataset(d.id));
    const delBtn = el("button", { className: "danger", text: "삭제", attrs: { type: "button" } });
    delBtn.addEventListener("click", () => confirmDelete(d));
    return el("li", { className: "dataset-item", attrs: { "data-id": d.id } }, [
      el("div", {}, [title, meta]),
      el("div", { className: "actions" }, [viewBtn, delBtn]),
    ]);
  }));
}

async function loadList() {
  const result = await api("GET", "/datasets");
  if (!result.ok) {
    showMessage("error", result.error.message, result.error.code, result.error.request_id);
    return;
  }
  renderList(result.body.datasets);
}

async function openDataset(id) {
  const result = await api("GET", "/datasets/" + encodeURIComponent(id));
  if (!result.ok) {
    showMessage("error", result.error.message, result.error.code, result.error.request_id);
    await loadList();
    return;
  }
  clearMessage();
  renderDetail(result.body);
  $("detail").scrollIntoView({ behavior: "smooth", block: "start" });
}

// ---- 삭제 ----
function confirmDelete(d) {
  const dialog = $("confirm-dialog");
  $("confirm-title").textContent = `'${d.original_name}' 데이터셋을 삭제할까요? 원본 파일도 함께 지워지며 되돌릴 수 없습니다.`;
  dialog.returnValue = "";
  dialog.addEventListener("close", async function onClose() {
    dialog.removeEventListener("close", onClose);
    if (dialog.returnValue !== "ok") return;
    const result = await api("DELETE", "/datasets/" + encodeURIComponent(d.id));
    if (!result.ok) {
      showMessage("error", result.error.message, result.error.code, result.error.request_id);
    } else {
      showMessage("ok", "데이터셋을 삭제했습니다.");
      if (activeDataset && activeDataset.id === d.id) {
        $("detail").hidden = true;
        resetTrainingUI();
        activeDataset = null;
      }
    }
    await loadList();
  });
  dialog.showModal();
}

// ---- 업로드 ----
const fileInput = $("file-input");
fileInput.addEventListener("change", () => {
  const f = fileInput.files[0];
  $("file-name").textContent = f ? `${f.name} (${formatBytes(f.size)})` : "선택된 파일 없음";
  clearMessage();
});

function setUploading(on) {
  $("upload-btn").disabled = on;
  $("upload-btn").textContent = on ? "올리는 중…" : "올리기";
  $("progress").hidden = !on;
  if (!on) $("progress").value = 0;
}

$("upload-form").addEventListener("submit", (event) => {
  event.preventDefault();
  clearMessage();
  const file = fileInput.files[0];
  if (!file) return showClientError("E-UP-010");
  if (!file.name.toLowerCase().endsWith(".csv")) return showClientError("E-UP-001");
  if (file.size > MAX_BYTES) return showClientError("E-UP-002");

  const data = new FormData();
  data.append("file", file, file.name);
  const xhr = new XMLHttpRequest();
  xhr.open("POST", "/upload");
  xhr.upload.addEventListener("progress", (e) => {
    if (e.lengthComputable) $("progress").value = Math.round((e.loaded / e.total) * 100);
  });
  xhr.addEventListener("load", async () => {
    setUploading(false);
    if (xhr.status === 201) {
      let detail;
      try { detail = JSON.parse(xhr.responseText); } catch (_) { return showClientError("E-SY-002"); }
      showMessage("ok", "업로드를 마쳤습니다.");
      renderDetail(detail);
      fileInput.value = "";
      $("file-name").textContent = "선택된 파일 없음";
      await loadList();
    } else {
      const err = parseError(xhr);
      showMessage("error", err.message, err.code, err.request_id);
    }
  });
  xhr.addEventListener("error", () => {
    setUploading(false);
    showClientError("E-SY-002");
  });
  setUploading(true);
  xhr.send(data);
});

// ---- 학습 ----
// 학습 화면(3. 학습 설정, 4. 학습 진행)을 초기 상태로 되돌린다. 다른 데이터셋을 열거나
// 활성 데이터셋을 지웠을 때, 남아 있던 이전 학습 화면이 그대로 보이지 않도록 호출한다.
function resetTrainingUI() {
  if (jobEventSource) {
    jobEventSource.close();
    jobEventSource = null;
  }
  currentJobId = null;
  $("train").hidden = true;
  clearMessageIn("train-message");
  $("progress-card").hidden = true;
  $("job-status").textContent = "";
  $("job-status").className = "status-badge";
  $("job-device").textContent = "";
  $("job-progress").value = 0;
  $("job-stats").replaceChildren();
  clearMessageIn("job-result");
  $("job-cancel-btn").hidden = false;
  $("job-cancel-btn").disabled = false;
}

function showTrainSection(dataset) {
  resetTrainingUI();
  $("train-dataset-name").textContent = dataset.original_name;

  const targetSelect = $("train-target");
  targetSelect.replaceChildren(...dataset.columns.map((c) =>
    el("option", { text: `${c.name} (${c.type_label})`, attrs: { value: c.name } })));
  const preferred = dataset.columns.find((c) => c.name === "TargetBin");
  if (preferred) targetSelect.value = preferred.name;

  $("train").hidden = false;
  $("train").scrollIntoView({ behavior: "smooth", block: "start" });
}

$("go-train-btn").addEventListener("click", () => {
  if (!activeDataset) return;
  showTrainSection(activeDataset);
});

$("train-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  clearMessageIn("train-message");
  if (!activeDataset) return;

  const task = document.querySelector('input[name="task"]:checked').value;
  const target = $("train-target").value;
  if (!target) {
    showMessageIn("train-message", "error", "타깃 열을 선택해 주세요.");
    return;
  }

  const body = {
    dataset_id: activeDataset.id,
    task,
    target,
    preset: $("train-preset").value,
    max_epochs: parseInt($("train-epochs").value, 10),
    batch_size: parseInt($("train-batch").value, 10),
    learning_rate: parseFloat($("train-lr").value),
    early_stopping: $("train-early-stopping").checked,
  };

  const startBtn = $("train-start-btn");
  startBtn.disabled = true;
  startBtn.textContent = "요청 중…";
  const result = await api("POST", "/jobs", body);
  startBtn.disabled = false;
  startBtn.textContent = "학습 시작";

  if (!result.ok) {
    showMessageIn("train-message", "error", result.error.message, result.error.code, result.error.request_id);
    return;
  }
  startJobTracking(result.body.id);
});

function startJobTracking(jobId) {
  if (jobEventSource) jobEventSource.close();
  currentJobId = jobId;

  $("train").hidden = true;
  $("progress-card").hidden = false;
  $("job-status").textContent = STATUS_LABELS.queued;
  $("job-status").className = "status-badge status-queued";
  $("job-device").textContent = "실행 장치: 학습 완료 후 확인 가능(CPU 전용 실행)";
  $("job-progress").value = 0;
  $("job-stats").replaceChildren();
  clearMessageIn("job-result");
  $("job-cancel-btn").hidden = false;
  $("job-cancel-btn").disabled = false;
  $("progress-card").scrollIntoView({ behavior: "smooth", block: "start" });

  const es = new EventSource(`/jobs/${encodeURIComponent(jobId)}/events`);
  jobEventSource = es;

  es.addEventListener("progress", (e) => {
    let payload;
    try { payload = JSON.parse(e.data); } catch (_) { return; }
    renderJobProgress(payload);
  });

  es.addEventListener("done", (e) => {
    let payload;
    try { payload = JSON.parse(e.data); } catch (_) { payload = null; }
    if (payload) renderJobProgress(payload);
    es.close();
    if (jobEventSource === es) jobEventSource = null;
    finalizeJob(jobId);
  });

  // 서버가 "event: error" 로 보내는 경우(존재하지 않는 잡)와, 연결 자체가 끊어지는 경우가
  // 둘 다 EventSource 의 "error" 이벤트로 들어온다(SSE 사양상 이름이 같으면 겹친다).
  es.addEventListener("error", (e) => {
    let payload = null;
    try { payload = e.data ? JSON.parse(e.data) : null; } catch (_) { /* 무시 */ }
    es.close();
    if (jobEventSource === es) jobEventSource = null;
    $("job-cancel-btn").hidden = true;
    if (payload && payload.code) {
      showMessageIn("job-result", "error", genericErrorMessage(payload.code), payload.code);
    } else {
      showMessageIn("job-result", "error", "진행률 연결이 끊어졌습니다. 목록에서 잡 상태를 다시 확인해 주세요.", "E-SY-002");
    }
  });
}

function renderJobProgress(payload) {
  const status = payload.status;
  $("job-status").textContent = STATUS_LABELS[status] || status;
  $("job-status").className = "status-badge status-" + status;

  const progress = payload.progress;
  if (progress && progress.max_epochs) {
    $("job-progress").value = Math.round((progress.epoch / progress.max_epochs) * 100);
    const stats = [
      statItem("에포크", `${progress.epoch} / ${progress.max_epochs}`),
      statItem("학습 손실", progress.train_loss != null ? progress.train_loss.toFixed(4) : "-"),
      statItem("검증 손실", progress.val_loss != null ? progress.val_loss.toFixed(4) : "-"),
      statItem("예상 남은 시간", formatSeconds(progress.eta_sec)),
    ];
    for (const [key, value] of Object.entries(progress.val_metrics || {})) {
      stats.push(statItem(key, typeof value === "number" ? value.toFixed(4) : String(value)));
    }
    $("job-stats").replaceChildren(...stats);
  }

  if (TERMINAL_JOB_STATUSES.has(status)) {
    $("job-cancel-btn").hidden = true;
  }
}

async function finalizeJob(jobId) {
  const result = await api("GET", "/jobs/" + encodeURIComponent(jobId));
  if (!result.ok) {
    showMessageIn("job-result", "error", result.error.message, result.error.code, result.error.request_id);
    return;
  }
  const job = result.body;
  $("job-device").textContent = job.device
    ? `실행 장치: ${DEVICE_LABELS[job.device] || job.device}`
    : "실행 장치: CPU (CPU 전용 실행)";

  if (job.status === "completed") {
    renderCompletedResult(job.metrics);
  } else if (job.status === "failed") {
    showMessageIn("job-result", "error", job.error_message || "학습이 실패했습니다.", job.error_code, "");
  } else if (job.status === "cancelled") {
    showMessageIn("job-result", "ok", "학습을 취소했습니다.");
  } else if (job.status === "timeout") {
    showMessageIn("job-result", "error", job.error_message || "학습이 시간 초과로 중단되었습니다.", job.error_code, "");
  } else if (job.status === "interrupted") {
    showMessageIn("job-result", "error", job.error_message || "서버 재시작으로 학습이 중단되었습니다.", job.error_code, "");
  }
}

$("job-cancel-btn").addEventListener("click", async () => {
  if (!currentJobId) return;
  $("job-cancel-btn").disabled = true;
  const result = await api("POST", `/jobs/${encodeURIComponent(currentJobId)}/cancel`);
  if (!result.ok) {
    $("job-cancel-btn").disabled = false;
    showMessageIn("job-result", "error", result.error.message, result.error.code, result.error.request_id);
  }
  // 성공하면 SSE 스트림이 곧 cancelled/기타 종료 상태를 내려보낸다.
});

loadList();
