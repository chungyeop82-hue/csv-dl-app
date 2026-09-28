"use strict";

// STEP 8: 모바일 우선 UI 보강 스크립트.
// app.js 의 업로드/학습/리포트 로직(및 API 호출)은 전혀 건드리지 않고,
// 그 위에 탭 전환·다크모드·온보딩만 추가한다. 모든 대상은 기존 요소의 id 를
// 그대로 사용하며, app.js 가 관리하는 hidden 속성/className 은 그대로 둔 채
// "탭 패널" 이라는 바깥쪽 레이어만 새로 얹는다.

(function () {
  const byId = (id) => document.getElementById(id);

  // ---- 탭 전환 (하단 고정 탭바: 업로드 · 학습 · 리포트) ----
  const tabPanels = Array.from(document.querySelectorAll("[data-tab-panel]"));
  const tabButtons = Array.from(document.querySelectorAll(".tab-btn[data-tab]"));

  function switchTab(name) {
    for (const panel of tabPanels) {
      panel.hidden = panel.getAttribute("data-tab-panel") !== name;
    }
    for (const btn of tabButtons) {
      const active = btn.getAttribute("data-tab") === name;
      btn.classList.toggle("is-active", active);
      if (active) btn.setAttribute("aria-current", "page");
      else btn.removeAttribute("aria-current");
    }
  }

  for (const btn of tabButtons) {
    btn.addEventListener("click", () => switchTab(btn.getAttribute("data-tab")));
  }

  // 데이터 확인 화면에서 "이 데이터로 학습하기"를 누르면 학습 탭으로 이동한다.
  const goTrainBtn = byId("go-train-btn");
  if (goTrainBtn) goTrainBtn.addEventListener("click", () => switchTab("train"));

  // 학습이 끝나 리포트 영역(#report-section)이 열리면(hidden 속성 변화) 리포트 탭으로 안내한다.
  const reportSection = byId("report-section");
  const reportEmpty = byId("report-empty");
  if (reportSection) {
    const syncReportEmptyState = () => {
      const visible = !reportSection.hidden;
      if (reportEmpty) reportEmpty.hidden = visible;
      if (visible) switchTab("report");
    };
    syncReportEmptyState();
    new MutationObserver(syncReportEmptyState).observe(reportSection, {
      attributes: true,
      attributeFilter: ["hidden"],
    });
  }

  // ---- 라이트 / 다크 모드 ----
  const THEME_KEY = "csv-ml-theme";
  const themeToggleBtn = byId("theme-toggle-btn");

  function applyTheme(theme) {
    if (theme === "dark" || theme === "light") {
      document.documentElement.setAttribute("data-theme", theme);
    } else {
      document.documentElement.removeAttribute("data-theme");
    }
    const isDark = theme === "dark" || (theme !== "light" &&
      window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches);
    if (themeToggleBtn) {
      themeToggleBtn.setAttribute("aria-pressed", String(isDark));
      themeToggleBtn.setAttribute("aria-label", isDark ? "라이트 모드로 전환" : "다크 모드로 전환");
      themeToggleBtn.textContent = "";
      const icon = document.createElement("span");
      icon.setAttribute("aria-hidden", "true");
      icon.textContent = isDark ? "☀️" : "🌙";
      themeToggleBtn.append(icon);
    }
  }

  let storedTheme = null;
  try { storedTheme = window.localStorage.getItem(THEME_KEY); } catch (_) { /* 저장소 접근 불가 시 시스템 설정을 따른다 */ }
  applyTheme(storedTheme);

  if (themeToggleBtn) {
    themeToggleBtn.addEventListener("click", () => {
      const current = document.documentElement.getAttribute("data-theme");
      const isDarkNow = current === "dark" || (current !== "light" &&
        window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches);
      const next = isDarkNow ? "light" : "dark";
      applyTheme(next);
      try { window.localStorage.setItem(THEME_KEY, next); } catch (_) { /* 저장 실패해도 화면 전환은 유지 */ }
    });
  }

  // ---- 3단계 온보딩 ----
  const ONBOARDING_KEY = "csv-ml-onboarding-done";
  const onboardingDialog = byId("onboarding-dialog");
  const onboardingSteps = onboardingDialog
    ? Array.from(onboardingDialog.querySelectorAll(".onboarding-step"))
    : [];
  const onboardingProgress = byId("onboarding-progress");
  const prevBtn = byId("onboarding-prev-btn");
  const nextBtn = byId("onboarding-next-btn");
  const skipBtn = byId("onboarding-skip-btn");
  const helpBtn = byId("help-btn");
  let onboardingIndex = 0;

  function renderOnboardingStep() {
    onboardingSteps.forEach((step, i) => { step.hidden = i !== onboardingIndex; });
    if (onboardingProgress) onboardingProgress.textContent = `${onboardingIndex + 1} / ${onboardingSteps.length}`;
    if (prevBtn) prevBtn.hidden = onboardingIndex === 0;
    if (nextBtn) nextBtn.textContent = onboardingIndex === onboardingSteps.length - 1 ? "시작하기" : "다음";
    // aria-labelledby 는 현재 숨겨진(hidden) 단계의 제목을 가리키면 스크린리더가 접근할 수 없으므로,
    // 다이얼로그의 접근성 이름을 현재 보이는 단계의 제목으로 매번 갱신한다.
    const currentStep = onboardingSteps[onboardingIndex];
    const heading = currentStep && currentStep.querySelector(".onboarding-title");
    if (onboardingDialog && heading) onboardingDialog.setAttribute("aria-label", heading.textContent);
  }

  function openOnboarding() {
    if (!onboardingDialog) return;
    onboardingIndex = 0;
    renderOnboardingStep();
    if (typeof onboardingDialog.showModal === "function") onboardingDialog.showModal();
  }

  function finishOnboarding() {
    try { window.localStorage.setItem(ONBOARDING_KEY, "1"); } catch (_) { /* 저장 실패해도 닫기는 진행 */ }
    if (onboardingDialog && onboardingDialog.open) onboardingDialog.close();
  }

  if (nextBtn) {
    nextBtn.addEventListener("click", () => {
      if (onboardingIndex >= onboardingSteps.length - 1) {
        finishOnboarding();
      } else {
        onboardingIndex += 1;
        renderOnboardingStep();
      }
    });
  }
  if (prevBtn) {
    prevBtn.addEventListener("click", () => {
      if (onboardingIndex > 0) {
        onboardingIndex -= 1;
        renderOnboardingStep();
      }
    });
  }
  if (skipBtn) skipBtn.addEventListener("click", finishOnboarding);
  if (onboardingDialog) {
    // Esc 로 닫는 경우(cancel 이벤트)도 "확인함"으로 처리해 다음 방문 때 반복 노출하지 않는다.
    onboardingDialog.addEventListener("cancel", () => {
      try { window.localStorage.setItem(ONBOARDING_KEY, "1"); } catch (_) { /* 무시 */ }
    });
  }
  if (helpBtn) helpBtn.addEventListener("click", openOnboarding);

  let onboardingSeen = null;
  try { onboardingSeen = window.localStorage.getItem(ONBOARDING_KEY); } catch (_) { /* 접근 불가 시 매번 노출 */ }
  if (!onboardingSeen) {
    // 첫 렌더 직후 열어 레이아웃이 자리잡은 뒤 다이얼로그가 뜨도록 한다.
    window.requestAnimationFrame(openOnboarding);
  }
})();
