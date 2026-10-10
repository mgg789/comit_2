import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "@comit/ui-kit/tokens.css";
import "@comit/ui-kit/design.css";
import "./index.css";
import "./showcase.css";
import App from "./App";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
