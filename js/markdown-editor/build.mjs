import { build } from "esbuild";
import { gzipSync } from "node:zlib";
import { readFileSync, existsSync, writeFileSync } from "node:fs";
import { dirname, resolve, join } from "node:path";
const outfile =
  "../../python/djust/components/static/djust_components/markdown-visual.js";
const result = await build({
  entryPoints: ["visual.js"],
  bundle: true,
  minify: true,
  format: "iife",
  target: ["es2020"],
  outfile,
  metafile: true,
  // Keep Markdown hard-break spaces in escaped strings, not trailing whitespace.
  supported: { "template-literal": false },
  legalComments: "none",
  banner: {
    js: "/* Optional djust Markdown visual engine. Third-party licenses: markdown-visual.LICENSE.txt */",
  },
});
const packages = new Map();
for (const input of Object.keys(result.metafile.inputs)) {
  if (!input.includes("node_modules/")) continue;
  let directory = dirname(resolve(input));
  while (
    !existsSync(join(directory, "package.json")) &&
    directory !== dirname(directory)
  )
    directory = dirname(directory);
  const pkg = JSON.parse(readFileSync(join(directory, "package.json"), "utf8"));
  if (packages.has(pkg.name)) continue;
  const file = [
    "LICENSE",
    "LICENSE.md",
    "LICENSE.txt",
    "license",
    "license.md",
  ].find((name) => existsSync(join(directory, name)));
  if (!file) throw new Error(`Missing license text for ${pkg.name}`);
  packages.set(
    pkg.name,
    `${pkg.name} ${pkg.version}\n${readFileSync(join(directory, file), "utf8")}`,
  );
}
writeFileSync(
  outfile.replace(".js", ".LICENSE.txt"),
  Array.from(packages)
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([, text]) => text)
    .join("\n\n---\n\n"),
);
process.stdout.write(
  `Optional visual bundle: ${gzipSync(readFileSync(outfile)).length} bytes gzip\n`,
);
