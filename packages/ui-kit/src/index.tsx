/// <reference path="./assets.d.ts" />
/**
 * @comit/ui-kit — shared components for all four apps (dark "glass" theme of the COMIT Figma file).
 * Styling relies on Tailwind utilities generated through theme.css; import tokens.css + theme.css in each app.
 */
export { cx } from "./cx";
export { Icon, ICONS, type IconName, type IconProps } from "./icon";
export {
  Button,
  IconButton,
  Spinner,
  type ButtonProps,
  type ButtonShape,
  type ButtonSize,
  type ButtonVariant,
  type IconButtonProps,
} from "./controls";
export { Dialog } from "./dialog";
export { AnimatePresence, DURATION, EASE, MotionRoot, REVEAL, Reveal, SPRING, StaggerItem, StaggerList, TAP, fadeUp, motion } from "./motion";
export { MessageBubble, MessageComposer } from "./composer";
export { ACTION_ROW_BUTTON, ActionRow, Avatar, Backdrop, Chip, CountBadge, Logo, LogoTile, Wordmark, MatchBadge, Panel, ProgressBar, StateMessage, StatusDot, type ChipTone } from "./display";
export {
  SelectField,
  TextArea,
  TextField,
  type SelectFieldProps,
  type SelectOption,
  type TextAreaProps,
  type TextFieldProps,
} from "./fields";
export { default as agentOrbUrl } from "./assets/brand/agent-orb.png";
export { default as plusDiscUrl } from "./assets/brand/plus-disc.svg";
export { default as qgrammIconUrl } from "./assets/brand/qgramm.svg";
export { default as droidjeIconUrl } from "./assets/brand/droidje.svg";
