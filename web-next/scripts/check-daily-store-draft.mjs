import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import vm from "node:vm";

const require = createRequire(import.meta.url);
const ts = require("typescript");
const source = readFileSync(new URL("../lib/assignmentDraft.ts", import.meta.url), "utf8");
const exports = {};
let scope = "seller-a:tenant-a:store-a";
let transport;
vm.runInNewContext(ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText, {
  exports,
  require: () => ({ sessionScopeKey: () => scope, workspaceRequest: (...args) => transport(...args) }),
  setTimeout: (callback) => setTimeout(callback, 0),
  Date, Map, Set, Promise, Error,
});

let posts = 0;
let reads = 0;
let release;
transport = async (_path, body) => {
  if (body !== undefined) { posts++; return { status: "preparing", day: "2026-10-24" }; }
  reads++;
  if (reads === 1) {
    await new Promise((resolve) => { release = resolve; });
    return { status: "idle", day: "2026-10-24" };
  }
  return { id: "daily-a", status: "ready", day: "2026-10-24", conversation_id: "actual-conversation" };
};
const first = exports.prepareDailyStoreDraft(scope);
const duplicate = exports.prepareDailyStoreDraft(scope);
assert.equal(first, duplicate);
release();
await first;
assert.equal(posts, 1);
assert.equal(exports.getDailyDraftState(scope).value.conversation_id, "actual-conversation");
await exports.prepareDailyStoreDraft(scope);
assert.equal(posts, 1, "a second home arrival reads the persisted result without paying again");

let lostPosts = 0;
scope = "seller-a:tenant-a:store-lost-response";
transport = async (_path, body) => {
  if (body !== undefined) { lostPosts++; throw new Error("POST response lost"); }
  return lostPosts ? { id: "recovered", day: "2026-10-24", status: "ready", conversation_id: "recovered-conversation" }
    : { day: "2026-10-24", status: "idle" };
};
await exports.prepareDailyStoreDraft(scope);
assert.equal(lostPosts, 1);
await exports.prepareDailyStoreDraft(scope, false, true);
assert.equal(lostPosts, 1, "lost POST recovery only reads server state");
assert.equal(exports.getDailyDraftState(scope).value.status, "ready");

transport = async (path, body) => {
  if (path.endsWith("/adopt")) throw new Error("This card expired");
  assert.equal(body, undefined, "adoption error recovers by GET, never by another prepare");
  return { id: "recovered", day: "2026-10-24", status: "stale" };
};
await assert.rejects(exports.actOnDailyStoreDraft(scope, "adopt"), /expired/);
assert.equal(exports.getDailyDraftState(scope).value.status, "stale");

let dismissed = false;
transport = async (path, body) => {
  if (path.endsWith("/dismiss")) {
    dismissed = true;
    return { id: "recovered", day: "2026-10-24", status: "dismissed" };
  }
  if (body !== undefined) throw new Error("A dismissed daily card must not regenerate");
  return { id: "recovered", day: "2026-10-24", status: "dismissed" };
};
await exports.actOnDailyStoreDraft(scope, "dismiss");
await exports.prepareDailyStoreDraft(scope);
assert.ok(dismissed);
assert.equal(exports.getDailyDraftState(scope).value.status, "dismissed");

const previous = scope;
scope = "seller-b:tenant-b:store-b";
transport = async () => { throw new Error("stale scope should not reach transport"); };
await exports.prepareDailyStoreDraft(previous);
assert.equal(exports.getDailyDraftState(scope), undefined);
console.log("Daily store checks passed: shared requests, persisted readiness, lost POST read-only recovery, expiry, dismissal and scope isolation.");
