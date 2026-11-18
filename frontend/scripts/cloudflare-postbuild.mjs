import { spawnSync } from "node:child_process";

const isWorkersBuild = process.env.WORKERS_CI === "1";
const isOpenNextBuild = process.env.OPENNEXT_CLOUDFLARE_BUILD === "1";

if (!isWorkersBuild || isOpenNextBuild) {
  process.exit(0);
}

const command = process.platform === "win32" ? "npx.cmd" : "npx";
const result = spawnSync(command, ["opennextjs-cloudflare", "build"], {
  stdio: "inherit",
  env: {
    ...process.env,
    OPENNEXT_CLOUDFLARE_BUILD: "1",
  },
});

if (result.error) {
  console.error("Failed to start the OpenNext Cloudflare build:", result.error);
  process.exit(1);
}

process.exit(result.status ?? 1);
