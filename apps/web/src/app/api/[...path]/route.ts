import { NextRequest, NextResponse } from "next/server";
import { events, links } from "@/lib/fixtures";
export const dynamic = "force-dynamic";

type Context = { params: Promise<{ path: string[] }> };
const dataMode = () => process.env.DATA_MODE ?? "fixture";
const responseHeaders = () => ({ "X-Data-Mode": dataMode(), "Cache-Control": "no-store" });

async function forward(request: NextRequest, path: string[]) {
  const headers = responseHeaders();
  try {
    const base = (process.env.API_BASE_URL ?? "http://127.0.0.1:43124").replace(/\/$/, "");
    const url = `${base}/${path.map(encodeURIComponent).join("/")}${request.nextUrl.search}`;
    const upstream = await fetch(url, { cache: "no-store", signal: AbortSignal.timeout(10000) });
    return new Response(upstream.body, {
      status: upstream.status,
      headers: { ...headers, "Content-Type": upstream.headers.get("Content-Type") ?? "application/json" },
    });
  } catch {
    return NextResponse.json({ error: "FastAPI is unavailable. Check API_BASE_URL and start the backend." }, { status: 502, headers });
  }
}

export async function GET(request: NextRequest, context: Context) {
  const { path } = await context.params;
  const allowed = ((path[0] === "health" || path[0] === "links") && path.length === 1) ||
    (path[0] === "events" && (path.length <= 2 || (path.length === 3 && path[2] === "links")));
  if (!allowed) return NextResponse.json({ error: "Not found" }, { status: 404 });
  const mode = dataMode();
  const headers = responseHeaders();
  if (mode === "fixture") {
    if (path[0] === "health") return NextResponse.json({ status: "ok", mode, backendConnected: false }, { headers });
    if (path[0] === "links") return NextResponse.json(links, { headers });
    if (path.length === 1) return NextResponse.json({
      generatedAt: new Date().toISOString(),
      sourceStatus: { news: "fixture", gdelt: "fixture" },
      events, nextCursor: null,
    }, { headers });
    const event = events.find((item) => item.id === path[1]);
    if (!event) return NextResponse.json({ error: "Event not found" }, { status: 404, headers });
    return NextResponse.json(path.length === 2 ? event : links.filter(
      (link) => link.sourceId === event.id || link.targetId === event.id,
    ), { headers });
  }
  if (mode !== "api") return NextResponse.json({ error: "DATA_MODE must be fixture or api" }, { status: 500, headers });
  return forward(request, path);
}

