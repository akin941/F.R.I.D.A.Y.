import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { EnrollForm, guessDeviceName } from "@/components/auth/EnrollForm";

const replace = vi.fn();
const push = vi.fn();
const refresh = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace, push, refresh }),
}));

function jsonResponse(status: number, body: unknown = {}) {
  return new Response(JSON.stringify(body), { status });
}

describe("EnrollForm", () => {
  beforeEach(() => {
    replace.mockReset();
    push.mockReset();
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("resumes straight into the app when this browser is already a trusted device", async () => {
    const fetchMock = vi.fn(async () => jsonResponse(200, { ok: true }));
    vi.stubGlobal("fetch", fetchMock);
    render(<EnrollForm />);
    await waitFor(() => expect(replace).toHaveBeenCalledWith("/chat"));
    expect(fetchMock).toHaveBeenCalledWith("/api/auth/session/start", expect.anything());
    expect(screen.queryByLabelText("Eşleştirme kodu")).not.toBeInTheDocument();
  });

  it("enrolls with a code and a device name", async () => {
    const fetchMock = vi.fn<(url: string, init?: RequestInit) => Promise<Response>>(
      async (url) =>
        url === "/api/auth/session/start" ? jsonResponse(401) : jsonResponse(201, { user: {} }),
    );
    vi.stubGlobal("fetch", fetchMock);
    render(<EnrollForm />);

    const code = await screen.findByLabelText("Eşleştirme kodu");
    await userEvent.type(code, " 1234 5678 ");
    const name = screen.getByLabelText("Cihaz adı");
    await userEvent.clear(name);
    await userEvent.type(name, "Ofis Mac");
    await userEvent.click(screen.getByRole("button", { name: "Cihazı eşleştir" }));

    await waitFor(() => expect(push).toHaveBeenCalledWith("/chat"));
    const [, init] = fetchMock.mock.calls.find(([url]) => url === "/api/auth/enroll")!;
    expect(JSON.parse(init!.body as string)).toEqual({
      code: "1234 5678",
      device_name: "Ofis Mac",
    });
  });

  it.each([
    [400, "invalid_code", "Kod geçersiz"],
    [403, "pin_network_refused", "yalnızca yerel ağdan"],
    [429, undefined, "Çok fazla deneme"],
  ])("explains a %s (%s) refusal", async (status, code, message) => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) =>
        url === "/api/auth/session/start" ? jsonResponse(401) : jsonResponse(status, { code }),
      ),
    );
    render(<EnrollForm />);
    await userEvent.type(await screen.findByLabelText("Eşleştirme kodu"), "00000000");
    await userEvent.click(screen.getByRole("button", { name: "Cihazı eşleştir" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(message);
    expect(push).not.toHaveBeenCalled();
  });

  it("has no password field", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => jsonResponse(401)));
    const { container } = render(<EnrollForm />);
    await screen.findByLabelText("Eşleştirme kodu");
    expect(container.querySelector('input[type="password"]')).toBeNull();
  });

  it("does not resume on its own after an explicit sign-out", async () => {
    window.history.pushState({}, "", "/enroll?signed_out=1");
    const fetchMock = vi.fn(async () => jsonResponse(200, { ok: true }));
    vi.stubGlobal("fetch", fetchMock);
    render(<EnrollForm />);
    const resume = await screen.findByRole("button", { name: "Bu cihazla devam et" });
    expect(fetchMock).not.toHaveBeenCalled();
    await userEvent.click(resume);
    await waitFor(() => expect(replace).toHaveBeenCalledWith("/chat"));
    window.history.pushState({}, "", "/enroll");
  });
});

describe("guessDeviceName", () => {
  it.each([
    ["Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Chrome/140 Safari/537", "Mac — Chrome"],
    ["Mozilla/5.0 (iPhone; CPU iPhone OS 18_0) Version/18 Mobile Safari/604", "iPhone — Safari"],
    ["Mozilla/5.0 (Windows NT 10.0) Firefox/140", "Windows — Firefox"],
  ])("%s -> %s", (ua, expected) => {
    expect(guessDeviceName(ua)).toBe(expected);
  });
});
