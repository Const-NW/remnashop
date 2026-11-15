# remnashop dashboard

React admin panel for remnashop, the primary way to run the shop; the backend's `api` process serves everything it calls.

React 19 + TypeScript, Vite, Tailwind CSS v4 (`@tailwindcss/vite`, no PostCSS config), react-router 7, `react-i18next`. Feature-Sliced Design: `app/` → `pages/` → `widgets/` → `features/` → `entities/` → `shared/`, path aliases `@app`/`@pages`/`@widgets`/`@features`/`@entities`/`@shared` (kept in sync between `vite.config.ts` and `tsconfig.app.json`). Every shared UI primitive is on `/dashboard/debug`.

## Scripts

```bash
npm install
npm run dev      # vite dev server, proxies /api to VITE_API_PROXY_TARGET (default http://127.0.0.1:5000)
npm run build    # tsc -b && vite build
npm run lint     # oxlint, locale check (scripts/check-locales.mjs), knip
npm run preview  # preview a production build
```

`npm run dev`'s `/api` proxy targets the `api` backend process: run it separately (`cd backend && uv run python -m app.api`) or point `VITE_API_PROXY_TARGET` at wherever it's running.

## Rules

Code rules (layers, imports, i18n, shared UI, type scale) are in the root [AGENTS.md](../AGENTS.md), section "Frontend"; `npm run lint` enforces the mechanical ones.
