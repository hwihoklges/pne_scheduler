import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './browser-tests',
  workers: 1,
  retries: 0,
  use: { baseURL: 'http://127.0.0.1:3000', trace: 'retain-on-failure' },
  webServer: [
    {
      command: 'python ../run_pne_scheduler_api.py',
      url: 'http://127.0.0.1:8000/api/library',
      reuseExistingServer: false,
      timeout: 30000,
    },
    {
      command: 'npm start',
      url: 'http://127.0.0.1:3000',
      reuseExistingServer: false,
      timeout: 60000,
    },
  ],
});