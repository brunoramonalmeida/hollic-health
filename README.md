# Hollic Health

API and the patient app. MIT license.

## API

```bash
cd api
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
.venv/bin/python scripts/seed_demo.py
.venv/bin/python -m uvicorn app.main:app --port 8000
```

http://127.0.0.1:8000

## Patient app

```bash
cd pwa
npm install
npm run dev
```

http://localhost:5174

Login from the seed: `foo.holder@example.com` / `saude123`
