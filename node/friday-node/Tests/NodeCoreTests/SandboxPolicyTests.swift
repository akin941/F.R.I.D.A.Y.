// Sandbox parity and the escape-policy export (runbook B6 contract tests 1
// and 3; Phase 10 brief D4, D11).
//
// 1. Every vector of backend/nodes/conformance/vectors/sandbox.json: the
//    Swift `SandboxPolicy` produces the settings document byte for byte
//    (`settings_canonical`), and the whole reference `document()` (process
//    spec, stripped environment, limits, `settings_sha256`).
// 2. The refusals of `build_sandbox` (the same inputs refused).
// 3. The escape suite's policies, exported in the `--export-policy` format
//    from the Swift generator, for
//
//        uv run python manage.py node_escape_suite --check-policy <file>
//
//    The file goes to `$FRIDAY_ESCAPE_EXPORT` when set (else the temp dir;
//    the path is printed). With `$FRIDAY_ESCAPE_REFERENCE` naming a file the
//    reference wrote with `--export-policy`, the Swift documents are built
//    over the reference's own context and must equal its documents.

import Foundation
import NodeCore
import XCTest

final class SandboxPolicyTests: XCTestCase {
    static let vectors = "backend/nodes/conformance/vectors/sandbox.json"

    private func mapping(_ body: JSONValue) throws -> CommandMapping {
        CommandMapping(
            kind: try XCTUnwrap(body["kind"]?.stringValue),
            argv: (body["argv"]?.arrayValue ?? []).compactMap(\.stringValue),
            path: body["path"]?.stringValue ?? CommandMapping.defaultPath,
            envNames: (body["env_names"]?.arrayValue ?? []).compactMap(\.stringValue),
            networkDomains: (body["network_domains"]?.arrayValue ?? []).compactMap(\.stringValue),
            maxRuntimeSeconds: body["max_runtime_seconds"]?.intValue ?? 900,
            mode: body["mode"]?.stringValue ?? "in_place")
    }

    func testEveryVectorIsReproducedByteForByte() throws {
        let doc = try Repo.json(Self.vectors)
        let nodeEnv = (doc["vector_node_env"]?.objectValue ?? [:]).compactMapValues(\.stringValue)
        let vectors = try XCTUnwrap(doc["vectors"]?.arrayValue)
        XCTAssertEqual(vectors.count, 6)
        for vector in vectors {
            let name = vector["name"]?.stringValue ?? "?"
            let inputs = try XCTUnwrap(vector["inputs"])
            let names = (inputs["node_env_names"]?.arrayValue ?? []).compactMap(\.stringValue)
            let sandbox = try SandboxPolicy.build(
                command: try mapping(try XCTUnwrap(inputs["mapping"])),
                home: try XCTUnwrap(inputs["home"]?.stringValue),
                repoRoot: try XCTUnwrap(inputs["repo_root"]?.stringValue),
                writeRoot: try XCTUnwrap(inputs["write_root"]?.stringValue),
                tempDir: try XCTUnwrap(inputs["temp_dir"]?.stringValue),
                contractMaxRuntime: try XCTUnwrap(inputs["contract_max_runtime"]?.intValue),
                nodeEnvironment: nodeEnv.filter { names.contains($0.key) })
            let settings = String(decoding: try sandbox.settingsBytes(), as: UTF8.self)
            XCTAssertEqual(settings, vector["settings_canonical"]?.stringValue, name)
            XCTAssertEqual(
                try JSON.canonicalString(sandbox.document), try JSON.canonicalString(try XCTUnwrap(vector["expected"])),
                name)
            // Nothing secret-shaped reached the environment.
            for key in sandbox.environment.keys {
                XCTAssertNil(key.range(of: "token|secret|key|password|credential|auth", options: [.regularExpression, .caseInsensitive]), name)
            }
        }
    }

    private func command(_ mode: String = "in_place", env: [String] = []) -> CommandMapping {
        CommandMapping(kind: "test", argv: ["true"], envNames: env, maxRuntimeSeconds: 60, mode: mode)
    }

    private func refused(_ command: CommandMapping, home: String = "/Users/o", repo: String = "/Users/o/r",
                         write: String = "/Users/o/r", temp: String = "/private/var/t/run") -> String? {
        do {
            _ = try SandboxPolicy.build(command: command, home: home, repoRoot: repo, writeRoot: write, tempDir: temp)
            return nil
        } catch let error as SandboxError {
            return error.description
        } catch {
            return "other"
        }
    }

    /// The reference's refusals, the same inputs.
    func testTheReferenceRefusals() {
        XCTAssertNil(refused(command()))
        XCTAssertEqual(refused(command(), write: "/Users/o/elsewhere"), "in_place mode writes to the repo root only")
        XCTAssertEqual(refused(command("worktree"), write: "/Users/o/r/wt"), "the worktree must live outside the source repo")
        XCTAssertEqual(refused(command("worktree"), write: "/"), "the write root and temp dir must be dedicated directories")
        for temp in ["/tmp", "/private/tmp", "/Users/o", "/"] {
            XCTAssertEqual(refused(command(), temp: temp), "the write root and temp dir must be dedicated directories", temp)
        }
        XCTAssertEqual(refused(command(), temp: "/Users/o/.ssh/t"), "a writable directory overlaps a denied directory")
        XCTAssertEqual(refused(command("worktree"), write: "/Users/o/Library"),
                       "a writable directory overlaps a denied directory")
        XCTAssertEqual(refused(command(), home: "relative"), "home must be an absolute path")
        XCTAssertEqual(refused(command(), repo: "r", write: "r"), "repo_root must be an absolute path")
        XCTAssertEqual(refused(command(), temp: "/x\u{0}y"), "temp_dir must be an absolute path")
        XCTAssertEqual(
            refused(CommandMapping(kind: "test", argv: ["true"], mode: "anywhere")), "unknown sandbox mode")
    }

    /// Secret-shaped names are skipped (never refused), the fixed names are
    /// never overridden from the node's environment, a NUL value is dropped.
    func testTheEnvironmentIsStrippedToTheAllowlist() throws {
        let sandbox = try SandboxPolicy.build(
            command: command(env: ["GITHUB_TOKEN", "PATH", "HOME", "PYTHONHASHSEED", "LANG", "BAD"]),
            home: "/Users/o", repoRoot: "/Users/o/r", writeRoot: "/Users/o/r", tempDir: "/private/var/t/run",
            nodeEnvironment: [
                "GITHUB_TOKEN": "ghp_x", "PATH": "/evil", "HOME": "/evil", "PYTHONHASHSEED": "0", "LANG": "C", "BAD": "a\u{0}b",
            ])
        XCTAssertEqual(
            sandbox.environment,
            [
                "PATH": CommandMapping.defaultPath, "HOME": "/Users/o", "LANG": "en_US.UTF-8", "LC_ALL": "en_US.UTF-8",
                "TERM": "dumb", "CI": "1", "NO_COLOR": "1", "TMPDIR": "/private/var/t/run", "PYTHONHASHSEED": "0",
            ])
        XCTAssertFalse(sandbox.networkEnabled)
        XCTAssertEqual(sandbox.settings["filesystem"]?["denyWrite"], ["/Users/o/r/.git"])
    }

    /// The app adds its own data directories to `denyRead`.
    func testTheAppDeniesItsOwnDirectories() throws {
        let sandbox = try SandboxPolicy.build(
            command: command(), home: "/Users/o", repoRoot: "/Users/o/r", writeRoot: "/Users/o/r",
            tempDir: "/private/var/t/run", nodeDirs: SandboxPolicy.appNodeDirs)
        let denied = (sandbox.settings["filesystem"]?["denyRead"]?.arrayValue ?? []).compactMap(\.stringValue)
        for directory in ["/Users/o/Library/Application Support/Friday", "/Users/o/.config/friday-node",
                          "/Users/o/.friday-node", "/Users/o/.ssh"] {
            XCTAssertTrue(denied.contains(directory), directory)
        }
    }

    /// What the app actually writes (its own data dirs added to `denyRead`)
    /// against the reference document, for every vector: the reference's
    /// `denyRead` is a prefix (and so a subset) of the app's, the additions
    /// are exactly the app's own directories, and every other byte of the
    /// document is the reference's (B6 review finding 8).
    func testTheAppDocumentOnlyAddsItsOwnDeniedDirectories() throws {
        let doc = try Repo.json(Self.vectors)
        let nodeEnv = (doc["vector_node_env"]?.objectValue ?? [:]).compactMapValues(\.stringValue)
        for vector in try XCTUnwrap(doc["vectors"]?.arrayValue) {
            let name = vector["name"]?.stringValue ?? "?"
            let inputs = try XCTUnwrap(vector["inputs"])
            let home = try XCTUnwrap(inputs["home"]?.stringValue)
            let names = (inputs["node_env_names"]?.arrayValue ?? []).compactMap(\.stringValue)
            let app = try SandboxPolicy.build(
                command: try mapping(try XCTUnwrap(inputs["mapping"])), home: home,
                repoRoot: try XCTUnwrap(inputs["repo_root"]?.stringValue),
                writeRoot: try XCTUnwrap(inputs["write_root"]?.stringValue),
                tempDir: try XCTUnwrap(inputs["temp_dir"]?.stringValue),
                contractMaxRuntime: try XCTUnwrap(inputs["contract_max_runtime"]?.intValue),
                nodeEnvironment: nodeEnv.filter { names.contains($0.key) }, nodeDirs: SandboxPolicy.appNodeDirs)
            let expected = try XCTUnwrap(vector["expected"])
            let reference = try XCTUnwrap(expected["settings"]?["filesystem"]?["denyRead"]?.arrayValue)
            let written = try XCTUnwrap(app.settings["filesystem"]?["denyRead"]?.arrayValue)
            XCTAssertEqual(Array(written.prefix(reference.count)), reference, name)
            let added = written.dropFirst(reference.count).compactMap(\.stringValue)
            let ownDirs = SandboxPolicy.appNodeDirs.filter { !SandboxPolicy.defaultNodeDirs.contains($0) }
            XCTAssertEqual(added, ownDirs.map { home + "/" + $0 }, name)
            // Everything else is the reference's, byte for byte.
            guard case .object(var document) = app.document, case .object(var settings)? = document["settings"],
                case .object(var filesystem)? = settings["filesystem"]
            else { return XCTFail(name) }
            filesystem["denyRead"] = .array(reference)
            settings["filesystem"] = .object(filesystem)
            document["settings"] = .object(settings)
            document["settings_sha256"] = expected["settings_sha256"]
            XCTAssertEqual(try JSON.canonicalString(.object(document)), try JSON.canonicalString(expected), name)
        }
    }

    // MARK: Contract test 3: the escape suite's policies

    static let defaultRuntime: Int64 = 30
    static let scriptPath = "/usr/bin:/bin"

    static func expand(_ text: String, _ context: [String: String]) -> String {
        var out = text
        for (key, value) in context { out = out.replacingOccurrences(of: "{" + key + "}", with: value) }
        return out
    }

    /// The `export_policies` document over `context`, from the Swift
    /// generator (backend/nodes/conformance/escape/runner.py).
    static func exportDocument(context: [String: String], tempDir: String) throws -> JSONValue {
        let cases = try XCTUnwrap(try Repo.json("backend/nodes/conformance/escape/cases.json")["cases"]?.arrayValue)
        var documents: [String: JSONValue] = [:]
        for escape in cases {
            let id = try XCTUnwrap(escape["id"]?.stringValue)
            let script = Self.expand(try XCTUnwrap(escape["script"]?.stringValue), context)
            let mapping = CommandMapping(
                kind: "test", argv: ["/bin/sh", "-c", script], path: scriptPath, envNames: ["GITHUB_TOKEN"],
                maxRuntimeSeconds: escape["runtime_seconds"]?.intValue ?? defaultRuntime, mode: "worktree")
            let sandbox = try SandboxPolicy.build(
                command: mapping, home: try XCTUnwrap(context["home"]), repoRoot: try XCTUnwrap(context["repo"]),
                writeRoot: try XCTUnwrap(context["worktree"]), tempDir: tempDir,
                nodeEnvironment: ["GITHUB_TOKEN": try XCTUnwrap(context["canary"])])
            documents[id] = sandbox.document
        }
        var contextValue: [String: JSONValue] = context.mapValues(JSONValue.string)
        contextValue["loopback_port"] = .int(Int64(context["loopback_port"] ?? "9") ?? 9)
        return ["context": .object(contextValue), "documents": .object(documents)]
    }

    func testTheEscapeSuitePoliciesAreExported() throws {
        let environment = ProcessInfo.processInfo.environment
        var context: [String: String]
        var tempDir: String
        var reference: JSONValue?
        if let referencePath = environment["FRIDAY_ESCAPE_REFERENCE"], !referencePath.isEmpty {
            // The reference's own context: the documents must be identical.
            let loaded = try JSON.parse(try Data(contentsOf: URL(fileURLWithPath: referencePath)))
            reference = loaded
            context = (loaded["context"]?.objectValue ?? [:]).mapValues { value in
                value.stringValue ?? value.intValue.map(String.init) ?? ""
            }
            let first = try XCTUnwrap(loaded["documents"]?.objectValue?.values.first)
            tempDir = try XCTUnwrap(first["process"]?["env"]?["TMPDIR"]?.stringValue)
        } else {
            let base = NSTemporaryDirectory() + "friday-escape-export-swift"
            let home = base + "/home"
            context = [
                "home": home, "repo": home + "/work/acme", "repo_parent": home + "/work",
                "worktree": base + "/worktrees/wt-export", "run": "export", "canary": "FRIDAY-CANARY-export",
                "loopback_port": "9", "hook_socket": home + "/.friday-node/hook-export.sock",
            ]
            tempDir = base + "/run-tmp"
        }
        let exported = try Self.exportDocument(context: context, tempDir: tempDir)
        XCTAssertEqual(exported["documents"]?.objectValue?.count, 32)
        if let reference {
            for (id, document) in reference["documents"]?.objectValue ?? [:] {
                XCTAssertEqual(
                    try JSON.canonicalString(try XCTUnwrap(exported["documents"]?[id])), try JSON.canonicalString(document),
                    id)
            }
            XCTAssertEqual(exported["documents"]?.objectValue?.keys.sorted(), reference["documents"]?.objectValue?.keys.sorted())
        }
        let target = environment["FRIDAY_ESCAPE_EXPORT"].flatMap { $0.isEmpty ? nil : $0 }
            ?? NSTemporaryDirectory() + "friday-node-swift-escape-policy.json"
        try Data((try JSON.encode(exported) + "\n").utf8).write(to: URL(fileURLWithPath: target))
        print("escape-suite policies exported to \(target)")
    }
}
