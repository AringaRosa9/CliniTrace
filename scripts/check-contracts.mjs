import { readFileSync } from "node:fs";
import { execFileSync } from "node:child_process";
const files = [
  "packages/contracts/openapi/v1.json",
  "packages/contracts/openapi/runtime.json",
  "packages/contracts/json-schema/outpatient-1.0.0.json",
  "packages/contracts/json-schema/laboratory-1.0.0.json",
  "frontend/src/lib/api/schema.d.ts",
];
const before = files.map((f) => readFileSync(f, "utf8"));
execFileSync("pnpm", ["contracts"], { stdio: "inherit" });
if (files.some((f, i) => readFileSync(f, "utf8") !== before[i])) {
  console.error(
    "Generated contracts drifted. Run pnpm contracts and include the updated artifacts.",
  );
  process.exit(1);
}
