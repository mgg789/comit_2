import { useId, type ButtonHTMLAttributes, type ReactNode } from "react";
import { motion } from "framer-motion";
import { asset } from "./runtime";

const icons = {
  vacancies: "6d4cf8ae48179dc57156f25db4b058cc5cb57a8e.svg",
  ai: "268c55656abd4564896f3ed9b7ea042f24cbf9d6.svg",
  candidates: "c56e9cc719a6147a0c25e9bc7877291a90ef720e.svg",
  statistics: "f82aa184ea85eb5960407a62897d24d7084bfd33.svg",
  settings: "944a3f714471682b2ae2b66d8711c46598ca1e19.svg",
  search: "6da721179169b7fd86cc9afd7a125e469ad945bd.svg",
  chevron: "be7a21bfb4b868880613999d5d0bc4a2650413ed.svg",
  bell: "29795c078097fdcbdd8d396900a5dfd4a5309de8.svg",
  logo: "dec04dda54551634f35985932f0a53e8bcda20f5.svg",
  back: "2cb013b9ea483d6e784177684fd4c6fbb33f9969.svg",
  clock: "8af299778072944cf9ce5dadca62d1ec4137e491.svg",
  arrow: "6e1dc9326ee3cba6de25bb4bcafc3823eaaaee71.svg",
} as const;

/** Original exported Figma vector; wrapper preserves the source aspect ratio. */
export function Icon({
  name,
  className = "",
}: {
  name: keyof typeof icons;
  className?: string;
}) {
  return (
    <span className={`icon icon-${name} ${className}`} aria-hidden="true">
      <img src={asset(icons[name])} alt="" />
    </span>
  );
}

/** Refractive backdrop filter and Figma background imagery, isolated from content. */
export function Backdrop() {
  const map = (vertical: boolean) =>
    `data:image/svg+xml,${encodeURIComponent(`<svg xmlns="http://www.w3.org/2000/svg" width="256" height="256"><defs><linearGradient id="g" x2="${vertical ? "0" : "1"}" y2="${vertical ? "1" : "0"}"><stop stop-color="${vertical ? "#000000" : "#00007f"}"/><stop offset=".08" stop-color="${vertical ? "#007f00" : "#7f007f"}"/><stop offset=".92" stop-color="${vertical ? "#007f00" : "#7f007f"}"/><stop offset="1" stop-color="${vertical ? "#00ff00" : "#ff007f"}"/></linearGradient></defs><rect width="256" height="256" fill="url(#g)"/></svg>`)}`;
  return (
    <>
      <svg className="glass-filter" aria-hidden="true">
        <defs>
          <filter
            id="glass-refraction"
            x="0"
            y="0"
            width="100%"
            height="100%"
            colorInterpolationFilters="sRGB"
          >
            <feImage
              href={map(false)}
              preserveAspectRatio="none"
              width="100%"
              height="100%"
              result="horizontal"
            />
            <feImage
              href={map(true)}
              preserveAspectRatio="none"
              width="100%"
              height="100%"
              result="vertical"
            />
            <feComposite
              in="horizontal"
              in2="vertical"
              operator="arithmetic"
              k2="1"
              k3="1"
              result="lens"
            />
            <feGaussianBlur in="lens" stdDeviation="2" result="smooth" />
            <feDisplacementMap
              in="SourceGraphic"
              in2="smooth"
              scale="24"
              xChannelSelector="R"
              yChannelSelector="G"
            />
          </filter>
        </defs>
      </svg>
      <div className="ambient" aria-hidden="true">
        <img
          className="ambient-upper"
          src={asset("b2e5fa1dc7bd97f4194c3281ddf16908ee7734b3.png")}
          alt=""
        />
        <img
          className="ambient-lower"
          src={asset("13441c23b2e09a38d4fef98d24cccd9bf355d639.png")}
          alt=""
        />
        <img
          className="ambient-glow"
          src={asset("9ac3e2c307951c9256502142bc9fbad65b066af3.svg")}
          alt=""
        />
      </div>
    </>
  );
}

/** Animated button with design-system variants and native form semantics. */
export function Action({
  children,
  className = "",
  variant = "glass",
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "glass" | "primary" | "light";
  children: ReactNode;
}) {
  return (
    <button
      {...props}
      className={`action ${variant === "glass" ? "glass" : variant} ${className}`}
    >
      {children}
    </button>
  );
}

/** Section title and back control matching the reference geometry. */
export function PageTitle({
  title,
  back,
  children,
}: {
  title: string;
  back?: () => void;
  children?: ReactNode;
}) {
  return (
    <div className="page-heading">
      {back && (
        <Action className="circle" onClick={back} aria-label="Назад">
          <Icon name="back" />
        </Action>
      )}
      <h1>{title}</h1>
      {children}
    </div>
  );
}

/** Accessible request state; retry never fabricates unavailable data. */
export function State({
  loading,
  error,
  empty,
  retry,
}: {
  loading?: boolean;
  error?: string | null;
  empty?: string;
  retry?: () => void;
}) {
  if (loading)
    return (
      <div className="state glass" role="status">
        <span className="spinner" />
        Загружаем…
      </div>
    );
  return (
    <div className="state glass" role={error ? "alert" : "status"}>
      <p>{error || empty || "Пока ничего нет"}</p>
      {retry && <Action onClick={retry}>Обновить</Action>}
    </div>
  );
}

/** Field label and help remain associated with the real form control. */
export function Field({
  label,
  children,
  hint,
}: {
  label: string;
  children: ReactNode;
  hint?: string;
}) {
  const id = useId();
  return (
    <label className="field">
      <span>{label}</span>
      {children}
      {hint && <small id={id}>{hint}</small>}
    </label>
  );
}

/** Staggered entry for page surfaces; reduced motion is managed at app level. */
export function Reveal({
  children,
  className = "",
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <motion.div
      className={className}
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: -8 }}
      transition={{ duration: 0.28 }}
    >
      {children}
    </motion.div>
  );
}
