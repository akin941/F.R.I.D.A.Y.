import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect } from "vitest";

/** The open confirmation dialog (Phase 4 branch 5's `ConfirmDialog`). */
export async function findConfirmDialog(): Promise<HTMLElement> {
  return screen.findByRole("alertdialog");
}

/** Clicks the open confirmation dialog's button named `name`. */
export async function confirmInDialog(name: string | RegExp): Promise<void> {
  const dialog = await findConfirmDialog();
  await userEvent.click(within(dialog).getByRole("button", { name }));
}

/** Dismisses the open confirmation dialog with "Vazgeç" and waits for it to close. */
export async function cancelDialog(): Promise<void> {
  const dialog = await findConfirmDialog();
  await userEvent.click(within(dialog).getByRole("button", { name: "Vazgeç" }));
  await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
}

/** Whether `fetchMock` was ever called with `method` on `url`. */
export function calledWith(
  fetchMock: { mock: { calls: unknown[][] } },
  method: string,
  url: string,
): boolean {
  return fetchMock.mock.calls.some(
    ([input, init]) =>
      input === url && ((init as RequestInit | undefined)?.method ?? "GET") === method,
  );
}
