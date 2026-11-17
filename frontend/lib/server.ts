export const COOKIE = "jh_session";
export const BACKEND = process.env.BACKEND_URL ?? "http://127.0.0.1:8000";
export const SESSION_SECONDS = 8 * 3600; // matches ACCESS_TOKEN_MINUTES on the API
