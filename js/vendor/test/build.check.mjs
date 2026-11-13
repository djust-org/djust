import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createHash } from "node:crypto";

const root = new URL("../../../python/djust/", import.meta.url);
const read = (p) => readFileSync(new URL(p, root));
const manifest = (app) => JSON.parse(read(`${app}/djust_assets.json`));
const sri = (buf) => "sha384-" + createHash("sha384").update(buf).digest("base64");

test("every declared vendored file matches its integrity", () => {
  for (const app of ["components", "admin_ext"]) {
    const { schema, assets } = manifest(app);
    assert.equal(schema, 1);
    for (const [name, asset] of Object.entries(assets)) {
      for (const file of asset.files) {
        const staticDir = app === "components" ? "components/static/" : "admin_ext/static/";
        assert.equal(sri(read(staticDir + file.path)), file.integrity, `${name} ${file.path}`);
      }
      for (const pkg of asset.packages) assert.match(pkg.purl, /^pkg:npm\/.+@[^@]+$/);
    }
  }
});

test("djust's assets are all declared", () => {
  assert.deepEqual(Object.keys(manifest("components").assets).sort(), ["highlight.js", "xterm"]);
  assert.deepEqual(Object.keys(manifest("admin_ext").assets), ["admin-css"]);
});

test("served license files carry no version numbers", () => {
  const text = read("components/static/djust_components/vendor/xterm/xterm.LICENSE.txt").toString();
  assert.doesNotMatch(text, /^@?[\w./-]+ \d+\.\d+\.\d+$/m);
  assert.match(text, /^@xterm\/xterm$/m);
});

test("scoped purls are percent-encoded", () => {
  const purls = manifest("components").assets["xterm"].packages.map((p) => p.purl);
  assert.ok(purls.some((p) => p.startsWith("pkg:npm/%40xterm/xterm@")), purls.join("\n"));
});

test("every direct vendor dependency is pinned to one exact version", () => {
  const pkg = JSON.parse(readFileSync(new URL("../package.json", import.meta.url)));
  for (const [name, spec] of Object.entries({ ...pkg.dependencies, ...pkg.devDependencies }))
    assert.match(spec, /^\d+\.\d+\.\d+$/, `${name}@${spec}`);
  const lock = JSON.parse(readFileSync(new URL("../package-lock.json", import.meta.url)));
  for (const [name, spec] of Object.entries(pkg.dependencies))
    assert.equal(lock.packages[`node_modules/${name}`].version, spec, name);
});
