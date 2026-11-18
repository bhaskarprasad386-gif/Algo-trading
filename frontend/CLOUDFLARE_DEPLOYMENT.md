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
