import { redirect } from "next/navigation";
import { cookies } from "next/headers";
import { hasSessionCookie } from "@/lib/cookie-names";

export default async function RootPage() {
  const store = await cookies();
  redirect(hasSessionCookie(store) ? "/chat" : "/enroll");
}
