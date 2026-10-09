import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { MotionRoot } from "@comit/ui-kit";
import "@comit/ui-kit/tokens.css";
import "./index.css";
import App from "./App";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <MotionRoot>
      <App />
    </MotionRoot>
  </StrictMode>,
);
