// The throwaway-worktree mode (Phase 10 brief D4, D11; a port of
// backend/nodes/reference/worktree.py).
//
// `worktree` mode creates a detached `git worktree` at `HEAD` outside the
// source repo, runs the mapped command there, and deletes it; afterwards
// `git worktree list` of the source repo is unchanged. Git runs with a
// minimal environment and hooks disabled, so nothing in the repo runs during
// setup or teardown.

import Foundation

public enum Worktree {
    public struct Failure: Error, Equatable {
        public let reason: String
    }

    static let gitPath = "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin"

    /// Run git with an argv (no shell), the minimal environment and hooks
    /// disabled; stdout as text. `posix_spawn` + `waitpid` rather than
    /// Foundation's `Process`, whose `waitUntilExit` polls the run loop and
    /// adds tens of milliseconds per call (the session runs this on its queue
    /// between the click and the first output frame).
    private static func git(_ repoRoot: String, _ args: [String]) throws -> String {
        let failure = Failure(reason: "git \(args.first ?? "") failed")
        guard let executable = gitPath.split(separator: ":").map({ "\($0)/git" })
            .first(where: { FileManager.default.isExecutableFile(atPath: $0) })
        else { throw Failure(reason: "git is not installed") }
        let argv = [executable, "-c", "core.hooksPath=/dev/null", "-C", repoRoot] + args
        let environment = [
            "PATH=\(gitPath)", "LANG=C", "HOME=\((repoRoot as NSString).deletingLastPathComponent)",
            "GIT_CONFIG_NOSYSTEM=1",
        ]
        var pipe: [Int32] = [0, 0]
        guard Darwin.pipe(&pipe) == 0 else { throw failure }
        var actions: posix_spawn_file_actions_t?
        posix_spawn_file_actions_init(&actions)
        defer { posix_spawn_file_actions_destroy(&actions) }
        posix_spawn_file_actions_addopen(&actions, 0, "/dev/null", O_RDONLY, 0)
        posix_spawn_file_actions_adddup2(&actions, pipe[1], 1)
        posix_spawn_file_actions_addopen(&actions, 2, "/dev/null", O_WRONLY, 0)
        posix_spawn_file_actions_addclose(&actions, pipe[0])
        posix_spawn_file_actions_addclose(&actions, pipe[1])
        var cArgv = argv.map { strdup($0) } + [nil]
        var cEnv = environment.map { strdup($0) } + [nil]
        defer {
            for pointer in cArgv { free(pointer) }
            for pointer in cEnv { free(pointer) }
        }
        var pid: pid_t = 0
        let spawned = posix_spawn(&pid, executable, &actions, nil, &cArgv, &cEnv)
        close(pipe[1])
        guard spawned == 0 else {
            close(pipe[0])
            throw failure
        }
        var output = Data()
        var buffer = [UInt8](repeating: 0, count: 4096)
        while true {
            let count = read(pipe[0], &buffer, buffer.count)
            if count > 0 {
                output.append(contentsOf: buffer[0..<count])
            } else if count < 0 && errno == EINTR {
                continue
            } else {
                break
            }
        }
        close(pipe[0])
        var status: Int32 = 0
        while waitpid(pid, &status, 0) < 0 && errno == EINTR {}
        // Exited normally (low 7 bits 0) with code 0.
        guard status & 0x7f == 0, (status >> 8) & 0xff == 0 else { throw failure }
        return String(decoding: output, as: UTF8.self)
    }

    /// `git worktree list --porcelain` paths of `repoRoot`.
    public static func list(_ repoRoot: String) throws -> [String] {
        try git(repoRoot, ["worktree", "list", "--porcelain"]).split(separator: "\n")
            .filter { $0.hasPrefix("worktree ") }.map { String($0.dropFirst("worktree ".count)) }
    }

    /// A detached worktree at HEAD in a fresh directory under `base`.
    public static func create(_ repoRoot: String, base: String) throws -> String {
        let suffix = UUID().uuidString.lowercased().replacingOccurrences(of: "-", with: "").prefix(12)
        let path = (base as NSString).appendingPathComponent("wt-\(suffix)")
        _ = try git(repoRoot, ["worktree", "add", "--detach", path, "HEAD"])
        return ReaderPaths.realpathC(path)
    }

    /// Remove the worktree and prune its metadata; the directory is gone
    /// afterwards whatever the command left in it.
    public static func remove(_ repoRoot: String, _ path: String) {
        _ = try? git(repoRoot, ["worktree", "remove", "--force", "--force", path])
        try? FileManager.default.removeItem(atPath: path)
        _ = try? git(repoRoot, ["worktree", "prune"])
    }
}

enum TempDirectory {
    /// `mkdtemp` under the user's temp directory, mode 0700.
    static func make(prefix: String) throws -> String {
        let template = (NSTemporaryDirectory() as NSString).appendingPathComponent("\(prefix)XXXXXX")
        var bytes = Array(template.utf8CString)
        guard let made = mkdtemp(&bytes) else { throw ExecutorError("mkdtemp failed") }
        let path = String(cString: made)
        chmod(path, 0o700)
        return path
    }

    static func remove(_ path: String?) {
        guard let path else { return }
        try? FileManager.default.removeItem(atPath: path)
    }
}
