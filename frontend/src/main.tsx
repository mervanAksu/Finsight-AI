// Giriş noktası.
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import "./styles.css";

const container = document.getElementById("root");
if (container) {
  createRoot(container).render(
    <StrictMode>
      <App />
    </StrictMode>,
  );
}

// Geliştirme sunucusunda (npm run dev) dosya değişince sayfayı yenile.
if (__DEV_RELOAD__ && typeof EventSource !== "undefined") {
  new EventSource("/esbuild").addEventListener("change", () => window.location.reload());
}
