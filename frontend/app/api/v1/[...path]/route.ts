import { cookies } from "next/headers";
import type { NextRequest } from "next/server";
import { BACKEND, COOKIE } from "@/lib/server";


/** Same-origin proxy to the API. Adds the bearer token from the httpOnly cookie; the browser never sees the token. */
async function forward(req: NextRequest, ctx: RouteContext<"/api/v1/[...path]">) {
  const jar = await cookies();
  const token = jar.get(COOKIE)?.value;
  if (!token) return Response.json({ detail: "Not signed in" }, { status: 401 });

  if (req.method !== "GET" && req.method !== "HEAD") {  // basic CSRF guard on top of SameSite=Lax
    const origin = req.headers.get("origin");
    if (origin && new URL(origin).host !== req.headers.get("host")) return Response.json({ detail: "Cross-site request refused" }, { status: 403 });
  }

  const { path } = await ctx.params;
  const url = `${BACKEND}/v1/${path.map(encodeURIComponent).join("/")}${req.nextUrl.search}`;
  const hasBody = req.method !== "GET" && req.method !== "HEAD";
  let up: Response;
  try {
    up = await fetch(url, {
      method: req.method, cache: "no-store", body: hasBody ? await req.text() : undefined,
      headers: { authorization: `Bearer ${token}`, ...(hasBody ? { "content-type": req.headers.get("content-type") ?? "application/json" } : {}) },
    });
  } catch {
    return Response.json({ detail: "Can't reach the Job Hunter service. Is it running? Start it with scripts/start.sh." }, { status: 502 });
  }
  if (up.status === 401) jar.delete(COOKIE);  // expired session: clear it so the login page does not bounce back
  const headers = new Headers({ "cache-control": "no-store" });
  for (const k of ["content-type", "content-disposition", "content-length"]) { const v = up.headers.get(k); if (v) headers.set(k, v); }
  return new Response(up.body, { status: up.status, headers });
}

export { forward as GET, forward as POST, forward as PUT, forward as PATCH, forward as DELETE };
