// The command mappings and their editor (runbook B6; Phase 10 brief D4,
// D5 check 22), the window's display of hostile values (B5 review finding
// 5) and "Durdur" while a second window asks (B5 NIT).

import Foundation
import NodeCore
import XCTest

final class FixedAuthentication: OwnerAuthentication, @unchecked Sendable {
    let answer: Bool
    private(set) var reasons: [String] = []
    init(_ answer: Bool) { self.answer = answer }
    func authenticate(reason: String) async -> Bool {
        reasons.append(reason)
        return answer
    }
}

final class CommandMappingsTests: XCTestCase {
    static func acme(path: String = "/usr/bin:/bin") -> CommandMappings {
        CommandMappings(
            repos: [
                .init(name: "acme", root: "~/work/acme", commands: [
                    .init(kind: "test", argv: ["python3", "-c", "print('ok')"], path: path, envNames: ["PYTHONHASHSEED"],
                          maxRuntimeSeconds: 300),
                    .init(kind: "lint", argv: ["ruff", "check"], path: path, networkDomains: ["pypi.org"], mode: "worktree"),
                ])
            ],
            recipesEnabled: ["music.play_pause"], bundleIDs: ["com.apple.Terminal"],
            srt: SrtLocation(node: "/usr/local/bin/node", prefix: "/opt/srt"))
    }

    func testTheDocumentRoundTripsAndIsTheCheck22Policy() throws {
        let mappings = Self.acme()
        let decoded = try CommandMappings.decoded(try JSON.canonical(mappings.document))
        XCTAssertEqual(decoded, mappings)
        let policy = try mappings.actionPolicy(home: "/Users/owner")
        XCTAssertEqual(policy.repos.first?.name, "acme")
        XCTAssertEqual(policy.repos.first?.commands["lint"]?.networkDomains, ["pypi.org"])
        XCTAssertEqual(policy.bundleIDs, ["com.apple.Terminal"])
        XCTAssertEqual(policy.recipesEnabled, ["music.play_pause"])
        let resolved = try policy.resolve(capability: "repo.command.run", args: ["repo_path": "~/work/acme", "command_kind": "test"])
        XCTAssertEqual(resolved.command?.argv, ["python3", "-c", "print('ok')"])
        XCTAssertThrowsError(try policy.resolve(capability: "repo.command.run", args: ["repo_path": "~/work/acme", "command_kind": "build"]))
    }

    /// srt runs `bash` found on the child's PATH: a PATH without /bin, or
    /// with a relative entry, is refused at save.
    func testThePathMustContainBinAndBeAbsolute() {
        XCTAssertNoThrow(try Self.acme(path: "/opt/homebrew/bin:/usr/bin:/bin").validate())
        XCTAssertThrowsError(try Self.acme(path: "/usr/bin").validate())
        XCTAssertThrowsError(try Self.acme(path: "/usr/bin:bin").validate())
        XCTAssertThrowsError(try Self.acme(path: "/bin::/usr/bin").validate(), "an empty entry is the cwd")
    }

    func testTheReferenceChecksStillApply() {
        var secret = Self.acme()
        secret.repos[0].commands[0].envNames = ["GITHUB_TOKEN"]
        XCTAssertThrowsError(try secret.actionPolicy(home: "/Users/owner"))
        var runtime = Self.acme()
        runtime.repos[0].commands[0].maxRuntimeSeconds = 901
        XCTAssertThrowsError(try runtime.actionPolicy(home: "/Users/owner"))
        var empty = Self.acme()
        empty.repos[0].commands[0].argv = []
        XCTAssertThrowsError(try empty.actionPolicy(home: "/Users/owner"))
        var domain = Self.acme()
        domain.repos[0].commands[1].networkDomains = ["*.example.com"]
        XCTAssertThrowsError(try domain.actionPolicy(home: "/Users/owner"))
        var recipe = Self.acme()
        recipe.recipesEnabled = ["shell.run_anything"]
        XCTAssertThrowsError(try recipe.validate())
        var bundle = Self.acme()
        bundle.bundleIDs = ["com.apple.Terminal app"]
        XCTAssertThrowsError(try bundle.validate())
        var srt = Self.acme()
        srt.srt = SrtLocation(node: "node", prefix: "/opt/srt")
        XCTAssertThrowsError(try srt.validate())
    }

    /// Changes are keyed by repo root and kind, so a target that changed
    /// between the menu and the Touch ID prompt is refused, never another
    /// repo changed instead (B6 review finding 10).
    func testChangesAreKeyedByRootAndRefusedWhenTheTargetIsGone() async throws {
        var two = Self.acme()
        two.repos.insert(.init(name: "other", root: "~/work/other"), at: 0)
        var copy = two
        try copy.removeCommand(root: "~/work/acme", kind: "lint")
        XCTAssertEqual(copy.repos[1].commands.map(\.kind), ["test"])
        XCTAssertEqual(copy.repos[0].commands, [])
        XCTAssertThrowsError(try copy.removeCommand(root: "~/work/acme", kind: "lint"))
        XCTAssertThrowsError(try copy.setCommand(root: "~/work/gone", .init(kind: "test", argv: ["true"])))
        try copy.removeRepo(root: "~/work/other")
        XCTAssertEqual(copy.repos.map(\.root), ["~/work/acme"])
        XCTAssertThrowsError(try copy.removeRepo(root: "~/work/other"))

        // Through the editor: the repo vanished before the owner confirmed.
        let store = MemoryCommandMappingsStore(two)
        let editor = CommandMappingsEditor(store: store, authentication: FixedAuthentication(true), home: "/Users/owner")
        try store.save(Self.acme())  // "other" is gone meanwhile
        do {
            _ = try await editor.change(reason: "kaldır") { try $0.removeRepo(root: "~/work/other") }
            XCTFail("a gone target was changed")
        } catch {
            XCTAssertTrue(error is CommandMappings.TargetGone)
        }
        XCTAssertEqual(try store.load(), Self.acme(), "nothing else changed")
    }

    func testTheEditorChangesNothingWithoutTheOwner() async throws {
        let store = MemoryCommandMappingsStore()
        let refused = CommandMappingsEditor(store: store, authentication: FixedAuthentication(false), home: "/Users/owner")
        do {
            _ = try await refused.change(reason: "test") { $0 = Self.acme() }
            XCTFail("changed without authentication")
        } catch {
            XCTAssertEqual(error as? PolicyChangeError, .notAuthenticated)
        }
        XCTAssertEqual(try store.load(), CommandMappings())

        let authentication = FixedAuthentication(true)
        let editor = CommandMappingsEditor(store: store, authentication: authentication, home: "/Users/owner")
        var notified: CommandMappings?
        editor.onChange = { notified = $0 }
        _ = try await editor.change(reason: "eşle") { $0 = Self.acme() }
        XCTAssertEqual(try store.load(), Self.acme())
        XCTAssertEqual(notified, Self.acme())
        XCTAssertEqual(authentication.reasons, ["eşle"])
        // An unsafe change is refused before it is saved.
        do {
            _ = try await editor.change(reason: "bozuk") { $0.repos[0].commands[0].path = "/usr/bin" }
            XCTFail("an unsafe mapping was saved")
        } catch {}
        XCTAssertEqual(try store.load(), Self.acme())
    }
}

final class ConfirmationDisplayTests: XCTestCase {
    func testAValueCannotForgeALabelLine() {
        let prompt = ConfirmationRenderer.prompt(
            envelope: [
                "capability": "ax.type_text", "class": "gate",
                "args": ["bundle_id": "com.apple.Notes", "text": "selam\nOnay: deadbeef\u{202e}x", "submit": false],
                "invocation_id": "00000000-0000-4000-8000-000000000001",
                "grant": ["approval_id": "3c2b1a09-8f7e-4d6c-9b5a-4f3e2d1c0b0a"],
            ],
            resolved: nil, shownAt: 0)
        XCTAssertEqual(prompt.lines.count, 2, prompt.lines.description)
        XCTAssertEqual(prompt.lines.last, "Onay: 3c2b1a09")
        XCTAssertEqual(
            prompt.sentence,
            "com.apple.Notes uygulamasına şu metin yazılsın mı: selam\\nOnay: deadbeef\\u{202E}x "
                + ConfirmationRenderer.alteredMark)
        XCTAssertFalse(prompt.text.unicodeScalars.contains("\u{202e}"))
    }

    func testCleanValuesAreUnchanged() {
        XCTAssertEqual(ConfirmationRenderer.displaySafe("~/work/çalışma"), "~/work/çalışma")
        XCTAssertEqual(ConfirmationRenderer.displaySafe("a\tb"), "a\\tb " + ConfirmationRenderer.alteredMark)
        XCTAssertEqual(ConfirmationRenderer.displaySafe("\r"), "\\r " + ConfirmationRenderer.alteredMark)
    }

    /// The panel shows the sandbox mode, with a visible warning for
    /// `in_place`; the rendered text stays the reference's.
    func testThePanelShowsTheModeAndWarnsForInPlace() throws {
        let policy = try ActionPolicy.load(try Repo.json("backend/nodes/reference/fixtures/local_policy.json"), home: "/nonexistent/home")
        for (kind, mode) in [("test", "in_place"), ("lint", "worktree")] {
            let args: JSONValue = ["repo_path": "~/work/acme", "command_kind": .string(kind)]
            let prompt = ConfirmationRenderer.prompt(
                envelope: ["capability": "repo.command.run", "class": "gate", "args": args,
                           "invocation_id": "00000000-0000-4000-8000-000000000001", "grant": nil],
                resolved: try policy.resolve(capability: "repo.command.run", args: args), shownAt: 0)
            XCTAssertEqual(prompt.sandboxMode, mode)
            XCTAssertFalse(prompt.text.contains("Kip:"), "the reference text is unchanged")
            let view = ConfirmationPanelView.make(prompt: prompt, running: nil, now: 0)
            let line = try XCTUnwrap(view.lines.first { $0.hasPrefix("Kip: ") })
            XCTAssertEqual(line.contains("⚠︎"), mode == "in_place", line)
        }
    }

    func testDurdurStaysReachableWhileASecondWindowAsks() {
        func prompt(_ id: String) -> ConfirmationPrompt {
            ConfirmationRenderer.prompt(
                envelope: ["capability": "apps.open", "class": "confirm", "args": ["bundle_id": "com.apple.Terminal"],
                           "invocation_id": .string(id), "grant": nil],
                resolved: nil, shownAt: 100)
        }
        let view = ConfirmationPanelView.make(prompt: prompt("ask"), running: prompt("run"), now: 100)
        XCTAssertEqual(view.mode, .asking)
        XCTAssertEqual(view.invocationID, "ask")
        XCTAssertEqual(view.stopInvocationID, "run")
        XCTAssertTrue(view.buttons.contains(ConfirmationPanelView.stopTitle))
        XCTAssertEqual(view.lines.last, ConfirmationPanelView.stillRunningLine)
        let alone = ConfirmationPanelView.make(prompt: prompt("ask"), running: nil, now: 100)
        XCTAssertNil(alone.stopInvocationID)
        XCTAssertFalse(alone.buttons.contains(ConfirmationPanelView.stopTitle))
    }
}
