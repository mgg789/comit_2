/** Global glass experiment; ?glass=classic provides a reversible per-tab rollback. */
export type GlassEffect = "classic" | "frosted" | "refractive";

/** Choose supported glass without allocating canvases, WebGL contexts or animation loops. */
export function glassEffect(search: string, userAgent: string, supportsSvg: boolean): GlassEffect {
  if (new URLSearchParams(search).get("glass") === "classic") return "classic";
  const chromium = /Chrome|Chromium|Edg\//.test(userAgent) && !/Firefox|FxiOS|CriOS|EdgiOS/.test(userAgent);
  return chromium && supportsSvg ? "refractive" : "frosted";
}
