import { test } from "node:test";
import assert from "node:assert/strict";
import { addPackage } from "../packages.mjs";

const pkg = (name, version) => ({ name, version, license: "MIT", text: "" });

test("a package bundled twice at one version is recorded once", () => {
  const packages = new Map();
  addPackage(packages, pkg("lib", "1.0.0"), "asset");
  addPackage(packages, pkg("lib", "1.0.0"), "asset");
  assert.deepEqual([...packages.keys()], ["lib"]);
});

test("a package bundled at two versions fails the build instead of dropping one", () => {
  const packages = new Map();
  addPackage(packages, pkg("lib", "1.0.0"), "markdown-visual");
  assert.throws(
    () => addPackage(packages, pkg("lib", "2.0.0"), "markdown-visual"),
    { message: "lib bundled at two versions (1.0.0, 2.0.0) in markdown-visual" },
  );
});
