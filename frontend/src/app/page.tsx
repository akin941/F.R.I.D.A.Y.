import { redirect } from "next/navigation";
import { cookies } from "next/headers";
import { SESSION_COOKIE } from "@/lib/cookie-names";

export default async function RootPage() {
  const store = await cookies();
  redirect(store.has(SESSION_COOKIE) ? "/chat" : "/enroll");
}
