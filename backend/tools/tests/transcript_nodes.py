"""Node doubles for the transcript pull tests (Phase 8B branch 8).

`ReferenceNodePort` stands in for the `nodes`-backed port
(`tools.transcript_node_port`, tested over the in-process gateway in
`test_transcript_node_port.py`): it re-runs the D13 amendment checks the
way `nodes.services.invoke_read` does (step 0, before anything is "sent"),
records one chain row per invoke (sent or refused), records every frame the
node "received", and answers with the reference owner-turn readers over a
fixture root, validating the result against the capability contract.

`HostileNodePort` answers with whatever output the test hands it.
"""

from dataclasses import dataclass, field
from datetime import UTC

import jsonschema

from nodes.reference import owner_turns
from tools.transcript_ingest import (
    SERVER_REFUSED,
    NodeHandle,
    ReadOutcome,
    refusal_for,
)


@dataclass
class ReferenceNodePort:
    owner: object
    root: object = owner_turns.FIXTURES_DIR
    granted: bool = True
    online: bool = True
    now: object = None
    ref: str = "node-ref-1"
    fail: bool = False
    received: list = field(default_factory=list)
    chain: list = field(default_factory=list)

    @property
    def node(self):
        return NodeHandle(ref=self.ref, owner=self.owner)

    def owner_node(self, owner):
        if not self.online or owner.pk != self.owner.pk:
            return None
        return self.node

    def capability_granted(self, node, capability):
        return self.granted and capability in owner_turns.CAPABILITIES

    def invoke_read(self, *, node, capability, args, workspace, actor, channel, trace_id):
        refusal = refusal_for(
            node=node, capability=capability, actor=actor, workspace=workspace, channel=channel
        )
        row = {
            "capability": capability,
            "workspace": workspace.pk,
            "actor": getattr(actor, "pk", None),
            "channel": channel,
            "trace_id": trace_id,
        }
        if refusal is not None:
            self.chain.append({**row, "status": SERVER_REFUSED, "code": refusal})
            return ReadOutcome(SERVER_REFUSED, code=refusal)
        self.received.append({**row, "args": args})
        if self.fail:
            self.chain.append({**row, "status": "failed"})
            return ReadOutcome("failed", code="node_timeout")
        reply = owner_turns.read_owner_turns(
            capability, args, root=self.root, granted=self.granted, now=self._now()
        )
        if reply["status"] != "ok":
            self.chain.append({**row, "status": "node_refused", "code": reply["code"]})
            return ReadOutcome("node_refused", code=reply["code"])
        contract = owner_turns.load_contract(capability)
        jsonschema.validate(reply["output"], contract["result_schema"])
        self.chain.append({**row, "status": "ok"})
        return ReadOutcome("ok", output=reply["output"], invocation_id=f"inv-{len(self.chain)}")

    def _now(self):
        from django.utils import timezone

        return (self.now or timezone.now()).astimezone(UTC)


@dataclass
class HostileNodePort(ReferenceNodePort):
    output: dict = None

    def invoke_read(self, *, node, capability, args, workspace, actor, channel, trace_id):
        self.received.append({"capability": capability, "args": args, "workspace": workspace.pk})
        return ReadOutcome("ok", output=self.output, invocation_id="inv-hostile")
