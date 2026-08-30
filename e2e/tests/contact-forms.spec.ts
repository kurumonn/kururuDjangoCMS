import { expect, test } from "@playwright/test";

const articlePath = "/articles/e2e-contact-form/";
const adminPath = "/admin/contact_forms/contactform/";

test("@authorization view-only staff cannot archive or duplicate forms", async ({
  page,
}) => {
  await page.goto(`${adminPath}`);
  await page.waitForURL(/\/accounts\/login\//);
  await page.locator('input[name="login"]').fill(process.env.E2E_VIEWER_EMAIL || "");
  await page
    .locator('input[name="password"]')
    .fill(process.env.E2E_VIEWER_PASSWORD || "");
  await page.getByRole("button", { name: /ログイン|Sign In/i }).click();
  await page.waitForURL(new RegExp(`${adminPath.replaceAll("/", "\\/")}`));

  const actions = page.locator('select[name="action"]');
  await expect(actions.locator('option[value="duplicate_forms"]')).toHaveCount(0);
  await expect(actions.locator('option[value="archive_forms"]')).toHaveCount(0);

  const csrf = await page.locator('input[name="csrfmiddlewaretoken"]').first().inputValue();
  const row = page.locator('input[name="_selected_action"]').first();
  const formId = await row.inputValue();
  const forged = await page.context().request.post(adminPath, {
    maxRedirects: 0,
    headers: {
      origin: process.env.E2E_BASE_URL || "https://e2e.local",
      referer: `${process.env.E2E_BASE_URL || "https://e2e.local"}${adminPath}`,
    },
    form: {
      csrfmiddlewaretoken: csrf,
      action: "archive_forms",
      _selected_action: formId,
      index: "0",
    },
  });
  expect([302, 403]).toContain(forged.status());

  await page.reload();
  await expect(page.getByRole("link", { name: "E2Eお問い合わせ" })).toBeVisible();
});

test("@enqueue duplicate public POST creates one durable outbox and no synchronous mail", async ({
  page,
}) => {
  await page.goto(articlePath);
  await expect(page.getByRole("heading", { name: "E2Eお問い合わせ" })).toBeVisible();

  const form = page.locator("form.kururu-form, .kururu-form form").first();
  await form.locator('input[name="name"]').fill("E2E利用者");
  await form.locator('input[name="email"]').fill("visitor@example.test");
  await form.locator('textarea[name="message"]').fill("Docker Compose E2E送信");

  const requestBody = await form.evaluate((element: HTMLFormElement) =>
    new URLSearchParams(new FormData(element) as unknown as Record<string, string>).toString(),
  );
  const action = await form.getAttribute("action");
  expect(action).toBeTruthy();

  await page.waitForTimeout(2_100);
  await Promise.all([
    page.waitForURL(new RegExp(`${articlePath.replaceAll("/", "\\/")}`)),
    form.getByRole("button", { name: "送信" }).click(),
  ]);
  await expect(page.getByText("お問い合わせを受け付けました。")).toBeVisible();

  const csrfCookie = (await page.context().cookies()).find(
    (cookie) => cookie.name === "csrftoken",
  );
  expect(csrfCookie).toBeTruthy();
  const duplicate = await page.context().request.post(action || "", {
    data: requestBody,
    maxRedirects: 0,
    headers: {
      "content-type": "application/x-www-form-urlencoded",
      "x-csrftoken": csrfCookie?.value || "",
      origin: process.env.E2E_BASE_URL || "https://e2e.local",
      referer: `${process.env.E2E_BASE_URL || "https://e2e.local"}${articlePath}`,
    },
  });
  expect(duplicate.status()).toBe(302);

  const mail = await page.request.get("http://smtp_capture:8025/messages");
  expect(mail.ok()).toBeTruthy();
  expect(await mail.json()).toEqual([]);
});

test("@delivery worker resumes the outbox and sends notification before autoreply", async ({
  request,
}) => {
  await expect
    .poll(
      async () => {
        const response = await request.get("http://smtp_capture:8025/messages");
        if (!response.ok()) return [];
        return response.json();
      },
      { timeout: 25_000, intervals: [250, 500, 1_000] },
    )
    .toMatchObject([
      { recipients: ["owner@example.test"] },
      { recipients: ["visitor@example.test"] },
    ]);
});
