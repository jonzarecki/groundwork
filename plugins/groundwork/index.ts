import { execSync } from "node:child_process";
import { existsSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function expandHome(p: string): string {
  return p.startsWith("~/") ? join(homedir(), p.slice(2)) : p;
}

function resolveConfig(api: OpenClawPluginApi) {
  const cfg = api.config as Record<string, unknown>;
  const projectPath = expandHome(
    (cfg["project_path"] as string | undefined) ?? "~/Projects/linked-collector"
  );
  const dbPath = expandHome(
    (cfg["db_path"] as string | undefined) ??
      join(projectPath, "data", "contacts.db")
  );
  const notifyChannel = (cfg["notify_channel"] as string | undefined) ?? "";
  const minScore = (cfg["min_score"] as number | undefined) ?? 15;
  const pythonBin = (cfg["python_bin"] as string | undefined) ?? "python3";
  return { projectPath, dbPath, notifyChannel, minScore, pythonBin };
}

/**
 * Run the notify-run.py script and return its output.
 * Returns an error message string on failure so callers can relay it to the
 * user rather than throwing.
 */
function runNotifyScript(
  projectPath: string,
  dbPath: string,
  pythonBin: string,
  minScore: number,
  format: "json" | "message",
  runId?: number
): string {
  const notifyScript = join(projectPath, "scripts", "notify-run.py");

  if (!existsSync(dbPath)) {
    return (
      `Groundwork database not found at \`${dbPath}\`.\n` +
      `Check that \`project_path\` in the plugin config points to the correct ` +
      `Groundwork directory, or set \`db_path\` explicitly.`
    );
  }

  if (!existsSync(notifyScript)) {
    return (
      `Groundwork scripts not found at \`${projectPath}/scripts\`.\n` +
      `Check that \`project_path\` in the plugin config is correct.`
    );
  }

  const args = [
    pythonBin,
    notifyScript,
    "--db",
    dbPath,
    "--min-score",
    String(minScore),
    "--format",
    format,
    ...(runId !== undefined ? ["--run-id", String(runId)] : []),
  ];

  try {
    return execSync(args.join(" "), { encoding: "utf8", timeout: 30_000 }).trim();
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    return `Groundwork digest failed: ${msg}`;
  }
}

/**
 * Lightweight three-step health-check. Returns a human-readable status string.
 */
function healthCheck(
  projectPath: string,
  dbPath: string,
  pythonBin: string
): string {
  const steps: string[] = [];

  // 1. DB file
  if (!existsSync(dbPath)) {
    return (
      `Health-check failed: database not found at \`${dbPath}\`.\n` +
      `Verify the \`project_path\` / \`db_path\` settings in the Groundwork plugin config.`
    );
  }
  steps.push(`DB: ${dbPath} ✓`);

  // 2. Schema + row count
  let peopleCount: number;
  let latestRun: string;
  try {
    const countOut = execSync(
      `sqlite3 "${dbPath}" "SELECT COUNT(*) FROM people;"`,
      { encoding: "utf8", timeout: 5_000 }
    ).trim();
    peopleCount = parseInt(countOut, 10) || 0;

    const runOut = execSync(
      `sqlite3 "${dbPath}" "SELECT COALESCE(MAX(finished_at),'none') FROM runs WHERE finished_at IS NOT NULL;"`,
      { encoding: "utf8", timeout: 5_000 }
    ).trim();
    latestRun = runOut || "none";
  } catch {
    return (
      `Health-check failed: database exists but schema is uninitialised.\n` +
      `Run \`./scripts/setup.sh\` in the Groundwork directory to create the schema.`
    );
  }
  steps.push(`Schema: ${peopleCount} contacts ✓`);
  steps.push(`Latest run: ${latestRun}`);

  // 3. Scripts
  if (!existsSync(join(projectPath, "scripts", "notify-run.py"))) {
    return (
      `Health-check failed: scripts not found at \`${projectPath}/scripts\`.\n` +
      `Check that \`project_path\` points to the Groundwork project directory.`
    );
  }
  steps.push(`Scripts: ${projectPath}/scripts ✓`);

  const hint =
    peopleCount === 0
      ? "\nThe database is empty — run `./scripts/run-collect.sh` to collect your first contacts."
      : "";

  return `Groundwork connected.\n${steps.join("\n")}${hint}`;
}

// ---------------------------------------------------------------------------
// Plugin registration
// ---------------------------------------------------------------------------

export default function register(api: OpenClawPluginApi): void {
  // ── Tool: groundwork_status ──────────────────────────────────────────────
  // On-demand query used by the SKILL.md agent guidance.
  api.registerTool("groundwork_status", {
    description:
      "Query Groundwork for notable contacts and enrichment candidates from the latest collect run. " +
      "Returns a structured JSON payload with new contacts, enrichment candidates, and review flags.",
    inputSchema: {
      type: "object",
      properties: {
        format: {
          type: "string",
          enum: ["json", "message"],
          description: "Output format. Use 'json' for structured data, 'message' for a chat-ready digest.",
          default: "json",
        },
        run_id: {
          type: "integer",
          description: "Specific run ID to report on. Omit to use the latest completed collect run.",
        },
        min_score: {
          type: "integer",
          description:
            "Override the minimum score threshold for this call. " +
            "Omit to use the plugin-level default.",
        },
        health_check: {
          type: "boolean",
          description: "Run a connectivity health-check instead of returning digest data.",
          default: false,
        },
      },
      additionalProperties: false,
    },
    handler: async (input: Record<string, unknown>) => {
      const { projectPath, dbPath, minScore, pythonBin } = resolveConfig(api);

      if (input["health_check"] === true) {
        return healthCheck(projectPath, dbPath, pythonBin);
      }

      const format = (input["format"] as "json" | "message" | undefined) ?? "json";
      const runId = input["run_id"] as number | undefined;
      const effectiveMinScore =
        (input["min_score"] as number | undefined) ?? minScore;

      return runNotifyScript(
        projectPath,
        dbPath,
        pythonBin,
        effectiveMinScore,
        format,
        runId
      );
    },
  });

  // ── Command: /groundwork ──────────────────────────────────────────────────
  // User-facing slash command on any connected messaging platform.
  api.registerCommand("groundwork", {
    description:
      "Show a Groundwork contact digest. Usage: /groundwork [check|collect|enrich]",
    handler: async (ctx) => {
      const { projectPath, dbPath, minScore, pythonBin } = resolveConfig(api);
      const arg = (ctx.args?.[0] ?? "").toLowerCase();

      if (arg === "check") {
        return ctx.reply(healthCheck(projectPath, dbPath, pythonBin));
      }

      if (arg === "collect") {
        const shell = `cd "${projectPath}" && ./scripts/run-collect.sh`;
        try {
          const out = execSync(shell, {
            encoding: "utf8",
            timeout: 300_000,
          }).trim();
          // Extract just the digest section if present
          const digestStart = out.indexOf("── Digest ──");
          return ctx.reply(
            digestStart >= 0
              ? out.slice(digestStart).trim()
              : out.slice(-2000).trim()
          );
        } catch (err) {
          const msg = err instanceof Error ? err.message : String(err);
          return ctx.reply(`Collect failed: ${msg}`);
        }
      }

      if (arg === "enrich") {
        const shell = `cd "${projectPath}" && ${pythonBin} scripts/enrich-linkedin.py --batch-size 5`;
        try {
          const out = execSync(shell, { encoding: "utf8", timeout: 120_000 }).trim();
          return ctx.reply(out || "Enrichment complete. No output.");
        } catch (err) {
          const msg = err instanceof Error ? err.message : String(err);
          return ctx.reply(`Enrich failed: ${msg}`);
        }
      }

      // Default: show digest
      const digest = runNotifyScript(
        projectPath,
        dbPath,
        pythonBin,
        minScore,
        "message"
      );
      return ctx.reply(digest || "No actionable contacts from the latest run.");
    },
  });

  // ── Cron: post-run digest ─────────────────────────────────────────────────
  // Watches for new completed runs and pushes a digest to notify_channel.
  // Polls the runs table every 10 minutes; only fires when a run finishes that
  // hasn't been notified yet.
  (() => {
    let lastNotifiedRunId = 0;

    api.registerCron("groundwork-digest", {
      // Weekly on Monday at 9 AM — push digest for the past week's collect runs
      schedule: "0 9 * * 1",
      handler: async () => {
        const { projectPath, dbPath, notifyChannel, minScore, pythonBin } =
          resolveConfig(api);

        if (!notifyChannel) return; // No channel configured — skip silently

        if (!existsSync(dbPath)) return; // DB not mounted yet

        // Check for a new completed collect run
        let latestRunId: number;
        try {
          const out = execSync(
            `sqlite3 "${dbPath}" "SELECT COALESCE(MAX(id),0) FROM runs WHERE finished_at IS NOT NULL AND source='all';"`,
            { encoding: "utf8", timeout: 5_000 }
          ).trim();
          latestRunId = parseInt(out, 10) || 0;
        } catch {
          return; // DB inaccessible — skip silently
        }

        if (latestRunId <= lastNotifiedRunId) return; // No new run

        const digest = runNotifyScript(
          projectPath,
          dbPath,
          pythonBin,
          minScore,
          "message",
          latestRunId
        );

        if (digest) {
          await api.send({ channel: notifyChannel, text: digest });
        }

        lastNotifiedRunId = latestRunId;
      },
    });
  })();
}
