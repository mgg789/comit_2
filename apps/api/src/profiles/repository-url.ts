/** Normalize public repository suffixes for claim binding; the provider validates its host/path allowlist. */
export function canonicalRepositoryClaimUrl(value: string | null | undefined): string | null {
  if (!value) return null;
  try {
    const url = new URL(value);
    if (url.protocol !== "https:" || url.username || url.password || url.search || url.hash || url.port) return null;
    return `https://${url.hostname.toLowerCase()}${url.pathname.replace(/\/$/, "").replace(/\.git$/, "")}`;
  } catch { return null; }
}
