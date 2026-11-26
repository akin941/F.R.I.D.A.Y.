// What a case document's `setup` configures besides the readers (a port of
// backend/nodes/conformance/runner.py `build_reference_node` and
// `narrow_catalogue`): the scripted window owner (`setup.window`), the
// scripted executor (`setup.executor`), the availability overrides and the
// `narrow` catalogue. Shared by the offline suite (`swift test`) and the
// Conformance build's hook, so both play a case exactly alike.
//
// DEBUG builds only, like the doubles it builds.

#if DEBUG

import Foundation

public struct CaseSetup {
    public let window: ConfirmationWindowModel
    public let executor: ScriptedExecutor
    public let catalogue: Catalogue

    /// `readerAvailability`: what the case's readers report; `setup.
    /// availability` overrides it (and the action entries' `granted`).
    public init(setup: JSONValue, clock: NodeClock, readerAvailability: [String: String]) {
        window = ConfirmationWindowModel(
            script: (setup["window"]?.arrayValue ?? []).compactMap(ConfirmationDecision.from))
        executor = ScriptedExecutor(
            clock: clock, scripts: (setup["executor"]?.arrayValue ?? []).map(CommandScript.from))
        var availability = readerAvailability
        for (name, value) in setup["availability"]?.objectValue ?? [:] { availability[name] = value.stringValue }
        let standard = Catalogue.standard(availability: availability, actions: true)
        catalogue = setup["catalogue"]?.stringValue == "narrow" ? Self.narrow(standard) : standard
    }

    /// `repo.command.run` advertising a narrower `command_kind` enum than
    /// the server contract (no `build`), so args valid for the server are
    /// outside the node's own schema (check 20).
    static func narrow(_ catalogue: Catalogue) -> Catalogue {
        Catalogue(entries: catalogue.entries.map { entry in
            guard entry["name"]?.stringValue == ActionContracts.commandCapability,
                case .object(var fields) = entry, case .object(var schema)? = fields["args_schema"],
                case .object(var properties)? = schema["properties"],
                case .object(var kind)? = properties["command_kind"], let kinds = kind["enum"]?.arrayValue
            else { return entry }
            kind["enum"] = .array(kinds.filter { $0 != "build" })
            properties["command_kind"] = .object(kind)
            schema["properties"] = .object(properties)
            fields["args_schema"] = .object(schema)
            return .object(fields)
        })
    }
}

#endif
