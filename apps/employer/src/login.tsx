import type { AccountView } from "@comit/shared";
import { EmailLogin } from "@comit/ui-kit/email-login";
import { api, errorText } from "./runtime";

/** Bind the shared sign-in surface to employer-only session APIs. */
export function Login({ onLogin }: { onLogin: (account: AccountView) => void }) {
  return (
    <EmailLogin
      requestCode={(email) => api.requestLoginCode("employer", email)}
      verifyCode={async (request) => (await api.verifyLoginCode("employer", request)).account}
      onLogin={onLogin}
      errorText={errorText}
      brandSubtitle="Для бизнеса"
    />
  );
}
