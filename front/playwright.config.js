// End-to-end tests: real browser, real front-end, real API.
//
// `npm run test:e2e` starts the API on port 8000 with its own SQLite file
// (e2e.db, seeded) and the Vite dev server on 5173, then runs the specs in
// `e2e/`. Point E2E_BASE_URL / E2E_API_URL at servers you already run (and
// set E2E_NO_SERVER=1) to skip the automatic start-up.
import { defineConfig, devices } from '@playwright/test'

const baseURL = process.env.E2E_BASE_URL || 'http://127.0.0.1:5173'
const apiURL = process.env.E2E_API_URL || 'http://127.0.0.1:8000'
const python = process.env.E2E_PYTHON || '.venv/bin/python'

export default defineConfig({
  testDir: './e2e',
  timeout: 60_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? 'github' : 'list',
  use: {
    baseURL,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    ...devices['Desktop Chrome'],
    viewport: { width: 1280, height: 900 },
  },
  metadata: { apiURL },
  webServer: process.env.E2E_NO_SERVER
    ? undefined
    : [
        {
          // The demo PKI (demo-ca/) lets the prescription signing be exercised: its root is the trust bundle.
          command: `cd ../api && ${python} scripts/make_demo_certificate.py && DATABASE_URL=sqlite:///./e2e.db PRESCRIPTIONS_DIR=./e2e-prescriptions ${python} scripts/seed_demo.py && DATABASE_URL=sqlite:///./e2e.db PRESCRIPTIONS_DIR=./e2e-prescriptions CHAT_ATTACHMENTS_DIR=./e2e-chat ICP_BRASIL_TRUST_BUNDLE=./demo-ca/roots.pem WORKER_ENABLED=0 MAIL_BACKEND=memory ${python} -m uvicorn app.main:app --port 8000`,
          url: `${apiURL}/docs`,
          reuseExistingServer: !process.env.CI,
          timeout: 60_000,
        },
        {
          command: 'npx vite --port 5173 --strictPort',
          url: baseURL,
          reuseExistingServer: !process.env.CI,
          timeout: 60_000,
        },
      ],
})
