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

// ---- 메시지 ----
function showMessage(kind, text, code, requestId) {
  const box = $("message");
  box.className = kind;
  box.replaceChildren(el("span", { text }));
  if (code) {
    const label = requestId ? `오류 코드 ${code} · 요청 번호 ${requestId}` : `오류 코드 ${code}`;
    box.append(el("span", { className: "code", text: label }));
  }
  box.hidden = false;
}
function clearMessage() {
  $("message").hidden = true;
  $("message").replaceChildren();
}
function showClientError(code) {
  showMessage("error", CLIENT_ERRORS[code], code);
}

// ---- API ----
function parseError(xhr) {
  try {
    const body = JSON.parse(xhr.responseText);
    if (body && body.error && body.error.message) return body.error;
  } catch (_) { /* 본문이 JSON 이 아니면 아래 기본 안내 사용 */ }
  return { code: "E-SY-002", message: CLIENT_ERRORS["E-SY-002"], request_id: "" };
}

async function api(method, url) {
  let response;
  try {
    response = await fetch(url, { method });
  } catch (_) {
    return { ok: false, error: { code: "E-SY-002", message: CLIENT_ERRORS["E-SY-002"], request_id: "" } };
  }
  let body = null;
  try { body = await response.json(); } catch (_) { /* 무시 */ }
  if (!response.ok) {
    const err = body && body.error ? body.error : { code: "E-SY-002", message: CLIENT_ERRORS["E-SY-002"], request_id: "" };
    return { ok: false, error: err };
  }
  return { ok: true, body };
}

// ---- 상세 ----
function statItem(label, value) {
  return el("div", {}, [el("dt", { text: label }), el("dd", { text: value })]);
}

function renderDetail(d) {
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
      if ($("detail-name").textContent === d.original_name) $("detail").hidden = true;
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

loadList();
