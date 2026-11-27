// The throwaway-worktree mode (Phase 10 brief D4, D11; a port of
// backend/nodes/reference/worktree.py).
//
// `worktree` mode creates a detached `git worktree` at `HEAD` outside the
// source repo, runs the mapped command there, and deletes it; afterwards
// `git worktree list` of the source repo is unchanged.
//
// Git is started through `BoundedRun` (posix_spawn, argv only,
// `POSIX_SPAWN_CLOEXEC_DEFAULT`, stdin /dev/null, stderr /dev/null) with a
// 60 s timeout per call (the reference's), registered with `ChildRegistry`
// so the kill switch can stop a hung git from any thread (B5 review findings
// 1 and 4). It runs with nothing of the owner's configuration and none of
// the repo's executables (finding 2):
//
// - environment: `PATH` (system dirs), `LANG=C`, `HOME=/var/empty`,
//   `XDG_CONFIG_HOME=/var/empty`, `GIT_CONFIG_GLOBAL=/dev/null`,
//   `GIT_CONFIG_NOSYSTEM=1`, `GIT_TERMINAL_PROMPT=0`, `GIT_ATTR_NOSYSTEM=1`:
//   no global, XDG or system config, no prompt;
// - `-c core.hooksPath=/dev/null` (no hook, e.g. post-checkout),
//   `-c core.fsmonitor=false` (no fsmonitor daemon);
// - every filter driver the repo's own config names (`filter.<d>.*`, read
//   with `git config --local --includes`) is blanked for `worktree add`:
//   `-c filter.<d>.smudge= -c filter.<d>.clean= -c filter.<d>.process=
//   -c filter.<d>.required=false`, so a configured smudge (git-lfs or
//   anything else) never runs during checkout. A driver name git's `-c`
//   cannot carry (containing `=`) refuses the worktree.
//
// What still runs is git itself reading the repo's local config (its
// non-executable settings) and the in-tree `.gitattributes` (built-in eol
// and text conversion only: without a driver no external program runs).

import Foundation

public enum Worktree {
    public struct Failure: Error, Equatable {
        public let reason: String
    }

    static let gitPath = "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin"
    public static let timeoutSeconds: Double = 60

    static let environment: [String: String] = [
        "PATH": gitPath, "LANG": "C", "HOME": "/var/empty", "XDG_CONFIG_HOME": "/var/empty",
        "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1", "GIT_TERMINAL_PROMPT": "0",
        "GIT_ATTR_NOSYSTEM": "1",
    ]
    static let safetyOptions = ["-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false"]

    static func gitExecutable() throws -> String {
        guard let executable = gitPath.split(separator: ":").map({ "\($0)/git" })
            .first(where: { FileManager.default.isExecutableFile(atPath: $0) })
        else { throw Failure(reason: "git is not installed") }
        return executable
    }

    /// Run git with an argv (no shell), the minimal environment, hooks and
    /// fsmonitor off, and `extra` `-c` options; stdout as text.
    static func git(_ repoRoot: String, _ args: [String], extra: [String] = [], allowStatus: Set<Int64> = [0])
        throws -> String
    {
        let executable = try gitExecutable()
        let argv = ["git"] + safetyOptions + extra + ["-C", repoRoot] + args
        let outcome: BoundedRun.Outcome
        do {
            outcome = try BoundedRun.run(
                executable: executable, argv: argv, environment: environment, timeout: timeoutSeconds)
        } catch {
            throw Failure(reason: "git \(args.first ?? "") could not start")
        }
        if outcome.timedOut { throw Failure(reason: "git \(args.first ?? "") timed out") }
        guard let code = outcome.exitCode, allowStatus.contains(code) else {
            throw Failure(reason: "git \(args.first ?? "") failed")
        }
        return String(decoding: outcome.stdout, as: UTF8.self)
    }

    /// The filter driver names the repo's own config defines.
    static func filterDrivers(_ repoRoot: String) throws -> [String] {
        // Exit 1: no such key (no driver at all).
        let names = try git(
            repoRoot, ["config", "--local", "--includes", "--name-only", "--get-regexp", "^filter\\."],
            allowStatus: [0, 1])
        var drivers: [String] = []
        for line in names.split(separator: "\n") {
            let key = String(line)
            guard key.hasPrefix("filter."), let dot = key.lastIndex(of: "."), dot > key.index(key.startIndex, offsetBy: 6)
            else { continue }
            let driver = String(key[key.index(key.startIndex, offsetBy: 7)..<dot])
            guard !driver.isEmpty else { continue }
            if !drivers.contains(driver) { drivers.append(driver) }
        }
        return drivers
    }

    /// `-c` options that blank every filter driver of the repo's config.
    static func blankedFilters(_ repoRoot: String) throws -> [String] {
        var options: [String] = []
        for driver in try filterDrivers(repoRoot) {
            guard !driver.contains("="), !driver.contains("\n") else {
                throw Failure(reason: "a filter driver name git -c cannot carry")
            }
            for key in ["smudge", "clean", "process"] { options += ["-c", "filter.\(driver).\(key)="] }
            options += ["-c", "filter.\(driver).required=false"]
        }
        return options
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
        let filters = try blankedFilters(repoRoot)
        do {
            _ = try git(repoRoot, ["worktree", "add", "--detach", path, "HEAD"], extra: filters)
        } catch {
            // A half-made worktree (timeout, kill switch) is removed at once.
            remove(repoRoot, path)
            throw error
        }
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
