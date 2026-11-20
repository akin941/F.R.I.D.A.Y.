/**
 * "A yes is not a done" (Phase 6 A7, ADR-015): Friday's spoken
 * confirmations are built ONLY from a receipt -- the id of a row that now
 * exists (a task, an approval request, a memory proposal) or an execution
 * status read back from the server. No receipt id, no confirmation: the
 * functions here return `null`, and the caller says nothing about it.
 */

export type ActionReceipt =
  | { kind: "task"; taskId: string }
  | { kind: "approval_request"; approvalId: string }
  | { kind: "memory_proposal"; memoryId: string }
  | { kind: "execution"; executionId: string; status: string };

function hasId(value: unknown): value is string {
  return typeof value === "string" && value.trim().length > 0;
}

export function spokenConfirmation(receipt: ActionReceipt | null | undefined): string | null {
  if (!receipt) return null;
  switch (receipt.kind) {
    case "task":
      return hasId(receipt.taskId) ? "Görev oluşturuldu; Görevler listesinde." : null;
    case "approval_request":
      // Created, NOT decided: the decision stays on /approvals (D8).
      return hasId(receipt.approvalId)
        ? "Onay isteği oluşturuldu. Kararı Onaylar sayfasında ekrandan verebilirsin."
        : null;
    case "memory_proposal":
      return hasId(receipt.memoryId)
        ? "Bunu hafıza önerisi olarak kaydettim; Hafıza sayfasında onaylarsan hatırlarım."
        : null;
    case "execution": {
      if (!hasId(receipt.executionId)) return null;
      if (receipt.status === "succeeded") return "İşlem tamamlandı.";
      if (receipt.status === "failed") return "İşlem başarısız oldu; ayrıntılar ekranda.";
      return "İşlem henüz bitmedi; durumu ekranda.";
    }
    default:
      return null;
  }
}

/** The fixed reply to a spoken "evet / onayla" (no receipt, no action). */
export const AFFIRMATION_REPLY =
  "Sesle onay veremem. Onay ve kararları ekrandaki karttan ya da Onaylar sayfasından verebilirsin.";

/** Spoken notice for a draft card that arrived in a voice turn. */
export const DRAFT_NOTICE: Record<"task" | "tool", string> = {
  task: "Görev taslağı ekranda; oluşturmak için karta tıkla.",
  tool: "Onay gerektiren bir istek taslağı ekranda; göndermek için karta tıkla.",
};

export interface ApiUsageReceipt {
  turn: {
    trace_id: string;
    calls: number;
    failed_calls: number;
    input_tokens: number;
    output_tokens: number;
    audio_seconds: number;
    estimated_cost: string;
    local_only: boolean;
    purposes: Record<string, number>;
  } | null;
  today: { calls: number; input_tokens: number; output_tokens: number; estimated_cost: string };
  currency: string;
}

function formatUsd(value: string): string {
  const amount = Number.parseFloat(value);
  if (!Number.isFinite(amount) || amount === 0) return "0 dolar";
  const digits = amount < 0.01 ? 4 : 2;
  return `${amount.toLocaleString("tr-TR", { minimumFractionDigits: digits, maximumFractionDigits: digits })} dolar`;
}

/** The spoken answer to "bu ne kadar tuttu?", from the ModelUsage receipt. */
export function receiptSentence(receipt: ApiUsageReceipt): string {
  const today = `Bugün bu çalışma alanında toplam ${receipt.today.calls} çağrı, ${formatUsd(receipt.today.estimated_cost)}.`;
  const turn = receipt.turn;
  if (!turn) return `Son tur için kayıt bulamadım. ${today}`;
  const head =
    turn.local_only || Number.parseFloat(turn.estimated_cost) === 0
      ? `Son tur ${turn.calls} model çağrısıydı ve yerel çalıştı; maliyeti sıfır.`
      : `Son tur ${turn.calls} model çağrısıydı ve yaklaşık ${formatUsd(turn.estimated_cost)} tuttu.`;
  return `${head} ${today}`;
}
