import { test, expect } from '@playwright/test';

test('workspace loads actual API views through the Next proxy', async ({ page }) => {
  const errors = [];
  page.on('pageerror', (error) => errors.push(error.message));
  const response = page.waitForResponse((r) =>
    r.url().endsWith('/api/views') && r.request().method() === 'POST');
  await page.goto('/');
  expect((await response).ok()).toBeTruthy();
  await expect(page.getByRole('button', { name: '1. 설정', exact: true })).toBeVisible();
  await page.getByRole('button', { name: '4. 검증', exact: true }).click();
  await expect(page.getByRole('button', { name: '5. 내보내기', exact: true })).toBeVisible();
  expect(errors).toEqual([]);
});

test('frontend rejects foreign browser origins before local-resource access', async ({ request }) => {
  const response = await request.get('/api/library', {
    headers: { Origin: 'https://untrusted.example', 'Sec-Fetch-Site': 'cross-site' },
  });
  expect(response.status()).toBe(403);
});