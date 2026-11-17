import { NextResponse, type NextRequest } from "next/server";

/** Sends signed-out visitors to /login. The API itself re-checks the token on every call; this only guards the pages. */
export function proxy(req: NextRequest) {
  const signedIn = req.cookies.has("jh_session");
  const { pathname } = req.nextUrl;
  if (pathname === "/login") return signedIn ? NextResponse.redirect(new URL("/", req.url)) : NextResponse.next();
  if (!signedIn) {
    const to = new URL("/login", req.url);
    if (pathname !== "/") to.searchParams.set("next", pathname);
    return NextResponse.redirect(to);
  }
  return NextResponse.next();
}

export const config = { matcher: ["/((?!api|_next/static|_next/image|icons|manifest|sw\\.js|offline\\.html|.*\\..*).*)"] };
