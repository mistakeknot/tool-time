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

const ToolStatsSchema = z.object({
  calls: z.number().int().min(0).max(100000),
  errors: z.number().int().min(0).max(100000),
  rejections: z.number().int().min(0).max(100000),
});

const SubmitSchema = z.object({
  submission_token: z.string().min(1).max(64),
  generated: z.string().min(1).max(30),
  total_events: z.number().int().min(0).max(1000000),
  tools: z.record(z.string().regex(TOOL_NAME_RE), ToolStatsSchema),
  edit_without_read: z.number().int().min(0).max(100000),
  model: z.string().max(100).nullable().optional(),
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

    // Insert tool stats
    const stmts = Object.entries(data.tools).map(([name, stats]) =>
      c.env.DB.prepare(
        "INSERT INTO tool_stats (submission_id, tool_name, calls, errors, rejections) VALUES (?, ?, ?, ?, ?)"
      ).bind(row.id, name, stats.calls, stats.errors, stats.rejections)
    );

    if (stmts.length > 0) {
      await c.env.DB.batch(stmts);
    }

    return c.json({ status: "ok" }, 200);
  } catch (e: any) {
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
     HAVING COUNT(DISTINCT s.submission_token) >= 10
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

  return c.json({
    overview: overview ?? {},
    tools: tools.results ?? [],
    models: models.results ?? [],
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
