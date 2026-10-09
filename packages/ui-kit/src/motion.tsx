import { MotionConfig, motion, type HTMLMotionProps, type Transition, type Variants } from "framer-motion";
import type { ReactNode } from "react";

/**
 * Motion presets for every app. AGENTS.md timing: interactions 120–180 ms, reveals 250–350 ms.
 * `MotionRoot` honours the OS «reduce motion» setting, so all of these collapse to instant changes there.
 */
export const DURATION = { fast: 0.15, reveal: 0.3 } as const;

/** Soft deceleration used for every reveal (easeOutQuint-like). */
export const EASE: [number, number, number, number] = [0.22, 1, 0.36, 1];

export const REVEAL: Transition = { duration: DURATION.reveal, ease: EASE };
export const TAP: Transition = { duration: DURATION.fast, ease: EASE };
/** Spring for physical things: swipe cards, the sliding nav pill. */
export const SPRING: Transition = { type: "spring", stiffness: 420, damping: 36, mass: 0.9 };

export const fadeUp: Variants = {
  hidden: { opacity: 0, y: 16 },
  show: { opacity: 1, y: 0, transition: REVEAL },
};

const staggerContainer = (step: number, delay: number): Variants => ({
  hidden: {},
  show: { transition: { staggerChildren: step, delayChildren: delay } },
});

/** Wrap the app once: reduced-motion support for all framer animations inside. */
export function MotionRoot({ children }: { children: ReactNode }) {
  return <MotionConfig reducedMotion="user">{children}</MotionConfig>;
}

/** One block that fades and rises into place on mount. */
export function Reveal({ delay = 0, children, ...rest }: HTMLMotionProps<"div"> & { delay?: number }) {
  return (
    <motion.div initial="hidden" animate="show" variants={fadeUp} transition={{ ...REVEAL, delay }} {...rest}>
      {children}
    </motion.div>
  );
}

/** List whose items reveal one after another (`step` seconds apart); pair with StaggerItem. */
export function StaggerList({ step = 0.04, delay = 0, children, ...rest }: HTMLMotionProps<"ul"> & { step?: number; delay?: number }) {
  return (
    <motion.ul initial="hidden" animate="show" variants={staggerContainer(step, delay)} {...rest}>
      {children}
    </motion.ul>
  );
}

/** Item of a StaggerList; `layout` keeps siblings gliding when items are added or removed. */
export function StaggerItem({ children, ...rest }: HTMLMotionProps<"li">) {
  return (
    <motion.li variants={fadeUp} layout="position" {...rest}>
      {children}
    </motion.li>
  );
}

export { AnimatePresence, motion } from "framer-motion";
