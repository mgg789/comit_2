import { rejectionReasonCategories, workingMillisecondsBetween, type RejectionReasonCategory, type RecruitmentDeadline } from "@comit/shared";
import {
  Children,
  isValidElement,
  useEffect,
  useId,
  useRef,
  useState,
  type ButtonHTMLAttributes,
  type ComponentPropsWithRef,
  type ReactNode,
  type Ref,
  type SelectHTMLAttributes,
} from "react";
import { createPortal } from "react-dom";
import { motion } from "framer-motion";
import {
  ArrowUpRight,
  Check,
  Copy,
  Heart,
  Info,
  MessageCircle,
  Pencil,
  ScrollText,
  UsersRound,
  X,
  type LucideIcon,
} from "lucide-react";
import { designAsset as asset, type DesignAssetName } from "./design-assets";

/**
 * Single-colour glyphs exported from Figma. Rendered as CSS masks so they take
 * `currentColor` (active pills, light buttons) without per-context image filters.
 */
const glyphs = {
  login: "public-login.svg",
  vacancies: "6d4cf8ae48179dc57156f25db4b058cc5cb57a8e.svg",
  plus: "43ffb38ed0f5604cad3f1adb9680d6c0b20d69c9.svg",
  send: "2cb013b9ea483d6e784177684fd4c6fbb33f9969.svg",
  back: "2cb013b9ea483d6e784177684fd4c6fbb33f9969.svg",
  statistics: "f82aa184ea85eb5960407a62897d24d7084bfd33.svg",
  settings: "944a3f714471682b2ae2b66d8711c46598ca1e19.svg",
  search: "6da721179169b7fd86cc9afd7a125e469ad945bd.svg",
  chevron: "be7a21bfb4b868880613999d5d0bc4a2650413ed.svg",
  "vacancy-chevron": "vacancy-card-chevron.svg",
  "chevron-wide": "chevron-wide.svg",
  bell: "29795c078097fdcbdd8d396900a5dfd4a5309de8.svg",
  logo: "dec04dda54551634f35985932f0a53e8bcda20f5.svg",
  clock: "8af299778072944cf9ce5dadca62d1ec4137e491.svg",
  at: "c179aee8cef2e1b481a0ab48e2c6a5af19c286b9.svg",
  "clear-conversation": "clear-conversation.svg",
  "usage-stats": "usage-stats.svg",
  reasoning: "reasoning.svg",
  "attached-file": "attached-file.svg",
  "radar-remove": "radar-remove.svg",
} as const satisfies Record<string, DesignAssetName>;

/** Glyphs the design takes from lucide; stroke icons in `currentColor`. */
const lucide = {
  resumes: ScrollText,
  ai: ScrollText,
  candidates: UsersRound,
  edit: Pencil,
  message: MessageCircle,
  copy: Copy,
  check: Check,
  heart: Heart,
  "heart-filled": Heart,
  close: X,
  info: Info,
} as const satisfies Record<string, LucideIcon>;

/** Every icon name: a Figma mask, a lucide glyph, or the light arrow disc of primary actions. */
export type IconName = keyof typeof glyphs | keyof typeof lucide | "arrow";

/** Design icon; the `.icon` wrapper keeps per-context sizing in CSS, colour comes from `color`. */
export function Icon({
  name,
  className = "",
}: {
  name: IconName;
  className?: string;
}) {
  let glyph: ReactNode;
  if (name === "arrow")
    glyph = (
      <span className="icon-disc">
        <ArrowUpRight strokeWidth={2.5} />
      </span>
    );
  else if (name in lucide) {
    const Glyph = lucide[name as keyof typeof lucide];
    glyph = <Glyph strokeWidth={2} fill={name === "heart-filled" ? "currentColor" : "none"} />;
  } else {
    const url = `url("${asset(glyphs[name as keyof typeof glyphs])}")`;
    glyph = (
      <span
        className="icon-mask"
        style={{ maskImage: url, WebkitMaskImage: url }}
      />
    );
  }
  return (
    <span className={`icon icon-${name} ${className}`} aria-hidden="true">
      {glyph}
    </span>
  );
}

/** Custom listbox backed by a real select, preserving existing typed change handlers and form values. */
export function Select({
  children,
  className = "",
  triggerIcon = "chevron",
  placement = "bottom",
  ...props
}: SelectHTMLAttributes<HTMLSelectElement> & {
  triggerIcon?: IconName;
  placement?: "bottom" | "right";
}) {
  const native = useRef<HTMLSelectElement>(null);
  const anchor = useRef<HTMLButtonElement>(null);
  const panel = useRef<HTMLDivElement>(null);
  const id = useId();
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const [geometry, setGeometry] = useState({
    left: 0,
    top: 0,
    width: 0,
    maxHeight: 0,
  });
  const options = Children.toArray(children).flatMap((child) => {
    if (
      !isValidElement<{
        value?: string | number;
        children?: ReactNode;
        disabled?: boolean;
        hidden?: boolean;
      }>(child) ||
      child.type !== "option"
    )
      return [];
    return [
      {
        value: String(child.props.value ?? ""),
        label: child.props.children,
        // A hidden option is the trigger placeholder: never listed, never reachable by keyboard.
        hidden: !!child.props.hidden,
        disabled: !!child.props.disabled || !!child.props.hidden,
      },
    ];
  });
  const selected = options.findIndex(
    (option) =>
      option.value === String(props.value ?? props.defaultValue ?? ""),
  );
  function show() {
    if (props.disabled || !anchor.current) return;
    const rect = anchor.current.getBoundingClientRect();
    const available = window.innerHeight - rect.bottom - 12;
    const unit = Number.parseFloat(getComputedStyle(document.documentElement).fontSize) || 1;
    const canvas = document.createElement("canvas");
    const context = canvas.getContext("2d");
    if (context) context.font = getComputedStyle(anchor.current).font;
    const longest = Math.max(0, ...Array.from(native.current?.options || []).map(option => context?.measureText(option.text).width || 0));
    const width = Math.min(window.innerWidth - 16, 560 * unit, Math.max(rect.width, longest + 76 * unit));
    const height = Math.min(320, options.length * 44 + 56);
    const above = available < Math.min(height, 180) && rect.top > available;
    if (placement === "right") {
      const left = rect.right + 8 + width <= window.innerWidth - 8
        ? rect.right + 8 : Math.max(8, rect.left - width - 8);
      const top = Math.max(8, Math.min(rect.top, window.innerHeight - height - 8));
      setGeometry({ left, top, width, maxHeight: window.innerHeight - top - 8 });
      setActive(selected >= 0 && !options[selected]?.hidden ? selected : Math.max(0, options.findIndex((option) => !option.disabled)));
      setOpen(true);
      return;
    }
    setGeometry({
      left: Math.max(
        8,
        Math.min(rect.left, window.innerWidth - width - 8),
      ),
      top: above ? Math.max(8, rect.top - height - 8) : rect.bottom + 8,
      width,
      maxHeight: above
        ? Math.min(height, rect.top - 16)
        : Math.max(80, available),
    });
    setActive(selected >= 0 && !options[selected]?.hidden ? selected : Math.max(0, options.findIndex((option) => !option.disabled)));
    setOpen(true);
  }
  function choose(index: number) {
    const option = options[index];
    if (!option || option.disabled || !native.current) return;
    native.current.value = option.value;
    native.current.dispatchEvent(new Event("change", { bubbles: true }));
    setOpen(false);
    anchor.current?.focus();
  }
  useEffect(() => {
    if (!open) return;
    panel.current?.focus();
    const outside = (event: PointerEvent) => {
      if (
        event.target instanceof Node &&
        !panel.current?.contains(event.target) &&
        !anchor.current?.contains(event.target)
      )
        setOpen(false);
    };
    const close = () => setOpen(false);
    document.addEventListener("pointerdown", outside);
    window.addEventListener("resize", close);
    return () => {
      document.removeEventListener("pointerdown", outside);
      window.removeEventListener("resize", close);
    };
  }, [open]);
  return (
    <div className={`custom-select ${className}`}>
      <select
        {...props}
        ref={native}
        tabIndex={-1}
        aria-hidden="true"
        className="custom-select-native"
      >
        {children}
      </select>
      <button
        ref={anchor}
        type="button"
        className="custom-select-trigger"
        disabled={props.disabled}
        aria-label={props["aria-label"]}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={open ? id : undefined}
        onClick={() => (open ? setOpen(false) : show())}
        onKeyDown={(event) => {
          if (["ArrowDown", "ArrowUp", "Enter", " "].includes(event.key)) {
            event.preventDefault();
            show();
          }
        }}
      >
        <span>{options[selected]?.label ?? options[0]?.label}</span>
        <Icon name={triggerIcon} />
      </button>
      {open &&
        createPortal(
          <motion.div
            ref={panel}
            id={id}
            className="custom-select-menu comit-design"
            role="listbox"
            aria-label={props["aria-label"]}
            aria-activedescendant={`${id}-${active}`}
            tabIndex={-1}
            initial={{ opacity: 0, y: -6, scale: 0.97 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            transition={{ duration: 0.18, ease: [0.22, 1, 0.36, 1] }}
            style={geometry}
            onKeyDown={(event) => {
              if (event.key === "Escape") {
                event.preventDefault();
                setOpen(false);
                anchor.current?.focus();
              }
              if (event.key === "Tab") {
                setOpen(false);
                const controls = Array.from(
                  document.querySelectorAll<HTMLElement>(
                    'button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), a[href], [tabindex="0"]',
                  ),
                ).filter(
                  (control) =>
                    control.getClientRects().length > 0 &&
                    !control.closest(".custom-select-menu") &&
                    !control.closest('[inert], [aria-hidden="true"]') &&
                    getComputedStyle(control).visibility !== "hidden" &&
                    control.tabIndex >= 0,
                );
                const current = anchor.current
                  ? controls.indexOf(anchor.current)
                  : -1;
                const next =
                  current >= 0
                    ? controls[current + (event.shiftKey ? -1 : 1)]
                    : undefined;
                if (next) {
                  event.preventDefault();
                  next.focus();
                }
              }
              if (event.key === "ArrowDown" || event.key === "ArrowUp") {
                event.preventDefault();
                const direction = event.key === "ArrowDown" ? 1 : -1;
                let next = active;
                for (let i = 0; i < options.length; i++) {
                  next = (next + direction + options.length) % options.length;
                  if (!options[next]?.disabled) break;
                }
                setActive(next);
                document
                  .getElementById(`${id}-${next}`)
                  ?.scrollIntoView({ block: "nearest" });
              }
              if (event.key === "Enter" || event.key === " ") {
                event.preventDefault();
                choose(active);
              }
              if (event.key === "Home" || event.key === "End") {
                event.preventDefault();
                setActive(event.key === "Home" ? 0 : options.length - 1);
              }
            }}
          >
            <div className="custom-select-caption">
              {props["aria-label"] || "Выберите вариант"}
            </div>
            {options.map((option, index) => option.hidden ? null : (
              <button
                key={`${option.value}-${index}`}
                id={`${id}-${index}`}
                type="button"
                role="option"
                aria-selected={index === selected}
                disabled={option.disabled}
                tabIndex={-1}
                className={index === active ? "is-active" : ""}
                onPointerMove={() => setActive(index)}
                onClick={() => choose(index)}
              >
                <span className="custom-select-check">
                  {index === selected && <Icon name="check" />}
                </span>
                {option.label}
              </button>
            ))}
          </motion.div>,
          anchor.current?.closest(".employer-app, .candidate-app") ?? document.body,
        )}
    </div>
  );
}

/** Refractive backdrop filter and Figma background imagery, isolated from content. */
export function Backdrop({ imagery = true }: { imagery?: boolean } = {}) {
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
              scale="42"
              xChannelSelector="R"
              yChannelSelector="G"
            />
          </filter>
        </defs>
      </svg>
      {imagery && <div className="ambient" aria-hidden="true">
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
      </div>}
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

export { designAsset } from "./design-assets";

/** Employer's existing tag group; variants reuse the platform palette, with optional pill geometry. */
export function Tags({ values, variant = "light", shape = "rounded", className = "" }: {
  values: readonly string[]; variant?: "light" | "blue" | "muted"; shape?: "rounded" | "pill"; className?: string;
}) {
  return <div className={`tags ${className}`.trim()}>{values.filter(Boolean).map((value, index) => <span key={`${value}-${index}`} className={`tag ${variant === "light" ? "" : variant} ${shape === "pill" ? "pill" : ""}`.trim()}>{value}</span>)}</div>;
}

/** Native checkbox sharing the proven login appearance; accepts standard input props, including refs. */
export function Checkbox({ className = "", ...props }: Omit<ComponentPropsWithRef<"input">, "type">) {
  return <input {...props} type="checkbox" className={`comit-checkbox ${className}`} />;
}

/** The employer vacancy card navigation glyph, shared by candidate offer cards. */
export function VacancyCardArrow() {
  return <span className="card-arrow" aria-hidden="true"><Icon name="vacancy-chevron" /></span>;
}

/** Existing employer inline search, shared across vacancy and candidate lists; submission never reloads the page. */
export function InlineSearch({ value, onChange, onSubmit, label, placeholder, inputRef, className = "" }: {
  value: string;
  onChange: (value: string) => void;
  onSubmit?: () => void;
  label: string;
  placeholder?: string;
  inputRef?: Ref<HTMLInputElement>;
  className?: string;
}) {
  return <form className={`discovery-search ${className}`} role="search" onSubmit={event => { event.preventDefault(); onSubmit?.(); }}>
    <input ref={inputRef} aria-label={label} placeholder={placeholder || label} value={value} onChange={event => onChange(event.target.value)} />
    <button aria-label="Найти" type="submit"><Icon name="search" /></button>
  </form>;
}

const rejectionLabels: Record<RejectionReasonCategory, string> = { technical_skills: "Профессиональные навыки", experience: "Опыт", communication: "Общение", salary: "Зарплатные ожидания", work_format: "Формат работы", location: "Местоположение", other: "Другая причина" };
/** Shared detailed feedback form; categories identify aggregate topics and do not reveal employer identity. */
export function RejectionFeedback({ reason, setReason, categories, setCategories, disabled = false }: { reason: string; setReason: (value: string) => void; categories: RejectionReasonCategory[]; setCategories: (value: RejectionReasonCategory[]) => void; disabled?: boolean }) {
  const helpId = useId();
  return <fieldset className="rejection-feedback" disabled={disabled}><legend>Причины отказа</legend><Field label="Подробная обратная связь"><textarea name="reason" aria-label="Подробная обратная связь" value={reason} onChange={event => setReason(event.target.value)} required minLength={80} maxLength={4000} aria-describedby={helpId} /></Field><p className="muted" id={helpId}>Объясните, каких навыков или опыта не хватило и что поможет улучшить результат. Не менее 80 символов и 10 слов.</p><div className="rejection-categories">{rejectionReasonCategories.map(category => <label key={category}><Checkbox checked={categories.includes(category)} onChange={event => setCategories(event.target.checked ? [...categories, category] : categories.filter(value => value !== category))} />{rejectionLabels[category]}</label>)}</div><p className="muted">Выберите хотя бы одну причину. Обратная связь поможет платформе подготовить обезличенные рекомендации.</p></fieldset>;
}

/** Display the server-owned weekday clock; this presentation never changes a hiring state. */
export function RecruitmentClock({ deadline }: { deadline?: RecruitmentDeadline | null }) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => { const timer = setInterval(() => setNow(Date.now()), 60_000); return () => clearInterval(timer); }, []);
  if (!deadline) return null;
  if (deadline.stage === "interview_wait") return null;
  if (!deadline.due_at) return null;
  let remaining = 0;
  try { remaining = workingMillisecondsBetween(new Date(now), deadline.due_at); } catch { return null; }
  const minutes = Math.ceil(remaining / 60_000), days = Math.floor(minutes / 1440), hours = Math.floor(minutes % 1440 / 60);
  const weekday = new Date(now + 3 * 3_600_000).getUTCDay();
  const label = deadline.stage === "initial" ? "Свяжитесь в чате, назначьте собеседование или примите решение" : deadline.stage === "conversation" ? "Назначьте собеседование или примите решение" : "Примите решение после собеседования";
  return <aside className="recruitment-deadline" aria-label="Срок следующего действия"><strong>{remaining > 0 ? `Осталось: ${days} д. ${String(hours).padStart(2, "0")}:${String(minutes % 60).padStart(2, "0")} рабочего времени` : "Срок действия истёк. Обновляем состояние предложения."}</strong><p>{label}. Выходные не учитываются.{[0, 6].includes(weekday) ? " Таймер приостановлен до понедельника." : ""}</p><small>До {new Intl.DateTimeFormat("ru-RU", { dateStyle: "medium", timeStyle: "short", timeZone: deadline.time_zone }).format(new Date(deadline.due_at))} · Москва</small></aside>;
}
