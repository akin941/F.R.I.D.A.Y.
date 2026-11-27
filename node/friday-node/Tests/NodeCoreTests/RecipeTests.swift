// The AppleScript recipes (runbook B6 contract test 2; Phase 10 brief M2).
//
// - Escaping: every recipe's rendered source carries its values only as
//   AppleScript string literals; hostile values (quotes, backslashes, an
//   attempt to close the literal and chain `do shell script`) stay inside
//   the literal, control characters are refused, and `osascript` reading a
//   literal back returns exactly the value (no app is ever told anything:
//   the check script is `return <literal>`).
// - The `params` fuzz families of every recipe are refused before
//   interpolation: a spy interpolator records no call, a spy runner runs
//   nothing.
// - Every recipe of the vendored catalogue has exactly one source file here.

import Foundation
import NodeCore
import XCTest

/// Records every value handed to interpolation.
final class SpyInterpolator: ScriptInterpolator {
    private(set) var values: [String] = []
    func literal(_ value: String) throws -> String {
        values.append(value)
        return try AppleScriptLiteral().literal(value)
    }
}

/// Records every source it is asked to run; runs nothing.
final class SpyScriptRunner: AppleScriptRunner {
    private(set) var sources: [String] = []
    var succeed = true
    func run(source: String, timeout: Double) -> Bool {
        sources.append(source)
        return succeed
    }
}

final class RecipeTests: XCTestCase {
    static let hostile = [
        "/Users/owner/work/a\"b", "/Users/owner/work/a\\b", "/Users/owner/x\" & (do shell script \"id\") & \"",
        "/Users/owner/çalışma dizini", "/Users/owner/a'b", "/Users/owner/¬continued", "/Users/owner/\\\"",
    ]

    func testTheCatalogueAndTheRecipeFilesMatch() {
        XCTAssertEqual(Set(RecipeBook.recipes.map { $0.name }), Set(RecipeContract.all.keys))
        XCTAssertEqual(RecipeBook.recipes.count, 5)
    }

    func testTheLiteralEscapesQuotesAndBackslashesAndRefusesControls() throws {
        let literal = AppleScriptLiteral()
        XCTAssertEqual(try literal.literal("plain"), "\"plain\"")
        XCTAssertEqual(try literal.literal("a\"b"), "\"a\\\"b\"")
        XCTAssertEqual(try literal.literal("a\\b"), "\"a\\\\b\"")
        XCTAssertEqual(try literal.literal("\\\""), "\"\\\\\\\"\"")
        for control in ["\n", "\r", "\t", "\u{0}", "\u{1b}", "\u{7f}", "\u{85}", "\u{9b}", "\u{2028}", "\u{2029}"] {
            XCTAssertThrowsError(try literal.literal("a\(control)b"), control.unicodeScalars.first!.description)
        }
    }

    /// The literal never ends early: between the opening and the closing
    /// quote every `"` is escaped.
    func testEveryRecipeKeepsHostileValuesInsideOneLiteral() throws {
        for root in Self.hostile {
            for name in ["finder.reveal_repo", "terminal.open_repo"] {
                let spy = SpyInterpolator()
                let source = try RecipeBook.render(name, params: ["repo_path": "~/work/acme"], repoRoot: root, interpolate: spy)
                XCTAssertEqual(spy.values, [root], name)
                let literal = try AppleScriptLiteral().literal(root)
                XCTAssertTrue(source.contains(literal), name)
                // Remove the one literal: no quote, `do shell script` or
                // value text is left outside the fixed source.
                let rest = source.replacingOccurrences(of: literal, with: "\"\"")
                XCTAssertFalse(rest.contains("do shell script"), name)
                XCTAssertFalse(rest.contains("owner"), name)
            }
        }
        let spy = SpyInterpolator()
        let volume = try RecipeBook.render("system.volume_set", params: ["level": 42], repoRoot: nil, interpolate: spy)
        XCTAssertEqual(volume, "set volume output volume (\"42\" as integer)")
        XCTAssertEqual(spy.values, ["42"])
        for name in ["music.next_track", "music.play_pause"] {
            let spy = SpyInterpolator()
            _ = try RecipeBook.render(name, params: [:], repoRoot: nil, interpolate: spy)
            XCTAssertEqual(spy.values, [], name)
        }
    }

    /// `osascript -e 'return <literal>'` gives the value back unchanged: the
    /// escaping is what AppleScript itself reads. No app is addressed.
    func testOsascriptReadsTheLiteralBackUnchanged() throws {
        guard FileManager.default.isExecutableFile(atPath: OsascriptRunner.executable) else {
            throw XCTSkip("no osascript")
        }
        for value in Self.hostile + ["", "\"", "\\", "ünïcödé ✓"] {
            let script = "return " + (try AppleScriptLiteral().literal(value))
            let outcome = try BoundedRun.run(
                executable: OsascriptRunner.executable, argv: ["osascript", "-e", script],
                environment: ["LANG": "en_US.UTF-8"], timeout: 20)
            XCTAssertEqual(outcome.exitCode, 0, value)
            var text = String(decoding: outcome.stdout, as: UTF8.self)
            if text.hasSuffix("\n") { text.removeLast() }
            XCTAssertTrue(Array(text.unicodeScalars) == Array(value.unicodeScalars), "\(value) -> \(text)")
        }
    }

    /// Contract test 2: every params fuzz family of every recipe is refused
    /// before any value is interpolated, and nothing runs.
    func testParamsFuzzFamiliesNeverReachInterpolation() throws {
        var checked = 0
        for (name, recipe) in RecipeContract.all {
            let valid = Fuzz.validParams[name] ?? [:]
            for (family, params) in Fuzz.cases(owner: name, schema: recipe.paramsSchema, valid: valid) {
                let spy = SpyInterpolator()
                XCTAssertThrowsError(
                    try RecipeBook.render(name, params: params, repoRoot: "/Users/owner/work/acme", interpolate: spy),
                    "\(name) \(family)")
                XCTAssertEqual(spy.values, [], "\(name) \(family) reached interpolation")
                let runner = SpyScriptRunner()
                let executor = MacActionExecutor(
                    srt: SrtExecutor(location: SrtLocation(node: "/nonexistent/node", prefix: "/nonexistent")),
                    bundleIDs: [], recipesEnabled: Set(RecipeContract.all.keys), permissions: GrantedProbe(),
                    accessibility: FakeAccessibility(), workspace: FakeWorkspace(), scripts: runner, interpolator: spy)
                let outcome = executor.perform(
                    ActionRequest(
                        capability: ActionContracts.recipeCapability, args: ["recipe": .string(name), "params": params],
                        recipe: name, repoRoot: "/Users/owner/work/acme"))
                XCTAssertFalse(outcome.ok, "\(name) \(family)")
                XCTAssertEqual(runner.sources, [], "\(name) \(family) ran")
                XCTAssertEqual(spy.values, [], "\(name) \(family) reached interpolation")
                checked += 1
            }
        }
        XCTAssertGreaterThan(checked, 100)
    }

    func testTheExecutorRunsOnlyEnabledRecipesWithTheResolvedRepo() {
        let runner = SpyScriptRunner()
        let executor = MacActionExecutor(
            srt: SrtExecutor(location: SrtLocation(node: "/nonexistent/node", prefix: "/nonexistent")),
            recipesEnabled: ["music.play_pause", "finder.reveal_repo"], permissions: GrantedProbe(),
            accessibility: FakeAccessibility(), workspace: FakeWorkspace(), scripts: runner)
        XCTAssertFalse(executor.perform(ActionRequest(
            capability: ActionContracts.recipeCapability, args: ["recipe": "music.next_track", "params": [:]],
            recipe: "music.next_track")).ok, "not enabled")
        XCTAssertTrue(executor.perform(ActionRequest(
            capability: ActionContracts.recipeCapability, args: ["recipe": "music.play_pause", "params": [:]],
            recipe: "music.play_pause")).ok)
        XCTAssertFalse(executor.perform(ActionRequest(
            capability: ActionContracts.recipeCapability,
            args: ["recipe": "finder.reveal_repo", "params": ["repo_path": "~/work/acme"]],
            recipe: "finder.reveal_repo")).ok, "a repo recipe needs the resolved repo")
        XCTAssertTrue(executor.perform(ActionRequest(
            capability: ActionContracts.recipeCapability,
            args: ["recipe": "finder.reveal_repo", "params": ["repo_path": "~/work/acme"]],
            recipe: "finder.reveal_repo", repoRoot: "/Users/owner/work/acme")).ok)
        XCTAssertEqual(runner.sources.count, 2)
        XCTAssertTrue(runner.sources[1].contains("POSIX file \"/Users/owner/work/acme\""))
        runner.succeed = false
        XCTAssertFalse(executor.perform(ActionRequest(
            capability: ActionContracts.recipeCapability, args: ["recipe": "music.play_pause", "params": [:]],
            recipe: "music.play_pause")).ok)
    }

    /// Availability: granted only when every enabled recipe's target app
    /// has the Automation grant (a recipe without a target needs none).
    func testRecipeAvailabilityFollowsTheAutomationGrants() {
        let probe = GrantedProbe()
        func availability(_ recipes: Set<String>) -> String {
            MacActionExecutor(
                srt: SrtExecutor(location: SrtLocation(node: "/nonexistent/node", prefix: "/nonexistent")),
                recipesEnabled: recipes, permissions: probe, accessibility: FakeAccessibility(),
                workspace: FakeWorkspace(), scripts: SpyScriptRunner()
            ).availability(for: ActionContracts.recipeCapability)
        }
        probe.automationAnswers = ["com.apple.Music": .granted, "com.apple.finder": .undetermined]
        XCTAssertEqual(availability([]), "granted")
        XCTAssertEqual(availability(["system.volume_set"]), "granted")
        XCTAssertEqual(availability(["music.play_pause"]), "granted")
        XCTAssertEqual(availability(["music.play_pause", "finder.reveal_repo"]), "not_granted")
        probe.automationAnswers["com.apple.finder"] = .notRunning
        XCTAssertEqual(availability(["finder.reveal_repo"]), "not_granted")
        probe.automationAnswers["com.apple.finder"] = .denied
        XCTAssertEqual(availability(["finder.reveal_repo"]), "not_granted")
    }
}
