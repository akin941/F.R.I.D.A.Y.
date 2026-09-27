import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { ApiError } from "@/lib/api-client";

function renderDialog(onConfirm: () => Promise<unknown>) {
  render(
    <ConfirmDialog
      trigger={<Button>Sil</Button>}
      title="Bu görev silinsin mi?"
      description="“Süt al” çöp kutusuna taşınır."
      confirmLabel="Evet, sil"
      busyLabel="Siliniyor…"
      errorFallback="Görev silinemedi."
      onConfirm={onConfirm}
    />,
  );
}

describe("ConfirmDialog", () => {
  it("runs nothing on the trigger; names the object; runs on confirm and closes", async () => {
    const onConfirm = vi.fn(async () => undefined);
    renderDialog(onConfirm);

    await userEvent.click(screen.getByRole("button", { name: "Sil" }));
    const dialog = await screen.findByRole("alertdialog");
    expect(dialog).toHaveTextContent("Bu görev silinsin mi?");
    expect(dialog).toHaveTextContent("“Süt al”");
    expect(onConfirm).not.toHaveBeenCalled();

    await userEvent.click(within(dialog).getByRole("button", { name: "Evet, sil" }));
    expect(onConfirm).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
    // Focus returns to the trigger.
    expect(screen.getByRole("button", { name: "Sil" })).toHaveFocus();
  });

  it("Vazgeç closes without running", async () => {
    const onConfirm = vi.fn(async () => undefined);
    renderDialog(onConfirm);

    await userEvent.click(screen.getByRole("button", { name: "Sil" }));
    await userEvent.click(
      within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Vazgeç" }),
    );

    await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
    expect(onConfirm).not.toHaveBeenCalled();
  });

  it("stays open and busy while running, then reports a failure inside", async () => {
    let reject: (err: unknown) => void = () => {};
    const onConfirm = vi.fn(
      () =>
        new Promise((_resolve, rej) => {
          reject = rej;
        }),
    );
    renderDialog(onConfirm);

    await userEvent.click(screen.getByRole("button", { name: "Sil" }));
    const dialog = await screen.findByRole("alertdialog");
    await userEvent.click(within(dialog).getByRole("button", { name: "Evet, sil" }));

    expect(within(dialog).getByRole("button", { name: "Siliniyor…" })).toBeDisabled();
    expect(within(dialog).getByRole("button", { name: "Vazgeç" })).toBeDisabled();
    await userEvent.keyboard("{Escape}");
    expect(screen.getByRole("alertdialog")).toBeInTheDocument();

    reject(new ApiError(400, { detail: "nope" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("Görev silinemedi.");
    expect(screen.getByRole("alertdialog")).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Evet, sil" })).toBeEnabled();
  });
});
