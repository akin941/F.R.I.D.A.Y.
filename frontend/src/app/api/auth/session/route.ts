import { NextResponse } from "next/server";
import { backendFetch } from "@/lib/backend-fetch";
import { getAccessToken } from "@/lib/auth-cookies";
import type { SessionResponse } from "@/types/api";

export const runtime = "nodejs";

export async function GET() {
  const accessToken = await getAccessToken();
  if (!accessToken) {
    return NextResponse.json({ detail: "Not authenticated." }, { status: 401 });
  }

  const meResponse = await backendFetch("/auth/me", { accessToken });
  if (!meResponse.ok) {
    return NextResponse.json(
      { detail: "Failed to load session." },
      { status: meResponse.status },
    );
  }
  const session = (await meResponse.json()) as SessionResponse;
  return NextResponse.json(session);
}
