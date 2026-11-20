# Cloudflare Workers deployment

This frontend uses OpenNext to build the Next.js application for Cloudflare Workers.
The standard `build` script remains `next build`; the `postbuild` lifecycle
script generates the Cloudflare Worker bundle when the Cloudflare build environment
is detected.

Configure Cloudflare Workers Builds as follows:

- Root directory: `frontend`
- Build command: `npm run cf:build` (or `npm run build`, whose postbuild creates the Worker)
- Deploy command: `npm run cf:deploy`
- Do not use `cf:preview` as a Deploy command; it starts a local server and remains running.

The Cloudflare-specific scripts in `package.json`:

- `cf:build` runs `opennextjs-cloudflare build`.
- `cf:preview` runs a build and starts a local Workers preview server. Use it locally only.
- `cf:deploy` runs `opennextjs-cloudflare deploy` against the bundle produced by the preceding Cloudflare Build command. It deliberately does not rebuild; Cloudflare Workers Builds runs Build and Deploy as separate phases.
- `cf:deploy:local` builds and then deploys in one command for local/manual use.

This separation avoids redundant OpenNext builds in Workers Builds. Do not put a local preview-server command in the Production Deploy command.

Do not merge until the current PR Preview deployment URL has been checked for frontend loading and backend REST API/WebSocket connectivity. Cloudflare is the production target; Vercel fallback deployment is intentionally disabled.
