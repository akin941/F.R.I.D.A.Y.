import { genericRenderer } from "@/components/approvals/renderers/generic";
import {
  harnessImplementRenderer,
  harnessPushRenderer,
} from "@/components/approvals/renderers/harness";
import { purgeTrashRenderer } from "@/components/approvals/renderers/purge-trash";
import type { ApprovalRenderer } from "@/components/approvals/renderers/types";
import { vaultExportRenderer } from "@/components/approvals/renderers/vault-export";

export type { ApprovalRenderer, Decision } from "@/components/approvals/renderers/types";

const RENDERERS: Record<string, ApprovalRenderer> = Object.fromEntries(
  [vaultExportRenderer, harnessImplementRenderer, harnessPushRenderer, purgeTrashRenderer].map(
    (renderer) => [renderer.toolName, renderer],
  ),
);

/** The renderer for `toolName`, or the generic fallback for any other tool. */
export function rendererFor(toolName: string): ApprovalRenderer {
  return Object.hasOwn(RENDERERS, toolName) ? RENDERERS[toolName]! : genericRenderer;
}

/** Tools with a dedicated renderer -- the inbox's tool filter options. */
export const RENDERED_TOOL_NAMES: string[] = Object.keys(RENDERERS);
