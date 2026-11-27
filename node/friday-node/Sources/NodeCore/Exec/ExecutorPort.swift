// The executor port (Phase 10 brief D4, D5 check 24; a port of the
// interface of backend/nodes/reference/executor.py).
//
// The session never runs anything itself: after the owner's click and check
// 24 it hands a `CommandSpec` (repo.command.run) or an `ActionRequest`
// (open, focus, recipes, AX) to an executor. A started command exposes
// `read()` (pending output chunks), `poll()` (the outcome once finished) and
// `terminate()` (SIGTERM, then SIGKILL after the grace): the session calls
// `terminate()` on socket loss, the kill switch, "Durdur" and the runtime
// cap, and sends no late result.
//
// Runbook B5 shipped the port, the throwaway-worktree mode and (DEBUG builds
// only) the scripted double the conformance suites run against. Runbook B6
// adds the real executors: `Exec/SrtExecutor.swift` (with the `srt`
// settings document of `Exec/SandboxPolicy.swift`, byte-identical to
// backend/nodes/reference/sandbox.py), the recipes, AX and open/focus,
// composed by `Exec/MacActionExecutor.swift`. `UnavailableExecutor` stays
// for a node without them (everything `unsupported`).

import Foundation

/// What one command run gets (reference `SandboxSpec`): the mapping's argv
/// and paths, and (`sandbox`) the `srt` settings document, the stripped
/// environment and the limits `SandboxPolicy` generated for this run.
public struct CommandSpec: Equatable, Sendable {
    public let mode: String
    public let argv: [String]
    /// The working directory: the repo root (`in_place`) or the throwaway
    /// worktree (`worktree`).
    public let cwd: String
    public let repoRoot: String
    public let writeRoot: String
    /// A fresh private (0700) directory for this run.
    public let tempDir: String
    /// The mapping's fixed PATH and its network domains (none by default).
    public let path: String
    public let networkDomains: [String]
    /// min(mapping, contract, 900) seconds (sandbox.py `wall`).
    public let wallSeconds: Int64
    public let killGraceSeconds: Int64
    /// The generated sandbox (settings, environment, limits). Nil only for a
    /// spec a test builds by hand; the real executor refuses such a spec.
    public let sandbox: SandboxDocument?

    public static let killGrace: Int64 = 5

    public init(
        mode: String, argv: [String], cwd: String, repoRoot: String, writeRoot: String, tempDir: String,
        path: String, networkDomains: [String], wallSeconds: Int64, killGraceSeconds: Int64 = CommandSpec.killGrace,
        sandbox: SandboxDocument? = nil
    ) {
        self.sandbox = sandbox
        self.mode = mode
        self.argv = argv
        self.cwd = cwd
        self.repoRoot = repoRoot
        self.writeRoot = writeRoot
        self.tempDir = tempDir
        self.path = path
        self.networkDomains = networkDomains
        self.wallSeconds = wallSeconds
        self.killGraceSeconds = killGraceSeconds
    }

    /// The wall-time cap of one run: the mapping's, the contract's and the
    /// protocol's ceiling, whichever is lowest.
    public static func wallSeconds(mapping: Int64, contract: Int64?) -> Int64 {
        min(mapping, contract ?? ActionContracts.maxRuntimeSeconds, ActionContracts.maxRuntimeSeconds)
    }
}

public struct CommandOutcome: Equatable, Sendable {
    /// -255...255 (a signal is its negative number), nil when unknown.
    public let exitCode: Int64?
    public let sandboxViolation: Bool
    public let error: String?

    public init(exitCode: Int64?, sandboxViolation: Bool = false, error: String? = nil) {
        self.exitCode = exitCode
        self.sandboxViolation = sandboxViolation
        self.error = error
    }
}

public struct ActionRequest: Equatable, Sendable {
    public let capability: String
    public let args: JSONValue
    public let bundleID: String?
    public let recipe: String?
    /// A repo recipe's repo root as check 22 resolved it (the realpath that
    /// equals a mapped root).
    public let repoRoot: String?

    public init(
        capability: String, args: JSONValue, bundleID: String? = nil, recipe: String? = nil, repoRoot: String? = nil
    ) {
        self.capability = capability
        self.args = args
        self.bundleID = bundleID
        self.recipe = recipe
        self.repoRoot = repoRoot
    }
}

public struct ActionOutcome: Equatable, Sendable {
    public let ok: Bool
    public let error: String?

    public init(ok: Bool, error: String? = nil) {
        self.ok = ok
        self.error = error
    }
}

public struct OutputChunk: Equatable, Sendable {
    public let stream: String
    public let data: Data
    /// Bytes past the output cap the executor counted but did not keep
    /// (they still count toward `output_bytes`).
    public let droppedBytes: Int64

    public init(stream: String, data: Data, droppedBytes: Int64 = 0) {
        self.stream = stream
        self.data = data
        self.droppedBytes = droppedBytes
    }
}

/// One started command.
public protocol RunningCommand: AnyObject {
    /// Output that arrived since the last call.
    func read() -> [OutputChunk]
    /// The outcome once the command finished; nil while it runs.
    func poll() -> CommandOutcome?
    /// SIGTERM, then SIGKILL after the spec's grace. Idempotent and
    /// non-blocking: the process may still be alive when it returns.
    func terminate()
    /// The process tree is gone: its files may be removed now.
    var hasExited: Bool { get }
    /// Run `work` once the process tree is gone (at once when it already
    /// is): the session defers removing the worktree and the temp dir until
    /// then (B5 review NIT: no cleanup under a live process).
    func afterExit(_ work: @escaping () -> Void)
}

extension RunningCommand {
    public var hasExited: Bool { poll() != nil }
    public func afterExit(_ work: @escaping () -> Void) { work() }
}

public protocol ActionExecutor: AnyObject {
    /// The `hello` availability of one action contract (`granted` only when
    /// the executor can run it and its TCC grant is present).
    func availability(for capability: String) -> String
    /// Start one command; throws when it cannot start (nothing runs).
    func start(_ spec: CommandSpec) throws -> RunningCommand
    /// Perform one non-command action.
    func perform(_ action: ActionRequest) -> ActionOutcome
}

public struct ExecutorError: Error, Equatable {
    public let reason: String
    public init(_ reason: String) { self.reason = reason }
}

/// A node without executors (the runner's default when no executor factory
/// is given): nothing can run, every action contract is `unsupported`. The
/// app uses `MacActionExecutor` since runbook B6.
public final class UnavailableExecutor: ActionExecutor {
    public init() {}
    public func availability(for capability: String) -> String { "unsupported" }
    public func start(_ spec: CommandSpec) throws -> RunningCommand { throw ExecutorError("no executor") }
    public func perform(_ action: ActionRequest) -> ActionOutcome { ActionOutcome(ok: false, error: "no executor") }
}
