import { Suspense } from "react";
import { ApprovalView } from "@/components/approvals/ApprovalView";

// ApprovalView reads `?request=<id>` via useSearchParams, which needs a
// Suspense boundary for the route to prerender.
export default function ApprovalsPage() {
  return (
    <Suspense>
      <ApprovalView />
    </Suspense>
  );
}
