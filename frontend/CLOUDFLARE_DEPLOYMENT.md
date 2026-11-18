# Cloudflare Workers deployment

This frontend uses OpenNext to build the Next.js application for Cloudflare Workers.
Keep the standard `build` script as `next build` for the existing Vercel workflow.

Configure the Cloudflare Workers Git build with:

- Root directory: `frontend`
- Build command: `npm run cf:build`
- Deploy command: `npx wrangler preview`

The Cloudflare-specific build script runs `opennextjs-cloudflare build`.
The Wrangler configuration is maintained in `wrangler.jsonc`, and the OpenNext
configuration is maintained in `open-next.config.ts`.

Do not promote a preview build to production until its preview URL has been
checked for frontend loading and API/WebSocket connectivity. Keep the existing
Vercel deployment available as a fallback.


## Existing Workers Builds projects: one-time Preview migration

If this Worker was connected to Workers Builds before Worker Previews were introduced, enabling Preview Builds and filling in the Preview tab is not sufficient. Cloudflare keeps the legacy Preview model until the one-time migration is completed.

In the Cloudflare dashboard, open the Worker and go to **Settings → Builds**. If the **Set up Worker Previews** banner is present, choose **Set up**, review the Preview command (it must invoke `npx wrangler preview`), and complete **Switch to Worker Previews**. This is a one-time, non-reversible switch from the legacy Preview model. Do not change the Production build or deploy commands as part of this migration.

Also confirm that Preview Builds are enabled under **Settings → Build → Branch control**, and that at least one Preview host is enabled under the Worker's **Domains** settings (for example, the `workers.dev` Preview URL). Without a Preview host, the build can succeed but no Preview URL will be available.

For this repository, a successful Preview build log must show both:

1. `npm run cf:build` (OpenNext build)
2. `npx wrangler preview` (Preview deployment)

The pull request comment or build output must provide a Preview URL for the current commit. A build that runs `npm run build` / `next build`, or a manually deployed Production build, does not verify the Cloudflare Preview path. Do not merge until the current PR Preview URL has been checked for frontend loading and backend REST API/WebSocket connectivity.
