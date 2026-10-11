/** Mail-client-safe verification template, based on Figma frame 556:16766. */
import { readFileSync } from "node:fs";
import type { VerificationMail } from "./mail.js";

/** Trusted bundled raster asset; Nodemailer emits each buffer as an inline MIME part. */
export interface VerificationAttachment {
  filename: string;
  content: Buffer;
  contentType: "image/png";
  contentDisposition: "inline";
  cid: string;
}

let attachments: VerificationAttachment[] | undefined;

function bundledAttachments(): VerificationAttachment[] {
  attachments ??= [
    { filename: "comit-wordmark.png", content: readFileSync(new URL("./email-assets/wordmark.png", import.meta.url)),
      contentType: "image/png", contentDisposition: "inline", cid: "comit-verification-logo" },
    { filename: "comit-background.png", content: readFileSync(new URL("./email-assets/context-background.png", import.meta.url)),
      contentType: "image/png", contentDisposition: "inline", cid: "comit-verification-background" },
  ];
  return attachments;
}

/** Escape untrusted names and labels before interpolating into mail HTML or attribute values. */
export function escapeMailHtml(value: string): string {
  return value.replace(/[&<>"']/g, character => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[character] ?? character);
}

/** Render a bounded shared dark email shell with local MIME assets and table layout. */
export function renderMailLayout(value: { title: string; preview: string; content: string; note: string; attachments?: VerificationAttachment[] }): { html: string; attachments: VerificationAttachment[] } {
  const html = `<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="color-scheme" content="dark"><title>${escapeMailHtml(value.title)}</title></head>
<body style="margin:0;padding:0;background-color:#111111;color:#f0f0f0;font-family:Inter,Arial,Helvetica,sans-serif;">
<div style="display:none;max-height:0;overflow:hidden;opacity:0;mso-hide:all;">${escapeMailHtml(value.preview)}</div>
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" bgcolor="#111111"><tr><td align="center" style="padding:32px 12px;">
<!--[if mso]><table role="presentation" width="578" cellspacing="0" cellpadding="0" border="0"><tr><td><![endif]-->
<table role="presentation" width="578" cellspacing="0" cellpadding="0" border="0" style="width:100%;max-width:578px;border-collapse:separate;"><tr><td align="center" bgcolor="#010101" background="cid:comit-verification-background" style="padding:22px 20px 26px;background-color:#010101;background-image:url('cid:comit-verification-background');background-position:center;background-size:cover;border-radius:36px;">
<table role="presentation" width="222" cellspacing="0" cellpadding="0" border="0" style="width:222px;border-collapse:separate;"><tr><td align="center" height="66" bgcolor="#171717" style="height:66px;background-color:#171717;border:1px solid #363636;border-radius:97px;">
<img src="cid:comit-verification-logo" width="160" height="38" alt="Comit" style="display:block;border:0;width:160px;height:38px;"></td></tr></table>
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0"><tr><td height="35" style="height:35px;font-size:0;line-height:0;">&nbsp;</td></tr></table>
${value.content}
<p style="margin:30px 0 0;font-size:14px;font-weight:600;line-height:20px;color:#9c9c9c;">Письмо отправлено сервисом Comit. Это не реклама</p>
</td></tr></table><!--[if mso]></td></tr></table><![endif]-->
${value.note ? `<p style="max-width:578px;margin:16px 0 0;color:#9c9c9c;font-size:12px;line-height:18px;">${escapeMailHtml(value.note)}</p>` : ""}
</td></tr></table></body></html>`;
  return { html, attachments: [...bundledAttachments(), ...(value.attachments ?? [])] };
}

/** Render Moon Walk digits as small bundled MIME PNGs: email clients need no web-font support. */
export function mailDigits(value: string): { html: string; attachments: VerificationAttachment[] } {
  if (!/^[0-9:]{1,8}$/.test(value)) throw new TypeError("invalid_mail_digits");
  const assets = [...new Set(value)].map(character => {
    const name = character === ":" ? "colon" : character;
    return { filename: `comit-digit-${name}.png`, content: readFileSync(new URL(`./email-assets/glyph-${name}.png`, import.meta.url)),
      contentType: "image/png" as const, contentDisposition: "inline" as const, cid: `comit-digit-${name}` };
  });
  const html = [...value].map(character => {
    const asset = assets.find(item => item.cid === `comit-digit-${character === ":" ? "colon" : character}`)!;
    // PNG IHDR provides the generated high-density raster width; render at its logical size.
    const width = Math.round(asset.content.readUInt32BE(16) / 3);
    return `<img src="cid:${asset.cid}" alt="${character}" width="${width}" height="51" style="display:inline-block;border:0;vertical-align:middle;width:${width}px;height:51px;">`;
  }).join("");
  return { html, attachments: assets };
}

/** Render a six-digit OTP with absolute UTC expiry, matching the Figma title/code layout. */
export function verificationHtml(message: VerificationMail, expiry: string): { html: string; attachments: VerificationAttachment[] } {
  if (!/^\d{6}$/.test(message.code) || !["candidate", "employer"].includes(message.role)) throw new TypeError("invalid verification mail");
  const digits = mailDigits(message.code);
  const role = message.role === "candidate" ? "кандидата" : "работодателя";
  return renderMailLayout({ attachments: digits.attachments, title: "Код входа в Comit", preview: `Код входа в Comit для ${role}. Действителен до ${expiry}`,
    content: `<table role="presentation" width="298" cellspacing="0" cellpadding="0" border="0" style="width:100%;max-width:298px;border-collapse:separate;"><tr><td align="center" bgcolor="#ededed" style="padding:8px 12px;border-radius:20px;color:#272930;font-size:20px;font-weight:500;line-height:23px;">Код входа в приложение</td></tr></table>
<p aria-label="${escapeMailHtml(message.code)}" style="margin:25px auto;max-width:439px;padding:10px 12px;border-radius:20px;background-color:#0064b5;background-image:linear-gradient(100deg,#0064b5 0%,#00467f 48%,#bfe2ff 100%);color:#f0f0f0;font-family:Arial,Helvetica,sans-serif;font-size:0;line-height:51px;white-space:nowrap;">${digits.html}</p>
<p style="margin:0;font-size:20px;font-weight:600;line-height:24px;letter-spacing:-0.6px;color:#f0f0f0;">Код действует ограниченное количество времени</p>
<p style="margin:3px 0 0;font-size:14px;line-height:20px;color:#9c9c9c;">Код действителен до ${escapeMailHtml(expiry)}</p>`,
    note: `Код для ${role}. Если вы не запрашивали код, проигнорируйте это письмо.` });
}
