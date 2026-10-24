import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import vm from "node:vm";

const require = createRequire(import.meta.url);
const ts = require("typescript");
const source = readFileSync(new URL("../lib/assignmentDraft.ts", import.meta.url), "utf8");
const exports = {};
let scope = "sales-a:company-a:store-a";
let transport;
vm.runInNewContext(ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText, {
  exports,
  require: () => ({ sessionScopeKey: () => scope, workspaceRequest: (...args) => transport(...args) }),
  setTimeout: (callback) => setTimeout(callback, 0),
  Date, Map, Set, Promise, Error,
});
const assignment = { id: "assignment-a", campaign_version: 1 };
let posts = 0;
let reads = 0;
let release;
transport = async (_path, body) => {
  if (body !== undefined) {
    posts++;
    await new Promise((resolve) => { release = resolve; });
    return { status: "preparing" };
  }
  reads++;
  return { status: "ready", result: { run_id: "real-run", status: "ready" } };
};
const first = exports.prepareAssignmentDraft(scope, assignment);
const duplicate = exports.prepareAssignmentDraft(scope, assignment);
assert.equal(first, duplicate, "two mounts share one request");
release();
await first;
assert.equal(posts, 1);
assert.equal(reads, 1);
await exports.prepareAssignmentDraft(scope, assignment);
assert.equal(posts, 1, "navigation does not generate again");
assert.equal(exports.getDraftState(exports.draftKey(scope, assignment)).value.result.run_id, "real-run");

transport = async (_path, body) => {
  assert.equal(body.refresh, true);
  posts++;
  return { status: "failed", error: { message: "Provider unavailable" } };
};
await exports.prepareAssignmentDraft(scope, assignment, true);
await exports.prepareAssignmentDraft(scope, assignment);
assert.equal(posts, 2, "terminal failures never auto retry");
assert.equal(exports.getDraftState(exports.draftKey(scope, assignment)).error, "Provider unavailable");

transport = async (_path, body) => {
  if (body !== undefined) { posts++; return { status: "preparing" }; }
  return { status: "needs_revision", result: { run_id: "revision-run", status: "needs_revision" } };
};
await exports.prepareAssignmentDraft(scope, assignment, true);
assert.equal(exports.getDraftState(exports.draftKey(scope, assignment)).value.status, "needs_revision");
assert.equal(posts, 3, "explicit retry alone starts new generation");

const otherScope = "sales-b:company-b:store-b";
scope = otherScope;
assert.equal(exports.getDraftState(exports.draftKey(otherScope, assignment)), undefined, "drafts remain workspace isolated");
await exports.prepareAssignmentDraft("old-scope", assignment);
assert.equal(posts, 3, "stale workspace cannot start generation");

const lostAssignment = { id: "lost-post", campaign_version: 1 };
let lostPosts = 0;
let recoveryReads = 0;
transport = async (_path, body) => {
  if (body !== undefined) { lostPosts++; throw new Error("POST response lost"); }
  recoveryReads++;
  return { status: "idle", job_id: null };
};
await exports.prepareAssignmentDraft(scope, lostAssignment);
await exports.prepareAssignmentDraft(scope, lostAssignment);
assert.equal(lostPosts, 1, "lost response never automatically resubmits");
await exports.prepareAssignmentDraft(scope, lostAssignment, false, true);
assert.equal(recoveryReads, 1, "idle recovery stops after one read");
assert.equal(exports.getDraftState(exports.draftKey(scope, lostAssignment)).loading, false);
transport = async (_path, body) => {
  assert.ok(body, "idle restart requires an explicit POST action");
  return { status: "ready", result: { run_id: "recovered-run", status: "ready" } };
};
await exports.prepareAssignmentDraft(scope, lostAssignment, true);
assert.equal(exports.getDraftState(exports.draftKey(scope, lostAssignment)).value.result.run_id, "recovered-run");
console.log("Assignment draft checks passed: deduplication, navigation, polling, failure, revision, scope, idle and lost POST recovery.");
