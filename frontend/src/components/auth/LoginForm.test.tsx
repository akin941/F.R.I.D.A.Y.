import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { LoginForm } from "@/components/auth/LoginForm";

const push = vi.fn();
const refresh = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push, refresh }),
}));

describe("LoginForm", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    push.mockClear();
    refresh.mockClear();
  });

  it("shows the backend error message on invalid credentials", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify({ detail: "Invalid credentials." }), { status: 401 })),
    );

    render(<LoginForm />);
    await userEvent.type(screen.getByLabelText("Email"), "alice@test.dev");
    await userEvent.type(screen.getByLabelText("Password"), "wrong-password");
    await userEvent.click(screen.getByRole("button", { name: /sign in/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Invalid credentials.");
    expect(push).not.toHaveBeenCalled();
  });

  it("redirects to /chat on a successful login", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(
          JSON.stringify({ user: { id: "u1", email: "alice@test.dev", name: "Alice", is_agent: false }, workspaces: [] }),
          { status: 200 },
        ),
      ),
    );

    render(<LoginForm />);
    await userEvent.type(screen.getByLabelText("Email"), "alice@test.dev");
    await userEvent.type(screen.getByLabelText("Password"), "correct-password");
    await userEvent.click(screen.getByRole("button", { name: /sign in/i }));

    await vi.waitFor(() => expect(push).toHaveBeenCalledWith("/chat"));
  });
});
