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
