import { cookies } from "next/headers";
import { BACKEND, COOKIE, SESSION_SECONDS } from "@/lib/server";


const MESSAGES: Record<number, string> = {
  401: "That username or password is not right.",
  429: "Too many attempts. Please wait a few minutes and try again.",
  503: "Login is not set up yet. Run scripts/set_password.py first.",
};

/** Sign in: the API token is stored in an httpOnly cookie, so page JavaScript can never read it. */
export async function POST(req: Request) {
  const body = (await req.json().catch(() => null)) as { username?: string; password?: string } | null;
  if (!body?.username || !body?.password) return Response.json({ message: "Enter your username and password." }, { status: 400 });
  let r: Response;
  try {
    r = await fetch(`${BACKEND}/auth/login`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ username: body.username, password: body.password }), cache: "no-store" });
  } catch {
    return Response.json({ message: "Can't reach the Job Hunter service. Start it with scripts/start.sh." }, { status: 502 });
  }
  if (!r.ok) return Response.json({ message: MESSAGES[r.status] ?? "Sign in failed. Please try again." }, { status: r.status });
  const { access_token } = (await r.json()) as { access_token: string };
  (await cookies()).set({ name: COOKIE, value: access_token, httpOnly: true, sameSite: "lax", path: "/", maxAge: SESSION_SECONDS, secure: new URL(req.url).protocol === "https:" });
  return Response.json({ ok: true });
}

export async function DELETE() {
  (await cookies()).delete(COOKIE);
  return Response.json({ ok: true });
}
