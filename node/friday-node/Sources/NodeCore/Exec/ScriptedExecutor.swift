// The scripted executor double (a port of backend/nodes/reference/
// executor.py `FakeExecutor`, `FakeRunning`, `FakeCommandScript`): it records
// every call and plays scripted output and exit codes on the node clock. The
// offline conformance suite (`swift test`) and the Conformance build's hook
// run against it, exactly as the reference node's suites do.
//
// DEBUG builds only (SwiftPM debug, Xcode Debug and Conformance): a Release
// binary carries no double that "runs" anything without the real executor
// (scripts/check-release-markers.sh refuses the type name;
// ReleaseHygieneTests checks `ReleaseProbe.scriptedDoublesCompiledIn`).

#if DEBUG

import Foundation

/// What a scripted command does: `chunks` (`(stream, bytes, at)`, `at`
/// seconds after start), then exit with `exitCode` after `duration`
/// seconds; `hang` never exits on its own.
public struct CommandScript: Equatable, Sendable {
    public var chunks: [(stream: String, data: Data, at: Int64)]
    public var exitCode: Int64 = 0
    public var duration: Int64 = 0
    public var hang = false
    public var sandboxViolation = false
    public var failToStart = false

    public init(chunks: [(stream: String, data: Data, at: Int64)] = [], exitCode: Int64 = 0, duration: Int64 = 0,
                hang: Bool = false, sandboxViolation: Bool = false, failToStart: Bool = false) {
        self.chunks = chunks
        self.exitCode = exitCode
        self.duration = duration
        self.hang = hang
        self.sandboxViolation = sandboxViolation
        self.failToStart = failToStart
    }

    public static func == (left: CommandScript, right: CommandScript) -> Bool {
        left.chunks.map { "\($0.stream)|\($0.data.base64EncodedString())|\($0.at)" }
            == right.chunks.map { "\($0.stream)|\($0.data.base64EncodedString())|\($0.at)" }
            && left.exitCode == right.exitCode && left.duration == right.duration && left.hang == right.hang
            && left.sandboxViolation == right.sandboxViolation && left.failToStart == right.failToStart
    }

    /// A case document's `setup.executor[i]` (FakeCommandScript.from_document):
    /// a chunk is `[stream, data, at?]`, data a string or `{"b64": ...}`.
    public static func from(_ body: JSONValue) -> CommandScript {
        var chunks: [(stream: String, data: Data, at: Int64)] = []
        for chunk in body["chunks"]?.arrayValue ?? [] {
            guard let parts = chunk.arrayValue, parts.count >= 2, let stream = parts[0].stringValue else { continue }
            let data: Data
            if let text = parts[1].stringValue {
                data = Data(text.utf8)
            } else if let encoded = parts[1]["b64"]?.stringValue, let decoded = Data(base64Encoded: encoded) {
                data = decoded
            } else {
                continue
            }
            chunks.append((stream, data, parts.count > 2 ? parts[2].intValue ?? 0 : 0))
        }
        return CommandScript(
            chunks: chunks, exitCode: body["exit_code"]?.intValue ?? 0, duration: body["duration"]?.intValue ?? 0,
            hang: body["hang"]?.boolValue ?? false, sandboxViolation: body["sandbox_violation"]?.boolValue ?? false,
            failToStart: body["fail_to_start"]?.boolValue ?? false)
    }
}

/// One recorded executor call.
public struct ExecutorCall: Equatable, Sendable {
    public let kind: String  // "command" or "action"
    public let capability: String?
    public let argv: [String]
    public let spec: CommandSpec?
    public var terminated = false
}

public final class ScriptedExecutor: ActionExecutor {
    private let clock: NodeClock
    private var scripts: [CommandScript]
    private let defaultScript: CommandScript
    private let actionOK: Bool
    private let availabilities: [String: String]
    public private(set) var calls: [ExecutorCall] = []

    public init(
        clock: NodeClock, scripts: [CommandScript] = [], actionOK: Bool = true,
        availability: [String: String] = [:]
    ) {
        self.clock = clock
        self.scripts = scripts
        defaultScript = CommandScript(chunks: [("stdout", Data("ok\n".utf8), 0)])
        self.actionOK = actionOK
        availabilities = availability
    }

    public var terminatedCount: Int { calls.filter(\.terminated).count }

    public func availability(for capability: String) -> String { availabilities[capability] ?? "granted" }

    public func start(_ spec: CommandSpec) throws -> RunningCommand {
        let script = scripts.isEmpty ? defaultScript : scripts.removeFirst()
        calls.append(ExecutorCall(kind: "command", capability: nil, argv: spec.argv, spec: spec))
        if script.failToStart { throw ExecutorError("scripted start failure") }
        return ScriptedRun(executor: self, script: script, call: calls.count - 1)
    }

    public func perform(_ action: ActionRequest) -> ActionOutcome {
        calls.append(ExecutorCall(kind: "action", capability: action.capability, argv: [], spec: nil))
        return ActionOutcome(ok: actionOK, error: actionOK ? nil : "scripted_failure")
    }

    fileprivate func markTerminated(_ call: Int) { calls[call].terminated = true }
    fileprivate var now: Int64 { clock.now() }
}

private final class ScriptedRun: RunningCommand {
    private weak var executor: ScriptedExecutor?
    private let script: CommandScript
    private let call: Int
    private let started: Int64
    private var pending: [(stream: String, data: Data, at: Int64)]
    private var done = false
    private var terminated = false

    init(executor: ScriptedExecutor, script: CommandScript, call: Int) {
        self.executor = executor
        self.script = script
        self.call = call
        started = executor.now
        pending = script.chunks
    }

    private var elapsed: Int64 { (executor?.now ?? started) - started }

    func read() -> [OutputChunk] {
        if terminated { return [] }
        let elapsed = self.elapsed
        let ready = pending.filter { $0.at <= elapsed }
        pending.removeAll { $0.at <= elapsed }
        return ready.map { OutputChunk(stream: $0.stream, data: $0.data) }
    }

    func poll() -> CommandOutcome? {
        if terminated { return CommandOutcome(exitCode: -Int64(SIGTERM)) }
        if script.hang || !pending.isEmpty || elapsed < script.duration { return nil }
        done = true
        return CommandOutcome(exitCode: script.exitCode, sandboxViolation: script.sandboxViolation)
    }

    func terminate() {
        if done || terminated { return }
        terminated = true
        executor?.markTerminated(call)
    }
}

#endif

/// Public build probes (ReleaseHygieneTests: NodeCoreTests compile in
/// Release without `@testable import`).
public enum ReleaseProbe {
    /// True only in DEBUG builds, which carry the scripted executor and the
    /// scripted window owner.
    public static var scriptedDoublesCompiledIn: Bool {
        #if DEBUG
        return true
        #else
        return false
        #endif
    }
}
