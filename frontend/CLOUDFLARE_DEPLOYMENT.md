# Cloudflare Workers deployment

This frontend uses OpenNext to build the Next.js application for Cloudflare Workers.
The standard `build` script remains `next build`; Cloudflare Workers is the intended hosting target.

A `postbuild` lifecycle script now runs `opennextjs-cloudflare build` automatically
after `npm run build`. This is a compatibility safeguard for existing Cloudflare
Workers Builds configurations that still invoke the Production Build command
(`npm run build`) for Preview branches. It ensures `.open-next/worker.js` exists
before Cloudflare invokes the Preview command, without requiring the dashboard's
legacy Build command to be renamed.

Configure Cloudflare Workers Builds as follows:

- Root directory: `frontend`
- Build command: `npm run build` (backward-compatible; postbuild generates the Worker)
- Production Deploy command: `npm run cf:deploy`
- Preview command: `npx wrangler preview` or `npm run cf:preview`

The Cloudflare-specific scripts in `package.json` are intentionally self-contained:

- `cf:build` runs `opennextjs-cloudflare build`.
- `cf:preview` runs `opennextjs-cloudflare build` and then `wrangler preview`.
- `cf:deploy` runs `opennextjs-cloudflare build` and then `opennextjs-cloudflare deploy`.

The postbuild step creates the additional OpenNext Worker bundle. If Vercel Git integration is still connected, disable its automatic production and preview deployments; do not use Vercel as a deployment target.

Do not put `npx wrangler preview` in the Production Deploy command.
Do not promote a Preview build to Production until its Preview URL has been checked
for frontend loading and backend REST API/WebSocket connectivity. Cloudflare is the
production target; Vercel fallback deployment is intentionally disabled.

## Existing Workers Builds projects: legacy Preview behavior

Cloudflare documents that existing Workers Builds projects can retain the legacy
Preview model until the one-time Worker Previews switch is completed. In that legacy
mode, Preview builds use the Production build settings. The postbuild safeguard above
makes the current build command work in either mode; it does not require changing
the Production command just to generate the Preview bundle.

For new Worker Previews, Preview-specific runtime variables, secrets, and bindings
belong in the Wrangler `previews` configuration block. The repository already has
an empty `previews` block in `wrangler.jsonc`.

For this repository, a successful Preview build log must show:

1. `npm run build` completing its Next.js build.
2. The automatic `postbuild` step running `opennextjs-cloudflare build`.
3. `wrangler preview` starting without an entry-point error.
4. A Preview URL for the current PR commit.

Do not merge until the current PR Preview URL has been checked for frontend loading
and backend REST API/WebSocket connectivity.
