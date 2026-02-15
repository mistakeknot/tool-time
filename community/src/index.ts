import { Hono } from "hono";
import { cors } from "hono/cors";
import { z } from "zod";

type Bindings = {
  DB: D1Database;
  ADMIN_TOKEN: string;
};

const app = new Hono<{ Bindings: Bindings }>();

// --- CORS: only on public endpoints ---
app.use("/v1/api/stats", cors());
app.use("/v1/api/submit", cors());

// --- Helpers ---

function hexToBytes(hex: string): Uint8Array {
  const bytes = new Uint8Array(hex.length / 2);
  for (let i = 0; i < hex.length; i += 2) {
    bytes[i / 2] = parseInt(hex.substring(i, i + 2), 16);
  }
  return bytes;
}

async function verifySignature(
  rawBody: ArrayBuffer,
  signature: string,
  token: string
): Promise<boolean> {
  const encoder = new TextEncoder();
  const key = await crypto.subtle.importKey(
    "raw",
    encoder.encode(token),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["verify"]
  );
  return crypto.subtle.verify("HMAC", key, hexToBytes(signature), rawBody);
}

function timingSafeEqual(a: string, b: string): boolean {
  const encoder = new TextEncoder();
  const aBuf = encoder.encode(a);
  const bBuf = encoder.encode(b);
  if (aBuf.byteLength !== bBuf.byteLength) return false;
  return crypto.subtle.timingSafeEqual(aBuf, bBuf);
}

function requireAdmin(c: any): Response | null {
  const auth = c.req.header("Authorization");
  if (!auth || !timingSafeEqual(auth, `Bearer ${c.env.ADMIN_TOKEN}`)) {
    return c.json({ error: "Unauthorized" }, 401);
  }
  return null;
}

// --- Validation ---

const TOOL_NAME_RE = /^[a-zA-Z0-9_.\-:]+$/;
const SKILL_NAME_RE = /^[a-zA-Z0-9_.\-:]+$/;
const MCP_SERVER_NAME_RE = /^[a-zA-Z0-9_.\-:]+$/;
const PLUGIN_NAME_RE = /^[a-zA-Z0-9_.\-:/@]+$/;

const ToolStatsSchema = z.object({
  calls: z.number().int().min(0).max(100000),
  errors: z.number().int().min(0).max(100000),
  rejections: z.number().int().min(0).max(100000),
});

const McpServerStatsSchema = z.object({
  calls: z.number().int().min(0).max(100000),
  errors: z.number().int().min(0).max(100000),
});

const SubmitSchema = z.object({
  submission_token: z.string().min(1).max(64),
  generated: z.string().min(1).max(30),
  total_events: z.number().int().min(0).max(1000000),
  tools: z.record(z.string().regex(TOOL_NAME_RE), ToolStatsSchema),
  edit_without_read: z.number().int().min(0).max(100000),
  model: z.string().max(100).nullable().optional(),
  skills: z.record(
    z.string().regex(SKILL_NAME_RE).max(100),
    z.number().int().min(0).max(100000)
  ).optional(),
  mcp_servers: z.record(
    z.string().regex(MCP_SERVER_NAME_RE).max(100),
    McpServerStatsSchema
  ).optional(),
  installed_plugins: z.array(z.string().regex(PLUGIN_NAME_RE).max(100))
    .max(100)
    .optional(),
  client: z.enum(["claude-code", "codex", "openclaw"]).nullable().optional(),
});

// --- Routes ---

app.post("/v1/api/submit", async (c) => {
  // Capture raw body for signature verification
  const rawBody = await c.req.arrayBuffer();
  const bodyText = new TextDecoder().decode(rawBody);
  let body: unknown;
  try {
    body = JSON.parse(bodyText);
  } catch {
    return c.json({ error: "Invalid JSON" }, 400);
  }

  const parsed = SubmitSchema.safeParse(body);
  if (!parsed.success) {
    return c.json({ error: parsed.error.issues }, 400);
  }

  const data = parsed.data;

  // Verify HMAC signature
  const signature = c.req.header("X-Signature");
  if (!signature) {
    return c.json({ error: "Missing signature" }, 401);
  }
  const valid = await verifySignature(rawBody, signature, data.submission_token);
  if (!valid) {
    return c.json({ error: "Invalid signature" }, 401);
  }

  // Reject future timestamps or timestamps >7 days old
  const generated = new Date(data.generated);
  const now = Date.now();
  if (isNaN(generated.getTime())) {
    return c.json({ error: "Invalid timestamp" }, 400);
  }
  if (generated.getTime() > now + 3600_000) {
    return c.json({ error: "Future timestamp" }, 400);
  }
  if (generated.getTime() < now - 7 * 86400_000) {
    return c.json({ error: "Timestamp too old" }, 400);
  }

  // Insert submission (UNIQUE constraint handles dedup)
  try {
    const result = await c.env.DB.prepare(
      `INSERT INTO submissions (submission_token, generated_at, total_events, edit_without_read, model, client)
       VALUES (?, ?, ?, ?, ?, ?)
       ON CONFLICT(submission_token, generated_at) DO NOTHING`
    )
      .bind(
        data.submission_token,
        data.generated,
        data.total_events,
        data.edit_without_read,
        data.model ?? null,
        data.client ?? null
      )
      .run();

    if (!result.meta.changes) {
      return c.json({ status: "duplicate" }, 200);
    }

    // Get the inserted ID
    const row = await c.env.DB.prepare(
      "SELECT id FROM submissions WHERE submission_token = ? AND generated_at = ?"
    )
      .bind(data.submission_token, data.generated)
      .first<{ id: number }>();

    if (!row) {
      return c.json({ error: "Insert failed" }, 500);
    }

    // Build all insert statements for a single atomic batch
    const allStmts: D1PreparedStatement[] = [];

    // Tool stats
    for (const [name, stats] of Object.entries(data.tools)) {
      allStmts.push(
        c.env.DB.prepare(
          "INSERT INTO tool_stats (submission_id, tool_name, calls, errors, rejections) VALUES (?, ?, ?, ?, ?)"
        ).bind(row.id, name, stats.calls, stats.errors, stats.rejections)
      );
    }

    // Skill stats
    if (data.skills) {
      for (const [name, calls] of Object.entries(data.skills)) {
        allStmts.push(
          c.env.DB.prepare(
            "INSERT INTO skill_stats (submission_id, name, calls) VALUES (?, ?, ?)"
          ).bind(row.id, name, calls)
        );
      }
    }

    // MCP server stats
    if (data.mcp_servers) {
      for (const [name, stats] of Object.entries(data.mcp_servers)) {
        allStmts.push(
          c.env.DB.prepare(
            "INSERT INTO mcp_server_stats (submission_id, name, calls, errors) VALUES (?, ?, ?, ?)"
          ).bind(row.id, name, stats.calls, stats.errors)
        );
      }
    }

    // Plugin installs (deduped per token)
    if (data.installed_plugins) {
      for (const plugin of data.installed_plugins) {
        allStmts.push(
          c.env.DB.prepare(
            `INSERT INTO plugin_installs (plugin_name, submission_token, last_seen)
             VALUES (?, ?, datetime('now'))
             ON CONFLICT(plugin_name, submission_token) DO UPDATE SET
               last_seen = datetime('now')`
          ).bind(plugin, data.submission_token)
        );
      }
    }

    if (allStmts.length > 0) {
      await c.env.DB.batch(allStmts);
    }

    return c.json({ status: "ok" }, 200);
  } catch (e: unknown) {
    console.error("POST /v1/api/submit failed:", e);
    if (e instanceof Error && e.message.includes("UNIQUE constraint")) {
      return c.json({ error: "Duplicate submission" }, 409);
    }
    return c.json({ error: "Server error" }, 500);
  }
});

app.get("/v1/api/stats", async (c) => {
  const clientFilter = c.req.query("client");
  const validClients = ["claude-code", "codex", "openclaw"];
  const filterClients = clientFilter
    ? clientFilter.split(",").filter((c) => validClients.includes(c))
    : [];

  // Build WHERE clause fragments
  const baseWhere = "s.submitted_at >= datetime('now', '-7 days')";
  const clientWhere = filterClients.length
    ? ` AND s.client IN (${filterClients.map(() => "?").join(",")})`
    : "";
  const bindClient = (stmt: ReturnType<D1Database["prepare"]>) =>
    filterClients.length ? stmt.bind(...filterClients) : stmt;

  const tools = await bindClient(c.env.DB.prepare(
    `SELECT
       ts.tool_name,
       SUM(ts.calls) as total_calls,
       SUM(ts.errors) as total_errors,
       SUM(ts.rejections) as total_rejections,
       COUNT(DISTINCT s.submission_token) as unique_submitters,
       ROUND(CAST(SUM(ts.calls) AS FLOAT) / COUNT(DISTINCT s.submission_token), 1) as avg_calls_per_submitter
     FROM tool_stats ts
     JOIN submissions s ON s.id = ts.submission_id
     WHERE ${baseWhere}${clientWhere}
     GROUP BY ts.tool_name
     ORDER BY total_calls DESC
     LIMIT 100`
  )).all();

  const overview = await bindClient(c.env.DB.prepare(
    `SELECT
       COUNT(*) as total_submissions,
       COUNT(DISTINCT submission_token) as unique_submitters,
       MIN(submitted_at) as earliest,
       MAX(submitted_at) as latest
     FROM submissions s
     WHERE ${baseWhere}${clientWhere}`
  )).first();

  const models = await bindClient(c.env.DB.prepare(
    `SELECT model, COUNT(*) as count
     FROM submissions s
     WHERE ${baseWhere}${clientWhere}
       AND model IS NOT NULL
     GROUP BY model
     ORDER BY count DESC`
  )).all();

  const skills = await bindClient(c.env.DB.prepare(
    `SELECT ss.name,
            SUM(ss.calls) as total_calls,
            COUNT(DISTINCT s.submission_token) as unique_submitters,
            ROUND(CAST(SUM(ss.calls) AS FLOAT) / COUNT(DISTINCT s.submission_token), 1) as avg_calls_per_submitter
     FROM skill_stats ss
     JOIN submissions s ON s.id = ss.submission_id
     WHERE ${baseWhere}${clientWhere}
     GROUP BY ss.name
     ORDER BY total_calls DESC
     LIMIT 50`
  )).all();

  const mcpServers = await bindClient(c.env.DB.prepare(
    `SELECT ms.name,
            SUM(ms.calls) as total_calls,
            SUM(ms.errors) as total_errors,
            COUNT(DISTINCT s.submission_token) as unique_submitters,
            ROUND(CAST(SUM(ms.calls) AS FLOAT) / COUNT(DISTINCT s.submission_token), 1) as avg_calls_per_submitter
     FROM mcp_server_stats ms
     JOIN submissions s ON s.id = ms.submission_id
     WHERE ${baseWhere}${clientWhere}
     GROUP BY ms.name
     ORDER BY total_calls DESC
     LIMIT 50`
  )).all();

  const plugins = await c.env.DB.prepare(
    `SELECT plugin_name, COUNT(DISTINCT submission_token) as install_count
     FROM plugin_installs
     GROUP BY plugin_name
     ORDER BY install_count DESC
     LIMIT 50`
  ).all();

  // Client distribution (always unfiltered)
  const clients = await c.env.DB.prepare(
    `SELECT client, COUNT(*) as count
     FROM submissions
     WHERE submitted_at >= datetime('now', '-7 days')
       AND client IS NOT NULL
     GROUP BY client
     ORDER BY count DESC`
  ).all();

  return c.json({
    overview: overview ?? {},
    tools: tools.results ?? [],
    models: models.results ?? [],
    clients: clients.results ?? [],
    dimensions: {
      skills: skills.results ?? [],
      mcp_servers: mcpServers.results ?? [],
      plugins: plugins.results ?? [],
    },
  });
});

// --- Admin ---

app.get("/v1/api/admin/submissions", async (c) => {
  const denied = requireAdmin(c);
  if (denied) return denied;

  const limit = Math.min(Number(c.req.query("limit") || 50), 200);
  const rows = await c.env.DB.prepare(
    `SELECT id, submission_token, generated_at, submitted_at, total_events, model
     FROM submissions
     ORDER BY submitted_at DESC
     LIMIT ?`
  ).bind(limit).all();

  return c.json({ submissions: rows.results ?? [] });
});

app.delete("/v1/api/admin/submissions/:id", async (c) => {
  const denied = requireAdmin(c);
  if (denied) return denied;

  const id = Number(c.req.param("id"));
  if (!Number.isInteger(id) || id <= 0) {
    return c.json({ error: "Invalid ID" }, 400);
  }

  const result = await c.env.DB.prepare(
    "DELETE FROM submissions WHERE id = ?"
  ).bind(id).run();

  return c.json({ status: "deleted", rows_affected: result.meta.changes });
});

// GDPR Article 17: data deletion (POST to keep token out of URL logs)
app.post("/v1/api/user/delete", async (c) => {
  const body = await c.req.json().catch(() => null);
  if (!body?.submission_token) {
    return c.json({ error: "Missing submission_token" }, 400);
  }

  const token = String(body.submission_token);

  await c.env.DB.batch([
    c.env.DB.prepare("DELETE FROM submissions WHERE submission_token = ?").bind(token),
    c.env.DB.prepare("DELETE FROM plugin_installs WHERE submission_token = ?").bind(token),
  ]);

  return c.json({ status: "deleted" });
});

export default app;
