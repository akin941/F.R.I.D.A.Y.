// `git.status` (Phase 9 brief M2; backend/nodes/reference/readers.py
// `_git_status`): counts only, never file names, for a repo on the local
// list. `repo_path` is expanded against the node's home and realpath-
// resolved; it must equal a listed root's realpath (a symlink resolving to
// a listed root is the listed repo, anything else is refused).
// `/usr/bin/git` runs with a fixed argv and environment, hooks and
// fsmonitor off, no optional locks, no prompt, HOME = the repo.

import Foundation

public struct LocalRepo: Equatable, Sendable {
    public let name: String
    /// As written (`~/...` or absolute).
    public let root: String
    /// realpath at load.
    public let realRoot: String

    public init(name: String, root: String, home: String) {
        self.name = name
        self.root = root
        realRoot = ReaderPaths.realpathC(LocalRepo.expand(root, home: home))
    }

    static func expand(_ path: String, home: String) -> String {
        if Scalars.equal(path, "~") { return home }
        if Scalars.hasPrefix(path, "~/") {
            let rest = Scalars.dropFirst(path, 2)
            return Scalars.hasPrefix(rest, "/") ? rest : (Scalars.hasPrefix(String(String.UnicodeScalarView(home.unicodeScalars.reversed())), "/") ? home + rest : home + "/" + rest)
        }
        return path
    }
}

enum Git {
    static let path = "/usr/bin/git"

    /// One git command in `root`; stdout, or ReaderError.
    static func run(_ root: String, _ arguments: [String]) throws -> String {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: path)
        process.arguments = ["-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false"] + arguments
        process.currentDirectoryURL = URL(fileURLWithPath: root)
        process.environment = [
            "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_TERMINAL_PROMPT": "0",
            "LANG": "C",
            "HOME": root,
        ]
        let output = Pipe()
        process.standardOutput = output
        process.standardError = FileHandle.nullDevice
        process.standardInput = FileHandle.nullDevice
        do { try process.run() } catch { throw ReaderError() }
        let deadline = Date().addingTimeInterval(ReaderLimits.gitTimeoutSeconds)
        var data = Data()
        let reader = DispatchQueue(label: "friday-node.git")
        let done = DispatchSemaphore(value: 0)
        reader.async {
            data = output.fileHandleForReading.readDataToEndOfFile()
            done.signal()
        }
        if done.wait(timeout: .now() + ReaderLimits.gitTimeoutSeconds) == .timedOut || Date() > deadline {
            process.terminate()
            throw ReaderError()
        }
        process.waitUntilExit()
        guard process.terminationStatus == 0 else { throw ReaderError() }
        return String(decoding: data, as: UTF8.self)
    }
}

extension NodeReaders {
    func gitStatus(_ args: JSONValue, now: Int64) throws -> JSONValue {
        guard let repos, let requested = args["repo_path"]?.stringValue else { throw ReaderError() }
        let real = ReaderPaths.realpathC(LocalRepo.expand(requested, home: home))
        guard let repo = repos.first(where: { Scalars.equal($0.realRoot, real) }) else { throw ReaderError() }
        let status = try Git.run(repo.realRoot, ["status", "--porcelain=v2", "--branch"])
        var branch: String?
        var ahead: Int64 = 0, behind: Int64 = 0, dirty: Int64 = 0, untracked: Int64 = 0
        for line in status.split(separator: "\n", omittingEmptySubsequences: true).map(String.init) {
            if line.hasPrefix("# branch.head ") {
                let head = String(line.dropFirst("# branch.head ".count))
                branch = head == "(detached)" ? nil : head
            } else if line.hasPrefix("# branch.ab ") {
                let parts = line.split(separator: " ")
                guard parts.count >= 4, let a = Int64(parts[2]), let b = Int64(parts[3]) else { throw ReaderError() }
                ahead = abs(a)
                behind = abs(b)
            } else if line.hasPrefix("1 ") || line.hasPrefix("2 ") || line.hasPrefix("u ") {
                dirty += 1
            } else if line.hasPrefix("? ") {
                untracked += 1
            }
        }
        var sha: JSONValue = .null
        var at: JSONValue = .null
        let head = ((try? Git.run(repo.realRoot, ["log", "-1", "--format=%H %ct"])) ?? "")
            .trimmingCharacters(in: .whitespacesAndNewlines)
        if !head.isEmpty {
            let parts = head.split(separator: " ", maxSplits: 1).map(String.init)
            sha = .string(parts[0])
            if parts.count == 2, parts[1].allSatisfy(\.isASCII), let stamp = Int64(parts[1]) { at = .int(stamp) }
        }
        var branchValue: JSONValue = .null
        if let name = branch {
            let scrubbed = scrubbedText(name, limit: 255)
            if !scrubbed.isEmpty { branchValue = .string(scrubbed) }
        }
        return [
            "branch": branchValue,
            "ahead": .int(ahead),
            "behind": .int(behind),
            "dirty_files_count": .int(dirty),
            "untracked_count": .int(untracked),
            "last_commit_sha": sha,
            "last_commit_at": at,
        ]
    }
}
