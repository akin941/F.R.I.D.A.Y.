// Live voice conformance (Phase 11 Mac session M1 contract test 2; runbook
// B7). Compiled ONLY in the unsigned `Conformance` configuration
// (`CONFORMANCE`); scripts/check-release-markers.sh refuses a Release
// binary that carries any of it.
//
// The Conformance build of the app, launched with
//
//   FridayNode --conformance-voice-cases <node/friday-voice/friday_voice/conformance/cases>
//              --conformance-voice-project <node/friday-voice>
//              (--conformance-voice-exec <node/friday-voice/.venv/bin/friday-voice> | --conformance-voice-uv <absolute uv>)
//              [--conformance-voice-case <id> ...] [--conformance-voice-time-scale <f>]
//
// runs every host case with the real `VoiceChildHost` (real process, real
// pipes, the real kill ladder) against the scripted hostile child, one
// process per child the host starts:
//
//   friday-voice conformance-child --case <id> [--cases <dir>] --time-scale <f>
//
// With `--conformance-voice-exec` the host spawns the project's entry point
// itself (what `uv run friday-voice` resolves to after `uv sync --frozen`,
// and how the app spawns the installed child); with `--conformance-voice-uv`
// it spawns `uv run --frozen friday-voice ...`. Under uv one case cannot
// pass: `uv run` keeps its own copy of the child's stdout open while it
// waits, so `host.child_stdout_short_read` (the child closes stdout and
// keeps running) never shows the host an end of stream; the watchdog
// releases instead. The exec form is the recorded run.
//
// and prints one JSON line per case: the host side's failures (mic, link,
// indicator, failure, restart, dropped plays, `to_child`) and every hostile
// child's verdict line (`"verdict": "pass"` each).
//
// Clock: cases marked `"clock": "any"` run on the real clock. The three
// `"clock": "manual"` cases (watchdog, child-exit backoff, watchdog kept
// quiet) run with real processes and a stepped host clock that only
// `advance_ms` moves (the hostile child sleeps the same time, scaled):
// their 999 ms / 1 ms boundaries cannot be asserted against a free-running
// clock without a race. A case whose host restarts the child is split at
// each restart (the `expect` that sees `hello`, `credential` again), and
// each restarted process plays its own part (`<id>.part<n>`, written to a
// temporary cases directory): the hostile child has no notion of being
// restarted.

#if CONFORMANCE

import Darwin
import Foundation

public final class VoiceConformanceLive {
    let casesDir: URL
    let project: String
    let uv: String?
    let exec: String?
    let only: Set<String>
    let timeScale: Double
    static let timeout: Double = 10

    public static func fromArguments(_ arguments: [String]) throws -> VoiceConformanceLive? {
        func value(_ flag: String) -> String? {
            guard let index = arguments.firstIndex(of: flag), index + 1 < arguments.count else { return nil }
            return arguments[index + 1]
        }
        guard let cases = value("--conformance-voice-cases") else { return nil }
        let uv = value("--conformance-voice-uv")
        let exec = value("--conformance-voice-exec")
        guard let project = value("--conformance-voice-project"), (uv ?? exec ?? "").hasPrefix("/"),
            (uv == nil) != (exec == nil)
        else {
            throw ConformanceError(
                "--conformance-voice-project and one absolute --conformance-voice-exec or --conformance-voice-uv are required")
        }
        var only: Set<String> = []
        for (index, flag) in arguments.enumerated() where flag == "--conformance-voice-case" && index + 1 < arguments.count {
            only.insert(arguments[index + 1])
        }
        let scale = value("--conformance-voice-time-scale").flatMap(Double.init) ?? 0.01
        return VoiceConformanceLive(
            casesDir: URL(fileURLWithPath: cases), project: project, uv: uv, exec: exec, only: only, timeScale: scale)
    }

    init(casesDir: URL, project: String, uv: String?, exec: String?, only: Set<String>, timeScale: Double) {
        self.casesDir = casesDir
        self.project = project
        self.uv = uv
        self.exec = exec
        self.only = only
        self.timeScale = timeScale
    }

    /// Run every (selected) host case; 0 when all pass.
    public func run() -> Int32 {
        let directory = casesDir.appendingPathComponent("host")
        let names = ((try? FileManager.default.contentsOfDirectory(atPath: directory.path)) ?? [])
            .filter { $0.hasSuffix(".json") }.sorted()
        var failed = 0
        var ran = 0
        for name in names {
            guard let data = FileManager.default.contents(atPath: directory.appendingPathComponent(name).path),
                let testCase = try? JSON.parse(data), let id = testCase["id"]?.stringValue
            else {
                emit(["case": .string(name), "verdict": "unreadable"])
                failed += 1
                continue
            }
            if !only.isEmpty && !only.contains(id) { continue }
            ran += 1
            let outcome = runCase(testCase, id: id)
            emit(outcome)
            if outcome["verdict"] != "pass" { failed += 1 }
        }
        emit(["summary": "voice host cases", "ran": .int(Int64(ran)), "failed": .int(Int64(failed))])
        return failed == 0 && ran > 0 ? 0 : 1
    }

    private func emit(_ value: JSONValue) {
        if let text = try? JSON.canonicalString(value) { print(text) }
        fflush(stdout)
    }

    /// The case's steps split at each restart of the child.
    static func parts(_ steps: [JSONValue]) -> [[JSONValue]] {
        var parts: [[JSONValue]] = [[]]
        var dropped = false
        for step in steps {
            if step["op"] == "expect", dropped, step["link"] == "open",
                step["to_child"]?.arrayValue?.prefix(2) == ["hello", "credential"]
            {
                parts.append([])
                dropped = false
            }
            if step["op"] == "expect", step["link"] == "dropped" { dropped = true }
            parts[parts.count - 1].append(step)
        }
        return parts
    }

    private func runCase(_ testCase: JSONValue, id: String) -> JSONValue {
        let steps = testCase["steps"]?.arrayValue ?? []
        let manual = testCase["clock"] == "manual"
        let parts = Self.parts(steps)
        var partsDir: URL?
        if parts.count > 1 {
            let dir = FileManager.default.temporaryDirectory.appendingPathComponent("friday-voice-live-\(UUID().uuidString)")
            let host = dir.appendingPathComponent("host")
            try? FileManager.default.createDirectory(at: host, withIntermediateDirectories: true)
            for (index, part) in parts.enumerated() {
                var file = testCase.objectValue ?? [:]
                file["id"] = .string("\(id).part\(index + 1)")
                file["steps"] = .array(part)
                try? JSON.canonical(.object(file)).write(to: host.appendingPathComponent("\(id).part\(index + 1).json"))
            }
            partsDir = dir
        }
        defer { if let partsDir { try? FileManager.default.removeItem(at: partsDir) } }

        let manualClock = ManualVoiceClock()
        let clock: VoiceClock = manual ? manualClock : SystemVoiceClock()
        let core = VoiceCasesLive.core(for: testCase, clock: clock)
        let install = VoiceChildHost.Install(
            home: project, executable: exec ?? uv ?? "/nonexistent", argv: ["friday-voice"], cwd: project,
            checkLock: false)
        let host = VoiceChildHost(core: core, install: install)
        let scale = String(timeScale)
        let prefix = exec != nil ? ["friday-voice"] : ["uv", "run", "--frozen", "friday-voice"]
        host.argvForStart = { start in
            var argv = prefix + ["conformance-child", "--time-scale", scale]
            if let partsDir {
                argv += ["--case", "\(id).part\(min(start, parts.count))", "--cases", partsDir.path]
            } else {
                argv += ["--case", id]
            }
            return argv
        }
        let lock = NSLock()
        var verdicts: [JSONValue] = []
        var sent: [String] = []
        host.onStderrLine = { line in
            guard let doc = try? JSON.parse(line), doc["verdict"] != nil else { return }
            lock.lock()
            verdicts.append(doc)
            lock.unlock()
        }
        host.onFramesWritten = { frames in
            let types = frames.compactMap(VoiceFraming.type(of:))
            lock.lock()
            sent += types
            lock.unlock()
        }
        host.start(autoTick: !manual)

        func wait(_ condition: () -> Bool, timeout: Double = VoiceConformanceLive.timeout) {
            let deadline = Date().addingTimeInterval(timeout)
            while Date() < deadline {
                if condition() { return }
                usleep(5_000)
            }
        }
        func state<T>(_ read: (VoiceHostCore) -> T) -> T { host.queue.sync { read(core) } }

        var failures: [String] = []
        var childFrames = 0
        var seen = 0
        for (index, step) in steps.enumerated() {
            switch step["op"]?.stringValue {
            case "child_send":
                childFrames += 1
                if manual {
                    let want = childFrames
                    wait({ state { $0.framesReceived >= want || !$0.linkOpen } }, timeout: 5)
                }
            case "child_close_stdout", "child_exit":
                wait { !state { $0.linkOpen } }
            case "host_local":
                host.queue.sync {
                    VoiceCasesLive.local(core, step)
                    host.tick()
                }
            case "host_capture":
                host.queue.sync {
                    for _ in 0..<(step["frames"]?.intValue ?? 0) { core.feedAudio(Data(count: VoiceHostCore.frameBytes)) }
                    host.tick()
                }
            case "advance_ms":
                let ms = step["ms"]?.intValue ?? 0
                if manual {
                    host.queue.sync {
                        manualClock.advance(ms)
                        host.tick()
                    }
                } else {
                    usleep(useconds_t(ms * 1000))
                }
            case "expect":
                let want = childFrames
                wait { state { $0.framesReceived >= want || !$0.linkOpen } }
                if step["link"] == "dropped" { wait { !state { $0.linkOpen } } }
                if let toChild = step["to_child"]?.arrayValue {
                    // The frames the host owes were written synchronously
                    // with the event; give the pipe a moment for nothing.
                    let count = toChild.count
                    wait({ lock.withLock { sent.count - seen } >= count }, timeout: count == 0 ? 0 : 5)
                }
                let batch: [String] = lock.withLock {
                    defer { seen = sent.count }
                    return Array(sent[seen...])
                }
                failures += host.queue.sync { VoiceCasesLive.check(step, core, toChild: batch, index: index) }
            default:
                failures.append("step \(index): unknown op")
            }
        }
        let starts = state { $0.starts }
        // Bounded like quit, with time for the hostile child to write its verdict.
        host.stop(wait: true, grace: 3)
        wait { lock.withLock { verdicts.count } >= starts }
        let collected = lock.withLock { verdicts }
        if collected.count < starts { failures.append("\(starts - collected.count) hostile child(ren) wrote no verdict") }
        for verdict in collected where verdict["verdict"] != "pass" {
            failures.append("hostile child: \((try? JSON.canonicalString(verdict)) ?? "?")")
        }
        return [
            "case": .string(id), "clock": manual ? "manual" : "any", "launcher": exec != nil ? "exec" : "uv", "processes": .int(Int64(starts)),
            "verdict": failures.isEmpty ? "pass" : "fail", "failures": .array(failures.map { .string($0) }),
            "child_verdicts": .array(collected.map { $0["verdict"] ?? .null }),
        ]
    }
}

/// The offline runner's helpers, for the live run (VoiceConformanceTests
/// keeps its own copy: the test target cannot see this file in SwiftPM).
enum VoiceCasesLive {
    static let credential: [String: JSONValue] = [
        "token": .string("jpat_" + String(repeating: "c0nf0rmanceT0ken", count: 2)),
        "expires_at": "2099-01-01T00:00:00Z",
        "server_url": "https://friday.example",
        "ca_sha256": .string(String(repeating: "ab", count: 32)),
    ]

    static func core(for testCase: JSONValue, clock: VoiceClock) -> VoiceHostCore {
        let host = testCase["host"]
        let muted: VoiceHostCore.Muted
        switch host?["muted"] {
        case .bool(true): muted = .yes
        case .string("unreadable"): muted = .unreadable
        default: muted = .no
        }
        return VoiceHostCore(
            clock: clock, credential: credential, mode: host?["mode"]?.stringValue ?? "push_to_talk", muted: muted,
            linkUp: host?["link_up"]?.boolValue ?? true, hostVersion: "0.1.0-reference")
    }

    static func local(_ core: VoiceHostCore, _ step: JSONValue) {
        let value = step["value"]
        switch step["event"]?.stringValue {
        case "link": core.setLink(value == "up")
        case "mute":
            switch value {
            case .bool(true): core.setMute(.yes)
            case .bool(false): core.setMute(.no)
            default: core.setMute(.unreadable)
            }
        case "mode": core.setMode(value?.stringValue ?? "off")
        case "ptt": core.ptt(value?.stringValue ?? "up")
        default: break
        }
    }

    static func check(_ step: JSONValue, _ core: VoiceHostCore, toChild: [String], index: Int) -> [String] {
        let observed: [String: JSONValue] = [
            "mic": core.micHeld ? "held" : "released",
            "link": core.linkOpen ? "open" : "dropped",
            "indicator": .string(core.indicator),
            "failure": core.lastFailure.map { .string($0) } ?? .null,
            "restart_in_ms": core.restartInMs.map { .int($0) } ?? .null,
            "dropped_play": .int(Int64(core.droppedPlay)),
            "to_child": .array(toChild.map { .string($0) }),
        ]
        var failures: [String] = []
        for (key, want) in step.objectValue ?? [:] where key != "op" && key != "describe" {
            let got = observed[key] ?? .null
            if got != want { failures.append("step \(index): \(key) is \(got), expected \(want)") }
        }
        return failures
    }
}

#endif
