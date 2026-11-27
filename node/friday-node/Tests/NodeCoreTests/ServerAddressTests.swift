// Phase 13 M1 (runbook E1): the pairing platform and "Sunucu adresini
// değiştir". The behaviour mirrors `friday-node-linux config set server-url`
// (node/friday-node-linux/tests/test_credential.py re-point tests,
// test_daemon_failclosed.py::test_the_pinned_client_checks_the_pin_before_sending_anything).
// The TLS servers here are local python3 processes on 127.0.0.1 with
// throwaway openssl certificates; nothing touches the Keychain.

import CryptoKit
import Foundation
import NodeCore
import XCTest

/// Counts the authentication prompts.
final class CountingAuthentication: OwnerAuthentication, @unchecked Sendable {
    private let answer: Bool
    private let lock = NSLock()
    private var _reasons: [String] = []
    init(_ answer: Bool) { self.answer = answer }
    var reasons: [String] {
        lock.lock()
        defer { lock.unlock() }
        return _reasons
    }
    func authenticate(reason: String) async -> Bool {
        lock.withLock { _reasons.append(reason) }
        return answer
    }
}

/// A probe that answers `result` and records its calls.
final class SpyProbe: PinProbe, @unchecked Sendable {
    private let result: PinProbeResult
    private let lock = NSLock()
    private var _calls: [(String, Int, Data)] = []
    init(_ result: PinProbeResult) { self.result = result }
    var calls: [(String, Int, Data)] {
        lock.lock()
        defer { lock.unlock() }
        return _calls
    }
    func probe(host: String, port: Int, pin: Data) async -> PinProbeResult {
        lock.withLock { _calls.append((host, port, pin)) }
        return result
    }
}

final class PairingPlatformTests: XCTestCase {
    /// backend/nodes/tests/test_pairing_platform.py: the Swift app sends
    /// `platform: "macos"`, and exactly the four fields the server accepts.
    func testPairingBodySendsPlatformMacos() {
        let request = PairingRequest(
            serverURL: URL(string: "https://friday-home.local")!, code: " CODE-1 \n", nodeName: "MacBook")
        let body = request.body
        XCTAssertEqual(body["platform"], "macos")
        XCTAssertEqual(Set(body.objectValue?.keys.map { $0 } ?? []), ["code", "node_name", "protocol_versions", "platform"])
        XCTAssertEqual(body["code"], "CODE-1")
        XCTAssertEqual(body["protocol_versions"], [.int(NodeProtocol.version)])
    }
}

final class ServerAddressCheckTests: XCTestCase {
    /// The Linux `check_server_url` outputs, case for case.
    func testAcceptedAddressesMatchLinux() throws {
        let cases: [(String, String)] = [
            ("https://friday-home.local", "https://friday-home.local"),
            ("https://Friday-Home.LOCAL:8443/", "https://Friday-Home.LOCAL:8443"),
            ("HTTPS://host", "https://host"),
            ("https://host:", "https://host:"),
            ("https://host:0", "https://host:0"),
            ("https://host:65535", "https://host:65535"),
            ("https://host:08443", "https://host:08443"),
            ("https://host?", "https://host"),
            ("https://host#", "https://host"),
            ("https://h/?", "https://h"),
            ("https://h:443#", "https://h:443"),
            ("https://[::1]:8443", "https://[::1]:8443"),
            (" https://host", "https://host"),
            ("https://192.168.1.20:8443", "https://192.168.1.20:8443"),
        ]
        for (input, expected) in cases {
            XCTAssertEqual(try ServerAddress.check(input).absoluteString, expected, input)
        }
    }

    /// Refused by Linux too.
    func testRefusedAddressesMatchLinux() {
        let refused = [
            "http://friday-home.local", "ftp://x", "https:host", "https://", "https://:443", "https:///x",
            "https://u:p@host", "https://u@host", "https://host/api", "https://host//", "https://host?a=1",
            "https://host#frag", "https://host:65536", "https://host:+1", "https://host:80:90", "https://[::1",
            "https://host]", "https://[127.0.0.1]", "http://10.0.0.5", "",
        ]
        for input in refused {
            XCTAssertThrowsError(try ServerAddress.check(input), input) { error in
                guard case ServerAddressError.invalid = error else { return XCTFail("\(input): \(error)") }
            }
        }
    }

    /// Stricter than Python where Python would accept a URL nobody means
    /// (recorded in the Phase 13 brief M1 record).
    func testStricterThanPythonOnDegenerateAddresses() {
        for input in ["https://@host", "https://:@host", "https://ho st", "https://ho\tst", "https://h\u{00F4}st",
            "https://h;x", "https://h%41", "https://h\\x", "https://[v1.x]", "https://host:\u{0661}"]
        {
            XCTAssertThrowsError(try ServerAddress.check(input), input)
        }
    }

    func testMenuHostComesFromTheCredential() {
        var credential = CredentialTests.sample()
        XCTAssertEqual(ServerAddress.displayHost(of: credential), "friday.example.net")
        credential.serverURL = URL(string: "https://friday-home.local:8443")!
        XCTAssertEqual(ServerAddress.displayHost(of: credential), "friday-home.local:8443")
        credential.serverURL = URL(string: "https://friday-home.local:443")!
        XCTAssertEqual(ServerAddress.displayHost(of: credential), "friday-home.local")
        XCTAssertNil(ServerAddress.displayHost(of: nil))
    }
}

final class ServerAddressEditorTests: XCTestCase {
    private func pairedStore() throws -> MemoryCredentialStore {
        let store = MemoryCredentialStore()
        try store.save(CredentialTests.sample())
        return store
    }

    /// test_repoint_keeps_the_pins_and_accepts_a_server_that_passes_them.
    func testRepointKeepsEveryPinAndOnlyChangesTheAddress() async throws {
        let store = try pairedStore()
        let original = try XCTUnwrap(store.load())
        let auth = CountingAuthentication(true)
        let probe = SpyProbe(.match)
        let editor = ServerAddressEditor(target: StoreServerAddressTarget(store: store), authentication: auth, probe: probe)
        let saved = try await editor.change(to: "https://friday-home.local:8443/")
        let moved = try XCTUnwrap(store.load())
        XCTAssertEqual(saved, moved)
        XCTAssertEqual(moved.serverURL.absoluteString, "https://friday-home.local:8443")
        XCTAssertEqual(moved.tlsPinSHA256, original.tlsPinSHA256)
        XCTAssertEqual(moved.pinnedKeys, original.pinnedKeys)
        XCTAssertEqual(moved.nodeID, original.nodeID)
        XCTAssertEqual(moved.secret, original.secret)
        XCTAssertEqual(moved.gatewayPath, original.gatewayPath)
        XCTAssertEqual(moved.rotateAfterDays, original.rotateAfterDays)
        XCTAssertEqual(moved.secretIssuedAt, original.secretIssuedAt)
        XCTAssertEqual(moved.gatewayURL?.absoluteString, "wss://friday-home.local:8443/ws/node/v1")
        XCTAssertEqual(auth.reasons, [ServerAddressEditor.reason])
        XCTAssertEqual(probe.calls.count, 1)
        XCTAssertEqual(probe.calls.first?.0, "friday-home.local")
        XCTAssertEqual(probe.calls.first?.1, 8443)
        XCTAssertEqual(probe.calls.first?.2, original.tlsPinSHA256)  // the pin taken at pairing
    }

    /// The LocalAuthentication gate: no owner, no probe, no change.
    func testNothingChangesWithoutTheOwner() async throws {
        let store = try pairedStore()
        let before = try store.load()?.encoded()
        let probe = SpyProbe(.match)
        let editor = ServerAddressEditor(
            target: StoreServerAddressTarget(store: store), authentication: CountingAuthentication(false), probe: probe)
        do {
            try await editor.change(to: "https://friday-home.local")
            XCTFail("changed without authentication")
        } catch ServerAddressError.notAuthenticated {}
        XCTAssertEqual(try store.load()?.encoded(), before)
        XCTAssertTrue(probe.calls.isEmpty)
    }

    /// test_repoint_refuses_a_server_whose_pins_differ: refused, no change.
    func testServerWhosePinsDifferIsRefusedAndNothingChanges() async throws {
        let store = try pairedStore()
        let before = try store.load()?.encoded()
        let editor = ServerAddressEditor(
            target: StoreServerAddressTarget(store: store), authentication: CountingAuthentication(true),
            probe: SpyProbe(.mismatch))
        do {
            try await editor.change(to: "https://other.example")
            XCTFail("a server off the pins was accepted")
        } catch ServerAddressError.identityChanged {}
        XCTAssertEqual(try store.load()?.encoded(), before)
        XCTAssertEqual(ServerAddress.identityChangedMessage, "Sunucu kimliği değişti — yeniden eşleştirin")
    }

    /// Linux: "cannot reach the new server ...; no change made".
    func testUnreachableServerChangesNothing() async throws {
        let store = try pairedStore()
        let before = try store.load()?.encoded()
        let editor = ServerAddressEditor(
            target: StoreServerAddressTarget(store: store), authentication: CountingAuthentication(true),
            probe: SpyProbe(.unreachable))
        do {
            try await editor.change(to: "https://friday-home.local")
            XCTFail("an unreachable server was accepted")
        } catch ServerAddressError.unreachable {}
        XCTAssertEqual(try store.load()?.encoded(), before)
    }

    /// test_repoint_refuses_plain_http: refused before the owner is even asked.
    func testInvalidAddressIsRefusedBeforeAuthentication() async throws {
        let store = try pairedStore()
        let before = try store.load()?.encoded()
        let auth = CountingAuthentication(true)
        let probe = SpyProbe(.match)
        let editor = ServerAddressEditor(target: StoreServerAddressTarget(store: store), authentication: auth, probe: probe)
        for text in ["http://10.0.0.5", "https://u:p@host", "https://host/api", "https://host?x=1"] {
            do {
                try await editor.change(to: text)
                XCTFail("accepted \(text)")
            } catch ServerAddressError.invalid {}
        }
        XCTAssertTrue(auth.reasons.isEmpty)
        XCTAssertTrue(probe.calls.isEmpty)
        XCTAssertEqual(try store.load()?.encoded(), before)
    }

    func testUnpairedNodeHasNoAddressToChange() async {
        let editor = ServerAddressEditor(
            target: StoreServerAddressTarget(store: MemoryCredentialStore()),
            authentication: CountingAuthentication(true), probe: SpyProbe(.match))
        do {
            try await editor.change(to: "https://friday-home.local")
            XCTFail("an unpaired node was re-pointed")
        } catch ServerAddressError.notPaired {
        } catch {
            XCTFail("\(error)")
        }
    }

    /// The runner is the app's target: the re-point is saved in the store,
    /// and every consumer (gateway URL, the voice mint's credential) reads
    /// the new address from the same credential.
    func testRunnerRepointFeedsEveryConsumer() throws {
        let store = try pairedStore()
        let runner = NodeRunner(
            store: store, policyStore: MemoryLocalPolicyStore(), desktop: EmptyDesktop(),
            home: NSTemporaryDirectory())
        XCTAssertEqual(runner.serverHost, "friday.example.net")
        let url = URL(string: "https://friday-home.local")!
        let original = try XCTUnwrap(store.load())
        let saved = try runner.repoint(to: url, nodeID: original.nodeID)
        XCTAssertEqual(try store.load(), saved)
        XCTAssertEqual(saved.tlsPinSHA256, original.tlsPinSHA256)
        XCTAssertEqual(saved.pinnedKeys, original.pinnedKeys)
        XCTAssertEqual(runner.serverHost, "friday-home.local")
        XCTAssertEqual(runner.currentCredential()?.gatewayURL?.absoluteString, "wss://friday-home.local/ws/node/v1")
        XCTAssertThrowsError(try runner.repoint(to: url, nodeID: "00000000-0000-4000-8000-000000000000"))
    }
}

struct EmptyDesktop: DesktopSource {
    func frontmost() -> DesktopApp? { nil }
    func windows(withTitles: Bool) -> [DesktopApp] { [] }
}

/// ADR-013 decision 5: no server message can change the address. The
/// session has no handle on the credential; address-shaped frames are
/// `unknown_type` (check 3), like the conformance policy-mutation family.
final class ServerAddressFrameTests: XCTestCase {
    final class Reader: ReadProvider {
        func read(capability: String, args: JSONValue, now: Int64) -> ReadOutcome { .failure }
    }

    func testAddressShapedFramesAreUnknownType() throws {
        let nodeID = "6f1e2d3c-4b5a-4987-8a6b-5c4d3e2f1a0b"
        let session = NodeSession(
            nodeID: nodeID, pinned: [:], catalogue: NodeCatalogue.defaultPolicy(), readers: Reader(),
            clock: ManualClock(1_796_000_000), nodeVersion: "friday-node test", osVersion: "macOS test")
        _ = session.connect()
        _ = session.receive(
            try JSON.canonical([
                "v": 1, "type": "hello.ack", "id": "4f5b027e-07eb-4be5-a70e-d157c4bb21de", "node_id": .string(nodeID),
                "heartbeat_seconds": 15, "rotate_after_days": 30, "server_time": 1_796_000_000, "accepted": [],
            ]))
        XCTAssertTrue(session.sessionReady)
        let frames: [JSONValue] = [
            ["v": 1, "type": "config.update", "id": "ccc8e837-20b3-4d0f-9b32-7e5e545b9161",
             "server_url": "https://evil.example"],
            ["v": 1, "type": "server.repoint", "id": "ccc8e837-20b3-4d0f-9b32-7e5e545b9162",
             "server_url": "https://evil.example"],
        ]
        for frame in frames {
            let out = session.receive(try JSON.canonical(frame))
            XCTAssertEqual(out.count, 1)
            XCTAssertEqual(out.first?["type"], "result")
            XCTAssertEqual(out.first?["refusal_code"], "unknown_type")
        }
    }
}

// MARK: - Live TLS: pins checked before anything is sent

final class ServerAddressTLSTests: XCTestCase {
    struct Cert {
        let certPath: String
        let keyPath: String
        let pin: Data
    }

    private var directory: URL!
    private var servers: [Process] = []

    override func setUpWithError() throws {
        guard FileManager.default.isExecutableFile(atPath: "/usr/bin/python3"),
            FileManager.default.isExecutableFile(atPath: "/usr/bin/openssl")
        else { throw XCTSkip("python3 or openssl missing") }
        directory = FileManager.default.temporaryDirectory.appendingPathComponent("fn-e1-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
    }

    override func tearDown() {
        for server in servers where server.isRunning { server.terminate() }
        if let directory { try? FileManager.default.removeItem(at: directory) }
    }

    private func makeCert(_ name: String) throws -> Cert {
        let cert = directory.appendingPathComponent("\(name).pem").path
        let key = directory.appendingPathComponent("\(name).key").path
        // A named-curve P-256 key (LibreSSL's `req -newkey ec` writes
        // explicit parameters, which Security.framework cannot read).
        for arguments in [
            ["ecparam", "-name", "prime256v1", "-genkey", "-noout", "-out", key],
            ["req", "-new", "-x509", "-key", key, "-out", cert, "-days", "1", "-subj", "/CN=localhost"],
        ] {
            let process = Process()
            process.executableURL = URL(fileURLWithPath: "/usr/bin/openssl")
            process.arguments = arguments
            process.standardOutput = FileHandle.nullDevice
            process.standardError = FileHandle.nullDevice
            try process.run()
            process.waitUntilExit()
            XCTAssertEqual(process.terminationStatus, 0)
        }
        let pem = try String(contentsOfFile: cert, encoding: .ascii)
        let body = pem.components(separatedBy: "\n").filter { !$0.hasPrefix("-----") }.joined()
        let der = try XCTUnwrap(Data(base64Encoded: body))
        let certificate = try XCTUnwrap(SecCertificateCreateWithData(nil, der as CFData))
        return Cert(certPath: cert, keyPath: key, pin: try XCTUnwrap(TLSPin.sha256(of: certificate)))
    }

    /// A TLS server that reports, per connection, whether the handshake
    /// completed and how many application bytes arrived.
    private static let serverScript = """
        import socket, ssl, sys
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(sys.argv[1], sys.argv[2])
        s = socket.socket(); s.bind(("127.0.0.1", 0)); s.listen(8)
        print(s.getsockname()[1], flush=True)
        while True:
            c, _ = s.accept()
            c.settimeout(3)
            try:
                t = ctx.wrap_socket(c, server_side=True)
            except Exception:
                print("conn handshake=fail bytes=0", flush=True); c.close(); continue
            n = 0
            try:
                while True:
                    b = t.recv(65536)
                    if not b: break
                    n += len(b)
            except Exception:
                pass
            print("conn handshake=ok bytes=%d" % n, flush=True); t.close()
        """

    private final class Lines: @unchecked Sendable {
        private let lock = NSLock()
        private var buffer = ""
        func append(_ text: String) {
            lock.lock()
            buffer += text
            lock.unlock()
        }
        var lines: [String] {
            lock.lock()
            defer { lock.unlock() }
            return buffer.components(separatedBy: "\n").filter { !$0.isEmpty }
        }
    }

    private func startServer(_ cert: Cert) throws -> (port: Int, lines: Lines) {
        let script = directory.appendingPathComponent("server.py")
        try Self.serverScript.write(to: script, atomically: true, encoding: .utf8)
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/python3")
        process.arguments = ["-I", script.path, cert.certPath, cert.keyPath]
        let pipe = Pipe()
        process.standardOutput = pipe
        process.standardError = FileHandle.nullDevice
        let lines = Lines()
        pipe.fileHandleForReading.readabilityHandler = { handle in
            let data = handle.availableData
            if !data.isEmpty { lines.append(String(decoding: data, as: UTF8.self)) }
        }
        try process.run()
        servers.append(process)
        let deadline = Date().addingTimeInterval(10)
        while lines.lines.isEmpty && Date() < deadline { Thread.sleep(forTimeInterval: 0.02) }
        let port = try XCTUnwrap(lines.lines.first.flatMap { Int($0) })
        return (port, lines)
    }

    private func waitForConnectionLine(_ lines: Lines, count: Int = 2) {
        let deadline = Date().addingTimeInterval(10)
        while lines.lines.count < count && Date() < deadline { Thread.sleep(forTimeInterval: 0.02) }
    }

    /// Same pins -> accepted; nothing but the handshake is sent.
    func testProbeAcceptsTheServerWithTheSameKey() async throws {
        let cert = try makeCert("same")
        let (port, lines) = try startServer(cert)
        let result = await TLSPinProbe().probe(host: "127.0.0.1", port: port, pin: cert.pin)
        XCTAssertEqual(result, .match)
        waitForConnectionLine(lines)
        XCTAssertEqual(Array(lines.lines.dropFirst()), ["conn handshake=ok bytes=0"])
    }

    /// Different SPKI -> refused; the server receives no application byte.
    func testProbeRefusesAServerWithAnotherKey() async throws {
        let paired = try makeCert("paired")
        let other = try makeCert("other")
        let (port, lines) = try startServer(other)
        let result = await TLSPinProbe().probe(host: "127.0.0.1", port: port, pin: paired.pin)
        XCTAssertEqual(result, .mismatch)
        waitForConnectionLine(lines)
        // The server saw the connection, the handshake failed, and not one
        // application byte arrived (an empty list must not pass).
        XCTAssertEqual(Array(lines.lines.dropFirst()), ["conn handshake=fail bytes=0"])
    }

    func testProbeReportsAnUnreachableServer() async throws {
        let listener = socket(AF_INET, SOCK_STREAM, 0)
        var address = sockaddr_in()
        address.sin_family = sa_family_t(AF_INET)
        address.sin_addr.s_addr = inet_addr("127.0.0.1")
        address.sin_port = 0
        var length = socklen_t(MemoryLayout<sockaddr_in>.size)
        _ = withUnsafePointer(to: &address) {
            $0.withMemoryRebound(to: sockaddr.self, capacity: 1) { Darwin.bind(listener, $0, length) }
        }
        _ = withUnsafeMutablePointer(to: &address) {
            $0.withMemoryRebound(to: sockaddr.self, capacity: 1) { Darwin.getsockname(listener, $0, &length) }
        }
        let port = Int(UInt16(bigEndian: address.sin_port))
        Darwin.close(listener)  // nothing listens there now
        let result = await TLSPinProbe(timeoutSeconds: 3).probe(
            host: "127.0.0.1", port: port, pin: Data(repeating: 1, count: 32))
        XCTAssertEqual(result, .unreachable)
    }

    /// The runner at a (re-pointed) address whose server presents another
    /// key: refused in the TLS handshake, before the WebSocket request (so
    /// no Bearer, no `hello`); status "Sunucu kimliği değişti"; the stored
    /// address stays the one the owner chose (no fallback).
    func testRunnerRefusesAMismatchedServerBeforeHello() throws {
        let paired = try makeCert("paired")
        let other = try makeCert("other")
        let (port, lines) = try startServer(other)
        var credential = CredentialTests.sample()
        credential.serverURL = URL(string: "https://127.0.0.1:\(port)")!
        credential.tlsPinSHA256 = paired.pin
        let store = MemoryCredentialStore()
        try store.save(credential)
        let runner = NodeRunner(
            store: store, policyStore: MemoryLocalPolicyStore(), desktop: EmptyDesktop(),
            home: NSTemporaryDirectory())
        let refused = expectation(description: "identity changed")
        refused.assertForOverFulfill = false
        runner.onStatus = { status in
            if status == .identityChanged { refused.fulfill() }
        }
        runner.start()
        wait(for: [refused], timeout: 15)
        runner.stop()
        waitForConnectionLine(lines)
        let connections = Array(lines.lines.dropFirst())
        XCTAssertFalse(connections.isEmpty)
        XCTAssertTrue(connections.allSatisfy { $0 == "conn handshake=fail bytes=0" }, "\(lines.lines)")
        XCTAssertEqual(try store.load()?.serverURL, credential.serverURL)
    }
}
