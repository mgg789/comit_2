/** Support-console configuration: operator email and access key live only in env. */
export interface SupportConfig {
  /** Exact normalized operator email; comparisons are constant-time. */
  email: string;
  /** Long random secret; entered in the console login form alongside the email. */
  accessKey: string;
  /** Session lifetime in days; matches the account session window. */
  sessionTtlDays: number;
}

/**
 * Load operator credentials without defaults: a fully unset pair disables the
 * console, a partially set pair is a loud misconfiguration. Never log values.
 */
export function loadSupportConfig(env: NodeJS.ProcessEnv = process.env): SupportConfig | null {
  const email = env.SUPPORT_EMAIL?.trim().toLowerCase() ?? "";
  const accessKey = env.SUPPORT_ACCESS_KEY?.trim() ?? "";
  if (!email && !accessKey) return null;
  if (!email || accessKey.length < 16 || accessKey.length > 200 || !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) {
    throw new Error("support_config_invalid");
  }
  return { email, accessKey, sessionTtlDays: 7 };
}
