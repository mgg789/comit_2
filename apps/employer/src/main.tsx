import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router";
import { MotionRoot } from "@comit/ui-kit";
import "@comit/ui-kit/tokens.css";
import "./index.css";
import App from "./App";
import { SessionProvider } from "./auth/session";

// BASE_URL is the Vite `base` ("/employer/"); the router must strip it so routes stay root-relative.
const basename = import.meta.env.BASE_URL.replace(/\/$/, "");

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter basename={basename}>
      <SessionProvider>
        <MotionRoot>
          <App />
        </MotionRoot>
      </SessionProvider>
    </BrowserRouter>
  </StrictMode>,
);
