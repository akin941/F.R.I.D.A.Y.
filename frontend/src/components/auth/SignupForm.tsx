"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { apiPostJson, ApiError } from "@/lib/api-client";

function fieldErrors(body: Record<string, unknown>, field: string): string | null {
  const value = body[field];
  if (Array.isArray(value) && typeof value[0] === "string") {
    return value[0];
  }
  return null;
}

export function SignupForm() {
  const router = useRouter();
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [signupDisabled, setSignupDisabled] = useState(false);
  const [emailError, setEmailError] = useState<string | null>(null);
  const [passwordError, setPasswordError] = useState<string | null>(null);

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    setSubmitting(true);
    setFormError(null);
    setEmailError(null);
    setPasswordError(null);
    setSignupDisabled(false);
    try {
      await apiPostJson("/api/auth/signup", { email, password, name });
      router.push("/chat");
      router.refresh();
    } catch (err) {
      if (err instanceof ApiError) {
        if (err.status === 403) {
          setSignupDisabled(true);
        } else if (err.status === 400) {
          const email400 = fieldErrors(err.body, "email");
          const password400 = fieldErrors(err.body, "password");
          setEmailError(email400);
          setPasswordError(password400);
          if (!email400 && !password400) {
            setFormError(err.body.detail ?? "Please check your details and try again.");
          }
        } else {
          setFormError(err.body.detail ?? "Something went wrong. Please try again.");
        }
      } else {
        setFormError("Something went wrong. Please try again.");
      }
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Card className="w-full max-w-sm">
      <CardHeader>
        <CardTitle>Create your Jarvis account</CardTitle>
      </CardHeader>
      <CardContent>
        {signupDisabled ? (
          <Alert variant="destructive" role="alert">
            <AlertDescription>
              Signup is disabled.{" "}
              <Link href="/login" className="underline underline-offset-4">
                Sign in instead
              </Link>
              .
            </AlertDescription>
          </Alert>
        ) : (
          <form className="flex flex-col gap-4" onSubmit={handleSubmit}>
            {formError ? (
              <Alert variant="destructive" role="alert">
                <AlertDescription>{formError}</AlertDescription>
              </Alert>
            ) : null}
            <div className="flex flex-col gap-2">
              <Label htmlFor="name">Name (optional)</Label>
              <Input id="name" value={name} onChange={(e) => setName(e.target.value)} />
            </div>
            <div className="flex flex-col gap-2">
              <Label htmlFor="email">Email</Label>
              <Input
                id="email"
                type="email"
                autoComplete="email"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
              />
              {emailError ? (
                <p className="text-sm text-destructive" role="alert">
                  {emailError}
                </p>
              ) : null}
            </div>
            <div className="flex flex-col gap-2">
              <Label htmlFor="password">Password</Label>
              <Input
                id="password"
                type="password"
                autoComplete="new-password"
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
              />
              {passwordError ? (
                <p className="text-sm text-destructive" role="alert">
                  {passwordError}
                </p>
              ) : null}
            </div>
            <Button type="submit" disabled={submitting}>
              {submitting ? "Creating account…" : "Sign up"}
            </Button>
            <p className="text-sm text-muted-foreground">
              Already have an account?{" "}
              <Link href="/login" className="underline underline-offset-4">
                Sign in
              </Link>
            </p>
          </form>
        )}
      </CardContent>
    </Card>
  );
}
