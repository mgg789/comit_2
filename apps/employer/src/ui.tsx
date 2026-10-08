import {
  Children,
  isValidElement,
  useEffect,
  useId,
  useRef,
  useState,
  type ButtonHTMLAttributes,
  type ReactNode,
  type SelectHTMLAttributes,
} from "react";
import { createPortal } from "react-dom";
import { motion } from "framer-motion";
import { asset } from "./runtime";

const icons = {
  vacancies: "6d4cf8ae48179dc57156f25db4b058cc5cb57a8e.svg",
  ai: "268c55656abd4564896f3ed9b7ea042f24cbf9d6.svg",
  resumes: "268c55656abd4564896f3ed9b7ea042f24cbf9d6.svg",
  plus: "43ffb38ed0f5604cad3f1adb9680d6c0b20d69c9.svg",
  send: "2cb013b9ea483d6e784177684fd4c6fbb33f9969.svg",
  candidates: "c56e9cc719a6147a0c25e9bc7877291a90ef720e.svg",
  statistics: "f82aa184ea85eb5960407a62897d24d7084bfd33.svg",
  settings: "944a3f714471682b2ae2b66d8711c46598ca1e19.svg",
  search: "6da721179169b7fd86cc9afd7a125e469ad945bd.svg",
  chevron: "be7a21bfb4b868880613999d5d0bc4a2650413ed.svg",
  "vacancy-chevron": "vacancy-card-chevron.svg",
  edit: "vacancy-edit-pencil.svg",
  bell: "29795c078097fdcbdd8d396900a5dfd4a5309de8.svg",
  message: "message-circle.svg",
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

/** Custom listbox backed by a real select, preserving existing typed change handlers and form values. */
export function Select({
  children,
  className = "",
  triggerIcon = "chevron",
  placement = "bottom",
  ...props
}: SelectHTMLAttributes<HTMLSelectElement> & {
  triggerIcon?: keyof typeof icons;
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
      }>(child) ||
      child.type !== "option"
    )
      return [];
    return [
      {
        value: String(child.props.value ?? ""),
        label: child.props.children,
        disabled: !!child.props.disabled,
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
    const height = Math.min(320, options.length * 44 + 56);
    const above = available < Math.min(height, 180) && rect.top > available;
    if (placement === "right") {
      const width = Math.min(280, window.innerWidth - 16);
      const left = rect.right + 8 + width <= window.innerWidth - 8
        ? rect.right + 8 : Math.max(8, rect.left - width - 8);
      const top = Math.max(8, Math.min(rect.top, window.innerHeight - height - 8));
      setGeometry({ left, top, width, maxHeight: window.innerHeight - top - 8 });
      setActive(Math.max(0, selected));
      setOpen(true);
      return;
    }
    setGeometry({
      left: Math.max(
        8,
        Math.min(rect.left, window.innerWidth - rect.width - 8),
      ),
      top: above ? Math.max(8, rect.top - height - 8) : rect.bottom + 8,
      width: rect.width,
      maxHeight: above
        ? Math.min(height, rect.top - 16)
        : Math.max(80, available),
    });
    setActive(Math.max(0, selected));
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
            className="custom-select-menu"
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
            {options.map((option, index) => (
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
                  {index === selected ? "✓" : ""}
                </span>
                {option.label}
              </button>
            ))}
          </motion.div>,
          anchor.current?.closest(".employer-app") ?? document.body,
        )}
    </div>
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
