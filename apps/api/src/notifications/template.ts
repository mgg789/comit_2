/** Event emails share the verified OTP shell, bundled CID assets and plain-text fallback. */
import { renderMailLayout, escapeMailHtml, mailDigits, type VerificationAttachment } from "../auth/email-template.js";

/** Four notification kinds represented by the product's Figma email frames. */
export type NotificationKind = "monthly_task" | "employer_interview" | "candidate_interview" | "resume_liked";

/** Guarded delivery data: private names/address exist only in the worker's memory. */
export interface NotificationMail {
  email: string;
  job_id: string;
  kind: NotificationKind;
  destination: string;
  company?: string;
  candidate?: string;
  task?: string;
  starts_at?: Date | string;
  time_zone?: string;
}

/** Validate an absolute, credential-free site origin. Production email links require HTTPS. */
export function notificationSiteUrl(value: string | undefined, production = true): URL {
  if (!value) throw new TypeError("notification_site_url_required");
  const url = new URL(value);
  const local = ["localhost", "127.0.0.1", "[::1]"].includes(url.hostname);
  if (url.username || url.password || url.search || url.hash || url.pathname !== "/"
    || (url.protocol !== "https:" && !(url.protocol === "http:" && local && !production))) {
    throw new TypeError("invalid_notification_site_url");
  }
  return url;
}

/** Render all event types without scripts, external images, personal contacts or meeting URLs. */
export function notificationContent(message: NotificationMail): {
  subject: string; text: string; html: string; attachments: VerificationAttachment[];
} {
  const destination = new URL(message.destination);
  if (!["https:", "http:"].includes(destination.protocol) || destination.username || destination.password
    || (destination.protocol === "http:" && !["localhost", "127.0.0.1", "[::1]"].includes(destination.hostname))) {
    throw new TypeError("invalid_notification_destination");
  }
  const clean = (value: string | undefined, fallback: string) => {
    const text = value?.trim() || fallback;
    if (text.length > 300 || /[\u0000-\u001f\u007f]/.test(text)) throw new TypeError("invalid_notification_text");
    return text;
  };
  let subject: string;
  let description: string;
  let button: string | undefined;
  let time: string | undefined;
  let timeDigits: ReturnType<typeof mailDigits> | undefined;
  let weekday: string | undefined;
  switch (message.kind) {
    case "monthly_task":
      subject = "Ежемесячное задание Comit";
      description = `Пройдите ежемесячное задание для повышения рейтинга: ${clean(message.task, "Задание на платформе")}`;
      button = "Пройти задание";
      break;
    case "employer_interview":
      subject = "Собеседование с кандидатом Comit";
      description = `Вы назначили собеседование кандидату ${clean(message.candidate, "на платформе")}. Не пропустите дату и время`;
      break;
    case "candidate_interview":
      subject = "Вам назначили собеседование Comit";
      description = `Вам назначили собеседование в компанию ${clean(message.company, "работодателя")}. Не пропустите дату и время`;
      break;
    case "resume_liked":
      subject = "Ваше резюме понравилось работодателю Comit";
      description = message.company
        ? `Ваше резюме понравилось компании ${clean(message.company, "")}. Узнайте подробности на платформе COMIT`
        : "Ваше резюме понравилось одной из компаний. Узнайте подробности на платформе COMIT";
      button = "В личный кабинет";
      break;
    default: throw new TypeError("invalid_notification_kind");
  }
  if (message.kind === "candidate_interview" || message.kind === "employer_interview") {
    const date = new Date(message.starts_at ?? "");
    if (!Number.isFinite(date.getTime()) || !message.time_zone) throw new TypeError("invalid_interview_time");
    time = new Intl.DateTimeFormat("ru-RU", { weekday: "short", day: "numeric", month: "long", year: "numeric", hour: "2-digit", minute: "2-digit", timeZone: message.time_zone }).format(date);
    timeDigits = mailDigits(new Intl.DateTimeFormat("ru-RU", { hour: "2-digit", minute: "2-digit", timeZone: message.time_zone }).format(date));
    weekday = new Intl.DateTimeFormat("ru-RU", { weekday: "short", timeZone: message.time_zone }).format(date).toUpperCase();
    // Include the zone explicitly: a recipient's local mail client must never silently shift the appointment.
    time += ` (${message.time_zone})`;
  }
  const descriptionHtml = escapeMailHtml(description);
  const action = button
    ? `<a href="${escapeMailHtml(destination.href)}" style="display:inline-block;padding:18px 24px;border:1px solid #656565;border-radius:60px;background-color:#303030;color:#f0f0f0;font-size:20px;font-weight:600;text-decoration:none;">${escapeMailHtml(button)}</a>`
    : `<div aria-label="${escapeMailHtml(time!)}" style="max-width:439px;margin:0 auto;padding:10px 12px;border-radius:20px;background-color:#0064b5;background-image:linear-gradient(100deg,#0064b5,#00467f 48%,#bfe2ff);font-size:42px;font-weight:400;line-height:51px;color:#f0f0f0;"><span style="vertical-align:middle;">${escapeMailHtml(weekday!)} </span>${timeDigits!.html}</div>
<p style="margin:10px 0 0;font-size:14px;line-height:20px;color:#9c9c9c;">${escapeMailHtml(time!)}</p>
<p style="margin:24px 0 0;"><a href="${escapeMailHtml(destination.href)}" style="display:inline-block;padding:18px 24px;border:1px solid #656565;border-radius:60px;background-color:#303030;color:#f0f0f0;font-size:20px;font-weight:600;text-decoration:none;">Открыть собеседование</a></p>`;
  const rendered = renderMailLayout({ title: subject, preview: description, ...(timeDigits ? { attachments: timeDigits.attachments } : {}),
    content: `<p style="margin:0 0 30px;color:#f0f0f0;font-size:20px;font-weight:600;line-height:24px;letter-spacing:-0.6px;">${descriptionHtml}</p>${action}`,
    note: "" });
  return { ...rendered, subject,
    text: `${description}\n\n${time ? `${time}\n\n` : ""}${button ?? "Открыть собеседование"}: ${destination.href}\n\nПисьмо отправлено сервисом Comit. Это не реклама` };
}
