import { Hono } from "hono";
import { cors } from "hono/cors";
import { z } from "zod";

type Bindings = {
  DB: D1Database;
};

const app = new Hono<{ Bindings: Bindings }>();

app.use("/v1/*", cors());

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
});

// --- Routes ---

app.post("/v1/api/submit", async (c) => {
  const body = await c.req.json().catch(() => null);
  if (!body) return c.json({ error: "Invalid JSON" }, 400);

  const parsed = SubmitSchema.safeParse(body);
  if (!parsed.success) {
    return c.json({ error: parsed.error.issues }, 400);
  }

  const data = parsed.data;

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
      `INSERT INTO submissions (submission_token, generated_at, total_events, edit_without_read, model)
       VALUES (?, ?, ?, ?, ?)
       ON CONFLICT(submission_token, generated_at) DO NOTHING`
    )
      .bind(
        data.submission_token,
        data.generated,
        data.total_events,
        data.edit_without_read,
        data.model ?? null
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

    // Plugins → aggregate table (no per-submission linkage for privacy)
    if (data.installed_plugins) {
      for (const plugin of data.installed_plugins) {
        allStmts.push(
          c.env.DB.prepare(
            `INSERT INTO plugin_usage_aggregate (plugin_name, install_count, last_seen)
             VALUES (?, 1, datetime('now'))
             ON CONFLICT(plugin_name) DO UPDATE SET
               install_count = install_count + 1,
               last_seen = datetime('now')`
          ).bind(plugin)
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
  // Live aggregation (no precomputed table — add later if needed)
  const tools = await c.env.DB.prepare(
    `SELECT
       ts.tool_name,
       SUM(ts.calls) as total_calls,
       SUM(ts.errors) as total_errors,
       SUM(ts.rejections) as total_rejections,
       COUNT(DISTINCT s.submission_token) as unique_submitters
     FROM tool_stats ts
     JOIN submissions s ON s.id = ts.submission_id
     WHERE s.submitted_at >= datetime('now', '-7 days')
     GROUP BY ts.tool_name
     ORDER BY total_calls DESC
     LIMIT 100`
  ).all();

  const overview = await c.env.DB.prepare(
    `SELECT
       COUNT(*) as total_submissions,
       COUNT(DISTINCT submission_token) as unique_submitters,
       MIN(submitted_at) as earliest,
       MAX(submitted_at) as latest
     FROM submissions
     WHERE submitted_at >= datetime('now', '-7 days')`
  ).first();

  const models = await c.env.DB.prepare(
    `SELECT model, COUNT(*) as count
     FROM submissions
     WHERE submitted_at >= datetime('now', '-7 days')
       AND model IS NOT NULL
     GROUP BY model
     ORDER BY count DESC`
  ).all();

  const skills = await c.env.DB.prepare(
    `SELECT ss.name,
            SUM(ss.calls) as total_calls,
            COUNT(DISTINCT s.submission_token) as unique_submitters
     FROM skill_stats ss
     JOIN submissions s ON s.id = ss.submission_id
     WHERE s.submitted_at >= datetime('now', '-7 days')
     GROUP BY ss.name
     ORDER BY total_calls DESC
     LIMIT 50`
  ).all();

  const mcpServers = await c.env.DB.prepare(
    `SELECT ms.name,
            SUM(ms.calls) as total_calls,
            SUM(ms.errors) as total_errors,
            COUNT(DISTINCT s.submission_token) as unique_submitters
     FROM mcp_server_stats ms
     JOIN submissions s ON s.id = ms.submission_id
     WHERE s.submitted_at >= datetime('now', '-7 days')
     GROUP BY ms.name
     ORDER BY total_calls DESC
     LIMIT 50`
  ).all();

  const plugins = await c.env.DB.prepare(
    `SELECT plugin_name, install_count
     FROM plugin_usage_aggregate
     ORDER BY install_count DESC
     LIMIT 50`
  ).all();

  return c.json({
    overview: overview ?? {},
    tools: tools.results ?? [],
    models: models.results ?? [],
    dimensions: {
      skills: skills.results ?? [],
      mcp_servers: mcpServers.results ?? [],
      plugins: plugins.results ?? [],
    },
  });
});

// GDPR Article 17: data deletion
app.delete("/v1/api/user/:token", async (c) => {
  const token = c.req.param("token");

  // Delete tool_stats via cascade, then submissions
  await c.env.DB.prepare(
    `DELETE FROM submissions WHERE submission_token = ?`
  ).bind(token).run();

  return c.json({ status: "deleted" });
});

export default app;
