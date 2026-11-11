import React from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
// Fonts are bundled locally (no internet needed at runtime).
// Inter ≈ the chat's interface/user text; Source Serif 4 ≈ Claude's reply text.
import "@fontsource/inter/400.css";
import "@fontsource/inter/600.css";
import "@fontsource/source-serif-4/400.css";
import "@fontsource/source-serif-4/400-italic.css";
import "@fontsource/source-serif-4/600.css";
import "./styles.css";

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
