import { build } from "esbuild";
await build({
  entryPoints: ["src/index.jsx"],
  bundle: true,
  minify: true,
  format: "iife",
  globalName: "dsherpStudio",
  outfile: "../frappe_app/dsherp_bridge/public/dist/studio.js",
  define: { "process.env.NODE_ENV": '"production"' },
  target: ["chrome120"],
  legalComments: "eof",
});
await build({
  entryPoints: ["src/desk-entry.js"],
  bundle: true,
  minify: true,
  format: "iife",
  outfile: "../frappe_app/dsherp_bridge/public/dist/context-agent.js",
  define: { "process.env.NODE_ENV": '"production"' },
  target: ["chrome120"],
  legalComments: "eof",
});
await build({
  entryPoints: ["src/agent-workbench-entry.js"],
  bundle: true,
  minify: true,
  format: "iife",
  globalName: "dsherpAgentWorkbench",
  outfile: "../frappe_app/dsherp_bridge/public/dist/agent-workbench.js",
  define: { "process.env.NODE_ENV": '"production"' },
  target: ["chrome120"],
  legalComments: "eof",
});
