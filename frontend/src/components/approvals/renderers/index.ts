import {
  GENERIC_CONNECTOR_WRITE_PREFIX,
  calendarCreateEventRenderer,
  genericConnectorWriteRenderer,
  githubOpenPrRenderer,
  haCallServiceRenderer,
  mailSendRenderer,
} from "@/components/approvals/renderers/connector-write";
import { deployRenderer } from "@/components/approvals/renderers/deploy";
import { genericRenderer } from "@/components/approvals/renderers/generic";
import {
  harnessImplementRenderer,
  harnessPushRenderer,
} from "@/components/approvals/renderers/harness";
import { nodeActionRenderer } from "@/components/approvals/renderers/node-action";
import { purgeTrashRenderer } from "@/components/approvals/renderers/purge-trash";
import type { ApprovalRenderer } from "@/components/approvals/renderers/types";
import { vaultExportRenderer } from "@/components/approvals/renderers/vault-export";

export type { ApprovalRenderer, Decision } from "@/components/approvals/renderers/types";

const RENDERERS: Record<string, ApprovalRenderer> = Object.fromEntries(
  [
    vaultExportRenderer,
    harnessImplementRenderer,
    harnessPushRenderer,
    purgeTrashRenderer,
    nodeActionRenderer,
    deployRenderer,
    githubOpenPrRenderer,
    haCallServiceRenderer,
    mailSendRenderer,
    calendarCreateEventRenderer,
  ].map((renderer) => [renderer.toolName, renderer]),
);

/**
 * The renderer for `toolName`: its dedicated one; the generic connector
 * write renderer for any other `mcp_<connector>_<tool>` gate (Phase 5,
 * brief D6); else the generic fallback.
 */
export function rendererFor(toolName: string): ApprovalRenderer {
  if (Object.hasOwn(RENDERERS, toolName)) return RENDERERS[toolName]!;
  if (toolName.startsWith(GENERIC_CONNECTOR_WRITE_PREFIX) && toolName.length > 4) {
    return genericConnectorWriteRenderer;
  }
  return genericRenderer;
}

/** Tools with a dedicated renderer -- the inbox's tool filter options. */
export const RENDERED_TOOL_NAMES: string[] = Object.keys(RENDERERS);
