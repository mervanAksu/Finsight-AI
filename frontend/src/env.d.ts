// build.mjs'in derleme anında gömdüğü sabitler (esbuild `define`).
declare const __API_BASE__: string;
declare const __DEV_RELOAD__: boolean;

// Yan etkili CSS içe aktarımı (esbuild CSS'i ayrı dosyaya paketler).
declare module "*.css";
