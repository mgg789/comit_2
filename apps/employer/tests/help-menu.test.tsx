import assert from "node:assert/strict";
import { test } from "node:test";
import { renderToStaticMarkup } from "react-dom/server";
import { AppShellHeader } from "../../../packages/ui-kit/src/app-shell";

test("both account roles share the public help link without interrupting their current page", () => {
  for (const role of ["candidate", "employer"]) {
    const props = { brandHref: "/", brandLabel: "Comit", navigationLabel: role, items: [], selected: "", navigate: () => {}, email: "synthetic@example.invalid", toggleMenu: () => {}, menuContent: <button>Sign out</button> };
    const open = renderToStaticMarkup(<AppShellHeader {...props} menu />);
    assert.match(open, /href="\/help\/"[^>]*target="_blank"[^>]*rel="noopener noreferrer"[^>]*>Справка<\/a>/);
    assert.doesNotMatch(renderToStaticMarkup(<AppShellHeader {...props} menu={false} />), /href="\/help\/"/);
  }
});
