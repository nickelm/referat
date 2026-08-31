// Bundle the extension into dist/extension.js.
//
// `vscode` is provided by the extension host at runtime and must stay external;
// bundling it is the standard way to get one file to load instead of a
// node_modules tree, which is also what step 12 will package.
import * as esbuild from "esbuild";

const watch = process.argv.includes("--watch");

const options = {
  entryPoints: ["src/extension.ts"],
  bundle: true,
  outfile: "dist/extension.js",
  external: ["vscode"],
  format: "cjs",
  platform: "node",
  target: "node20",
  sourcemap: true,
  minify: !watch,
  logLevel: "info",
};

// The two markers the .vscode/tasks.json background problem matcher waits on.
// Without them VS Code never decides the prelaunch task has finished, and F5
// hangs instead of starting the extension host.
//
// The third line is the errors, restated one per line. esbuild's own pretty
// output spreads a single error over a "[ERROR] message" line, a blank line
// and an indented "file:line:col:" line, and a VS Code problem pattern has to
// capture the file and the message together -- multi-line patterns match only
// consecutive lines, so the blank one defeats them. Rather than depend on an
// external matcher extension, print the compact form the matcher can read.
// esbuild's own output stays: logLevel "info" still prints it, with the frame.
const notify = {
  name: "notify",
  setup(build) {
    build.onStart(() => console.log("[watch] build started"));
    build.onEnd((result) => {
      for (const e of result.errors) {
        const l = e.location;
        const where = l ? `${l.file}:${l.line}:${l.column}` : "src/extension.ts:1:0";
        console.log(`[watch] error ${where}: ${e.text}`);
      }
      console.log("[watch] build finished");
    });
  },
};

if (watch) {
  const ctx = await esbuild.context({ ...options, plugins: [notify] });
  await ctx.watch();
} else {
  await esbuild.build(options);
}
