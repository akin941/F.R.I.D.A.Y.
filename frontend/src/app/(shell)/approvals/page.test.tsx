import { beforeEach, describe, expect, it, vi } from "vitest";

const redirectMock = vi.fn((url: string) => {
  throw new Error(`NEXT_REDIRECT ${url}`);
});
vi.mock("next/navigation", () => ({ redirect: (url: string) => redirectMock(url) }));
vi.mock("@/components/approvals/ApprovalInbox", () => ({ ApprovalInbox: () => null }));

const ID = "11111111-1111-1111-1111-111111111111";

describe("/approvals page", () => {
  beforeEach(() => {
    redirectMock.mockClear();
  });

  it("redirects the legacy ?request=<id> link to the routed detail", async () => {
    const { default: ApprovalsPage } = await import("@/app/(shell)/approvals/page");
    await expect(ApprovalsPage({ searchParams: Promise.resolve({ request: ID }) })).rejects.toThrow(
      `NEXT_REDIRECT /approvals/${ID}`,
    );
  });

  it.each([{}, { request: "../evil" }, { workspace: ID }])(
    "renders the inbox otherwise (%j)",
    async (searchParams) => {
      const { default: ApprovalsPage } = await import("@/app/(shell)/approvals/page");
      await ApprovalsPage({ searchParams: Promise.resolve(searchParams) });
      expect(redirectMock).not.toHaveBeenCalled();
    },
  );
});
