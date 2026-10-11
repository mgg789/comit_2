import { useId, useState, type ReactNode } from "react";
import { motion, useReducedMotion } from "framer-motion";

/** Animate both disclosure directions while keeping collapsed content unfocusable. */
export function ResumeAccordion({ title, children, bodyClassName = "" }: {
  title: string; children: ReactNode; bodyClassName?: string;
}) {
  const [open, setOpen] = useState(false);
  const id = useId();
  const reducedMotion = useReducedMotion();
  return <section className="discovery-accordion" data-open={open}>
    <button type="button" className="discovery-accordion-trigger" aria-expanded={open}
      aria-controls={id} onClick={() => setOpen(value => !value)}>
      {title}<span className="discovery-accordion-toggle" aria-hidden="true"><span>+</span></span>
    </button>
    <motion.div id={id} inert={!open} initial={false}
      animate={{ height: open ? "auto" : 0, opacity: open ? 1 : 0 }}
      transition={{ duration: reducedMotion ? 0 : 0.25, ease: "easeOut" }}
      className="discovery-accordion-content">
      <div className={`discovery-accordion-body ${bodyClassName}`}>{children}</div>
    </motion.div>
  </section>;
}
