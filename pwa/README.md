# Hollic Health — PWA

Patient app. This copy talks to the FastAPI API in this repository (`/api/app/v1`), in the same shape as before. The production repository is left untouched.

## Stack

- Vite + React + TypeScript
- React Router
- vite-plugin-pwa
- Native HTTP client (`fetch`) → `/api/app/v1/*`

## How to run

1. Start the API (for example uvicorn on `http://127.0.0.1:8000`)
2. In the PWA:

```bash
cp .env.example .env
npm install
npm run dev
```

With `VITE_API_URL` empty, Vite proxies `/api` → `http://127.0.0.1:8000`.

To point straight at staging or production:

```env
VITE_API_URL=https://api2.hollic-health.example/api
```

Or change the proxy target:

```env
VITE_API_PROXY_TARGET=http://127.0.0.1:8000
```

On the API, allow the origin (`http://127.0.0.1:5174`) if you are not using the proxy.

## Auth

- `POST /api/app/v1/login` → Bearer token
- Session in `localStorage` (`hollic_pwa_session`)
- `401` clears the session and sends the user back to sign-in

## Endpoints in use

Login/logout, highlights, appointments, checkout, categories, addresses, notifications, partners, accredited-network, update customer, change-password, forgot-password.
