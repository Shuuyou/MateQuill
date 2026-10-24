import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import vm from "node:vm";
import { webcrypto } from "node:crypto";

// Exercise the production API client with transport failures, not a replacement
// implementation. No live credentials or generation service are required.
const require = createRequire(import.meta.url);
const ts = require("typescript");
const source = readFileSync(new URL("../lib/api.ts", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText;
const storage = new Map();
const exported = {};
let scope = "user-a:tenant-a:store-a";
let transport;
vm.runInNewContext(compiled, {
  exports: exported,
  require: (name) => {
    assert.equal(name, "./workspace");
    return { sessionScopeKey: () => scope, workspaceFetch: (...args) => transport(...args) };
  },
  sessionStorage: {
    getItem: (key) => storage.get(key) ?? null,
    setItem: (key, value) => storage.set(key, value),
    removeItem: (key) => storage.delete(key),
  },
  crypto: webcrypto,
  setTimeout,
  Map,
  JSON,
  Error,
});
const response = (value) => ({ ok: true, json: async () => value });
const task = { product: "Test product", platform: "xiaohongshu", scene: "Store demonstration", mode: "local" };
let posts = [];
let reads = 0;
transport = async (url, options) => {
  if (options?.method === "POST") {
    posts.push(JSON.parse(options.body));
    return response({ id: "job-1" });
  }
  assert.equal(url, "/api/jobs/job-1");
  // Polling tolerates transient transport failures; only a streak of four
  // consecutive failures rejects the wait.
  if (++reads <= 4) throw new Error("Disconnected while polling");
  return response({ status: "succeeded", result: { run_id: "result-1" } });
};
let queued = "";
await assert.rejects(exported.runTask(task, (id) => { queued = id; }), /Disconnected/);
assert.equal(queued, "job-1");
assert.equal(reads, 4, "polling gives up only after four consecutive failures");
assert.equal((await exported.runTask(task)).run_id, "result-1");
assert.equal(posts.length, 1, "polling recovery must reuse the queued job");

storage.clear(); posts = [];
transport = async (_url, options) => {
  if (options?.method === "POST") {
    posts.push(JSON.parse(options.body));
    if (posts.length === 1) throw new Error("Lost queue response");
    return response({ id: "job-2" });
  }
  return response({ status: "succeeded", result: { run_id: "result-2" } });
};
await assert.rejects(exported.runTask(task), /Lost queue response/);
await exported.runTask(task);
assert.equal(posts[0].idempotency_key, posts[1].idempotency_key, "uncertain queue response must retain the idempotency key");

storage.clear(); posts = []; reads = 0;
transport = async (_url, options) => {
  if (options?.method === "POST") {
    posts.push(JSON.parse(options.body));
    return response({ id: `job-retry-${posts.length}` });
  }
  if (++reads === 1) return response({ status: "failed", error: { message: "Generation failed" } });
  return response({ status: "succeeded", result: { run_id: "result-retry" } });
};
await assert.rejects(exported.runTask(task), exported.GenerationJobError);
await exported.runTask(task);
assert.notEqual(posts[0].idempotency_key, posts[1].idempotency_key, "terminal failure must allow a new job");

storage.clear(); posts = [];
transport = async (_url, options) => {
  if (options?.method === "POST") {
    posts.push(JSON.parse(options.body));
    return response({ id: `scoped-${posts.length}` });
  }
  throw new Error("Keep job pending");
};
await assert.rejects(exported.runTask(task), /Keep job pending/);
scope = "user-b:tenant-b:store-b";
await assert.rejects(exported.runTask(task), /Keep job pending/);
assert.equal(posts.length, 2, "a different workspace must not resume another account's job");
assert.notEqual(posts[0].idempotency_key, posts[1].idempotency_key);
console.log("PASS: polling recovery, queue idempotency, terminal retry, workspace isolation");
