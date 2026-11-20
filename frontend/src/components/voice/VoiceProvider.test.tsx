import { afterEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { VoiceProvider } from "@/components/voice/VoiceProvider";
import { WakeSettingsPanel } from "@/components/voice/WakeSettingsPanel";
import { AUTH_LOST_EVENT } from "@/lib/auth-events";
import { ModelIntegrityError } from "@/lib/voice/model-loader";
import { MUTE_STORAGE_KEY, setVoiceMuted } from "@/lib/voice/mute";
import { ScriptedDetector, fakeStream, memoryPort, wakeDeps } from "@/test/wake-fakes";

const push = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push, refresh: vi.fn() }) }));

function setup(overrides = {}) {
  const port = memoryPort(new ScriptedDetector());
  const stream = fakeStream();
  const deps = wakeDeps(port, stream, {
    readMute: () => {
      try {
        return { muted: window.localStorage.getItem(MUTE_STORAGE_KEY) === "1", readable: true };
      } catch {
        return { muted: true, readable: false };
      }
    },
    ...overrides,
  });
  // Only the edges are replaced; snapshot/settings/mute plumbing stays the provider's.
  const { verifyModels, openStream, createPort, userActivationActive } = deps;
  render(
    <VoiceProvider depsOverride={{ verifyModels, openStream, createPort, userActivationActive }}>
      <WakeSettingsPanel />
    </VoiceProvider>,
  );
  return { port, stream, deps };
}

afterEach(() => {
  window.localStorage.clear();
  document.title = "";
});

describe("VoiceProvider + wake settings", () => {
  it("starts off (push-to-talk default); arming by click shows the indicator and the title prefix", async () => {
    document.title = "Friday";
    const { stream } = setup();
    expect(screen.getByTestId("wake-state")).toHaveTextContent("Uyandırma modu kapalı.");
    expect(screen.queryByTestId("wake-indicator")).toBeNull();
    await userEvent.click(screen.getByTestId("wake-toggle"));
    const indicator = await screen.findByTestId("wake-indicator");
    expect(indicator).toHaveTextContent('Dinliyor — "Hey Friday"');
    expect(document.title.startsWith("● Dinliyor")).toBe(true);
    // The indicator's own button disarms and releases the mic.
    await userEvent.click(screen.getByTestId("wake-indicator-disarm"));
    expect(screen.queryByTestId("wake-indicator")).toBeNull();
    expect(stream.closeSpy).toHaveBeenCalled();
    expect(document.title).toBe("Friday");
  });

  it("disarms on logout / a hard 401 (auth-lost signal)", async () => {
    const { stream } = setup();
    await userEvent.click(screen.getByTestId("wake-toggle"));
    await screen.findByTestId("wake-indicator");
    act(() => {
      window.dispatchEvent(new Event(AUTH_LOST_EVENT));
    });
    expect(screen.queryByTestId("wake-indicator")).toBeNull();
    expect(stream.closeSpy).toHaveBeenCalled();
    expect(screen.getByTestId("wake-notice")).toHaveTextContent("Oturum kapandığı için");
  });

  it("disarms when the shell mute is switched on", async () => {
    const { stream } = setup();
    await userEvent.click(screen.getByTestId("wake-toggle"));
    await screen.findByTestId("wake-indicator");
    act(() => setVoiceMuted(true));
    await waitFor(() => expect(screen.queryByTestId("wake-indicator")).toBeNull());
    expect(stream.closeSpy).toHaveBeenCalled();
  });

  it("explains a hash mismatch and keeps the mic closed", async () => {
    const { deps } = setup({
      verifyModels: vi.fn(async () => {
        throw new ModelIntegrityError("model_hash_mismatch", "hey_friday.onnx");
      }),
    });
    await userEvent.click(screen.getByTestId("wake-toggle"));
    expect(await screen.findByTestId("wake-error")).toHaveTextContent("özet uyuşmuyor");
    expect(deps.openStream).not.toHaveBeenCalled();
    expect(screen.queryByTestId("wake-indicator")).toBeNull();
  });

  it("explains the not-yet-trained hey_friday model", async () => {
    setup({
      verifyModels: vi.fn(async () => {
        throw new ModelIntegrityError("model_unpinned", "hey_friday.onnx");
      }),
    });
    await userEvent.click(screen.getByTestId("wake-toggle"));
    expect(await screen.findByTestId("wake-error")).toHaveTextContent("henüz sabitlenmedi");
  });

  it("stores the threshold per device", async () => {
    setup();
    const slider = screen.getByTestId("wake-threshold") as HTMLInputElement;
    fireEvent.change(slider, { target: { value: "0.7" } });
    await waitFor(() => expect(window.localStorage.getItem("friday:voice:wake-threshold")).toBe("0.7"));
  });
});
