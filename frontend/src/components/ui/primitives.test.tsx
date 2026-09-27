import { afterEach, describe, expect, it, vi } from "vitest";
import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ThemeProvider } from "next-themes";
import { toast } from "sonner";
import { Inbox } from "lucide-react";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorState } from "@/components/ui/error-state";
import { PageHeader } from "@/components/ui/page-header";
import { StatusPill } from "@/components/ui/status-pill";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Toaster } from "@/components/ui/sonner";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { ThemeToggle } from "@/components/theme/ThemeToggle";
import { ApiError } from "@/lib/api-client";

afterEach(() => {
  document.documentElement.className = "";
  window.localStorage.clear();
});

describe("StatusPill", () => {
  it("renders the label with its domain status tone", () => {
    render(<StatusPill domain="approval" status="pending" label="Beklemede" />);
    const pill = screen.getByText("Beklemede");
    expect(pill).toHaveAttribute("data-tone", "warning");
    expect(pill.className).toContain("bg-warning-muted");
  });

  it("maps harness pipeline and failure statuses", () => {
    render(
      <>
        <StatusPill domain="implementation" status="implementing" label="Uygulanıyor" />
        <StatusPill domain="implementation" status="failed" label="Başarısız" />
        <StatusPill domain="task" status="brand_new_status" label="Yeni" />
      </>,
    );
    expect(screen.getByText("Uygulanıyor")).toHaveAttribute("data-tone", "info");
    expect(screen.getByText("Başarısız")).toHaveAttribute("data-tone", "danger");
    expect(screen.getByText("Yeni")).toHaveAttribute("data-tone", "neutral");
  });
});

describe("AlertDialog", () => {
  function Confirm({ onConfirm }: { onConfirm: () => void }) {
    return (
      <AlertDialog>
        <AlertDialogTrigger asChild>
          <Button>Sil</Button>
        </AlertDialogTrigger>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Belge silinsin mi?</AlertDialogTitle>
            <AlertDialogDescription>“Rapor” çöp kutusuna taşınır.</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Vazgeç</AlertDialogCancel>
            <AlertDialogAction onClick={onConfirm}>Evet, sil</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    );
  }

  it("opens as role=alertdialog, traps focus and restores it on Escape", async () => {
    const user = userEvent.setup();
    const onConfirm = vi.fn();
    render(<Confirm onConfirm={onConfirm} />);
    const trigger = screen.getByRole("button", { name: "Sil" });

    await user.click(trigger);
    const dialog = await screen.findByRole("alertdialog", { name: "Belge silinsin mi?" });
    expect(dialog).toHaveAccessibleDescription("“Rapor” çöp kutusuna taşınır.");
    // Radix focuses the least destructive action (Cancel) first.
    expect(within(dialog).getByRole("button", { name: "Vazgeç" })).toHaveFocus();

    // Focus trap: tabbing never leaves the dialog.
    for (let i = 0; i < 4; i++) {
      await user.tab();
      expect(dialog).toContainElement(document.activeElement as HTMLElement);
    }

    await user.keyboard("{Escape}");
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
    expect(onConfirm).not.toHaveBeenCalled();
  });

  it("runs the action only when confirmed", async () => {
    const user = userEvent.setup();
    const onConfirm = vi.fn();
    render(<Confirm onConfirm={onConfirm} />);
    await user.click(screen.getByRole("button", { name: "Sil" }));
    await user.click(await screen.findByRole("button", { name: "Evet, sil" }));
    expect(onConfirm).toHaveBeenCalledTimes(1);
  });
});

describe("Dialog", () => {
  it("is a labelled dialog with a Turkish close button", async () => {
    const user = userEvent.setup();
    render(
      <Dialog>
        <DialogTrigger asChild>
          <Button>Aç</Button>
        </DialogTrigger>
        <DialogContent>
          <DialogTitle>Ayrıntılar</DialogTitle>
          <DialogDescription>Açıklama</DialogDescription>
        </DialogContent>
      </Dialog>,
    );
    await user.click(screen.getByRole("button", { name: "Aç" }));
    const dialog = await screen.findByRole("dialog", { name: "Ayrıntılar" });
    await user.click(within(dialog).getByRole("button", { name: "Kapat" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});

describe("Tabs", () => {
  it("moves between tabs with the arrow keys", async () => {
    const user = userEvent.setup();
    render(
      <Tabs defaultValue="brief">
        <TabsList aria-label="İş ayrıntıları">
          <TabsTrigger value="brief">Brifing</TabsTrigger>
          <TabsTrigger value="plan">Plan</TabsTrigger>
        </TabsList>
        <TabsContent value="brief">Brifing içeriği</TabsContent>
        <TabsContent value="plan">Plan içeriği</TabsContent>
      </Tabs>,
    );
    expect(screen.getByRole("tab", { name: "Brifing" })).toHaveAttribute("aria-selected", "true");
    await user.click(screen.getByRole("tab", { name: "Brifing" }));
    await user.keyboard("{ArrowRight}");
    expect(screen.getByRole("tab", { name: "Plan" })).toHaveFocus();
    expect(screen.getByRole("tab", { name: "Plan" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tabpanel")).toHaveTextContent("Plan içeriği");
  });
});

describe("Tooltip", () => {
  it("opens on keyboard focus and closes on Escape", async () => {
    const user = userEvent.setup();
    render(
      <TooltipProvider delayDuration={0}>
        <Tooltip>
          <TooltipTrigger asChild>
            <Button aria-label="Yenile">↻</Button>
          </TooltipTrigger>
          <TooltipContent>Listeyi yenile</TooltipContent>
        </Tooltip>
      </TooltipProvider>,
    );
    await user.tab();
    expect(screen.getByRole("button", { name: "Yenile" })).toHaveFocus();
    expect(await screen.findByRole("tooltip")).toHaveTextContent("Listeyi yenile");
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();
  });
});

describe("Toast", () => {
  it("renders a sonner toast in the Toaster outlet", async () => {
    render(
      <ThemeProvider attribute="class">
        <Toaster />
      </ThemeProvider>,
    );
    act(() => {
      toast.success("Kaydedildi");
    });
    expect(await screen.findByText("Kaydedildi")).toBeInTheDocument();
  });
});

describe("EmptyState / PageHeader", () => {
  it("renders the empty message and action", () => {
    render(
      <EmptyState icon={Inbox} title="Henüz ajan yok." action={<Button>Yeni ajan</Button>} />,
    );
    expect(screen.getByText("Henüz ajan yok.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Yeni ajan" })).toBeInTheDocument();
  });

  it("renders a single h1 with description and actions", () => {
    render(<PageHeader title="Ajanlar" description="Açıklama" actions={<Button>Ekle</Button>} />);
    expect(screen.getByRole("heading", { level: 1, name: "Ajanlar" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Ekle" })).toBeInTheDocument();
  });
});

describe("ErrorState", () => {
  it("shows the Turkish message and keeps the English detail inside the disclosure", async () => {
    const user = userEvent.setup();
    const onRetry = vi.fn();
    const error = new ApiError(400, { detail: "This request has expired.", code: "approval.expired" });
    render(<ErrorState error={error} title="Onay yüklenemedi." onRetry={onRetry} />);

    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("Bu onay isteğinin süresi doldu.");
    const details = alert.querySelector("details")!;
    expect(details).not.toHaveAttribute("open");
    expect(within(details).getByText("This request has expired.")).toBeInTheDocument();
    // The English detail lives only inside <details>.
    const outside = [...alert.childNodes].filter((n) => n !== details);
    expect(outside.map((n) => n.textContent).join(" ")).not.toContain("expired.");

    await user.click(screen.getByText("Teknik ayrıntılar"));
    expect(details).toHaveAttribute("open");

    await user.click(screen.getByRole("button", { name: "Tekrar dene" }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it("uses the caller's context for a generic error and has no disclosure for a network error", () => {
    render(<ErrorState error={new TypeError("Failed to fetch")} title="Ajanlar yüklenemedi." />);
    expect(screen.getByRole("alert")).toHaveTextContent("Bir şeyler ters gitti");
    expect(screen.queryByText("Teknik ayrıntılar")).not.toBeInTheDocument();
  });
});

describe("ThemeToggle", () => {
  it("switches between light, dark and system via the class strategy", async () => {
    const user = userEvent.setup();
    render(
      <ThemeProvider attribute="class" defaultTheme="system" enableSystem>
        <ThemeToggle />
      </ThemeProvider>,
    );
    const group = screen.getByRole("radiogroup", { name: "Tema" });
    expect(within(group).getByRole("radio", { name: "Sistem" })).toHaveAttribute(
      "aria-checked",
      "true",
    );

    await user.click(within(group).getByRole("radio", { name: "Koyu" }));
    expect(document.documentElement).toHaveClass("dark");
    expect(within(group).getByRole("radio", { name: "Koyu" })).toHaveAttribute(
      "aria-checked",
      "true",
    );

    await user.click(within(group).getByRole("radio", { name: "Açık" }));
    expect(document.documentElement).toHaveClass("light");
    expect(document.documentElement).not.toHaveClass("dark");
  });

  it("moves the choice with the arrow keys", async () => {
    const user = userEvent.setup();
    render(
      <ThemeProvider attribute="class" defaultTheme="system" enableSystem>
        <ThemeToggle />
      </ThemeProvider>,
    );
    await user.tab();
    expect(screen.getByRole("radio", { name: "Sistem" })).toHaveFocus();
    await user.keyboard("{ArrowRight}");
    expect(screen.getByRole("radio", { name: "Açık" })).toHaveFocus();
    expect(document.documentElement).toHaveClass("light");
  });
});
