/**
 * Tiny local site the demo tasks run against. It has a registration form (with
 * a deliberately seeded validation bug), a paginated product catalogue, and a
 * multi-step expense-claim workflow.
 *
 *   npm run demo            -> http://127.0.0.1:4173
 */
import { randomBytes } from "node:crypto";
import { readFile } from "node:fs/promises";
import { createServer, type IncomingMessage, type Server } from "node:http";
import { extname, join, normalize, dirname } from "node:path";
import { fileURLToPath } from "node:url";

export const DEMO_PORT = 4173;
// Loopback only, and the IP rather than "localhost" so IPv4/IPv6 resolution can't miss.
export const DEMO_ORIGIN = `http://127.0.0.1:${DEMO_PORT}`;

const PUBLIC_DIR = join(dirname(fileURLToPath(import.meta.url)), "public");
const TYPES: Record<string, string> = { ".html": "text/html; charset=utf-8", ".css": "text/css", ".js": "text/javascript" };

// SEEDED BUG (intentional, for the testing demo): no TLD required, so "jane@example" is accepted.
const EMAIL_RE = /^[^@\s]+@[^@\s]+$/;

// Survey fraud screening. SEEDED GAP (intentional, for the detection demo): the
// virtual-GPU blocklist has VirtualBox but not VMware, and CPU/screen hints are ignored.
const VM_GPU_BLOCKLIST = /virtualbox|vbox|llvmpipe/i;

function screenRespondent(s: Record<string, unknown>): { verdict: "passed" | "flagged"; score: number; reasons: string[] } {
  const reasons: string[] = [];
  const ua = String(s.userAgent ?? "");
  const platform = `${s.uaDataPlatform ?? ""} ${s.platform ?? ""}`.toLowerCase();
  const gpu = `${s.webglVendor ?? ""} ${s.webglRenderer ?? ""}`;
  if (s.webdriver) reasons.push("Automation detected (navigator.webdriver)");
  if (/HeadlessChrome/.test(ua)) reasons.push("Headless browser detected");
  if (VM_GPU_BLOCKLIST.test(gpu)) reasons.push(`Virtual machine GPU detected (${s.webglRenderer})`);
  const uaMac = /Macintosh|Mac OS X/.test(ua);
  const uaWin = /Windows/.test(ua);
  if ((uaMac && !platform.includes("mac")) || (uaWin && !platform.includes("win"))) {
    reasons.push("Fingerprint mismatch: user agent OS differs from the platform");
  }
  if (/apple/i.test(gpu) && !platform.includes("mac")) reasons.push("Fingerprint mismatch: Apple GPU on a non-Mac platform");
  if (String(s.language ?? "").startsWith("en-US") && !/^America\/|^US\/|^Pacific\/Honolulu/.test(String(s.timezone ?? ""))) {
    reasons.push(`Location mismatch: time zone ${s.timezone} for language ${s.language}`);
  }
  return { verdict: reasons.length ? "flagged" : "passed", score: Math.min(100, reasons.length * 35), reasons };
}

const registrations: unknown[] = [];
const claims: unknown[] = [];

const id = (prefix: string) => `${prefix}-${randomBytes(3).toString("hex").toUpperCase()}`;

async function body(req: IncomingMessage): Promise<Record<string, unknown>> {
  const chunks: Buffer[] = [];
  for await (const chunk of req) chunks.push(chunk as Buffer);
  try {
    return JSON.parse(Buffer.concat(chunks).toString("utf8") || "{}");
  } catch {
    return {};
  }
}

export function startDemoServer(port = DEMO_PORT): Promise<Server> {
  const server = createServer(async (req, res) => {
    const url = new URL(req.url ?? "/", `http://127.0.0.1:${port}`);
    const json = (status: number, payload: unknown) => {
      res.writeHead(status, { "content-type": "application/json" });
      res.end(JSON.stringify(payload));
    };

    if (req.method === "POST" && url.pathname === "/api/register") {
      const b = await body(req);
      const errors: Record<string, string> = {};
      if (!String(b.fullName ?? "").trim()) errors.fullName = "Full name is required";
      if (!EMAIL_RE.test(String(b.email ?? ""))) errors.email = "Enter a valid work email";
      if (!b.terms) errors.terms = "You must accept the terms";
      if (Object.keys(errors).length) return json(422, { errors });
      const confirmation = id("REG");
      registrations.push({ ...b, confirmation });
      return json(201, { confirmation });
    }
    if (req.method === "POST" && url.pathname === "/api/claims") {
      const b = await body(req);
      const claimId = id("EXP");
      claims.push({ ...b, claimId, status: "Pending approval" });
      return json(201, { claimId, status: "Pending approval" });
    }
    if (req.method === "POST" && url.pathname === "/api/screen") return json(200, screenRespondent(await body(req)));
    if (req.method === "GET" && url.pathname === "/api/claims") return json(200, claims);
    if (req.method === "GET" && url.pathname === "/api/registrations") return json(200, registrations);

    const path = normalize(url.pathname === "/" ? "/index.html" : url.pathname).replace(/^([\\/])+/, "");
    const filePath = join(PUBLIC_DIR, path);
    try {
      if (!filePath.startsWith(PUBLIC_DIR)) throw new Error("outside public dir");
      const file = await readFile(filePath);
      res.writeHead(200, { "content-type": TYPES[extname(path)] ?? "application/octet-stream" });
      res.end(file);
    } catch {
      res.writeHead(404, { "content-type": "text/plain" });
      res.end("Not found");
    }
  });
  return new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(port, "127.0.0.1", () => resolve(server));
  });
}

export async function isDemoServerUp(): Promise<boolean> {
  try {
    return (await fetch(DEMO_ORIGIN, { signal: AbortSignal.timeout(1000) })).ok;
  } catch {
    return false;
  }
}

if (process.argv[1] && fileURLToPath(import.meta.url) === normalize(process.argv[1])) {
  await startDemoServer();
  console.log(`Demo site running at ${DEMO_ORIGIN}`);
}
