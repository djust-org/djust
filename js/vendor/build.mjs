import { build } from "esbuild";
import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import { copyFileSync, existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { gzipSync } from "node:zlib";
import { APPS, BUNDLES } from "./bundles.mjs";

const LICENSE_FILES = ["LICENSE", "LICENSE.md", "LICENSE.txt", "license", "license.md"];
const sri = (buf) => "sha384-" + createHash("sha384").update(buf).digest("base64");
const staticPath = (app, rel) => join(APPS[app], "static", rel);
const purl = (name, version) => `pkg:npm/${name.replace(/^@/, "%40")}@${version}`;

function ensureDir(file) {
  mkdirSync(dirname(file), { recursive: true });
}

// A package's root is the nearest package.json that names it: nested ones such as
// highlight.js/es/package.json ({"type": "module"}) only set the module format.
function hasName(dir) {
  const file = join(dir, "package.json");
  return existsSync(file) && typeof JSON.parse(readFileSync(file, "utf8")).name === "string";
}

function packageDir(input) {
  let dir = dirname(resolve(input));
  while (!hasName(dir) && dir !== dirname(dir)) dir = dirname(dir);
  return dir;
}

function describe(dir) {
  const pkg = JSON.parse(readFileSync(join(dir, "package.json"), "utf8"));
  if (typeof pkg.license !== "string" || !pkg.license.trim())
    throw new Error(`${pkg.name}@${pkg.version} has no SPDX "license" field`);
  const file = LICENSE_FILES.find((name) => existsSync(join(dir, name)));
  if (!file) throw new Error(`Missing license text for ${pkg.name}`);
  return { name: pkg.name, version: pkg.version, license: pkg.license, text: readFileSync(join(dir, file), "utf8") };
}

function fileEntry(app, rel, extra = {}) {
  return { path: rel, integrity: sri(readFileSync(staticPath(app, rel))), ...extra };
}

function reportSize(bundle, outfile) {
  process.stdout.write(`${bundle.asset}: ${gzipSync(readFileSync(outfile)).length} bytes gzip\n`);
}

async function buildEsbuild(bundle) {
  const outfile = staticPath(bundle.app, bundle.out);
  const result = await build({
    entryPoints: [bundle.entry],
    bundle: true,
    minify: true,
    format: bundle.format,
    target: ["es2020"],
    outfile,
    metafile: true,
    // Keep Markdown hard-break spaces in escaped strings, not trailing whitespace.
    supported: { "template-literal": false },
    legalComments: "none",
    banner: { js: `/* djust vendored asset "${bundle.asset}". Third-party licenses: see the adjacent .LICENSE.txt */` },
  });
  const packages = new Map();
  for (const input of Object.keys(result.metafile.inputs)) {
    if (!input.includes("node_modules/")) continue;
    const info = describe(packageDir(input));
    packages.set(info.name, info);
  }
  const sorted = [...packages.values()].sort((a, b) => a.name.localeCompare(b.name));
  const licenseRel = bundle.out.replace(/\.m?js$/, ".LICENSE.txt");
  // Names and license texts only: a served file must not publish the version list.
  writeFileSync(
    staticPath(bundle.app, licenseRel),
    sorted.map((p) => `${p.name}\n${p.text}`).join("\n\n---\n\n"),
  );
  const files = [fileEntry(bundle.app, bundle.out)];
  for (const item of [...(bundle.extra ?? []), ...(bundle.variants ?? [])]) {
    const target = staticPath(bundle.app, item.out);
    ensureDir(target);
    // copyFileSync throws on a missing source, so a theme absent from the pinned
    // highlight.js release fails the build instead of being dropped.
    copyFileSync(item.from, target);
    files.push(fileEntry(bundle.app, item.out, item.variant ? { variant: item.variant } : {}));
  }
  reportSize(bundle, outfile);
  return {
    files,
    license_file: licenseRel,
    packages: sorted.map((p) => ({ purl: purl(p.name, p.version), license: p.license })),
  };
}

function buildTailwind(bundle) {
  const outfile = staticPath(bundle.app, bundle.out);
  ensureDir(outfile);
  execFileSync("npx", ["tailwindcss", "-c", bundle.config, "-i", bundle.input, "-o", outfile, "--minify"], {
    stdio: "inherit",
  });
  const packages = bundle.packages.map((name) => describe(join("node_modules", name)));
  reportSize(bundle, outfile);
  return {
    files: [fileEntry(bundle.app, bundle.out)],
    packages: packages.map((p) => ({ purl: purl(p.name, p.version), license: p.license })),
  };
}

const manifests = Object.fromEntries(Object.keys(APPS).map((app) => [app, {}]));
for (const bundle of BUNDLES) {
  manifests[bundle.app][bundle.asset] =
    bundle.kind === "tailwind" ? buildTailwind(bundle) : await buildEsbuild(bundle);
}
for (const [app, assets] of Object.entries(manifests)) {
  const sortedAssets = Object.fromEntries(Object.entries(assets).sort(([a], [b]) => a.localeCompare(b)));
  writeFileSync(join(APPS[app], "djust_assets.json"), JSON.stringify({ schema: 1, assets: sortedAssets }, null, 2) + "\n");
}
