/** Mail-client-safe verification template, based on Figma frame 325:15652. */
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
    { filename: "comit-logo.png", content: readFileSync(new URL("./email-assets/logo.png", import.meta.url)),
      contentType: "image/png", contentDisposition: "inline", cid: "comit-verification-logo" },
    { filename: "comit-background.png", content: readFileSync(new URL("./email-assets/context-background.png", import.meta.url)),
      contentType: "image/png", contentDisposition: "inline", cid: "comit-verification-background" },
  ];
  return attachments;
}

function escapeHtml(value: string): string {
  return value.replace(/[&<>"']/g, character => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[character] ?? character);
}

/** Render a validated OTP and its absolute expiry; decorative CID images have solid-color fallbacks. */
export function verificationHtml(message: VerificationMail, expiry: string): { html: string; attachments: VerificationAttachment[] } {
  const role = message.role === "candidate" ? "кандидата" : "работодателя";
  const code = escapeHtml(message.code);
  const until = escapeHtml(expiry);
  const html = `<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="color-scheme" content="dark"><title>Код входа в Comit</title></head>
<body style="margin:0;padding:0;background-color:#111111;color:#f0f0f0;font-family:Inter,Arial,Helvetica,sans-serif;">
<div style="display:none;max-height:0;overflow:hidden;opacity:0;mso-hide:all;">Код входа в Comit для ${role}. Действителен до ${until}</div>
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" bgcolor="#111111" style="width:100%;background-color:#111111;"><tr><td align="center" style="padding:32px 12px;">
<!--[if mso]><table role="presentation" width="578" cellspacing="0" cellpadding="0" border="0"><tr><td><![endif]-->
<table role="presentation" width="578" cellspacing="0" cellpadding="0" border="0" style="width:100%;max-width:578px;border-collapse:separate;">
<tr><td align="center" bgcolor="#010101" background="cid:comit-verification-background" style="padding:22px 20px 26px;background-color:#010101;background-image:url('cid:comit-verification-background');background-position:center;background-size:cover;border-radius:36px;">
<table role="presentation" width="222" cellspacing="0" cellpadding="0" border="0" style="width:222px;border-collapse:separate;"><tr><td align="center" height="66" bgcolor="#171717" style="height:66px;background-color:#171717;border:1px solid #363636;border-radius:97px;">
<table role="presentation" cellspacing="0" cellpadding="0" border="0"><tr><td style="padding-right:12px;line-height:0;"><img src="cid:comit-verification-logo" width="38" height="38" alt="" style="display:block;border:0;width:38px;height:38px;"></td><td style="font-family:'Moon Walk',Arial,Helvetica,sans-serif;font-size:32px;line-height:38px;color:#f0f0f0;">comit</td></tr></table>
</td></tr></table>
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0"><tr><td height="35" style="height:35px;font-size:0;line-height:0;">&nbsp;</td></tr></table>
<table role="presentation" width="298" cellspacing="0" cellpadding="0" border="0" style="width:100%;max-width:298px;border-collapse:separate;"><tr><td align="center" bgcolor="#ededed" style="padding:8px 12px;border-radius:20px;background-color:#ededed;color:#272930;font-size:20px;font-weight:500;line-height:23px;">Код входа в приложение</td></tr></table>
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0"><tr><td height="25" style="height:25px;font-size:0;line-height:0;">&nbsp;</td></tr></table>
<table role="presentation" width="463" cellspacing="0" cellpadding="0" border="0" style="width:100%;max-width:463px;border-collapse:separate;"><tr><td align="center" bgcolor="#0064b5" style="padding:10px 12px;border-radius:20px;background-color:#0064b5;background-image:linear-gradient(100deg,#0064b5 0%,#00467f 48%,#bfe2ff 100%);color:#f0f0f0;font-family:'Moon Walk',Arial,Helvetica,sans-serif;font-size:42px;font-weight:700;letter-spacing:5px;line-height:51px;white-space:nowrap;">${code}</td></tr></table>
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0"><tr><td height="35" style="height:35px;font-size:0;line-height:0;">&nbsp;</td></tr></table>
<p style="margin:0;font-size:16px;font-weight:600;line-height:24px;color:#f0f0f0;">Код действителен до ${until}</p>
<p style="margin:3px 0 0;font-size:14px;font-weight:600;line-height:20px;color:#9c9c9c;">Письмо отправлено сервисом Comit. Это не реклама</p>
</td></tr></table>
<!--[if mso]></td></tr></table><![endif]-->
<p style="max-width:578px;margin:16px 0 0;color:#9c9c9c;font-size:12px;line-height:18px;">Код для ${role}. Если вы не запрашивали код, проигнорируйте это письмо.</p>
</td></tr></table></body></html>`;
  return { html, attachments: bundledAttachments() };
}
