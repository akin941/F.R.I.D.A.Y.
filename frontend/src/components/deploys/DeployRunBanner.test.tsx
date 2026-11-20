import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { DeployRunBanner } from "@/components/deploys/DeployRunBanner";
import { SHA_LIVE } from "@/components/deploys/test-fixtures";
import { tr } from "@/lib/i18n/tr";

const ATTENTION = [
  "rolled_back",
  "rollback_failed",
  "health_failed",
  "succeeded_degraded",
  "unknown_outcome",
] as const;

function banner(status: string, props: { errorCode?: string } = {}) {
  const { container } = render(<DeployRunBanner status={status} rollbackSha={SHA_LIVE} {...props} />);
  return { node: screen.getByTestId(`deploy-banner-${status}`), container };
}

describe("DeployRunBanner", () => {
  it.each(ATTENTION)("%s has its own banner, never the success wording or tone", (status) => {
    const { node } = banner(status);
    expect(node).not.toHaveTextContent(tr.deploys.banner.succeeded);
    expect(node.dataset.tone).not.toBe("success");
    expect(node).toHaveAttribute("role", "alert");
  });

  it("every attention state reads differently", () => {
    const texts = ATTENTION.map((status) => {
      const { node, container } = banner(status);
      const text = node.textContent;
      container.remove();
      return text;
    });
    expect(new Set(texts).size).toBe(ATTENTION.length);
  });

  it("a degraded deploy is a warning that says it is not clean", () => {
    const { node } = banner("succeeded_degraded", { errorCode: "health_url_failed" });
    expect(node.dataset.tone).toBe("warning");
    expect(node).toHaveTextContent("temiz değil");
    expect(node).toHaveTextContent("Sağlık adresi kontrolü başarısız.");
  });

  it("rolled_back names the commit that is live again", () => {
    expect(banner("rolled_back").node).toHaveTextContent(`“${SHA_LIVE.slice(0, 7)}”`);
  });

  it.each(["rollback_failed", "health_failed", "unknown_outcome"])("%s is danger", (status) => {
    expect(banner(status).node.dataset.tone).toBe("danger");
  });

  it("a clean success is a status in the success tone", () => {
    const { node } = banner("succeeded");
    expect(node.dataset.tone).toBe("success");
    expect(node).toHaveAttribute("role", "status");
    expect(node).toHaveTextContent(tr.deploys.banner.succeeded);
  });

  it("an active run says it is running; a pending one mentions the offline runner", () => {
    expect(banner("deploying").node).toHaveTextContent("Deploy sürüyor");
    expect(banner("pending").node).toHaveTextContent("çevrimdışı");
  });

  it("an unknown status renders nothing", () => {
    const { container } = render(<DeployRunBanner status="something_new" />);
    expect(container).toBeEmptyDOMElement();
  });
});
