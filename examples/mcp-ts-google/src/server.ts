/** MCP protocol layer: tools only. Auth is resolved before this runs and arrives as ctx.http.authInfo. */
import { McpServer } from "@modelcontextprotocol/server";
import { z } from "zod";
import type { GoogleClient } from "./google.js";

const CALENDAR_EVENTS = "https://www.googleapis.com/calendar/v3/calendars/primary/events"; // fixed: never from input

function caller(ctx: { http?: { authInfo?: { extra?: Record<string, unknown> } } }) {
  const extra = ctx.http?.authInfo?.extra ?? {};
  const sub = typeof extra.sub === "string" ? extra.sub : undefined;
  const email = typeof extra.email === "string" ? extra.email : undefined;
  if (!sub) throw new Error("unauthenticated"); // fail closed: never serve a tool without an identity
  return { sub, email };
}

export function buildServer(google: GoogleClient): McpServer {
  const server = new McpServer({ name: "mcp-ts-google-example", version: "0.1.0" });

  server.registerTool(
    "whoami",
    { description: "Report the Google account this MCP session is signed in as." },
    async (ctx) => {
      const who = caller(ctx);
      return { content: [{ type: "text", text: JSON.stringify(who) }] };
    },
  );

  server.registerTool(
    "list_calendar_events",
    {
      description: "List the caller's upcoming Google Calendar events (primary calendar).",
      inputSchema: z.object({
        days: z.number().int().min(1).max(30).default(7).describe("How many days ahead to look"),
      }),
    },
    async ({ days }, ctx) => {
      const { sub } = caller(ctx);
      const token = await google.accessTokenFor(sub);
      const url = new URL(CALENDAR_EVENTS);
      url.searchParams.set("timeMin", new Date().toISOString());
      url.searchParams.set("timeMax", new Date(Date.now() + days * 86_400_000).toISOString());
      url.searchParams.set("singleEvents", "true");
      url.searchParams.set("orderBy", "startTime");
      url.searchParams.set("maxResults", "50");

      const res = await fetch(url, { headers: { Authorization: `Bearer ${token}` } });
      if (!res.ok) {
        return { isError: true, content: [{ type: "text", text: `calendar api error ${res.status}` }] };
      }
      const body = (await res.json()) as { items?: Array<Record<string, unknown>> };
      const events = (body.items ?? []).map((e) => ({
        summary: e.summary,
        start: (e.start as Record<string, unknown> | undefined)?.dateTime ?? (e.start as Record<string, unknown> | undefined)?.date,
        end: (e.end as Record<string, unknown> | undefined)?.dateTime ?? (e.end as Record<string, unknown> | undefined)?.date,
      }));
      return { content: [{ type: "text", text: JSON.stringify(events, null, 2) }] };
    },
  );

  return server;
}
