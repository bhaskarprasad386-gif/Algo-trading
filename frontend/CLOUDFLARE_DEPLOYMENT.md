# Cloudflare Workers deployment

This frontend uses OpenNext to build the Next.js application for Cloudflare Workers.
Keep the standard `build` script as `next build` for the existing Vercel workflow.

Configure Cloudflare Workers Builds with separate production and preview commands:

- Root directory: `frontend`
- Build command (both Production and Previews Base): `npm run cf:build`
- Production Deploy command: `npx opennextjs-cloudflare deploy`
- Preview command (Previews Base): `npx wrangler preview`

The Cloudflare-specific build script runs `opennextjs-cloudflare build`, which
creates the `.open-next/worker.js` entry point. A plain `npm run build`
only runs Next.js and does not create that Cloudflare Worker entry point.

Do not put `npx wrangler preview` in the Production Deploy command. Production
must use the OpenNext deploy command. Keep the existing Vercel build command
(`npm run build`) unchanged in the Vercel configuration.

The Wrangler configuration is maintained in `wrangler.jsonc`, and the OpenNext
configuration is maintained in `open-next.config.ts`.

Do not promote a preview build to production until its preview URL has been
checked for frontend loading and API/WebSocket connectivity. Keep the existing
Vercel deployment available as a fallback.

## Existing Workers Builds projects: one-time Preview migration

If this Worker was connected to Workers Builds before Worker Previews were introduced,
enabling Preview Builds and filling in the Preview tab is not sufficient. Cloudflare
may retain the legacy Preview model until the one-time migration is completed.

In the Cloudflare dashboard, open the Worker and go to **Settings → Builds**. If the
**Set up Worker Previews** banner is present, choose **Set up**, review the Preview
command, and complete **Switch to Worker Previews**. This is a one-time,
non-reversible switch from the legacy Preview model. Do not change the Production
build or deploy commands as part of this migration.

Also confirm that Preview Builds are enabled and that at least one Preview host is
enabled under the Worker's **Domains** settings (for example, the `workers.dev`
Preview URL). Without a Preview host, the build can succeed but no Preview URL
will be available.

For this repository, a successful Preview build log must show:

1. `npm run cf:build` (OpenNext build)
2. `npx wrangler preview` (Preview command)

The pull request comment or build output must provide a Preview URL for the current
commit. A build that runs `npm run build` / `next build`, or a manually deployed
Production build, does not verify the Cloudflare Preview path. Do not merge until
the current PR Preview URL has been checked for frontend loading and backend REST
API/WebSocket connectivity.
