/** Keep public assets under the configured welcome mount in production builds. */
export const asset = (name: string) => `${import.meta.env.BASE_URL}design/${name}`;

/** Resolve real application mounts; development uses the sibling Vite servers. */
function applicationUrl(override: string | undefined, mount: string, port: number): string {
  if (override?.trim()) return override.trim();
  if (!import.meta.env.DEV) return mount;
  const sibling = new URL(window.location.origin);
  sibling.port = String(port);
  sibling.pathname = mount;
  return sibling.toString();
}

export const candidateUrl = applicationUrl(import.meta.env.VITE_CANDIDATE_URL, "/candidate/", 5173);
export const employerUrl = applicationUrl(import.meta.env.VITE_EMPLOYER_URL, "/employer/", 5174);
/** Exact welcome ribbon assets shared by the landing and public catalog. */
export function WelcomeBackground() { return (<div className="welcome-background" aria-hidden="true">
        <span className="welcome-sweep welcome-sweep-right"><img src={asset("background-sweep-right.png")} alt="" /></span>
        <span className="welcome-sweep welcome-sweep-left"><img src={asset("background-sweep-left.png")} alt="" /></span>
        <span className="welcome-glow welcome-glow-one"><img src={asset("landing-glow-1.svg")} alt="" /></span>
        <span className="welcome-glow welcome-glow-two"><img src={asset("landing-glow-2.svg")} alt="" /></span>
      </div>); }

