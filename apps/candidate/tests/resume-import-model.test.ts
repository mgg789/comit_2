import assert from "node:assert/strict";
import { test } from "node:test";
import { mergeResumeSuggestions } from "../src/resume-import-model";

test("import preserves existing edits and trusted identity while filling empty declarations", () => {
  const current = { display_name: "Existing", fsp_id: "verified-id", experience: [{ title: "Existing work" }], city: "" };
  const result = mergeResumeSuggestions(current, { display_name: "Imported", city: "Москва", description: "Parsed text", experience: [{ title: "Imported work" }] });
  assert.deepEqual(result, { ...current, city: "Москва", description: "Parsed text" });
  assert.equal(current.city, "");
  assert.deepEqual(mergeResumeSuggestions({ experience: [] }, { experience: [{ title: "Imported work" }] }).experience, [{ title: "Imported work" }]);
});
