// The `srt` sandbox policy generator (Phase 10 brief D4; runbook B6): a
// line-for-line port of backend/nodes/reference/sandbox.py `build_sandbox`.
//
// One node-local command mapping becomes:
// - the `srt` settings document: network (`allowedDomains` empty unless the
//   mapping names domains, no Unix sockets, no local binding) and filesystem
//   (`denyRead` of every credential and browser-profile directory and the
//   node's own data, `allowWrite` of the write root and the per-run private
//   temp dir only, `denyWrite` of the write root's `.git`); `/tmp` itself is
//   never writable;
// - the process spec: argv (no shell), the working directory (the write
//   root), and an environment stripped to an allowlist (`PATH` fixed per
//   mapping, `HOME`, `LANG`, `LC_ALL`, `TERM=dumb`, `CI=1`, `NO_COLOR=1`,
//   `TMPDIR` = the private temp dir, plus the names the mapping lists, taken
//   from the node's environment), never a name matching
//   `(?i)token|secret|key|password|credential|auth`;
// - the limits: wall = min(mapping, contract, 900) s, the same CPU time,
//   output <= 1 MiB, at most 256 processes, a 5 s SIGTERM -> SIGKILL grace.
//
// `settingsBytes()` is the canonical JSON of the settings document; it is
// byte-identical to the reference's for every vector of
// backend/nodes/conformance/vectors/sandbox.json (SandboxPolicyTests), and
// `document` is the reference `SandboxSpec.document()` shape that
// `manage.py node_escape_suite --check-policy` reads.
//
// The limits are part of the document; how the executor enforces them on
// macOS is SrtExecutor's business (`max_processes` is a process-tree count,
// not RLIMIT_NPROC, which macOS applies per user).

import Foundation

public struct SandboxError: Error, Equatable, CustomStringConvertible {
    public let description: String
    init(_ description: String) { self.description = description }
}

/// One run's sandbox (reference `SandboxSpec`).
public struct SandboxDocument: Equatable, Sendable {
    public let mode: String
    public let settings: JSONValue
    public let argv: [String]
    public let cwd: String
    public let environment: [String: String]
    public let wallSeconds: Int64
    public let cpuSeconds: Int64
    public let maxOutputBytes: Int64
    public let maxProcesses: Int64
    public let killGraceSeconds: Int64
    public let writeRoot: String
    public let tempDir: String

    public var networkEnabled: Bool { !(settings["network"]?["allowedDomains"]?.arrayValue ?? []).isEmpty }

    public var limits: JSONValue {
        [
            "wall_seconds": .int(wallSeconds),
            "cpu_seconds": .int(cpuSeconds),
            "max_output_bytes": .int(maxOutputBytes),
            "max_processes": .int(maxProcesses),
            "kill_grace_seconds": .int(killGraceSeconds),
        ]
    }

    /// The settings document `srt` reads: canonical JSON.
    public func settingsBytes() throws -> Data { try JSON.canonical(settings) }

    /// The reference `SandboxSpec.document()`.
    public var document: JSONValue {
        [
            "mode": .string(mode),
            "settings": settings,
            "process": [
                "argv": .array(argv.map(JSONValue.string)),
                "cwd": .string(cwd),
                "env": .object(environment.mapValues(JSONValue.string)),
            ],
            "limits": limits,
            "network_enabled": .bool(networkEnabled),
            "settings_sha256": .string((try? JSON.sha256Hex(settings)) ?? ""),
        ]
    }
}

public enum SandboxPolicy {
    /// Home-relative directories no sandboxed command may read (D4).
    public static let denyReadHome = [
        ".ssh",
        ".aws",
        ".config/gh",
        ".claude",
        ".codex",
        ".gnupg",
        ".netrc",
        ".docker",
        "Library/Keychains",
        "Library/Safari",
        "Library/Application Support/Google/Chrome",
        "Library/Application Support/Firefox",
        "Library/Application Support/Arc",
        "Library/Cookies",
        ".mozilla",
        ".config/google-chrome",
        ".config/chromium",
    ]
    /// The reference node's own container and data (the vectors use these).
    public static let defaultNodeDirs = [
        "Library/Containers/ai.friday.node",
        "Library/Application Support/Friday Node",
        ".friday-node",
    ]
    /// What the Swift app adds: its spool, hook socket and `srt` prefix
    /// (`~/Library/Application Support/Friday`), its local config and its
    /// caches. The app passes these; the parity vectors use the defaults.
    public static let appNodeDirs = defaultNodeDirs + [
        "Library/Application Support/Friday",
        ".config/friday-node",
        "Library/Caches/dev.friday.friday-node",
        "Library/Containers/dev.friday.friday-node",
    ]
    static let fixedEnvironment: [(String, String)] = [
        ("LANG", "en_US.UTF-8"),
        ("LC_ALL", "en_US.UTF-8"),
        ("TERM", "dumb"),
        ("CI", "1"),
        ("NO_COLOR", "1"),
    ]
    public static let maxProcesses: Int64 = 256
    public static let killGraceSeconds: Int64 = 5
    public static let maxOutputBytes = Int64(OutputStreamer.maxOutputBytes)

    /// `os.path.isabs` + no NUL, then `os.path.normpath`.
    static func absolute(_ path: String, _ label: String) throws -> String {
        guard Scalars.hasPrefix(path, "/"), !path.unicodeScalars.contains("\u{0}") else {
            throw SandboxError("\(label) must be an absolute path")
        }
        return ReaderPaths.normpath(path)
    }

    /// `os.path.join(base, relative)` for a relative `relative`.
    static func join(_ base: String, _ relative: String) -> String {
        base.unicodeScalars.last == "/" ? base + relative : base + "/" + relative
    }

    /// The reference `_under`: `path == root` or under `root/`.
    static func under(_ path: String, _ root: String) -> Bool {
        if Scalars.equal(path, root) { return true }
        var trimmed = Array(root.unicodeScalars)
        while trimmed.last == "/" { trimmed.removeLast() }
        return Scalars.hasPrefix(path, String(String.UnicodeScalarView(trimmed)) + "/")
    }

    /// The sandbox for one run of `command` (reference `build_sandbox`).
    /// `writeRoot` is `repoRoot` in `in_place` mode and the throwaway
    /// worktree in `worktree` mode; `tempDir` a fresh private directory.
    public static func build(
        command: CommandMapping, home rawHome: String, repoRoot rawRepoRoot: String, writeRoot rawWriteRoot: String,
        tempDir rawTempDir: String, contractMaxRuntime: Int64 = ActionContracts.maxRuntimeSeconds,
        nodeEnvironment: [String: String] = [:], nodeDirs: [String] = defaultNodeDirs
    ) throws -> SandboxDocument {
        guard CommandMapping.modes.contains(where: { Scalars.equal($0, command.mode) }) else {
            throw SandboxError("unknown sandbox mode")
        }
        let home = try absolute(rawHome, "home")
        let repoRoot = try absolute(rawRepoRoot, "repo_root")
        let writeRoot = try absolute(rawWriteRoot, "write_root")
        let tempDir = try absolute(rawTempDir, "temp_dir")
        if Scalars.equal(command.mode, "in_place") && !Scalars.equal(writeRoot, repoRoot) {
            throw SandboxError("in_place mode writes to the repo root only")
        }
        if Scalars.equal(command.mode, "worktree") && under(writeRoot, repoRoot) {
            throw SandboxError("the worktree must live outside the source repo")
        }
        if ["/", home].contains(where: { Scalars.equal($0, writeRoot) })
            || ["/", home, "/tmp", "/private/tmp"].contains(where: { Scalars.equal($0, tempDir) })
        {
            throw SandboxError("the write root and temp dir must be dedicated directories")
        }
        let denyRead = denyReadHome.map { join(home, $0) } + nodeDirs.map { join(home, $0) }
        for path in [writeRoot, tempDir] {
            if denyRead.contains(where: { under(path, $0) || under($0, path) }) {
                throw SandboxError("a writable directory overlaps a denied directory")
            }
        }
        let wall = min(command.maxRuntimeSeconds, contractMaxRuntime, ActionContracts.maxRuntimeSeconds)
        var environment: [String: String] = ["PATH": command.path, "HOME": home, "TMPDIR": tempDir]
        for (name, value) in fixedEnvironment { environment[name] = value }
        for name in command.envNames {
            if ActionPolicy.looksSecret(name) || environment[name] != nil { continue }
            if let value = nodeEnvironment[name], !value.unicodeScalars.contains("\u{0}") {
                environment[name] = value
            }
        }
        if environment.keys.contains(where: ActionPolicy.looksSecret) {  // defence in depth
            throw SandboxError("a secret-shaped environment name reached the sandbox")
        }
        var domains: [String] = []
        for domain in command.networkDomains where !domains.contains(where: { Scalars.equal($0, domain) }) {
            domains.append(domain)
        }
        domains.sort(by: Scalars.less)
        let settings: JSONValue = [
            "network": [
                "allowedDomains": .array(domains.map(JSONValue.string)),
                "deniedDomains": [],
                "allowUnixSockets": [],
                "allowLocalBinding": false,
            ],
            "filesystem": [
                "denyRead": .array(denyRead.map(JSONValue.string)),
                "allowWrite": [.string(writeRoot), .string(tempDir)],
                "denyWrite": [.string(join(writeRoot, ".git"))],
            ],
        ]
        return SandboxDocument(
            mode: command.mode, settings: settings, argv: command.argv, cwd: writeRoot, environment: environment,
            wallSeconds: wall, cpuSeconds: wall, maxOutputBytes: maxOutputBytes, maxProcesses: maxProcesses,
            killGraceSeconds: killGraceSeconds, writeRoot: writeRoot, tempDir: tempDir)
    }
}
