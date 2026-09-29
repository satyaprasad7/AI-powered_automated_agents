import { readFile } from "node:fs/promises";
import { z } from "zod";

export const TaskSpec = z.object({
  name: z.string().min(1),
  /** testing | form-submission | data-extraction | workflow | validation - used for reporting only. */
  category: z.string().default("general"),
  description: z.string().optional(),
  driver: z.enum(["playwright", "puppeteer"]).default("playwright"),
  startUrl: z.url(),
  /** Natural-language objective handed to the agent. */
  goal: z.string().min(1),
  /** Checks the agent must verify and record before finishing. */
  successCriteria: z.array(z.string()).default([]),
  /** Non-sensitive input data the agent may type into the page. */
  inputs: z.record(z.string(), z.string()).default({}),
  /**
   * Secret placeholders: name -> environment variable. The agent only ever
   * sees `{{secret:NAME}}`; the executor substitutes the real value on fill.
   */
  secrets: z.record(z.string(), z.string()).default({}),
  /** Optional JSON Schema (as an object) describing the data `finish` should return. */
  outputSchema: z.record(z.string(), z.unknown()).optional(),
  /** Hostnames the agent may visit (subdomains included). */
  allowedDomains: z.array(z.string()).min(1),
  maxSteps: z.number().int().positive().max(200).default(30),
  /** Regexes matched against an element's accessible name; matching clicks need operator approval. */
  requireApprovalFor: z.array(z.string()).default([]),
  effort: z.enum(["low", "medium", "high", "xhigh", "max"]).default("medium"),
  /** Browser profile for bot / fraud detection testing (see src/detection/profiles.ts). */
  profile: z.enum(["default", "vm", "anti-detect"]).default("default"),
  headless: z.boolean().default(true),
});

export type TaskSpec = z.infer<typeof TaskSpec>;

export async function loadTask(path: string): Promise<TaskSpec> {
  const raw = JSON.parse(await readFile(path, "utf8"));
  const parsed = TaskSpec.safeParse(raw);
  if (!parsed.success) {
    throw new Error(`Invalid task file ${path}:\n${z.prettifyError(parsed.error)}`);
  }
  return parsed.data;
}
