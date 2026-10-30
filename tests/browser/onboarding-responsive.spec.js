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

test("durable feedback survives reload without another POST", async ({ page }, testInfo) => {
  let submitted = false;
  let completed = false;
  let posts = 0;
  const feedback = { exchange_id: "browser-feedback", rating: "up", resolved: true,
    needs_human_followup: false, issue_summary: null,
    knowledge_write_backs: [{ document_id: "document", title: "Public source", created: true }] };
  await page.route("https://fonts.**", route => route.abort());
  await page.route("**/chat/feedback**", async route => {
    if (route.request().method() === "POST") {
      const payload = route.request().postDataJSON();
      expect(payload.request_id).toBeTruthy();
      posts += 1;
      submitted = true;
      await route.fulfill({ status: 503, json: { detail: {
        code: "feedback_knowledge_pending", feedback_saved: true, operation_id: "browser-operation" } } });
    } else if (!submitted) {
      await route.fulfill({ status: 404, json: { detail: "not found" } });
    } else {
      await route.fulfill({ json: { operation_id: "browser-operation",
        status: completed ? "completed" : "pending",
        feedback: { ...feedback, knowledge_write_backs: completed ? feedback.knowledge_write_backs : [] } } });
    }
  });
  const showSavedAnswer = async () => {
    await page.waitForFunction(() => typeof renderAssistantMessage === "function");
    await page.evaluate(() => {
      const article = appendMessage("assistant", "Twin", "公开研究资料回答");
      renderAssistantMessage(article, "公开研究资料回答", [], [], [], null, false, "browser-feedback", [], false);
    });
  };
  await page.goto(fixtureBaseUrl, { waitUntil: "domcontentloaded" });
  await showSavedAnswer();
  const box = page.locator('[data-feedback-exchange="browser-feedback"]');
  const up = box.locator('[data-feedback-up]');
  await expect(up).toBeEnabled();
  await up.click();
  await expect(box.locator('.message-feedback-status')).toContainText("后台处理中");
  await expect(up).toBeDisabled();
  expect(posts).toBe(1);
  await page.reload({ waitUntil: "domcontentloaded" });
  // Reattach the saved answer as history rendering does; all client attempt
  // state was destroyed by reload. The API fixture retains committed work.
  await showSavedAnswer();
  await expect(box.locator('.message-feedback-status')).toContainText("后台处理中");
  completed = true;
  await expect(box.locator('.message-feedback-status')).toContainText("已沉淀 1 条", { timeout: 7000 });
  await expect(up).toBeDisabled();
  expect(posts).toBe(1);
  await page.screenshot({ path: testInfo.outputPath("feedback-recovered.png"), fullPage: true });
});

let fixtureServer;
let fixtureBaseUrl;
let deliveryScenario = null;

for (const lostHttp of [false, true]) {
  test(`durable delivery reconnects after ${lostHttp ? "lost HTTP" : "HTTP success"}`, async ({ page }, testInfo) => {
    const answer = { answer: "原请求已恢复，公开研究证据完整保留。", conversation_id: "recovered-conversation",
      exchange_id: "recovered-turn", workflow_trace: [], answer_basis: [], follow_up_actions: [], knowledge_hits: [] };
    const scenario = { connections: [], posts: 0, answer };
    deliveryScenario = scenario;
    try {
      await page.route("https://fonts.**", route => route.fulfill({ contentType: "text/css", body: "" }));
      await page.route("**/chat/feedback**", route => route.fulfill({ status: 404, json: { detail: "not found" } }));
      await page.route("**/chat?**", async route => {
        scenario.posts += 1;
        if (lostHttp) await route.abort("connectionreset");
        else await route.fulfill({ json: answer });
      });
      await page.goto(fixtureBaseUrl, { waitUntil: "domcontentloaded" });
      await page.evaluate(() => {
        localStorage.setItem("sageOnboardingCompleted", "true");
        localStorage.setItem("sageOnboardingDismissed", "true");
      });
      await page.reload({ waitUntil: "domcontentloaded" });
      await page.locator("#chat-question").fill("请根据公开 Wiki 介绍研究工作");
      await page.getByRole("button", { name: "发送问题" }).click();
      await expect(page.locator("#chat-stream .message-body").filter({ hasText: answer.answer })).toBeVisible();
      await expect.poll(() => scenario.connections.length).toBeGreaterThanOrEqual(2);
      expect(scenario.connections[1]).toBe("1");
      await expect.poll(() => page.evaluate(() => activeWorkflowStream === null)).toBe(true);
      expect(await page.evaluate(() => activeWorkflowSteps.some(step => step.key === "memory_usefulness_score"))).toBe(true);
      expect(scenario.posts).toBe(1);
      await expect.poll(() => page.evaluate(() => document.fonts.status)).toBe("loaded");
      await page.locator("#chat-stream").screenshot({ path: testInfo.outputPath("delivery-recovered.png"),
        animations: "disabled", timeout: 10000 });
    } finally {
      deliveryScenario = null;
    }
  });
}

function sendFile(response, filename, contentType) {
  response.writeHead(200, {
    "cache-control": "no-store",
    "content-type": contentType,
  });
  fs.createReadStream(path.join(WEB_ROOT, filename)).pipe(response);
}

function handleFixtureRequest(request, response) {
  const pathname = new URL(request.url, "http://fixture.invalid").pathname;
  if (pathname === "/chat/workflow-events" && deliveryScenario) {
    const scenario = deliveryScenario;
    scenario.connections.push(request.headers["last-event-id"] || null);
    response.writeHead(200, { "content-type": "text/event-stream", "cache-control": "no-store" });
    if (scenario.connections.length === 1) {
      // Actual EventSource reconnect, not an injected/mock browser API.
      response.end('retry: 100\nid: 1\ndata: {"type":"keepalive"}\n\n');
    } else {
      setTimeout(() => {
        response.write(`id: 2\ndata: ${JSON.stringify({ type: "answer_done", response: scenario.answer })}\n\n`);
        response.write(`id: 3\ndata: ${JSON.stringify({ type: "trace-step", step: {
          key: "memory_usefulness_score", title: "后台核验", summary: "恢复后的后台任务已完成", status: "completed" } })}\n\n`);
        response.end('id: 4\ndata: {"type":"complete"}\n\n');
      }, 200);
    }
    return;
  }
  if (pathname === "/" || pathname === "/index.html") {
    sendFile(response, "index.html", "text/html; charset=utf-8");
    return;
  }
  if (pathname === "/styles.4222.css" || pathname === "/styles.css") {
    sendFile(response, "styles.css", "text/css; charset=utf-8");
    return;
  }
  if (pathname === "/app.4222.js" || pathname === "/app.js") {
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
  if (pathname === "/static/minimal.1009.css") {
    sendFile(response, "minimal.1009.css", "text/css; charset=utf-8");
    return;
  }
  if (pathname === "/health" || pathname === "/admin/health") {
    response.writeHead(200, {
      "cache-control": "no-store",
      "content-type": "application/json; charset=utf-8",
    });
    response.end(JSON.stringify({
      status: "ok",
      app_version: "4.6.30",
      model_name: "vllm-ascend/DeepSeek-V4-Flash-w8a8-mtp",
      engine_image: "vllm-ascend-hust:graph-runtime",
      npu_devices: "0,1,2,3,4,5,6,7",
      npu_active_count: "8",
      npu_utilization: "42%",
      npu_memory_usage: "378 / 488 GiB",
      npu_utilization_by_device: "0:40% · 1:43% · 2:41% · 3:44% · 4:40% · 5:43% · 6:41% · 7:44%",
      registered_user_accounts: "12",
      conversation_memory_records: "850",
      llm_status: "healthy",
      llm_request_count: "20",
      llm_avg_latency_ms: "1250",
      llm_app_cache_hit_rate: "0.4",
      llm_request_throughput_rps: "1.5",
    }));
    return;
  }
  if (pathname === "/wiki/questions") {
    response.writeHead(200, { "content-type": "application/json" });
    response.end(JSON.stringify({ questions: ["系统研究概览", "推理入门", "科研入门", "使用边界", "开放研究问题", "论文写作", "培养写作能力", "综述写作", "从选题到高质量论文", "系统论文修改", "推理状态管理开放研究问题", "RAG 系统", "端到端验收", "KV Cache"].map((label, i) => ({ label, source_name: `wiki:${i}`, url: `https://example.test/${i}` })) }));
    return;
  }
  if (pathname === "/auth/user/session") {
    response.writeHead(200, {
      "cache-control": "no-store",
      "content-type": "application/json; charset=utf-8",
    });
    response.end(JSON.stringify({ is_authenticated: false, mode: "public" }));
    return;
  }
  if (pathname === "/chat/workflow-events") {
    response.writeHead(200, {
      "cache-control": "no-store",
      "content-type": "text/event-stream; charset=utf-8",
    });
    response.end([
      `data: ${JSON.stringify({ type: "answer_done", response: {
        answer: "这是测试回答，包含可核验的公开资料。",
        answer_basis: [{
          basis_label: "公开资料",
          source_label: "研究主页",
          title: "研究方向与公开项目",
          detail: "依据公开主页与项目文档整理。",
        }],
        knowledge_hits: [{ title: "研究方向与公开项目", source_name: "研究主页" }],
      } })}\n\n`,
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
      answer: "这是测试回答，包含可核验的公开资料。",
      conversation_id: "fixture-conversation",
      workflow_trace: [],
      answer_basis: [{
        basis_label: "公开资料",
        source_label: "研究主页",
        title: "研究方向与公开项目",
        detail: "依据公开主页与项目文档整理。",
      }],
      follow_up_actions: [],
      knowledge_hits: [{ title: "研究方向与公开项目", source_name: "研究主页" }],
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
      `data: ${JSON.stringify({ type: "answer_done", response: {
        answer: "这是测试回答，包含可核验的公开资料。",
        answer_basis: [{
          basis_label: "公开资料",
          source_label: "研究主页",
          title: "研究方向与公开项目",
          detail: "依据公开主页与项目文档整理。",
        }],
        knowledge_hits: [{ title: "研究方向与公开项目", source_name: "研究主页" }],
      } })}\n\n`,
      `data: ${JSON.stringify({ type: "complete" })}\n\n`,
    ].join(""),
  }));
  await page.route("**/chat?**", (route) => route.fulfill({
    status: 200,
    contentType: "application/json; charset=utf-8",
    headers: { "cache-control": "no-store" },
    body: JSON.stringify({
      answer: "这是测试回答，包含可核验的公开资料。",
      conversation_id: "fixture-conversation",
      workflow_trace: [],
      answer_basis: [{
        basis_label: "公开资料",
        source_label: "研究主页",
        title: "研究方向与公开项目",
        detail: "依据公开主页与项目文档整理。",
      }],
      follow_up_actions: [],
      knowledge_hits: [{ title: "研究方向与公开项目", source_name: "研究主页" }],
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
    expect(shellDisplay).toBe("flex");
    if (viewport.width <= 920) {
      await page.getByRole("button", { name: "下一步" }).click();
      await expect(page.locator("#onboarding-step-label")).toHaveText("2 / 2");
      await expectInsideViewport(page, viewport);
    } else {
      const cardBox = await page.locator("#onboarding-card").boundingBox();
      const chatShellBox = await page.locator(".chat-shell").boundingBox();
      expect(cardBox.width).toBeLessThanOrEqual(640.5);
      expect(Math.abs(
        (cardBox.x + cardBox.width / 2) - (chatShellBox.x + chatShellBox.width / 2),
      )).toBeLessThanOrEqual(1);
    }
  });
}

for (const viewport of [VIEWPORTS[1], VIEWPORTS[3]]) {
  test(`submitting from onboarding reveals the conversation at ${viewport.name}`, async ({ page }) => {
    await openOnboarding(page, viewport);

    const question = "如果我对 LLM 推理优化感兴趣，建议先了解哪些关键词或系统？";
    await page.locator("#chat-question").fill(question);
    await page.getByRole("button", { name: "发送问题" }).click();

    await expect(page.locator("body")).not.toHaveClass(/onboarding-active/);
    await expect(page.locator("#onboarding-card")).toBeHidden();
    await expect(page.locator(".chat-stream")).toBeVisible();
    await expect(page.locator(".message-user")).toContainText(question);
    await expect(page.locator(".message-ready")).toBeVisible();
    await expect(page.locator(".message-ready .message-reply-block > .message-body")).toContainText("这是测试回答");

    const overlap = await page.evaluate(() => {
      const stream = document.querySelector(".message-ready")?.getBoundingClientRect();
      // The composer shell intentionally has a transparent fade above its
      // solid control. Content must not intersect the actual control surface.
      const composer = document.querySelector(".composer-row")?.getBoundingClientRect();
      if (!stream || !composer) return null;
      return Math.max(0, Math.min(stream.bottom, composer.bottom) - Math.max(stream.top, composer.top));
    });
    expect(overlap).toBe(0);
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
    await openComposerTools(page);

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

test("system status remains readable in both themes and responsive viewports", async ({ page }) => {
  await mockAdminSession(page);
  for (const theme of ["dark", "light"]) {
    for (const viewport of [{ width: 1280, height: 800 }, { width: 390, height: 844 }]) {
      await page.setViewportSize(viewport);
      await page.goto(fixtureBaseUrl, { waitUntil: "domcontentloaded" });
      await page.evaluate((selectedTheme) => {
        localStorage.setItem("sageMateTheme", selectedTheme);
        localStorage.setItem("sageOnboardingCompleted", "true");
        localStorage.setItem("sageOnboardingDismissed", "true");
      }, theme);
      await page.reload({ waitUntil: "domcontentloaded" });
      if (viewport.width <= 720) {
        await page.getByRole("button", { name: "打开菜单" }).click();
      }
      await page.getByRole("button", { name: "系统状态" }).click();
      await expect(page.locator("#view-model-name .status-value")).toContainText("DeepSeek-V4");

      const audit = await page.evaluate(() => {
        const parseColor = (value) => {
          const values = value.match(/[\d.]+/g)?.map(Number) || [0, 0, 0, 0];
          return { r: values[0], g: values[1], b: values[2], a: values[3] ?? 1 };
        };
        const blend = (front, back) => ({
          r: front.r * front.a + back.r * (1 - front.a),
          g: front.g * front.a + back.g * (1 - front.a),
          b: front.b * front.a + back.b * (1 - front.a),
          a: 1,
        });
        const background = (element) => {
          const layers = [];
          for (let node = element; node; node = node.parentElement) {
            layers.push(parseColor(getComputedStyle(node).backgroundColor));
          }
          return layers.reverse().reduce(
            (result, layer) => (layer.a > 0 ? blend(layer, result) : result),
            { r: 255, g: 255, b: 255, a: 1 },
          );
        };
        const luminance = ({ r, g, b }) => {
          const channels = [r, g, b].map((value) => {
            const channel = value / 255;
            return channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4;
          });
          return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2];
        };
        const contrast = (foreground, backdrop) => {
          const first = luminance(foreground);
          const second = luminance(backdrop);
          return (Math.max(first, second) + 0.05) / (Math.min(first, second) + 0.05);
        };
        const sample = (selector) => {
          const element = document.querySelector(selector);
          const style = getComputedStyle(element);
          const backdrop = background(element);
          return {
            selector,
            color: style.color,
            background: style.backgroundColor,
            border: style.borderTopColor,
            textContrast: contrast(parseColor(style.color), backdrop),
          };
        };
        const card = document.querySelector("#view-model-name");
        const cardStyle = getComputedStyle(card);
        const cardBackdrop = background(card);
        const pageWidth = {
          clientWidth: document.documentElement.clientWidth,
          scrollWidth: document.documentElement.scrollWidth,
        };
        return {
          theme: document.documentElement.dataset.theme,
          state: document.querySelector("#status-view").dataset.state,
          samples: [
            sample(".chat-view-title"),
            sample(".status-section-title"),
            sample("#view-model-name .status-label"),
            sample("#view-model-name .status-value"),
            sample(".app-stack-chip"),
            sample(".app-version-badge"),
          ],
          cardBorder: cardStyle.borderTopColor,
          cardBackground: cardStyle.backgroundColor,
          cardBorderContrast: contrast(parseColor(cardStyle.borderTopColor), cardBackdrop),
          footerBorderContrasts: [".app-stack-chip", ".app-version-badge"].map((selector) => {
            const element = document.querySelector(selector);
            return contrast(parseColor(getComputedStyle(element).borderTopColor), background(element));
          }),
          pageWidth,
        };
      });

      expect(audit.theme).toBe(theme);
      expect(audit.state).toBe("ready");
      expect(audit.samples.every((sample) => sample.textContrast >= 4.5)).toBe(true);
      expect(audit.cardBorderContrast).toBeGreaterThanOrEqual(3);
      expect(audit.footerBorderContrasts.every((value) => value >= 3)).toBe(true);
      expect(audit.pageWidth.scrollWidth).toBeLessThanOrEqual(audit.pageWidth.clientWidth);
    }
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
  await expect(page.locator("#sage-companion")).toHaveClass(/is-conversation-docked/);
  const dockedToggleBox = await page.locator("#sage-companion-toggle").boundingBox();
  const contentColumnBox = await page.locator(".composer-inner").boundingBox();
  expect(dockedToggleBox).not.toBeNull();
  expect(contentColumnBox).not.toBeNull();
  expect(dockedToggleBox.x).toBeGreaterThanOrEqual(contentColumnBox.x + contentColumnBox.width + 12);
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

test("mobile conversation hides the floating companion without losing its sidebar entry", async ({ page }) => {
  const viewport = VIEWPORTS[1];
  await openOnboarding(page, viewport);
  await page.getByRole("button", { name: "跳过引导" }).click();
  await page.locator("#chat-question").fill("介绍一下主要研究方向");
  await page.getByRole("button", { name: "发送问题" }).click();
  await expect(page.locator(".message-ready")).toBeVisible();
  await expect(page.locator("#sage-companion")).toHaveClass(/is-conversation-docked/);
  await expect(page.locator("#sage-companion")).toBeHidden();

  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
  expect(overflow).toBeLessThanOrEqual(0);
  await page.getByRole("button", { name: "打开菜单" }).click();
  await expect(page.getByRole("button", { name: /电子伙伴/ })).toBeVisible();
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
  await expect.poll(
    () => stopButton.locator(".send-button-spinner").evaluate(
      (element) => getComputedStyle(element).opacity,
    ),
  ).toBe("1");
  await expect.poll(
    () => stopButton.locator(".send-button-spinner").evaluate(
      (element) => getComputedStyle(element).borderRadius,
    ),
  ).toBe("3px");

  await stopButton.click();
  await expect(page.getByText("已停止生成。你可以修改问题后重新发送。")).toBeVisible();
  await expect(page.getByRole("button", { name: "发送问题" })).toHaveAttribute("data-mode", "send");
  expect(cancelRequestUrl).toContain("/chat/cancel?request_id=");
});

async function openThemeFixture(page, theme, viewport) {
  await mockAdminSession(page);
  await page.setViewportSize(viewport);
  await page.route("https://fonts.**", (route) => route.fulfill({
    status: 200,
    contentType: "text/css; charset=utf-8",
    body: "",
  }));
  await page.goto(fixtureBaseUrl, { waitUntil: "domcontentloaded" });
  await page.evaluate((selectedTheme) => {
    localStorage.setItem("sageMateTheme", selectedTheme);
    localStorage.setItem("sageOnboardingCompleted", "true");
    localStorage.setItem("sageOnboardingDismissed", "true");
  }, theme);
  await page.reload({ waitUntil: "domcontentloaded" });
  await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
}

async function mockAdminSession(page) {
  await page.route("**/auth/session", (route) => route.fulfill({
    status: 200,
    contentType: "application/json; charset=utf-8",
    body: JSON.stringify({ is_admin: true, username: "fixture-admin" }),
  }));
}

async function openComposerTools(page) {
  const disclosure = page.locator(".composer-tools-disclosure");
  if (!(await disclosure.evaluate((element) => element.open))) {
    await disclosure.locator("summary").click();
  }
}

for (const viewport of [
  { name: "desktop", width: 1280, height: 800 },
  { name: "mobile", width: 390, height: 844 },
]) {
  test(`returning visitor ${viewport.name} landing does not wait for remote status initialization`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await page.addInitScript(() => {
      localStorage.setItem("sageOnboardingCompleted", "true");
      localStorage.setItem("sageOnboardingDismissed", "true");
    });

    let releaseVersions;
    const versionsReleased = new Promise((resolve) => {
      releaseVersions = resolve;
    });
    await page.route("**/stack/versions", async (route) => {
      await versionsReleased;
      await route.fulfill({
        status: 200,
        contentType: "application/json; charset=utf-8",
        body: JSON.stringify({ app_version: "4.6.30" }),
      });
    });

    await page.goto(fixtureBaseUrl, { waitUntil: "domcontentloaded" });
    await expect(page.locator("#welcome-greeting")).toBeVisible({ timeout: 750 });
    await expect(page.locator("#seed-chips")).toBeVisible({ timeout: 750 });
    await expect(page.locator("#seed-chips-list .seed-chip")).toHaveCount(3);

    const chips = page.locator("#seed-chips-list .seed-chip");
    const originalText = await chips.allTextContents();
    const originalFirst = await chips.first().elementHandle();
    await chips.first().focus();
    releaseVersions();
    await expect(page.locator("#app-version-badge")).toHaveText("v4.6.30");
    await page.evaluate(async () => { await refreshUserSession(); await renderSeedChips(visitorProfileInput.value); });
    expect(await chips.allTextContents()).toEqual(originalText);
    expect(await originalFirst.evaluate(node => node.isConnected && node === document.activeElement)).toBe(true);
    await page.reload({waitUntil: "domcontentloaded"});
    await expect(chips).toHaveCount(3);
    expect(await chips.allTextContents()).toEqual(originalText);
  });
}

for (const viewport of [
  { name: "phone-390", width: 390, height: 844 },
  { name: "phone-430", width: 430, height: 932 },
]) {
  for (const theme of ["light", "dark"]) {
    test(`mobile sidebar is a closed-by-default overlay at ${viewport.name} ${theme}`, async ({ page }) => {
      await page.setViewportSize(viewport);
      await page.route("https://fonts.**", (route) => route.abort());
      await page.addInitScript((selectedTheme) => {
        localStorage.setItem("sageMateTheme", selectedTheme);
        localStorage.setItem("sageOnboardingCompleted", "true");
        localStorage.setItem("sageOnboardingDismissed", "true");
      }, theme);
      await page.goto(fixtureBaseUrl, { waitUntil: "domcontentloaded" });

      const sidebar = page.locator("#primary-sidebar");
      const menu = page.getByRole("button", { name: "打开菜单" });
      const appShell = page.locator(".app-shell");
      await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
      await expect(sidebar).toBeHidden();
      await expect(sidebar).toHaveAttribute("aria-hidden", "true");
      await expect(sidebar).toHaveAttribute("inert", "");
      await expect(menu).toHaveAttribute("aria-expanded", "false");

      const assertMainFits = async ({ controls = true } = {}) => {
        if (controls) {
          const disclosure = page.locator(".composer-tools-disclosure");
          if (!(await disclosure.evaluate((element) => element.open))) {
            await disclosure.locator("summary").click();
          }
        }
        const shellBox = await appShell.boundingBox();
        expect(shellBox).not.toBeNull();
        expect(shellBox.x).toBeGreaterThanOrEqual(0);
        expect(shellBox.x + shellBox.width).toBeLessThanOrEqual(viewport.width + 0.5);
        const layout = await page.evaluate(() => ({
          clientWidth: document.documentElement.clientWidth,
          scrollWidth: document.documentElement.scrollWidth,
        }));
        expect(layout.scrollWidth).toBeLessThanOrEqual(layout.clientWidth);
        if (!controls) return;
        for (const selector of [
          "#composer-upload-button",
          "#voice-input-button",
          '#chat-form button[type="submit"]',
          "#lucky-question-button",
          ".token-usage-toggle",
          "#mobile-workflow-trigger",
        ]) {
          const box = await page.locator(selector).boundingBox();
          expect(box, selector).not.toBeNull();
          expect(box.x, selector).toBeGreaterThanOrEqual(0);
          expect(box.x + box.width, selector).toBeLessThanOrEqual(viewport.width + 0.5);
        }
      };

      await assertMainFits();
      await page.evaluate(() => applyAppProfilePresentation({ app_profile: "code_assistant" }));
      await expect(sidebar).toBeHidden();
      await expect(menu).toHaveAttribute("aria-expanded", "false");
      // Code Assistant intentionally replaces Faculty Twin's lucky/token tools;
      // only the shared shell geometry is invariant across profiles.
      await assertMainFits({ controls: false });

      await page.reload({ waitUntil: "domcontentloaded" });
      await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
      await expect(sidebar).toBeHidden();
      await assertMainFits();

      const closedShell = await appShell.boundingBox();
      await menu.click();
      await expect(page.getByRole("button", { name: "关闭菜单" })).toHaveAttribute("aria-expanded", "true");
      await expect(sidebar).toBeVisible();
      await expect(sidebar).toHaveAttribute("aria-hidden", "false");
      await expect(sidebar).not.toHaveAttribute("inert", "");
      expect(await appShell.boundingBox()).toEqual(closedShell);

      await page.keyboard.press("Escape");
      await expect(sidebar).toBeHidden();
      await expect(menu).toBeFocused();
      await expect(menu).toHaveAttribute("aria-expanded", "false");
      await assertMainFits();
    });
  }
}

test("desktop sidebar retains its collapsed rail and expanded layout", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.route("https://fonts.**", (route) => route.abort());
  await page.goto(fixtureBaseUrl, { waitUntil: "domcontentloaded" });
  const sidebar = page.locator("#primary-sidebar");
  const shell = page.locator(".app-shell");
  await expect(sidebar).toBeVisible();
  await expect(page.locator("#mobile-sidebar-toggle")).toBeHidden();
  expect(Math.round((await sidebar.boundingBox()).width)).toBe(56);
  expect(Math.round((await shell.boundingBox()).x)).toBe(56);
  await page.getByRole("button", { name: "切换侧边栏" }).click();
  await expect(sidebar).toHaveCSS("width", "280px");
  // Preserve the existing desktop layout: the 280px rail overlaps the content
  // edge by 20px while the application shell settles at its established 260px.
  await expect.poll(async () => Math.round((await shell.boundingBox()).x)).toBe(260);
  expect(Math.round((await sidebar.boundingBox()).width)).toBe(280);
});

async function openRailView(page, buttonName, viewport) {
  if (viewport.width <= 720) {
    const menu = page.getByRole("button", { name: "打开菜单" });
    if (await menu.isVisible()) {
      await menu.click();
    }
  }
  const target = buttonName === "账号设置"
    ? page.locator("#sidebar-user-icon")
    : page.getByRole("button", { name: buttonName });
  await target.click();
}

async function auditThemeSelectors(page, selectors) {
  return page.evaluate((requestedSelectors) => {
    const parseColor = (value) => {
      const serialized = String(value || "").trim();
      const values = serialized.match(/[\d.]+/g)?.map(Number) || [0, 0, 0, 0];
      if (serialized.startsWith("color(srgb")) {
        return {
          r: values[0] * 255,
          g: values[1] * 255,
          b: values[2] * 255,
          a: values[3] ?? 1,
        };
      }
      return { r: values[0], g: values[1], b: values[2], a: values[3] ?? 1 };
    };
    const blend = (front, back) => ({
      r: front.r * front.a + back.r * (1 - front.a),
      g: front.g * front.a + back.g * (1 - front.a),
      b: front.b * front.a + back.b * (1 - front.a),
      a: 1,
    });
    const backdrop = (element, includeSelf = true) => {
      const layers = [];
      for (let node = includeSelf ? element : element.parentElement; node; node = node.parentElement) {
        const style = getComputedStyle(node);
        layers.push({ color: parseColor(style.backgroundColor), opacity: Number(style.opacity || 1) });
      }
      return layers.reverse().reduce((result, layer) => {
        const color = { ...layer.color, a: layer.color.a * layer.opacity };
        return color.a > 0 ? blend(color, result) : result;
      }, { r: 255, g: 255, b: 255, a: 1 });
    };
    const luminance = ({ r, g, b }) => {
      const channels = [r, g, b].map((value) => {
        const channel = value / 255;
        return channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4;
      });
      return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2];
    };
    const contrast = (firstColor, secondColor) => {
      const first = luminance(firstColor);
      const second = luminance(secondColor);
      return (Math.max(first, second) + 0.05) / (Math.min(first, second) + 0.05);
    };
    return requestedSelectors.map((selector) => {
      const element = document.querySelector(selector);
      if (!element) {
        return { selector, missing: true };
      }
      const style = getComputedStyle(element);
      const bounds = element.getBoundingClientRect();
      const hidden = style.display === "none" || style.visibility === "hidden" || bounds.width === 0 || bounds.height === 0;
      const ownBackdrop = backdrop(element);
      const outsideBackdrop = backdrop(element, false);
      const foreground = blend(parseColor(style.color), ownBackdrop);
      const border = blend(parseColor(style.borderTopColor), outsideBackdrop);
      return {
        selector,
        hidden,
        color: style.color,
        background: style.backgroundColor,
        border: style.borderTopColor,
        textContrast: contrast(foreground, ownBackdrop),
        borderContrast: style.borderTopStyle === "none" || Number.parseFloat(style.borderTopWidth) === 0
          ? null
          : contrast(border, outsideBackdrop),
      };
    });
  }, selectors);
}

function expectThemeAuditPasses(audit, { requireBorders = [] } = {}) {
  expect(audit.filter((sample) => sample.missing || sample.hidden)).toEqual([]);
  for (const sample of audit) {
    expect(sample.textContrast, `${sample.selector} text contrast ${JSON.stringify(sample)}`).toBeGreaterThanOrEqual(4.5);
    if (requireBorders.includes(sample.selector)) {
      expect(sample.borderContrast, `${sample.selector} border contrast`).toBeGreaterThanOrEqual(3);
    }
  }
}

test("semantic theme contract covers chat, Support, status, account, settings, and feedback states", async ({ page }) => {
  const viewports = [{ width: 1280, height: 800 }, { width: 390, height: 844 }];
  for (const theme of ["dark", "light"]) {
    for (const viewport of viewports) {
      await openThemeFixture(page, theme, viewport);
      await openComposerTools(page);
      if (viewport.width <= 720) {
        const composerBackground = await page.locator(".composer-shell").evaluate(
          (element) => getComputedStyle(element).backgroundImage,
        );
        expect(composerBackground).not.toContain("247, 247, 248");
        expect(composerBackground).not.toContain("7, 18, 37");
      }
      expectThemeAuditPasses(await auditThemeSelectors(page, [
        ".greeting-text",
        ".greeting-subtitle",
        ".seed-chips-label",
        ".seed-chip",
        "#chat-question",
        ".composer-pill-toggle .pill-toggle-label",
        "#lucky-question-button",
        ".token-usage-toggle",
      ]), { requireBorders: [".seed-chip", "#lucky-question-button", ".token-usage-toggle"] });

      await page.locator("#chat-question").fill("请介绍研究方向并给出依据");
      await page.getByRole("button", { name: "发送问题" }).click();
      await expect(page.locator(".message-ready")).toBeVisible();
      const basisToggle = page.locator(".message-ready .message-section-support .message-section-toggle");
      await expect(basisToggle).toHaveAttribute("aria-expanded", "false");
      await expect(page.locator(".message-ready .message-section-support .message-section-content")).toBeHidden();
      await basisToggle.click();
      await expect(basisToggle).toHaveAttribute("aria-expanded", "true");
      expectThemeAuditPasses(await auditThemeSelectors(page, [
        ".message-ready .message-body",
        ".message-section-title",
        ".message-basis-copy",
        ".message-basis-tag",
        ".message-basis-title",
        ".message-basis-detail",
      ]), { requireBorders: [".message-basis-tag"] });

      await openRailView(page, "系统状态", viewport);
      await expect(page.locator("#status-view")).toBeVisible();
      expectThemeAuditPasses(await auditThemeSelectors(page, [
        ".chat-view-title",
        ".status-section-title",
        "#view-model-name .status-label",
        "#view-model-name .status-value",
      ]));

      await openRailView(page, "账号设置", viewport);
      await expect(page.locator("#settings-view")).toBeVisible();
      await page.locator("#settings-view-body").evaluate((element) => {
        const states = document.createElement("section");
        states.id = "theme-contract-states";
        states.innerHTML = `
          <p class="inline-status inline-status-success">成功状态：操作已完成</p>
          <p class="inline-status inline-status-warning">警告状态：需要确认</p>
          <p class="inline-status inline-status-error">错误状态：请重试</p>
        `;
        element.prepend(states);
      });
      expectThemeAuditPasses(await auditThemeSelectors(page, [
        "#settings-view .chat-view-title",
        "#settings-view .drawer-card",
        ".inline-status-success",
        ".inline-status-warning",
        ".inline-status-error",
      ]), {
        requireBorders: [
          "#settings-view .drawer-card",
          ".inline-status-success",
          ".inline-status-warning",
          ".inline-status-error",
        ],
      });

      await page.locator("#open-user-register").click();
      await expect(page.locator("#account-view")).toBeVisible();
      expectThemeAuditPasses(await auditThemeSelectors(page, [
        "#account-view .chat-view-title",
        "#account-view .chat-view-desc",
        ".account-tab.active",
        ".account-tab:not(.active)",
        "#user-register-name",
        "#user-register-profile-select",
      ]), { requireBorders: ["#user-register-name", "#user-register-profile-select"] });

      const pageWidth = await page.evaluate(() => ({
        clientWidth: document.documentElement.clientWidth,
        scrollWidth: document.documentElement.scrollWidth,
      }));
      expect(pageWidth.scrollWidth).toBeLessThanOrEqual(pageWidth.clientWidth);
    }
  }
});

test("dark minimal chrome keeps sidebar, composer, and answer controls readable", async ({ page }) => {
  await openThemeFixture(page, "dark", { width: 1280, height: 800 });
  await page.locator("#sidebar-toggle").click();
  await page.locator("#sidebar-toggle").hover();
  await expect(page.locator(".history-empty")).toBeVisible();
  expectThemeAuditPasses(await auditThemeSelectors(page, [
    ".rail-btn",
    ".history-empty",
    ".composer-tools-summary",
    ".seed-chip",
  ]));
  const menuIcon = (await auditThemeSelectors(page, [".rail-brand-btn .brand-hover-icon"]))[0];
  expect(menuIcon.textContrast, "hovered menu icon contrast").toBeGreaterThanOrEqual(3);

  await page.locator("#chat-question").fill("请介绍研究方向并给出依据");
  await page.getByRole("button", { name: "发送问题" }).click();
  await expect(page.locator(".message-ready")).toBeVisible();
  await page.locator(".message-ready .message-section-support .message-section-toggle").click();
  expectThemeAuditPasses(await auditThemeSelectors(page, [
    ".message-ready .message-main-copy",
    ".message-ready .message-section-toggle",
    ".message-ready .message-basis-copy",
  ]));
  const sendButton = (await auditThemeSelectors(page, [".send-button"]))[0];
  expect(sendButton.textContrast, "send icon contrast").toBeGreaterThanOrEqual(3);
});

test("theme release screenshots remain stable across light, dark, desktop, and narrow layouts", async ({ page }) => {
  for (const theme of ["dark", "light"]) {
    for (const viewport of [{ width: 1280, height: 800 }, { width: 390, height: 844 }]) {
      await openThemeFixture(page, theme, viewport);
      await openRailView(page, "系统状态", viewport);
      await expect(page.locator("#status-view")).toBeVisible();
      await expect(page).toHaveScreenshot(`theme-${theme}-${viewport.width}x${viewport.height}-status.png`, {
        animations: "disabled",
        caret: "hide",
        fullPage: true,
        maxDiffPixelRatio: 0.005,
        timeout: 20000,
      });
    }
  }
});

test("composer modes retain native keyboard focus and visible boundaries", async ({ page }) => {
  for (const theme of ["dark", "light"]) {
    await openThemeFixture(page, theme, { width: 1280, height: 800 });
    const deep = page.locator("#deep-thinking-checkbox");
    await page.getByRole("button", { name: "发送问题" }).focus();
    await page.keyboard.press("Tab");
    await expect(page.locator(".composer-tools-summary")).toBeFocused();
    await page.keyboard.press("Enter");
    await expect(page.locator(".composer-tools-disclosure")).toHaveAttribute("open", "");
    await page.keyboard.press("Tab");
    await expect(deep).toBeFocused();
    const label = page.locator(".composer-pill-toggle").first();
    expect(await label.evaluate(el => getComputedStyle(el).outlineStyle)).toBe("solid");
    await page.keyboard.press("Space");
    await expect(deep).toBeChecked();
    await page.keyboard.press("Tab");
    await expect(page.locator("#web-search-checkbox")).toBeFocused();
    await page.keyboard.press("Space");
    await expect(page.locator("#web-search-checkbox")).toBeChecked();
    await deep.focus();
    await page.keyboard.press("Space");
    await expect(deep).not.toBeChecked();
    await expect(label).not.toHaveClass(/is-active/);
    await page.locator("#web-search-checkbox").focus();
    await page.keyboard.press("Space");
    await expect(page.locator("#web-search-checkbox")).not.toBeChecked();
    // The minimalist palette may change, but unselected controls must remain legible.
    expectThemeAuditPasses(await auditThemeSelectors(page, [
      ".composer-pill-toggle", ".rail-user-avatar",
    ]), { requireBorders: [".composer-pill-toggle", ".rail-user-avatar"] });
    await deep.evaluate((element) => { element.disabled = true; });
    expectThemeAuditPasses(await auditThemeSelectors(page, [
      ".composer-pill-toggle",
    ]), { requireBorders: [".composer-pill-toggle"] });
  }
});

test("mobile long answers and citations stay inside the message and clear the composer", async ({ page }) => {
  for (const theme of ["dark", "light"]) {
    await openThemeFixture(page, theme, { width: 390, height: 844 });
    // Mode status adds a composer row; test the largest actual composer.
    await openComposerTools(page);
    await page.locator(".composer-pill-toggle").first().click();
    await page.locator("#chat-question").fill("请解释长上下文实验");
    await page.getByRole("button", { name: "发送问题" }).click();
    await expect(page.locator(".message-ready")).toBeVisible();
    // Reproduce mixed prose/list intrinsic grid sizing, not just body.scrollWidth:
    // the shell intentionally clips horizontal overflow, masking a broken child.
    await page.locator(".message-ready .message-body").first().evaluate(el => {
      const list = document.createElement("ul");
      for (let i = 0; i < 18; i++) {
        const item = document.createElement("li");
        item.textContent = `检查 ${i}: baseline_${"long_context_".repeat(12)} 公平对比与关键消融`;
        list.append(item);
      }
      el.replaceChildren(list);
    });
    const geometry = await page.locator(".message-ready").evaluate(el => {
      const frame = el.getBoundingClientRect();
      return [...el.querySelectorAll(".message-frame, .message-main-copy, .message-body, .message-section, li")]
        .map(child => ({ name: child.className || child.tagName, right: child.getBoundingClientRect().right, limit: frame.right }));
    });
    for (const box of geometry) expect(box.right, JSON.stringify(box)).toBeLessThanOrEqual(box.limit + 1);
    await page.locator(".chat-stream").evaluate(el => { el.scrollTop = el.scrollHeight; });
    const bottom = await page.evaluate(() => ({
      reply: document.querySelector(".message-ready").getBoundingClientRect().bottom,
      composer: document.querySelector("#chat-form").getBoundingClientRect().top,
      width: document.documentElement.scrollWidth,
    }));
    expect(bottom.width).toBeLessThanOrEqual(390);
    expect(bottom.reply).toBeLessThanOrEqual(bottom.composer + 1);
  }
});

for (const authenticated of [false, true]) {
  test(`starter questions wait for delayed ${authenticated ? "member" : "guest"} identity and stay stable`, async ({ page }, testInfo) => {
    await page.route(/https:\/\/fonts\.(googleapis|gstatic)\.com\//, route => route.fulfill({contentType:"text/css", body:""}));
    await page.addInitScript(() => {
      localStorage.setItem("sageOnboardingCompleted", "true");
      localStorage.setItem("sageOnboardingDismissed", "true");
    });
    let resolveIdentity;
    const identityReady = new Promise(resolve => { resolveIdentity = resolve; });
    const session = authenticated ? {is_authenticated: true, account: {name: "Synthetic QA", email: "qa@example.test", visitor_profile: "lab_member"}} : {is_authenticated: false};
    await page.route("**/auth/user/session", async route => { await identityReady; await route.fulfill({json: session}); });
    await page.goto(fixtureBaseUrl, {waitUntil: "domcontentloaded"});
    const chips = page.locator("#seed-chips-list .seed-chip");
    await expect(page.locator("#seed-chips-list")).toBeAttached();
    expect(await chips.count()).toBe(0);
    await page.evaluate(() => {
      window.seedSnapshots = [];
      window.seedObserver = new MutationObserver(() => {
        const text = [...document.querySelectorAll("#seed-chips-list .seed-chip")].map(n => n.textContent);
        if (text.length && JSON.stringify(text) !== JSON.stringify(window.seedSnapshots.at(-1))) window.seedSnapshots.push(text);
      });
      window.seedObserver.observe(document.querySelector("#chat-stream"), {childList:true, subtree:true});
    });
    resolveIdentity();
    await expect(chips).toHaveCount(3);
    const firstText = await chips.allTextContents();
    await page.evaluate(async () => { await refreshUserSession(); applyVisitorProfilePresentation(); await renderSeedChips(visitorProfileInput.value); });
    expect(await page.evaluate(() => window.seedSnapshots)).toEqual([firstText]);
    expect(await page.locator("#seed-chips-list").getAttribute("data-seed-profile")).toBe(authenticated ? "lab_member" : "general_visitor");
    if (authenticated) {
      await page.evaluate(() => applyUserSession({is_authenticated:false}));
      await expect(page.locator("#seed-chips-list")).toHaveAttribute("data-seed-profile", "general_visitor");
      await expect(chips).toHaveCount(3);
      expect(await chips.allTextContents()).not.toEqual(firstText);
    }
    const beforeNewChat = await chips.allTextContents();
    await page.evaluate(() => renderDefaultLandingForProfile());
    await expect(chips).toHaveCount(3);
    expect(await chips.allTextContents()).toEqual(beforeNewChat);
    await page.screenshot({path:testInfo.outputPath("stable-starters.png"), animations:"disabled", timeout:5000});
  });
}

for (const width of [2048, 1280, 390, 320]) for (const theme of ["light", "dark"]) {
  test(`readable inspector live state and overview ${width} ${theme}`, async ({ page }, testInfo) => {
    await page.setViewportSize({width, height:width===320?568:width===390?844:900});
    await page.route(/https:\/\/fonts\.(googleapis|gstatic)\.com\//, route => route.fulfill({contentType:"text/css",body:""}));
    await page.addInitScript(theme => {
      localStorage.setItem("sageMateTheme", theme);
      localStorage.setItem("sageOnboardingCompleted","true");
      localStorage.setItem("sageOnboardingDismissed","true");
    }, theme);
    await page.goto(fixtureBaseUrl, {waitUntil:"domcontentloaded"});
    await page.waitForFunction(() => typeof appendMessage === "function" && seedChipsIdentityReady);
    await page.evaluate(() => {
      appendMessage("user", "Synthetic QA", "这个分身能帮我做什么，哪些事情不能代办？");
      const message = appendMessage("assistant", "Twin", "我可以帮你了解研究方向、查找公开资料、准备交流问题。需要你确认的预约不会直接提交。");
      message.classList.add("message-ready");
      const topology = Array.from({length:79}, (_,i) => ({node_id:`node-${i}`,operator_type:"map",operator_key:i===0?"understand":"answer",is_entry:i===0,is_return:i===78,downstream_node_ids:i===78?[]:[`node-${i+1}`]}));
      const events = topology.map((node,i) => ({version:1,event_id:`e${i}`,sequence:i+1,trace_id:"qa",request_id:"qa",execution_id:`exec${i}`,parent_execution_id:null,program_uri:"flow://qa",program_rev:"v1",node_id:node.node_id,operator_key:node.operator_key,state:"completed",upstream_node_ids:i?[`node-${i-1}`]:[],downstream_node_ids:node.downstream_node_ids,next_nodes:node.downstream_node_ids.map(node_id=>({program_uri:"flow://qa",program_rev:"v1",node_id})),...(i===0?{program_topology:topology}:{})}));
      message.insertAdjacentHTML("beforeend", `<section class="message-workflow-summary"><button class="message-workflow-summary-toggle" aria-expanded="false">详情</button><div class="message-section-content" hidden>${buildNativeExecutionHtml(events)}</div></section>`);
      window.layoutQaMessage = message; window.layoutQaEvents = events;
      updateChatEmptyState();
    });
    await page.getByRole("button", {name:"详情",exact:true}).click();
    const panel=page.locator("#workflow-shell");
    await expect(panel).toBeVisible();
    const metrics=await page.evaluate(() => {
      const box=sel=>{const r=document.querySelector(sel).getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height,right:r.right,bottom:r.bottom};};
      return {shell:box(".chat-shell"),layout:box(".chat-layout"),panel:box("#workflow-shell"),composer:box(".composer-row"),svg:box("#workflow-shell .native-flow-atlas-diagram"),scroll:box("#workflow-shell .native-flow-scroll"),pageWidth:document.documentElement.scrollWidth};
    });
    expect(metrics.pageWidth).toBeLessThanOrEqual(width+1);
    if (width>920) {
      expect(Math.abs(metrics.shell.x-metrics.layout.x)).toBeLessThan(2);
      expect(metrics.shell.right).toBeLessThanOrEqual(metrics.panel.x+1);
      expect(metrics.panel.width).toBeGreaterThanOrEqual(360);
      expect(Math.abs(metrics.composer.x+metrics.composer.width/2-(metrics.shell.x+metrics.shell.width/2))).toBeLessThan(3);
    }
    if (width<=920) expect(metrics.scroll.bottom).toBeLessThanOrEqual(metrics.panel.bottom+1);
    await expect(panel.locator(".native-flow-view")).toHaveAttribute("open", "");
    expect(metrics.svg.height).toBeGreaterThan(metrics.scroll.height);
    expect(await panel.locator(".native-flow-node circle").first().evaluate(el=>el.getBoundingClientRect().width)).toBeGreaterThanOrEqual(35);
    expect(await panel.locator(".native-flow-label").nth(1).evaluate(el=>parseFloat(getComputedStyle(el).fontSize))).toBeGreaterThanOrEqual(14);
    await expect(panel.locator("[data-flow-node]")).toHaveCount(79);
    await page.screenshot({path:testInfo.outputPath("readable-default.png"),animations:"disabled",timeout:10000});
    await panel.locator(".native-flow-view > summary").focus();
    const graph=panel.locator(".native-flow-scroll");
    expect(await graph.evaluate(el=>el.scrollHeight>el.clientHeight)).toBe(true);
    await graph.evaluate(el=>{el.scrollTop=200;});
    const before=await page.locator("#chat-stream").evaluate(el=>el.scrollTop);
    await page.evaluate(() => {layoutQaMessage.querySelector(".message-section-content").insertAdjacentHTML("afterbegin", `<details class="qa-inserted"><summary>新来源 · 3</summary>新增资料</details>`);});
    await expect(panel.locator(".native-flow-view")).toHaveAttribute("open", "");
    expect(await graph.evaluate(el=>el.scrollTop)).toBe(200);
    await expect(panel.locator(".native-flow-view > summary")).toBeFocused();
    expect(await page.locator("#chat-stream").evaluate(el=>el.scrollTop)).toBe(before);
    await panel.locator(".native-flow-view > summary").click();
    await expect(panel.locator(".native-flow-view")).not.toHaveAttribute("open", "");
    const overview = await panel.locator(".native-flow-atlas-diagram").boundingBox();
    const viewport = await graph.boundingBox();
    expect(overview.height).toBeLessThanOrEqual(viewport.height+1);
    expect(overview.width).toBeLessThanOrEqual(viewport.width+1);
    // Exercise both live writers, not only an inspected static answer.
    await page.evaluate(() => {
      inspectedWorkflowMessage = null; activeWorkflowMessage = layoutQaMessage;
      activeExecutionEvents = layoutQaEvents;
      syncNativeExecutionDisplay(); renderWorkflowTrace([], {isStreaming:true});
    });
    await expect(panel.locator(".native-flow-view")).not.toHaveAttribute("open", "");
    await panel.locator(".native-flow-view > summary").click();
    expect(await graph.evaluate(el=>el.scrollTop)).toBe(200);
    await graph.evaluate(el=>{el.scrollTop=260;el.focus();});
    await page.evaluate(() => {
      const branches = Array.from({length:12}, (_,i)=>`late-node-${i}`);
      const topology = layoutQaEvents[0].program_topology.map(node => ({...node,
        downstream_node_ids:node.node_id==="node-38" ? [...node.downstream_node_ids,...branches] : node.downstream_node_ids}));
      for (const node_id of branches) topology.push({node_id,operator_type:"map",operator_key:"read-wiki",downstream_node_ids:[]});
      activeExecutionEvents = layoutQaEvents.map((event,i)=>i===0 ? {...event,program_topology:topology}:event);
      activeExecutionEvents.push({...layoutQaEvents[1],node_id:"late-node-0",event_id:"new",execution_id:"late-exec",sequence:100,state:"started",operator_key:"read-wiki",upstream_node_ids:["node-38"],downstream_node_ids:[],next_nodes:[]});
      syncNativeExecutionDisplay(); renderWorkflowTrace([], {isStreaming:true});
    });
    await expect(panel.locator(".native-flow-view")).toHaveAttribute("open", "");
    expect(await graph.evaluate(el=>el.scrollTop)).toBe(260);
    await expect(panel.locator("[data-flow-node]")).toHaveCount(91);
    const centered=await graph.evaluate(el=>Math.abs(el.scrollLeft-(el.scrollWidth-el.clientWidth)/2));
    expect(centered).toBeLessThanOrEqual(1);
    await graph.evaluate(el=>{el.scrollLeft+=30;});
    const pan=await graph.evaluate(el=>el.scrollLeft);
    await panel.locator(".native-flow-view > summary").click();
    await panel.locator(".native-flow-view > summary").click();
    expect(await graph.evaluate(el=>el.scrollLeft)).toBe(pan);
    expect(await graph.evaluate(el=>el.scrollTop)).toBe(260);
    await page.evaluate(() => { closeWorkflowMobileSheet(); openWorkflowMobileSheet(); });
    expect(await graph.evaluate(el=>el.scrollLeft)).toBe(pan);
    expect(await graph.evaluate(el=>el.scrollTop)).toBe(260);
    await graph.focus();
    await expect(graph).toBeFocused();
    expect(await page.locator("#chat-stream").evaluate(el=>el.scrollTop)).toBe(before);
    // Completion replaces the source hierarchy; retain the explicit mode.
    await page.evaluate(() => inspectMessageWorkflow(layoutQaMessage, layoutQaMessage.querySelector("button")));
    await expect(panel.locator(".native-flow-view")).toHaveAttribute("open", "");
    expect(await graph.evaluate(el=>el.scrollTop)).toBe(260);
    await page.screenshot({path:testInfo.outputPath("after-live-updates.png"),animations:"disabled",timeout:10000});
  });
}
