import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { HarnessRepoPicker } from "@/components/harness/HarnessRepoPicker";
import { REPO_2_ID, REPO_ID, jsonResponse, repo } from "@/components/harness/test-fixtures";

function Harness({
  initial = "",
  requireTestCommand = false,
  onChange = () => undefined,
}: {
  initial?: string;
  requireTestCommand?: boolean;
  onChange?: (value: string) => void;
}) {
  const [value, setValue] = useState(initial);
  return (
    <>
      <HarnessRepoPicker
        id="picker"
        workspaceId="w1"
        value={value}
        onChange={(next) => {
          setValue(next);
          onChange(next);
        }}
        requireTestCommand={requireTestCommand}
      />
      <output data-testid="value">{value}</output>
    </>
  );
}

describe("HarnessRepoPicker", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("shows a loading placeholder, then the enabled repos only", async () => {
    const fetchMock = vi.fn(async () =>
      jsonResponse([repo(), repo({ id: REPO_2_ID, name: "old-repo", enabled: false })]),
    );
    vi.stubGlobal("fetch", fetchMock);

    const { container } = render(<Harness />);
    expect(container.querySelector('[data-slot="skeleton"]')).not.toBeNull();

    await userEvent.click(await screen.findByRole("combobox", { name: "Depo" }));

    expect(await screen.findByRole("option", { name: "jarvis" })).toBeInTheDocument();
    expect(screen.queryByRole("option", { name: /old-repo/ })).not.toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/harness/repos?workspace=w1",
      expect.objectContaining({ method: "GET" }),
    );
  });

  it("reports the picked repo id", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => jsonResponse([repo()])));
    const onChange = vi.fn();

    render(<Harness onChange={onChange} />);
    await userEvent.click(await screen.findByRole("combobox", { name: "Depo" }));
    await userEvent.click(await screen.findByRole("option", { name: "jarvis" }));

    expect(onChange).toHaveBeenCalledWith(REPO_ID);
    expect(screen.getByTestId("value")).toHaveTextContent(REPO_ID);
  });

  it("points to the repo registry when no repo is available", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => jsonResponse([repo({ enabled: false })])));

    render(<Harness />);

    expect(await screen.findByText(/kullanılabilir kayıtlı depo yok/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Depolar sayfasından" })).toHaveAttribute(
      "href",
      "/harness/repos",
    );
    expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
  });

  it("shows an error when the repo list cannot be loaded", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => jsonResponse({ detail: "no" }, 403)));

    render(<Harness />);

    expect(await screen.findByText("Depolar yüklenemedi.")).toBeInTheDocument();
  });

  it("disables repos without a test command when one is required", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResponse([repo(), repo({ id: REPO_2_ID, name: "untested", test_command: "" })]),
      ),
    );

    render(<Harness requireTestCommand />);
    await userEvent.click(await screen.findByRole("combobox", { name: "Depo" }));

    const option = await screen.findByRole("option", { name: /untested \(test komutu tanımlı değil\)/ });
    expect(option).toHaveAttribute("aria-disabled", "true");
  });

  it("clears a prefilled repo that is no longer selectable and says why", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse([repo({ id: REPO_2_ID, name: "other" })])),
    );

    render(<Harness initial={REPO_ID} />);

    expect(await screen.findByText(/Önceden seçilen depo artık kullanılamıyor/)).toBeInTheDocument();
    await waitFor(() => expect(screen.getByTestId("value")).toHaveTextContent(""));
  });

  it("keeps a prefilled repo that is still selectable", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => jsonResponse([repo()])));

    render(<Harness initial={REPO_ID} requireTestCommand />);

    expect(await screen.findByRole("combobox", { name: "Depo" })).toHaveTextContent("jarvis");
    expect(screen.getByTestId("value")).toHaveTextContent(REPO_ID);
  });
});
