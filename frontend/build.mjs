// FinSight AI arayüzü derleme betiği (esbuild JS API).
//
//   node build.mjs            → dist/ (üretim derlemesi, küçültülmüş + sourcemap)
//   node build.mjs --serve    → http://127.0.0.1:5173 (izleme + otomatik yenileme)
//
// API adresi derleme anında gömülür: FINSIGHT_API_BASE ortam değişkeni.
// Varsayılan: üretimde "" (aynı sunucu), geliştirmede "http://127.0.0.1:8000".
import { copyFile, mkdir, rm } from "node:fs/promises";
import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath } from "node:url";

// createRequire NODE_PATH'i dikkate alır (bulut ortamında paketler /opt/npm-tools altındadır).
const require = createRequire(import.meta.url);
const esbuild = require("esbuild");

const root = path.dirname(fileURLToPath(import.meta.url));
const dist = path.join(root, "dist");
const serve = process.argv.includes("--serve");
const apiBase = process.env.FINSIGHT_API_BASE ?? (serve ? "http://127.0.0.1:8000" : "");
const nodePaths = (process.env.NODE_PATH ?? "").split(path.delimiter).filter(Boolean);

/** index.html'i dist/ altına kopyalar (her derlemenin sonunda). */
const copyStatic = {
  name: "copy-static",
  setup(build) {
    build.onEnd(async (result) => {
      if (result.errors.length > 0) return;
      await mkdir(dist, { recursive: true });
      await copyFile(path.join(root, "index.html"), path.join(dist, "index.html"));
      if (serve) console.log(`[${new Date().toLocaleTimeString("tr-TR")}] derlendi`);
    });
  },
};

/** @type {import("esbuild").BuildOptions} */
const options = {
  absWorkingDir: root,
  entryPoints: [{ in: "src/main.tsx", out: "app" }],
  outdir: path.join(dist, "assets"),
  bundle: true,
  format: "esm",
  target: ["es2020", "chrome100", "firefox100", "safari15"],
  jsx: "automatic",
  minify: !serve,
  sourcemap: true,
  legalComments: "none",
  loader: { ".svg": "file", ".woff": "file", ".woff2": "file" },
  nodePaths,
  define: {
    "process.env.NODE_ENV": JSON.stringify(serve ? "development" : "production"),
    __API_BASE__: JSON.stringify(apiBase),
    __DEV_RELOAD__: JSON.stringify(serve),
  },
  logLevel: "info",
  plugins: [copyStatic],
};

if (serve) {
  const ctx = await esbuild.context(options);
  await ctx.watch();
  const { host, port } = await ctx.serve({ servedir: dist, host: "127.0.0.1", port: 5173 });
  console.log(`FinSight AI geliştirme sunucusu: http://${host}:${port}  (API: ${apiBase || "aynı sunucu"})`);
} else {
  await rm(dist, { recursive: true, force: true });
  await esbuild.build(options);
  console.log(`dist/ hazır (API: ${apiBase || "aynı sunucu"})`);
}
