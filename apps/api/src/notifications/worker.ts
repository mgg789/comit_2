/** One bounded event-email delivery, sharing SMTP with the existing OTP worker. */
import { MailDeliveryError, type NotificationMailer } from "../auth/mail.js";
import type { NotificationJobStore } from "./store.js";

/** Source authority is refreshed after claim and immediately before the mail transport. */
export class NotificationWorker {
  constructor(private readonly store: NotificationJobStore, private readonly mailer: NotificationMailer) {}

  /** Return false only when no valid deliverable event was available. */
  async processOnce(): Promise<boolean> {
    await this.store.reconcileLikes();
    const claimed = await this.store.claim();
    if (!claimed) return false;
    try {
      const current = await this.store.refresh(claimed.job_id, claimed.lease_token);
      if (!current) return true;
      await this.mailer.sendNotification(current);
      await this.store.finish(current.job_id, current.lease_token);
    } catch (error) {
      const failure = error instanceof MailDeliveryError ? error : new MailDeliveryError("smtp_unavailable", true);
      await this.store.fail(claimed.job_id, claimed.lease_token, failure.code, failure.retryable);
    }
    return true;
  }
}
