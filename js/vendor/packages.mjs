// Collect the npm packages an esbuild bundle pulled in, keyed by name.
// The SBOM lists one version per name, so a second, different version of
// the same package would otherwise be dropped silently: fail the build.
export function addPackage(packages, info, asset) {
  const seen = packages.get(info.name);
  if (seen && seen.version !== info.version) {
    throw new Error(`${info.name} bundled at two versions (${seen.version}, ${info.version}) in ${asset}`);
  }
  packages.set(info.name, info);
}
