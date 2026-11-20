import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";
import { tr } from "@/lib/i18n/tr";

// Phase 5 closed vocabularies, parsed from the backend models so a value
// added there without a Turkish label here fails CI.
const BACKEND = path.resolve(__dirname, "../../../../backend");

function textChoices(file: string, className: string): string[] {
  const source = readFileSync(path.join(BACKEND, file), "utf8");
  const start = source.indexOf(`class ${className}(models.TextChoices)`);
  expect(start, `${className} in ${file}`).toBeGreaterThan(-1);
  const rest = source.slice(start).split("\n").slice(1);
  const values: string[] = [];
  for (const line of rest) {
    if (/^\s{0,4}\S/.test(line) && !/^\s{8}/.test(line)) break;
    const match = line.match(/^\s+[A-Z_]+ = "([a-z_]+)"/);
    if (match) values.push(match[1]!);
  }
  return values;
}

describe("Phase 5 catalog entries", () => {
  it("every deploy run status has a label", () => {
    const statuses = textChoices("deploys/models.py", "Status");
    expect(statuses.length).toBeGreaterThan(10);
    expect(statuses.filter((s) => !Object.hasOwn(tr.status.deployRun, s))).toEqual([]);
  });

  it("every deploy error code has a label", () => {
    const codes = textChoices("deploys/models.py", "ErrorCode");
    expect(codes.length).toBeGreaterThan(20);
    expect(codes.filter((c) => !Object.hasOwn(tr.deploys.errorCodes, c))).toEqual([]);
  });

  it("every connector write status and error type has a label", () => {
    const statuses = textChoices("connectors/models.py", "Status");
    expect(statuses.filter((s) => !Object.hasOwn(tr.status.connectorWrite, s))).toEqual([]);
    const errors = textChoices("connectors/models.py", "ErrorType");
    expect(errors.length).toBeGreaterThan(8);
    expect(errors.filter((e) => !Object.hasOwn(tr.connectors.writeError, e))).toEqual([]);
  });

  it("every Phase 5 gate has a title and both consequences", () => {
    for (const tool of ["deploy", "github_open_pr", "ha_call_service", "mail_send", "calendar_create_event"]) {
      expect(tr.approvals.toolTitles[tool], tool).toBeTruthy();
      const consequence = tr.approvals.consequence[tool as keyof typeof tr.approvals.consequence];
      expect(consequence.approve, tool).toBeTruthy();
      expect(consequence.reject, tool).toBeTruthy();
    }
  });
});
