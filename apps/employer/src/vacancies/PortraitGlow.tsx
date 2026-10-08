/**
 * Stand-in for the blurred candidate photo of the design: public projections carry no photos, so a
 * soft colour field derived from the label sits behind the text, with the dark left vignette from the mockup.
 */
export function PortraitGlow({ name }: { name: string }) {
  let hash = 0;
  for (const char of name) hash = (hash * 31 + char.charCodeAt(0)) >>> 0;
  const hue = hash % 360;
  return (
    <span aria-hidden className="pointer-events-none absolute inset-0">
      <span
        className="absolute top-[-8%] right-[-20%] h-[115%] w-[85%] rounded-full opacity-80 blur-[0.9375rem]"
        style={{ background: `radial-gradient(circle at 40% 40%, hsl(${hue} 65% 58%), hsl(${(hue + 40) % 360} 55% 30%) 70%, transparent)` }}
      />
      <span className="absolute inset-y-0 left-0 w-[70%] bg-gradient-to-r from-black/85 to-transparent" />
    </span>
  );
}
