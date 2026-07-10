import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { SignupForm } from "@/components/auth/SignupForm";

const push = vi.fn();
const refresh = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push, refresh }),
}));

describe("SignupForm", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    push.mockClear();
    refresh.mockClear();
  });

  it("shows the disabled-signup message on a 403 response", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify({ detail: "Signup is disabled." }), { status: 403 })),
    );

    render(<SignupForm />);
    await userEvent.type(screen.getByLabelText("Email"), "bob@test.dev");
    await userEvent.type(screen.getByLabelText("Password"), "Str0ngDevPass!42");
    await userEvent.click(screen.getByRole("button", { name: /sign up/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/signup is disabled/i);
  });

  it("shows a field-level error for a duplicate email", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(
          JSON.stringify({ email: ["A user with this email already exists."] }),
          { status: 400 },
        ),
      ),
    );

    render(<SignupForm />);
    await userEvent.type(screen.getByLabelText("Email"), "bob@test.dev");
    await userEvent.type(screen.getByLabelText("Password"), "Str0ngDevPass!42");
    await userEvent.click(screen.getByRole("button", { name: /sign up/i }));

    expect(await screen.findByText("A user with this email already exists.")).toBeInTheDocument();
  });

  it("redirects to /chat on successful signup", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(
          JSON.stringify({ user: { id: "u1", email: "bob@test.dev", name: "Bob", is_agent: false }, workspaces: [] }),
          { status: 201 },
        ),
      ),
    );

    render(<SignupForm />);
    await userEvent.type(screen.getByLabelText("Email"), "bob@test.dev");
    await userEvent.type(screen.getByLabelText("Password"), "Str0ngDevPass!42");
    await userEvent.click(screen.getByRole("button", { name: /sign up/i }));

    await vi.waitFor(() => expect(push).toHaveBeenCalledWith("/chat"));
  });
});
