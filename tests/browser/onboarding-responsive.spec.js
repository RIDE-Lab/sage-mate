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

    await page.locator(".sage-companion-settings summary").click();
    const settingsPanelBox = await page.locator("#sage-companion-panel").boundingBox();
    expect(settingsPanelBox).not.toBeNull();
    expect(settingsPanelBox.x).toBeGreaterThanOrEqual(0);
    expect(settingsPanelBox.x + settingsPanelBox.width).toBeLessThanOrEqual(viewport.width + 0.5);
    expect(settingsPanelBox.y).toBeGreaterThanOrEqual(0);
    expect(settingsPanelBox.y + settingsPanelBox.height).toBeLessThanOrEqual(viewport.height + 0.5);
    await page.locator(".sage-companion-settings summary").click();

    await page.getByRole("button", { name: "摸摸它" }).click();
    await expect(companion).toHaveAttribute("data-state", "happy");
    await expect(page.locator("#sage-companion-bond-value")).toHaveText("24%");
    await page.keyboard.press("Escape");
    await expect(page.locator("#sage-companion-panel")).toBeHidden();
    await expect(toggle).toHaveAttribute("aria-expanded", "false");
  });
}

test("Sage companion customizes, grows, persists, hides, and restores", async ({ page }) => {
  const viewport = VIEWPORTS[1];
  await openOnboarding(page, viewport);
  await page.getByRole("button", { name: "跳过引导" }).click();
  await page.evaluate(() => {
    localStorage.removeItem("sageMateCompanion:v2");
    localStorage.setItem("sageMateCompanion:v1", JSON.stringify({ bond: 58 }));
  });
  await page.reload({ waitUntil: "domcontentloaded" });

  const companion = page.locator("#sage-companion");
  const toggle = page.locator("#sage-companion-toggle");
  await expect(page.locator("#sage-companion-bond-value")).toHaveText("58%");
  await expect(page.locator("#sage-companion-stage")).toHaveText("熟悉伙伴");
  await toggle.click();
  await page.locator(".sage-companion-settings summary").click();
  await page.locator("#sage-companion-name-input").fill("小火花");
  await page.getByRole("button", { name: "保存" }).click();
  await page.getByLabel("薄荷").check();
  await page.locator("#sage-companion-temperament").selectOption("lively");
  await page.locator("#sage-companion-sound").check();
  await expect(companion).toHaveAttribute("data-appearance", "mint");
  await expect(companion).toHaveAttribute("data-temperament", "lively");
  await expect(page.locator("#sage-companion-name")).toHaveText("小火花");

  await page.getByRole("button", { name: "喂颗灵感豆" }).click();
  await expect(page.locator("#sage-companion-bond-value")).toHaveText("65%");
  await expect(page.locator("#sage-companion-stage")).toHaveText("默契搭档");
  await expect(page.locator("#sage-companion-message")).toContainText("新的成长阶段");

  await page.reload({ waitUntil: "domcontentloaded" });
  await expect(companion).toHaveAttribute("data-appearance", "mint");
  await expect(companion).toHaveAttribute("data-temperament", "lively");
  await expect(page.locator("#sage-companion-name")).toHaveText("小火花");
  await expect(page.locator("#sage-companion-bond-value")).toHaveText("65%");
  await toggle.click();
  await page.locator(".sage-companion-settings summary").click();
  await page.getByRole("button", { name: "隐藏伙伴" }).click();
  await expect(companion).toBeHidden();

  await page.getByRole("button", { name: "打开菜单" }).click();
  await page.getByRole("button", { name: "恢复电子伙伴小火花" }).click();
  await expect(companion).toBeVisible();
  await expect(page.locator("#sage-companion-panel")).toBeVisible();

  const stored = await page.evaluate(() => JSON.parse(localStorage.getItem("sageMateCompanion:v2")));
  expect(stored).toMatchObject({
    version: 2,
    name: "小火花",
    appearance: "mint",
    temperament: "lively",
    soundEnabled: true,
    hidden: false,
    bond: 65,
  });
  expect(stored).not.toHaveProperty("question");
});
