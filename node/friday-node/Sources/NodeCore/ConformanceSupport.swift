// Live conformance mode (Phase 9 brief M1, contract test 2). Compiled ONLY
// in the unsigned `Conformance` configuration of FridayNode.xcodeproj, which
// defines the Swift compilation condition CONFORMANCE. SwiftPM and the
// Release build never see this file's contents: there is no environment
// variable, user default or launch argument in the release build that
// changes the TLS pin, the pinned signing keys or the server URL (ADR-013
// decisions 2 and 4). ReleaseHygieneTests checks the type below is absent,
// and scripts/build-sign-local.sh refuses a release binary carrying any
// marker of it.
//
// The fake gateway (`manage.py node_conformance_server --external
// --tls-self-signed --protocol 1 --hook-socket PATH`) drives the node over a
// JSON-lines Unix socket (backend/nodes/conformance/gateway.py
// `ExternalHook`): for each case it sends `prepare` (the node builds a fresh
// session for the case and connects to the gateway), then `advance`,
// `tick`, `kill_switch`, `voice_state`, `report` and `wait_disconnected`.
// The node's clock is the case's manual clock; frames go over the real WSS
// socket with the self-signed certificate pinned.
//
// Launch arguments (read only here):
//   --conformance-hook PATH         the hook socket
//   --conformance-gateway HOST:PORT the fake gateway
//   --conformance-cert PATH         its self-signed certificate (PEM)
//   --conformance-keys PATH         backend/nodes/conformance/keys/conformance_keys.json
//   --conformance-fixtures PATH     backend/nodes/reference/fixtures
//   --conformance-home PATH         a fixture home built by
//                                   nodes/reference/fixtures/factory.py
//                                   (build_fixture_home), which the readers
//                                   read (M2): transcripts, the two repos,
//                                   desktop.json in place of NSWorkspace

#if CONFORMANCE

import Foundation
import Security

@objc(FridayNodeConformancePins)
public final class ConformancePins: NSObject {
    public let signingKeys: [String: Data]
    public let tlsPin: Data

    public init(keysFile: URL, certificateFile: URL) throws {
        let keys = try JSON.parse(try Data(contentsOf: keysFile))
        guard let text = keys["signing"]?["public_key_b64"]?.stringValue, let raw = Signing.publicKey(base64: text)
        else { throw ConformanceError("conformance key file has no signing public key") }
        signingKeys = [Signing.keyID(for: raw): raw]
        let pem = try String(contentsOf: certificateFile, encoding: .utf8)
        let body = pem.components(separatedBy: "\n").filter { !$0.hasPrefix("-----") }.joined()
        guard let der = Data(base64Encoded: body), let certificate = SecCertificateCreateWithData(nil, der as CFData),
            let pin = TLSPin.sha256(of: certificate)
        else { throw ConformanceError("cannot read the conformance certificate") }
        tlsPin = pin
    }
}

public struct ConformanceError: Error, CustomStringConvertible {
    public let description: String
    init(_ description: String) { self.description = description }
}

public final class ConformanceHookClient {
    static let nodeID = "6f1e2d3c-4b5a-4987-8a6b-5c4d3e2f1a0b"

    let hookPath: String
    let gateway: String
    let pins: ConformancePins
    let fixtures: URL
    let home: String
    let repos: [LocalRepo]
    let desktop: FixtureDesktop
    /// sha256 of the fixture policy document: the same before and after
    /// every case (nothing in the protocol can change the local policy).
    let policyFingerprint: String
    private let queue = DispatchQueue(label: "friday-node.conformance")
    private var session: NodeSession?
    private var clock: ManualClock?
    private var reader: NodeReaders?
    private var connection: NodeConnection?

    /// Parse the launch arguments; nil when the app was not started in
    /// conformance mode.
    public static func fromArguments(_ arguments: [String]) throws -> ConformanceHookClient? {
        func value(_ flag: String) -> String? {
            guard let index = arguments.firstIndex(of: flag), index + 1 < arguments.count else { return nil }
            return arguments[index + 1]
        }
        guard let hook = value("--conformance-hook") else { return nil }
        guard let gateway = value("--conformance-gateway"), let cert = value("--conformance-cert"),
            let keys = value("--conformance-keys"), let fixtures = value("--conformance-fixtures"),
            let home = value("--conformance-home")
        else { throw ConformanceError("missing --conformance-* arguments") }
        let pins = try ConformancePins(
            keysFile: URL(fileURLWithPath: keys), certificateFile: URL(fileURLWithPath: cert))
        return try ConformanceHookClient(
            hookPath: hook, gateway: gateway, pins: pins, fixtures: URL(fileURLWithPath: fixtures), home: home)
    }

    init(hookPath: String, gateway: String, pins: ConformancePins, fixtures: URL, home: String) throws {
        self.hookPath = hookPath
        self.gateway = gateway
        self.pins = pins
        self.fixtures = fixtures
        self.home = ReaderPaths.realpathC(home)
        let policyData = try Data(contentsOf: fixtures.appendingPathComponent("local_policy.json"))
        let policy = try JSON.parse(policyData)
        repos = (policy["repos"]?.arrayValue ?? []).compactMap { repo in
            guard let name = repo["name"]?.stringValue, let root = repo["root"]?.stringValue else { return nil }
            return LocalRepo(name: name, root: root, home: ReaderPaths.realpathC(home))
        }
        desktop = try FixtureDesktop(fixturesDirectory: fixtures)
        policyFingerprint = JSON.hexDigest(policyData)
    }

    /// Serve the hook until the gateway closes it. Returns the exit status.
    public func run() -> Int32 {
        let fd = socket(AF_UNIX, SOCK_STREAM, 0)
        guard fd >= 0 else { return 1 }
        defer { close(fd) }
        var address = sockaddr_un()
        address.sun_family = sa_family_t(AF_UNIX)
        let pathBytes = Array(hookPath.utf8)
        guard pathBytes.count < MemoryLayout.size(ofValue: address.sun_path) else { return 1 }
        withUnsafeMutableBytes(of: &address.sun_path) { buffer in
            buffer.copyBytes(from: pathBytes)
        }
        let connected = withUnsafePointer(to: &address) {
            $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                Foundation.connect(fd, $0, socklen_t(MemoryLayout<sockaddr_un>.size))
            }
        }
        guard connected == 0 else { return 1 }
        var buffer = Data()
        var chunk = [UInt8](repeating: 0, count: 65_536)
        while true {
            let count = recv(fd, &chunk, chunk.count, 0)
            if count <= 0 { break }
            buffer.append(contentsOf: chunk[0..<count])
            while let newline = buffer.firstIndex(of: 0x0A) {
                let line = buffer[buffer.startIndex..<newline]
                buffer.removeSubrange(buffer.startIndex...newline)
                let answer = (try? handle(Data(line))) ?? ["ok": false]
                guard var reply = try? JSON.encode(answer).data(using: .utf8) else { return 1 }
                reply.append(0x0A)
                let sent = reply.withUnsafeBytes { send(fd, $0.baseAddress, $0.count, 0) }
                if sent != reply.count { return 1 }
            }
        }
        queue.sync { connection?.stop() }
        return 0
    }

    /// One hook op; the reply. Ops run on the connection's queue.
    func handle(_ line: Data) throws -> JSONValue {
        // The gateway's JSON may carry floats nowhere: a case is integers only.
        let op = try JSON.parse(line)
        switch op["op"]?.stringValue {
        case "prepare":
            try queue.sync { try prepare(op["case"] ?? [:]) }
            return ["ok": true, "policy_fingerprint": .string(policyFingerprint)]
        case "advance":
            let seconds = op["seconds"]?.intValue ?? 0
            queue.sync {
                clock?.advance(seconds)
                connection?.tick()
            }
        case "tick":
            queue.sync { connection?.tick() }
        case "kill_switch":
            break  // v1: no window or executor to stop
        case "voice_state":
            queue.sync { _ = session?.relayVoiceState(op["state"] ?? .null) }
        case "report":
            return queue.sync { report() }
        case "wait_disconnected":
            for _ in 0..<100 {
                if queue.sync(execute: { connection?.isFinished ?? true }) { break }
                usleep(50_000)
            }
        default:
            return ["ok": false]
        }
        return ["ok": true]
    }

    private func prepare(_ document: JSONValue) throws {
        connection?.stop()
        let setup = document["setup"] ?? [:]
        let clock = ManualClock(setup["now"]?.intValue ?? 0)
        // Both directory grants on unless the case turns one off; titles
        // off unless the case turns them on (D13 item 5).
        var grants = ReaderGrants(claude: true, codex: true)
        if let claude = setup["reader_grants"]?["claude"]?.boolValue { grants.claude = claude }
        if let codex = setup["reader_grants"]?["codex"]?.boolValue { grants.codex = codex }
        let reader = NodeReaders(
            home: home, repos: repos, grants: grants, windowTitles: setup["window_titles"]?.boolValue ?? false,
            desktop: desktop, usage: .transcripts)
        var availability = reader.availability()
        for (name, value) in setup["availability"]?.objectValue ?? [:] { availability[name] = value.stringValue }
        let session = NodeSession(
            nodeID: Self.nodeID, pinned: pins.signingKeys, catalogue: .standard(availability: availability),
            readers: reader, clock: clock, nodeVersion: NodeInfo.nodeVersion, osVersion: NodeInfo.osVersion,
            readDelaySeconds: setup["read_delay"]?.intValue ?? 0)
        guard let url = URL(string: "wss://\(gateway)\(NodeProtocol.gatewayPath)") else {
            throw ConformanceError("bad --conformance-gateway")
        }
        let connection = NodeConnection(
            url: url, secret: "jnd_fake_gateway_token", pin: pins.tlsPin, session: session, queue: queue,
            autoTick: false)
        self.clock = clock
        self.reader = reader
        self.session = session
        self.connection = connection
        connection.start()
    }

    private func report() -> JSONValue {
        guard let session else { return ["ok": false] }
        return [
            "reader_calls": .int(Int64(reader?.calls.count ?? 0)),
            "executor_calls": 0,
            "executor_terminated": 0,
            "windows_shown": 0,
            "closed_reason": session.closedReason.map(JSONValue.string) ?? .null,
            "reconnect_allowed": .bool(session.reconnectAllowed),
            "policy_fingerprint": .string(policyFingerprint),
            // Seconds as an integer number of milliseconds would change the
            // check; the gateway compares 0 <= delay <= max, so whole seconds,
            // rounded up, keep the bound honest.
            "next_reconnect_delay": session.nextReconnectDelay.map { .int(Int64($0.rounded(.up))) } ?? .null,
            "voice_states_dropped": .int(Int64(session.voiceStatesDropped)),
        ]
    }
}

#endif
