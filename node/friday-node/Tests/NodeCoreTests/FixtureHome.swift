// The reference node's fixture home, built the way backend/nodes/reference/
// fixtures/factory.py builds it, so the reader cases see the same files and
// the same git object ids (the git.status case pins the commit sha):
// - work/acme and code/friday: git repos with one commit each, same
//   identity, dates, branch and message, HOME = the repo, no system config;
// - work/other, the symlinks work/link -> work/other and
//   work/acme-link -> work/acme;
// - the transcript templates under fixtures/transcripts/ rendered with
//   {{HOME}} = the home (JSON-escaped) and the <<>> split marker removed,
//   files mode 0600.

import Foundation
import NodeCore

enum FixtureHome {
    static let fixtures = Repo.url("backend/nodes/reference/fixtures")

    private static func git(_ repo: URL, _ arguments: [String]) throws {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/git")
        process.arguments = ["-c", "core.hooksPath=/dev/null", "-c", "init.defaultBranch=main"] + arguments
        process.currentDirectoryURL = repo
        process.environment = [
            "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
            "GIT_AUTHOR_NAME": "Friday Fixture",
            "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
            "GIT_COMMITTER_NAME": "Friday Fixture",
            "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
            "GIT_AUTHOR_DATE": "2026-11-20T00:00:00Z",
            "GIT_COMMITTER_DATE": "2026-11-20T00:00:00Z",
            "GIT_CONFIG_NOSYSTEM": "1",
            "LANG": "C",
            "HOME": repo.path,
        ]
        process.standardOutput = FileHandle.nullDevice
        process.standardError = FileHandle.nullDevice
        try process.run()
        process.waitUntilExit()
        guard process.terminationStatus == 0 else { throw NSError(domain: "git", code: Int(process.terminationStatus)) }
    }

    private static func makeRepo(_ path: URL, _ name: String) throws {
        try FileManager.default.createDirectory(at: path, withIntermediateDirectories: true)
        try git(path, ["init", "-q"])
        try Data("# \(name)\n".utf8).write(to: path.appendingPathComponent("README.md"))
        try git(path, ["add", "README.md"])
        try git(path, ["commit", "-q", "-m", "fixture"])
    }

    /// Build a fresh fixture home under a temporary directory; its realpath.
    static func build() throws -> String {
        let base = FileManager.default.temporaryDirectory.appendingPathComponent("friday-fixture-\(UUID().uuidString)")
        let home = base.appendingPathComponent("home")
        try makeRepo(home.appendingPathComponent("work/acme"), "acme")
        try makeRepo(home.appendingPathComponent("code/friday"), "friday")
        try FileManager.default.createDirectory(at: home.appendingPathComponent("work/other"), withIntermediateDirectories: true)
        try FileManager.default.createSymbolicLink(
            at: home.appendingPathComponent("work/link"), withDestinationURL: home.appendingPathComponent("work/other"))
        try FileManager.default.createSymbolicLink(
            at: home.appendingPathComponent("work/acme-link"), withDestinationURL: home.appendingPathComponent("work/acme"))
        let real = realpath(home.path)
        // json.dumps(home)[1:-1]: the home as a JSON string body.
        let escaped = String(try JSON.encode(.string(real)).dropFirst().dropLast())
        for (family, target) in [("claude", ".claude"), ("codex", ".codex")] {
            let root = fixtures.appendingPathComponent("transcripts/\(family)")
            guard let walker = FileManager.default.enumerator(at: root, includingPropertiesForKeys: nil) else { continue }
            for case let file as URL in walker where file.pathExtension == "jsonl" {
                let relative = file.path.dropFirst(root.path.count + 1)
                let destination = home.appendingPathComponent(target).appendingPathComponent(String(relative))
                try FileManager.default.createDirectory(
                    at: destination.deletingLastPathComponent(), withIntermediateDirectories: true)
                let text = try String(contentsOf: file, encoding: .utf8)
                    .replacingOccurrences(of: "{{HOME}}", with: escaped)
                    .replacingOccurrences(of: "<<>>", with: "")
                try Data(text.utf8).write(to: destination)
                try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: destination.path)
            }
        }
        return real
    }

    static func realpath(_ path: String) -> String {
        guard let resolved = Darwin.realpath(path, nil) else { return path }
        defer { free(resolved) }
        return String(cString: resolved)
    }

    /// The fixture policy's repos (fixtures/local_policy.json).
    static func repos(home: String) throws -> [LocalRepo] {
        let doc = try JSON.parse(try Data(contentsOf: fixtures.appendingPathComponent("local_policy.json")))
        return (doc["repos"]?.arrayValue ?? []).compactMap { repo in
            guard let name = repo["name"]?.stringValue, let root = repo["root"]?.stringValue else { return nil }
            return LocalRepo(name: name, root: root, home: home)
        }
    }
}
