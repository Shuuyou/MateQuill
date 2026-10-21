import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import ts from "typescript";

const source = await readFile(
  new URL("../lib/server/publicOrigin.ts", import.meta.url),
  "utf8",
);
const output = ts.transpileModule(source, {
  compilerOptions: {
    target: ts.ScriptTarget.ES2022,
    module: ts.ModuleKind.ES2022,
  },
}).outputText;
const { canonicalRedirect, publicOrigin } = await import(
  `data:text/javascript;base64,${Buffer.from(output).toString("base64")}`
);
const origin = "https://matequill.example:8443";
assert.equal(
  canonicalRedirect(
    "http://127.0.0.1:3003/",
    "127.0.0.1:3003",
    null,
    undefined,
  ),
  null,
);
assert.equal(
  canonicalRedirect(
    "http://localhost:7860/a",
    "matequill.example:8443",
    "https",
    origin,
  ),
  null,
);
assert.equal(
  canonicalRedirect(`${origin}/a`, "matequill.example:8443", null, origin),
  null,
);
assert.equal(
  canonicalRedirect(
    "http://localhost:7860/api/auth/me",
    "localhost:7860",
    "https",
    origin,
  ).href,
  `${origin}/api/auth/me`,
);
assert.equal(
  canonicalRedirect(
    "http://matequill.example:8443/a?x=1",
    "matequill.example:8443",
    "http",
    origin,
  ).href,
  `${origin}/a?x=1`,
);
assert.equal(
  canonicalRedirect(
    "http://evil.example//attacker.example/?next=https://attacker.example",
    "evil.example",
    "https",
    origin,
  ).origin,
  origin,
);
assert.equal(
  canonicalRedirect(
    "http://localhost/a",
    "matequill.example:8443",
    "https,http",
    origin,
  ).href,
  `${origin}/a`,
);
for (const invalid of [
  "http://example.test",
  "https://user:pass@example.test",
  "https://example.test/path",
  "https://example.test?target=evil",
  "https://example.test/#evil",
])
  assert.throws(() => publicOrigin(invalid));
console.log(
  "Canonical HTTPS origin: 12 routing and configuration checks passed.",
);
