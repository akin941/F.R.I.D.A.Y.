import { describe, expect, it } from "vitest";
import {
  AFFIRMATION_REPLY,
  receiptSentence,
  spokenConfirmation,
  type ApiUsageReceipt,
} from "@/lib/voice/receipts";
import { isInterruptWord, normalizeUtterance, routeTranscript } from "@/lib/voice/routes";

describe("normalizeUtterance", () => {
  it("lower-cases the Turkish way and drops punctuation", () => {
    expect(normalizeUtterance("  İSTANBUL, Işık!  ")).toBe("istanbul ışık");
    expect(normalizeUtterance("Dur.")).toBe("dur");
  });
});

describe("routeTranscript", () => {
  it.each(["dur", "Dur!", "bekle.", "STOP", "Durdur", "sus"])("%s is an interrupt word", (text) => {
    expect(routeTranscript(text)).toEqual({ kind: "interrupt" });
    expect(isInterruptWord(text)).toBe(true);
  });

  it("an interrupt word inside a sentence is not an interrupt", () => {
    expect(routeTranscript("Dur bakalım, yarın ne var?").kind).toBe("brain");
  });

  it.each([
    ["Hatırla ki annemin doğum günü 3 Mart.", "Annemin doğum günü 3 Mart."],
    ["hatırla ki, kahveyi sade içiyorum", "Kahveyi sade içiyorum"],
    ["Friday, unutma ki toplantı salı günü", "Toplantı salı günü"],
    ["Hatırla: İzmir ofisi kapandı", "İzmir ofisi kapandı"],
  ])("%s -> a memory proposal", (text, content) => {
    expect(routeTranscript(text)).toEqual({ kind: "remember", content });
  });

  it("'hatırla' without the explicit form goes to the brain", () => {
    expect(routeTranscript("Hatırla bakalım dün ne konuştuk").kind).toBe("brain");
    expect(routeTranscript("hatırla ki").kind).toBe("brain");
  });

  it.each(["Bu ne kadar tuttu?", "ne kadar tuttu", "Peki bu ne kadar tuttu", "maliyeti ne oldu"])(
    "%s -> the ModelUsage receipt",
    (text) => {
      expect(routeTranscript(text)).toEqual({ kind: "receipt" });
    },
  );

  it.each(["Evet", "evet, onayla!", "Onaylıyorum.", "approve", "reddet", "iptal et"])(
    "%s is an affirmation/decision word: never routed to an action",
    (text) => {
      expect(routeTranscript(text)).toEqual({ kind: "affirmation" });
    },
  );

  it("sessize al -> mute", () => {
    expect(routeTranscript("Sessize al.")).toEqual({ kind: "mute" });
  });

  it("everything else goes to the brain verbatim", () => {
    expect(routeTranscript(" Yarın saat 10'da diş hekimi ")).toEqual({
      kind: "brain",
      text: "Yarın saat 10'da diş hekimi",
    });
  });
});

describe("receipts-only confirmations ('a yes is not a done')", () => {
  it("a spoken 'evet' never produces a confirmation without a receipt id", () => {
    expect(routeTranscript("evet").kind).toBe("affirmation");
    expect(AFFIRMATION_REPLY).toMatch(/Sesle onay veremem/);
    expect(spokenConfirmation(null)).toBeNull();
    expect(spokenConfirmation(undefined)).toBeNull();
    expect(spokenConfirmation({ kind: "task", taskId: "" })).toBeNull();
    expect(spokenConfirmation({ kind: "approval_request", approvalId: "  " })).toBeNull();
    expect(spokenConfirmation({ kind: "memory_proposal", memoryId: "" })).toBeNull();
    expect(spokenConfirmation({ kind: "execution", executionId: "", status: "succeeded" })).toBeNull();
  });

  it("confirms only what the receipt proves", () => {
    expect(spokenConfirmation({ kind: "task", taskId: "t1" })).toMatch(/Görev oluşturuldu/);
    // A created approval REQUEST is not a decision.
    const approval = spokenConfirmation({ kind: "approval_request", approvalId: "a1" })!;
    expect(approval).toMatch(/Onay isteği oluşturuldu/);
    expect(approval).not.toMatch(/onaylandı/i);
    expect(spokenConfirmation({ kind: "memory_proposal", memoryId: "m1" })).toMatch(/onaylarsan/);
    expect(spokenConfirmation({ kind: "execution", executionId: "e1", status: "running" })).toMatch(
      /henüz bitmedi/,
    );
  });
});

describe("receiptSentence", () => {
  const base: ApiUsageReceipt = {
    turn: {
      trace_id: "t",
      calls: 4,
      failed_calls: 0,
      input_tokens: 10,
      output_tokens: 5,
      audio_seconds: 2,
      estimated_cost: "0.002100",
      local_only: false,
      purposes: { chat: 1 },
    },
    today: { calls: 12, input_tokens: 0, output_tokens: 0, estimated_cost: "1.250000" },
    currency: "USD",
  };

  it("reads the turn and today's total", () => {
    expect(receiptSentence(base)).toBe(
      "Son tur 4 model çağrısıydı ve yaklaşık 0,0021 dolar tuttu. Bugün bu çalışma alanında toplam 12 çağrı, 1,25 dolar.",
    );
  });

  it("a local turn costs zero", () => {
    expect(receiptSentence({ ...base, turn: { ...base.turn!, local_only: true, estimated_cost: "0.000000" } })).toMatch(
      /yerel çalıştı; maliyeti sıfır/,
    );
  });

  it("no trace -> says so", () => {
    expect(receiptSentence({ ...base, turn: null })).toMatch(/^Son tur için kayıt bulamadım/);
  });
});
