import assert from "node:assert/strict";
import { test } from "node:test";
import { systemNotificationPath } from "../src/system-navigation";

test("system notifications keep navigation inside known application routes", () => {
  for (const path of ["/resume", "/settings", "/statistics", "/offers", "/chat/process-123", "/vacancy/123/resumes"]) assert.equal(systemNotificationPath(path), path);
  for (const path of ["https://evil.invalid", "//evil.invalid", "/\\evil", "/chat/a?redirect=evil", "/legal/private", "/../settings"]) assert.equal(systemNotificationPath(path), null);
});
