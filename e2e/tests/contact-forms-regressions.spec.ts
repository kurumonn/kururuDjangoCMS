// ブラウザでしか確かめられない再発防止の検査。
//
// ここに置くのは「サーバー側のテストでは通ってしまう種類の不具合」だけにする。
// 実際、テーマの本文色が反映されない不具合も、フォームのID衝突も、
// Django のテストは全部通ったまま本番相当の画面だけが壊れていた。
import { expect, test } from "@playwright/test";

const twoFormPath = "/articles/e2e-contact-form-twice/";
const origin = process.env.E2E_BASE_URL || "https://e2e.local";

test("@form-identity 同じフォームを2つ置いても id が衝突しない", async ({ page }) => {
  await page.goto(twoFormPath);
  await expect(page.locator(".kururu-form form")).toHaveCount(2);

  // Django の既定では id が項目名から決まるため、同じ項目名を持つ
  // フォームを並べると両方 id_email になる。
  const ids = await page.evaluate(() =>
    [...document.querySelectorAll('[id^="kururu-form"]')].map((element) => element.id),
  );
  expect(ids.length).toBeGreaterThan(0);
  expect(new Set(ids).size).toBe(ids.length);

  // ラベルの参照先が実在し、かつ全部ばらばらであること。
  const targets = await page.evaluate(() =>
    [...document.querySelectorAll('label[for^="kururu-form"]')].map((label) => ({
      target: (label as HTMLLabelElement).htmlFor,
      resolves: Boolean(document.getElementById((label as HTMLLabelElement).htmlFor)),
    })),
  );
  expect(targets.length).toBeGreaterThan(0);
  expect(targets.every((entry) => entry.resolves)).toBe(true);
  expect(new Set(targets.map((entry) => entry.target)).size).toBe(targets.length);

  // 2つ目のラベルをクリックしたら、2つ目の入力欄にフォーカスが入る。
  const second = page.locator(".kururu-form form").nth(1);
  const emailId = await second.locator('input[name="email"]').getAttribute("id");
  await second.locator('label[for$="-email"]').click();
  expect(await page.evaluate(() => document.activeElement?.id)).toBe(emailId);

  // 送信項目名は分けない。送信先URLがフォームごとに違うので取り違えない。
  const sections = await page.locator("section.kururu-form").count();
  expect(sections).toBe(2);
});

test("@invalid-input 入力エラーは送信元の配置に値とエラーを戻す", async ({ page }) => {
  await page.goto(twoFormPath);
  const second = page.locator(".kururu-form form").nth(1);

  await second.locator('input[name="name"]').fill("E2E再表示");
  // "a@b" はブラウザの type=email 検証を通るが、Django は弾く。
  // "not-an-email" だとブラウザが送信自体を止めるため、
  // サーバー側の再表示経路まで到達しない。
  await second.locator('input[name="email"]').fill("a@b");
  await second.locator('textarea[name="message"]').fill("入力エラー再表示の確認");

  // 最短入力時間を満たしてから送る。
  await page.waitForTimeout(2_100);
  await second.getByRole("button", { name: "送信" }).click();
  await page.waitForLoadState("load");

  const secondAfter = page.locator(".kururu-form form").nth(1);
  const firstAfter = page.locator(".kururu-form form").nth(0);
  const emailAfter = secondAfter.locator('input[name="email"]');

  // 送った側にだけ、入力値と項目別エラーが戻る。
  await expect(emailAfter).toHaveAttribute("aria-invalid", "true");
  await expect(emailAfter).toHaveValue("a@b");
  await expect(secondAfter.locator('input[name="name"]')).toHaveValue("E2E再表示");
  const describedBy = await emailAfter.getAttribute("aria-describedby");
  expect(describedBy).toBeTruthy();
  await expect(page.locator(`[id="${describedBy}"]`)).toBeVisible();

  // 1つ目は無傷（他人の入力が別のフォームに出ない）。
  await expect(firstAfter.locator('input[name="name"]')).toHaveValue("");
  expect(
    await firstAfter.locator('input[name="email"]').getAttribute("aria-invalid"),
  ).toBeNull();

  // 再表示は1回だけ。リロードで古い入力が復活しない。
  await page.reload();
  await expect(
    page.locator(".kururu-form form").nth(1).locator('input[name="name"]'),
  ).toHaveValue("");

  // ブラウザ側の検証で止まる経路も確認する（サーバーまで届かない）。
  const first = page.locator(".kururu-form form").nth(0);
  await first.locator('input[name="name"]').fill("ネイティブ検証");
  await first.locator('input[name="email"]').fill("not-an-email");
  await first.locator('textarea[name="message"]').fill("ネイティブ検証の確認");
  await page.waitForTimeout(2_100);
  await first.getByRole("button", { name: "送信" }).click();
  await page.waitForTimeout(500);
  expect(
    await first
      .locator('input[name="email"]')
      .evaluate((element: HTMLInputElement) => element.validity.valid),
  ).toBe(false);
});

test("@reduced-motion 利用者の「動きを減らす」設定を優先する", async ({ browser }) => {
  // 動く側と止まる側を同じ条件で見比べる。
  // 片方だけを見ると、セレクターを間違えていても気づけない。
  const read = async (reducedMotion: "reduce" | "no-preference") => {
    const context = await browser.newContext({
      reducedMotion,
      ignoreHTTPSErrors: true,
      baseURL: origin,
    });
    const page = await context.newPage();
    await page.goto(twoFormPath);
    const state = await page.evaluate(() => {
      const target = document.querySelector(".article") as HTMLElement;
      const style = getComputedStyle(target);
      return {
        motion: document.documentElement.dataset.motion,
        theme: document.documentElement.dataset.theme,
        animationName: style.animationName,
        animationDuration: style.animationDuration,
      };
    });
    await context.close();
    return state;
  };

  const moving = await read("no-preference");
  // 管理画面でアニメーションを有効にしてある前提の検査。
  expect(moving.theme).toBe("motion");
  expect(moving.motion).toBe("enabled");
  expect(moving.animationName).not.toBe("none");

  const reduced = await read("reduce");
  expect(reduced.motion).toBe("enabled");
  expect(
    reduced.animationName === "none" || reduced.animationDuration === "0.00001s",
  ).toBe(true);
});
