# Herminator Dashboard

<p align="center">
  <img src="./docs/banner.svg" alt="Herminator Dashboard banner" width="100%" />
</p>

<p align="center">
  A polished synthwave operator console for local Hermes installations.
</p>

<p align="center">
  <a href="https://github.com/smittyPNW/herminator-dashboard/blob/main/LICENSE">MIT License</a>
  ·
  <a href="https://github.com/smittyPNW/herminator-dashboard/issues">Issues</a>
</p>

## What It Is

Herminator Dashboard is a Next.js control surface for running Hermes like an actual product instead of a loose collection of terminals and config files.

It gives you one place to:

- inspect gateway health
- restart or stop the gateway
- manage cron jobs
- switch Hermes profiles
- browse and install skills
- inspect sessions and logs
- chat through Hermes and local fallbacks

## Screenshots

### Visual Overview

The repo banner shows the full visual direction, but the screenshots below are from the actual interface.

<p align="center">
  <img src="./docs/screenshots/dashboard-hero-detail.png" alt="Herminator dashboard hero and operator controls" width="100%" />
</p>

<p align="center">
  <em>Main dashboard styling, hero copy, and operator controls.</em>
</p>

### Interface Details

<p align="center">
  <img src="./docs/screenshots/sidebar-detail.png" alt="Herminator sidebar and navigation detail" width="360" />
</p>

<p align="center">
  <em>Sidebar branding, navigation hierarchy, and the synthwave command-deck treatment.</em>
</p>

### Why Both Matter

- The banner gives the repo a strong first impression.
- The screenshots prove the shipped UI actually looks like the product being described.
- Together they make the project feel more credible to someone deciding whether to clone it.

## Highlights

- Distinct synthwave control-room art direction instead of generic admin UI
- Real Hermes-backed actions instead of fake dashboard buttons
- Mobile navigation and responsive operator shell
- Profile, skills, sessions, logs, and cron workflows in one app
- Public-repo-safe config layout with `.env.example` and ignored local secrets

## Stack

- Next.js 15
- React 19
- TypeScript
- Tailwind CSS 4

## Requirements

- Node.js 18+
- A working local Hermes installation
- Hermes CLI and Hermes runtime files accessible via `HERMES_DIR`

## Quick Start

1. Install dependencies.

```bash
npm install
```

2. Create your local environment file.

```bash
cp .env.example .env.local
```

3. Update `.env.local`.

```env
DASHBOARD_PASSWORD=your-dashboard-password
AUTH_SECRET=your-long-random-secret
HERMES_DIR=/path/to/.hermes
APP_ORIGIN=http://localhost:3000
```

4. Start the development server.

```bash
npm run dev
```

5. Open the app at [http://localhost:3000](http://localhost:3000).

## Production

Build and run:

```bash
npm run build
npm run start
```

This app reads live Hermes state from `HERMES_DIR`, so production use makes the most sense:

- on the same machine that runs Hermes
- or inside a trusted internal network

## Project Structure

- `src/lib/hermes.ts`
  Hermes filesystem and CLI integration layer
- `src/app/api/*`
  Server routes for auth, gateway, chat, skills, sessions, weather, and admin actions
- `src/components/*`
  Navigation, operator panels, tables, controls, and shared UI
- `src/app/*`
  Dashboard pages for chat, cron, config, skills, logs, and sessions

## Environment Notes

- `.env.local` is intentionally not committed
- provider keys should stay in your local Hermes environment
- if you previously exposed secrets anywhere, rotate them before reuse

## License

Released under the [MIT License](./LICENSE).
