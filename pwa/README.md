# Santé Saúde — PWA

Aplicativo do paciente. Nesta cópia ele fala com a API FastAPI deste repositório (`/api/app/v1`), no mesmo formato de antes. O repositório de produção não é alterado.

## Stack

- Vite + React + TypeScript
- React Router
- vite-plugin-pwa
- Cliente HTTP nativo (`fetch`) → `/api/app/v1/*`

## Como rodar

1. Suba a API (ex.: `php artisan serve` em `http://127.0.0.1:8000`)
2. No PWA:

```bash
cp .env.example .env
npm install
npm run dev
```

Com `VITE_API_URL` vazio, o Vite faz proxy de `/api` → `http://127.0.0.1:8000`.

Para apontar direto para homolog/produção:

```env
VITE_API_URL=https://api2.santesaude.com.br/api
```

Ou altere o alvo do proxy:

```env
VITE_API_PROXY_TARGET=http://127.0.0.1:8000
```

Na API, configure CORS/origens (`FRONTEND_APP_URL=http://127.0.0.1:5173`) se não usar o proxy.

## Auth

- `POST /api/app/v1/login` → Bearer Sanctum
- Sessão em `localStorage` (`sante_pwa_session`)
- `401` limpa sessão e redireciona para login

## Endpoints usados

Login/logout, highlights, appointments, checkout, categories, subscriptions, sante-card, addresses, notifications, partners, accredited-network, update customer, change-password, forgot-password.
