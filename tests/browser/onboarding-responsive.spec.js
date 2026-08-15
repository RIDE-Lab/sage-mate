const { test, expect } = require("@playwright/test");
const http = require("node:http");
const fs = require("node:fs");
const path = require("node:path");

const WEB_ROOT = path.resolve(__dirname, "../../src/sage_faculty_twin/web");
const VIEWPORTS = [
  { name: "phone-320", width: 320, height: 568 },
  { name: "phone-393", width: 393, height: 659 },
  { name: "tablet-768", width: 768, height: 1024 },
  { name: "desktop-1280", width: 1280, height: 800 },
];

let fixtureServer;
let fixtureBaseUrl;

function sendFile(response, filename, contentType) {
  response.writeHead(200, {
    "cache-control": "no-store",
    "content-type": contentType,
  });
  fs.createReadStream(path.join(WEB_ROOT, filename)).pipe(response);
}

function handleFixtureRequest(request, response) {
  const pathname = new URL(request.url, "http://fixture.invalid").pathname;
  if (pathname === "/" || pathname === "/index.html") {
    sendFile(response, "index.html", "text/html; charset=utf-8");
    return;
  }
  if (pathname === "/styles.4219.css" || pathname === "/styles.css") {
    sendFile(response, "styles.css", "text/css; charset=utf-8");
    return;
  }
  if (pathname === "/app.4219.js" || pathname === "/app.js") {
    sendFile(response, "app.js", "text/javascript; charset=utf-8");
    return;
  }
  if (pathname === "/companion.js") {
    sendFile(response, "companion.js", "text/javascript; charset=utf-8");
    return;
  }
  if (pathname === "/companion.css") {
    sendFile(response, "companion.css", "text/css; charset=utf-8");
    return;
  }
  if (pathname === "/chat/workflow-events") {
    response.writeHead(200, {
      "cache-control": "no-store",
      "content-type": "text/event-stream; charset=utf-8",
    });
    response.end([
      `data: ${JSON.stringify({ type: "answer_done", response: { answer: "这是测试回答。" } })}\n\n`,
      `data: ${JSON.stringify({ type: "complete" })}\n\n`,
    ].join(""));
    return;
  }
  if (pathname === "/chat" && request.method === "POST") {
    response.writeHead(200, {
      "cache-control": "no-store",
      "content-type": "application/json; charset=utf-8",
    });
    response.end(JSON.stringify({
      answer: "这是测试回答。",
      conversation_id: "fixture-conversation",
      workflow_trace: [],
      answer_basis: [],
      follow_up_actions: [],
      knowledge_hits: [],
    }));
    return;
  }
  response.writeHead(200, {
    "cache-control": "no-store",
    "content-type": "application/json; charset=utf-8",
  });
  response.end("{}");
}

test.beforeAll(async () => {
  fixtureServer = http.createServer(handleFixtureRequest);
  await new Promise((resolve, reject) => {
    fixtureServer.once("error", reject);
    fixtureServer.listen(0, "127.0.0.1", resolve);
  });
  const address = fixtureServer.address();
  fixtureBaseUrl = `http://127.0.0.1:${address.port}`;
});

test.afterAll(async () => {
  await new Promise((resolve, reject) => {
    fixtureServer.close((error) => (error ? reject(error) : resolve()));
  });
});

async function openOnboarding(page, viewport) {
  await page.setViewportSize({ width: viewport.width, height: viewport.height });
  await page.route("https://fonts.**", (route) => route.abort());
  await page.route("**/chat/workflow-events**", (route) => route.fulfill({
    status: 200,
    contentType: "text/event-stream; charset=utf-8",
    headers: { "cache-control": "no-store" },
    body: [
      `data: ${JSON.stringify({ type: "answer_done", response: { answer: "这是测试回答。" } })}\n\n`,
      `data: ${JSON.stringify({ type: "complete" })}\n\n`,
    ].join(""),
  }));
  await page.route("**/chat?**", (route) => route.fulfill({
    status: 200,
    contentType: "application/json; charset=utf-8",
    headers: { "cache-control": "no-store" },
    body: JSON.stringify({
      answer: "这是测试回答。",
      conversation_id: "fixture-conversation",
      workflow_trace: [],
      answer_basis: [],
      follow_up_actions: [],
      knowledge_hits: [],
    }),
  }));
  await page.goto(fixtureBaseUrl, { waitUntil: "domcontentloaded" });
  await page.evaluate(() => localStorage.clear());
  await page.reload({ waitUntil: "domcontentloaded" });

  if (viewport.width <= 720) {
    await page.getByRole("button", { name: "打开菜单" }).click();
  }
  await page.getByRole("button", { name: "新手引导" }).click();
  await expect(page.locator("#onboarding-card")).toBeVisible();
}

async function expectInsideViewport(page, viewport) {
  const card = page.locator("#onboarding-card");
  const box = await card.boundingBox();
  expect(box).not.toBeNull();
  expect(box.x).toBeGreaterThanOrEqual(0);
  expect(box.x + box.width).toBeLessThanOrEqual(viewport.width + 0.5);
  const pageWidth = await page.evaluate(() => ({
    clientWidth: document.documentElement.clientWidth,
    scrollWidth: document.documentElement.scrollWidth,
  }));
  expect(pageWidth.scrollWidth).toBeLessThanOrEqual(pageWidth.clientWidth);
}

for (const viewport of VIEWPORTS) {
  test(`onboarding layout is safe at ${viewport.name}`, async ({ page }) => {
    await openOnboarding(page, viewport);
    await expectInsideViewport(page, viewport);

    const shellDisplay = await page.locator(".chat-shell").evaluate(
      (element) => getComputedStyle(element).display,
    );
    if (viewport.width <= 920) {
      expect(shellDisplay).toBe("flex");
      await page.getByRole("button", { name: "下一步" }).click();
      await expect(page.locator("#onboarding-step-label")).toHaveText("2 / 2");
      await expectInsideViewport(page, viewport);
    } else {
      expect(shellDisplay).toBe("grid");
      const columns = await page.locator(".chat-shell").evaluate(
        (element) => getComputedStyle(element).gridTemplateColumns,
      );
      expect(columns.trim().split(/\s+/)).toHaveLength(2);
    }
  });
}

for (const viewport of [VIEWPORTS[0], VIEWPORTS[1], VIEWPORTS[3]]) {
  test(`Sage companion is interactive at ${viewport.name}`, async ({ page }) => {
    await openOnboarding(page, viewport);
    await page.getByRole("button", { name: "跳过引导" }).click();

    const companion = page.locator("#sage-companion");
    const toggle = page.locator("#sage-companion-toggle");
    await expect(companion).toBeVisible();
    const toggleBox = await toggle.boundingBox();
    const composerBox = await page.locator("#chat-form").boundingBox();
    expect(toggleBox).not.toBeNull();
    expect(composerBox).not.toBeNull();
    expect(toggleBox.y + toggleBox.height).toBeLessThanOrEqual(composerBox.y + 0.5);
    await toggle.click();
    await expect(page.locator("#sage-companion-panel")).toBeVisible();
    await expect(toggle).toHaveAttribute("aria-expanded", "true");

    const panelBox = await page.locator("#sage-companion-panel").boundingBox();
    expect(panelBox).not.toBeNull();
    expect(panelBox.x).toBeGreaterThanOrEqual(0);
    expect(panelBox.x + panelBox.width).toBeLessThanOrEqual(viewport.width + 0.5);
    expect(panelBox.y).toBeGreaterThanOrEqual(0);
    expect(panelBox.y + panelBox.height).toBeLessThanOrEqual(viewport.height + 0.5);

    if (viewport.width === 320) {
      await expect(page.locator("#sage-companion-scroll-cue")).toBeVisible();
      await page.locator("#sage-companion-scroll-area").evaluate((element) => element.scrollTo(0, element.scrollHeight));
      await expect(page.locator("#sage-companion-scroll-cue")).toBeHidden();
    }

    await page.getByRole("tab", { name: "装扮" }).click();
    await expect(page.locator("#sage-companion-panel-customize")).toBeVisible();
    const settingsPanelBox = await page.locator("#sage-companion-panel").boundingBox();
    expect(settingsPanelBox).not.toBeNull();
    expect(settingsPanelBox.x).toBeGreaterThanOrEqual(0);
    expect(settingsPanelBox.x + settingsPanelBox.width).toBeLessThanOrEqual(viewport.width + 0.5);
    expect(settingsPanelBox.y).toBeGreaterThanOrEqual(0);
    expect(settingsPanelBox.y + settingsPanelBox.height).toBeLessThanOrEqual(viewport.height + 0.5);
    await page.getByRole("tab", { name: "陪伴" }).click();

    await page.getByRole("button", { name: "摸摸它" }).click();
    await expect(companion).toHaveAttribute("data-state", "happy");
    await expect(page.locator("#sage-companion-bond-value")).toHaveText("24%");
    await page.keyboard.press("Escape");
    await expect(page.locator("#sage-companion-panel")).toBeHidden();
    await expect(toggle).toHaveAttribute("aria-expanded", "false");
  });
}

test("shared icons render from valid symbols on phone and desktop", async ({ page }) => {
  for (const viewport of [VIEWPORTS[1], VIEWPORTS[3]]) {
    await openOnboarding(page, viewport);
    await page.getByRole("button", { name: "跳过引导" }).click();

    const iconAudit = await page.locator("svg.ui-icon use").evaluateAll((uses) => uses.map((use) => {
      const icon = use.closest("svg");
      const reference = use.getAttribute("href") || "";
      const bounds = icon.getBoundingClientRect();
      return {
        reference,
        symbolExists: reference.startsWith("#") && Boolean(document.querySelector(reference)),
        hasSize: bounds.width > 0 && bounds.height > 0,
        width: bounds.width,
        height: bounds.height,
      };
    }));

    expect(iconAudit.length).toBeGreaterThan(15);
    expect(iconAudit.every((icon) => icon.symbolExists)).toBe(true);
    const renderedIcons = iconAudit.filter((icon) => icon.hasSize);
    expect(renderedIcons.length).toBeGreaterThan(8);
    expect(renderedIcons.every((icon) => (
      icon.width >= 12 && icon.width <= 24 && icon.height >= 12 && icon.height <= 24
    ))).toBe(true);
    await expect(page.locator('.pill-toggle-label use[href="#icon-brain"]')).toBeVisible();
    await expect(page.locator('.pill-toggle-label use[href="#icon-globe-search"]')).toBeVisible();
    await expect(page.locator('.send-button use[href="#icon-send"]')).toBeVisible();
  }
});

test("Sage companion customizes, grows, persists, hides, and restores", async ({ page }) => {
  const viewport = VIEWPORTS[1];
  await openOnboarding(page, viewport);
  await page.getByRole("button", { name: "跳过引导" }).click();
  await page.evaluate(() => {
    localStorage.removeItem("sageMateCompanion:v3");
    localStorage.setItem("sageMateCompanion:v2", JSON.stringify({ bond: 58 }));
  });
  await page.reload({ waitUntil: "domcontentloaded" });

  const companion = page.locator("#sage-companion");
  const toggle = page.locator("#sage-companion-toggle");
  await expect(page.locator("#sage-companion-bond-value")).toHaveText("58%");
  await expect(page.locator("#sage-companion-stage")).toHaveText("熟悉伙伴");
  await expect(page.locator(".sage-companion-accessory-star")).toBeVisible();
  await toggle.click();
  await page.getByRole("tab", { name: "装扮" }).click();
  await page.locator("#sage-companion-name-input").fill("小火花");
  await page.getByRole("button", { name: "保存" }).click();
  await page.getByLabel("薄荷").check();
  await page.locator("#sage-companion-temperament").selectOption("lively");
  await page.locator("#sage-companion-sound").check();
  await expect(companion).toHaveAttribute("data-appearance", "mint");
  await expect(companion).toHaveAttribute("data-temperament", "lively");
  await expect(page.locator("#sage-companion-name")).toHaveText("小火花");

  await page.getByRole("tab", { name: "陪伴" }).click();
  await page.getByRole("button", { name: "喂颗灵感豆" }).click();
  await expect(page.locator("#sage-companion-bond-value")).toHaveText("65%");
  await expect(page.locator("#sage-companion-stage")).toHaveText("默契搭档");
  await expect(page.locator(".sage-companion-accessory-scarf")).toBeVisible();
  await expect(page.locator("#sage-companion-message")).toContainText("新的成长阶段");

  const firstQuest = await page.locator("#sage-companion-quest-text").textContent();
  await page.getByRole("button", { name: "换一张" }).click();
  await expect(page.locator("#sage-companion-quest-text")).not.toHaveText(firstQuest);
  await page.getByRole("button", { name: "完成啦" }).click();
  await expect(page.locator("#sage-companion-quest-status")).toHaveText("今日完成");
  await expect(page.locator("#sage-companion-bond-value")).toHaveText("70%");

  await page.reload({ waitUntil: "domcontentloaded" });
  await expect(companion).toHaveAttribute("data-appearance", "mint");
  await expect(companion).toHaveAttribute("data-temperament", "lively");
  await expect(page.locator("#sage-companion-name")).toHaveText("小火花");
  await expect(page.locator("#sage-companion-bond-value")).toHaveText("70%");
  await toggle.click();
  await page.getByRole("tab", { name: "装扮" }).click();
  await page.getByRole("button", { name: "隐藏伙伴" }).click();
  await expect(companion).toBeHidden();

  await page.getByRole("button", { name: "打开菜单" }).click();
  await page.getByRole("button", { name: "恢复电子伙伴小火花" }).click();
  await expect(companion).toBeVisible();
  await expect(page.locator("#sage-companion-panel")).toBeVisible();
  await expect(page.getByRole("tab", { name: "装扮" })).toHaveAttribute("aria-selected", "true");
  await expect.poll(() => page.locator("#sage-companion-scroll-area").evaluate((element) => element.scrollTop)).toBe(0);

  const stored = await page.evaluate(() => JSON.parse(localStorage.getItem("sageMateCompanion:v3")));
  expect(stored).toMatchObject({
    version: 3,
    name: "小火花",
    appearance: "mint",
    temperament: "lively",
    soundEnabled: true,
    hidden: false,
    bond: 70,
    dailyQuestCompleted: true,
  });
  expect(stored).not.toHaveProperty("question");
});

test("Sage companion records one learning footprint per completed request", async ({ page }) => {
  const viewport = VIEWPORTS[3];
  await openOnboarding(page, viewport);
  await page.getByRole("button", { name: "跳过引导" }).click();

  await page.locator("#chat-question").fill("用一句话解释测试驱动开发");
  await page.getByRole("button", { name: "发送问题" }).click();
  await page.locator("#sage-companion-toggle").click();
  await expect(page.locator("#sage-companion-answers")).toHaveText("1");
  await expect(page.locator("#sage-companion-streak")).toHaveText("1 天");
  await expect(page.locator("#sage-companion-bond-value")).toHaveText("23%");
  await expect(page.locator("#sage-companion-message")).toContainText("第 1 个问题");
  await expect(page.getByRole("button", { name: "发送问题" })).toBeEnabled();

  await page.keyboard.press("Escape");
  await page.locator("#chat-question").fill("再解释一次测试驱动开发");
  await page.getByRole("button", { name: "发送问题" }).click();
  await page.locator("#sage-companion-toggle").click();
  await expect(page.locator("#sage-companion-answers")).toHaveText("2");
  await expect(page.locator("#sage-companion-streak")).toHaveText("1 天");
  await expect(page.locator("#sage-companion-bond-value")).toHaveText("26%");

  const stored = await page.evaluate(() => JSON.parse(localStorage.getItem("sageMateCompanion:v3")));
  expect(stored).toMatchObject({
    answersCompleted: 2,
    streakDays: 1,
    bond: 26,
  });
  expect(stored).not.toHaveProperty("question");
  expect(stored).not.toHaveProperty("answer");
});

test("Sage companion tabs support keyboard navigation and reduced motion", async ({ page }) => {
  const viewport = VIEWPORTS[1];
  await page.emulateMedia({ reducedMotion: "reduce" });
  await openOnboarding(page, viewport);
  await page.getByRole("button", { name: "跳过引导" }).click();
  await page.locator("#sage-companion-toggle").click();

  const companionTab = page.getByRole("tab", { name: "陪伴" });
  const customizeTab = page.getByRole("tab", { name: "装扮" });
  await expect(companionTab).toHaveAttribute("aria-selected", "true");
  await companionTab.press("End");
  await expect(customizeTab).toBeFocused();
  await expect(customizeTab).toHaveAttribute("aria-selected", "true");
  await expect(page.locator("#sage-companion-panel-customize")).toBeVisible();
  await customizeTab.press("Home");
  await expect(companionTab).toBeFocused();
  await expect(page.locator("#sage-companion-panel-companion")).toBeVisible();

  const reducedMotion = await page.locator("#sage-companion-panel").evaluate((element) => ({
    animationName: getComputedStyle(element).animationName,
    indicatorTransition: getComputedStyle(element.querySelector(".sage-companion-tab-indicator")).transitionDuration,
  }));
  expect(reducedMotion.animationName).toBe("none");
  expect(reducedMotion.indicatorTransition).toBe("0s");
});

test("active chat exposes a usable stop control and sends server cancellation", async ({ page }) => {
  await page.setViewportSize({ width: 393, height: 659 });
  await page.route("https://fonts.**", (route) => route.abort());
  await page.route("**/chat/workflow-events**", (route) => route.fulfill({
    status: 200,
    contentType: "text/event-stream; charset=utf-8",
    body: `data: ${JSON.stringify({ type: "keepalive" })}\n\n`,
  }));
  await page.route("**/chat?**", async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 3000));
    await route.fulfill({
      status: 200,
      contentType: "application/json; charset=utf-8",
      body: JSON.stringify({
        answer: "不应在取消后显示",
        conversation_id: "cancel-test",
        workflow_trace: [],
        answer_basis: [],
        follow_up_actions: [],
        knowledge_hits: [],
      }),
    });
  });
  let cancelRequestUrl = "";
  await page.route("**/chat/cancel?**", async (route) => {
    cancelRequestUrl = route.request().url();
    await route.fulfill({
      status: 200,
      contentType: "application/json; charset=utf-8",
      body: JSON.stringify({ cancelled: true }),
    });
  });
  await page.goto(fixtureBaseUrl, { waitUntil: "domcontentloaded" });
  await page.evaluate(() => {
    localStorage.setItem("sageOnboardingCompleted", "true");
    localStorage.setItem("sageOnboardingDismissed", "true");
  });
  await page.reload({ waitUntil: "domcontentloaded" });

  await page.locator("#chat-question").fill("请开始一个需要较长时间的深度分析");
  await page.getByRole("button", { name: "发送问题" }).click();
  const stopButton = page.getByRole("button", { name: "停止生成" });
  await expect(stopButton).toBeEnabled();
  await expect(stopButton).toHaveAttribute("data-mode", "stop");
  const stopGlyph = await stopButton.locator(".send-button-spinner").evaluate((element) => ({
    opacity: getComputedStyle(element).opacity,
    borderRadius: getComputedStyle(element).borderRadius,
  }));
  expect(stopGlyph.opacity).toBe("1");
  expect(stopGlyph.borderRadius).toBe("3px");

  await stopButton.click();
  await expect(page.getByText("已停止生成。你可以修改问题后重新发送。")).toBeVisible();
  await expect(page.getByRole("button", { name: "发送问题" })).toHaveAttribute("data-mode", "send");
  expect(cancelRequestUrl).toContain("/chat/cancel?request_id=");
});
