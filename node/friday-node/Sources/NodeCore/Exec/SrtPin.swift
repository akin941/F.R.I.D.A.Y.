// What the owner may confirm as `srt` (hardening N2): not whatever is in the
// prefix on first use, but exactly the packages runbook A6 installed and
// docs/mcp/dependencies/node-gateway.md records.
//
// Confirming a location (Touch ID, the policy UI) refuses unless:
// 1. the prefix's `package-lock.json` names every pinned package below at
//    its version with its exact npm `integrity`, and nothing else;
// 2. each package's tarball, found offline in the npm cache by that
//    integrity (`~/.npm/_cacache/content-v2/sha512/…`, content-addressed),
//    hashes (SHA-512) to the pinned integrity;
// 3. the installed package directory (`<prefix>/node_modules/<name>`) holds
//    exactly the tarball's `package/` files, byte for byte (the tarball is
//    unpacked with /usr/bin/tar into a private temp dir; argv only).
// So the confirmed tree is the published npm package, not just a version
// string. Only then are the digests of the installed files stored. A
// missing cache entry refuses (verification needs the tarball; `npm cache
// add <name>@<version>` with network restores it).
//
// The integrity of the sandbox-runtime itself is the one A6 recorded from
// the registry; the four dependencies' are the ones of the lockfile A6
// produced (`npm install --save-exact`), reviewed here.

import CryptoKit
import Foundation

public enum SrtPin {
    public struct Package: Equatable, Sendable {
        public let path: String
        public let version: String
        public let integrity: String
    }

    public static let packages: [Package] = [
        Package(
            path: "node_modules/@anthropic-ai/sandbox-runtime", version: "0.0.83",
            integrity: "sha512-yEusORnVJrNseF/9fvjVYyk6tr79/aFbIB3T/X8p2puLZdaaiGNeYN50v0CE+1DT70v7XuzKOAnTivh1f5cksA=="),
        Package(
            path: "node_modules/@pondwader/socks5-server", version: "1.0.10",
            integrity: "sha512-bQY06wzzR8D2+vVCUoBsr5QS2U6UgPUQRmErNwtsuI6vLcyRKkafjkr3KxbtGFf9aBBIV2mcvlsKD1UYaIV+sg=="),
        Package(
            path: "node_modules/commander", version: "12.1.0",
            integrity: "sha512-Vw8qHK3bZM9y/P10u3Vib8o/DdkvA2OtPtZvD871QKjy74Wj1WSKFILMPRPSdUSx5RFK1arlJzEtA4PkFgnbuA=="),
        Package(
            path: "node_modules/node-forge", version: "1.4.4",
            integrity: "sha512-T6VRRpF3+czIGcXW/LOeoD7Ej28d8CF1UMEAbJyJRUtYMvyXAgxUPW2kbUibumvV9LiUQAznJOFyeYclzSJMMg=="),
        Package(
            path: "node_modules/zod", version: "3.25.76",
            integrity: "sha512-gzUt/qt81nXsFGKIFcC3YnfEAx5NkunCfnDlvuBSSFS02bcXu4Lmea0AFIUwbLWxWPx3d9p8S5QoaujKcNQxcQ=="),
    ]

    public static var defaultCache: String { NSHomeDirectory() + "/.npm/_cacache" }

    /// Why `prefix` is not the pinned srt, or nil when it is (all three
    /// checks above). `cache` is the npm cache directory.
    public static func problem(prefix: String, cache: String = SrtPin.defaultCache) -> String? {
        guard let data = FileManager.default.contents(atPath: prefix + "/package-lock.json"),
            let lock = try? JSON.parse(data), let entries = lock["packages"]?.objectValue
        else { return "package-lock.json unreadable" }
        let listed = Set(entries.keys.filter { !$0.isEmpty })
        let pinned = Set(packages.map(\.path))
        guard listed == pinned else {
            return "package-lock.json lists other packages: " + listed.symmetricDifference(pinned).sorted().joined(separator: ", ")
        }
        if let problem = layoutProblem(prefix: prefix) { return problem }
        for package in packages {
            guard entries[package.path]?["version"]?.stringValue == package.version,
                entries[package.path]?["integrity"]?.stringValue == package.integrity
            else { return "\(package.path): not the pinned \(package.version) / integrity" }
            if let problem = verifyInstalled(package, prefix: prefix, cache: cache) { return problem }
        }
        return nil
    }

    /// The `.bin` links npm creates for the pinned packages, with their
    /// exact targets.
    public static let binLinks = ["srt": "../@anthropic-ai/sandbox-runtime/dist/cli.js"]

    /// `<prefix>/node_modules` holds exactly the pinned package roots (a
    /// scope directory holds exactly its pinned packages), `.bin` (exactly
    /// `binLinks`) and `.package-lock.json`: no extra module that a
    /// `require` could find (hardening review, N2).
    static func layoutProblem(prefix: String) -> String? {
        let modules = prefix + "/node_modules"
        func entries(_ path: String) -> Set<String> {
            Set((try? FileManager.default.contentsOfDirectory(atPath: path)) ?? [])
        }
        let names = packages.map { String($0.path.dropFirst("node_modules/".count)) }
        var expectedTop: Set<String> = [".bin", ".package-lock.json"]
        var scopes: [String: Set<String>] = [:]
        for name in names {
            let parts = name.split(separator: "/", maxSplits: 1).map(String.init)
            if parts.count == 2 {
                expectedTop.insert(parts[0])
                scopes[parts[0], default: []].insert(parts[1])
            } else {
                expectedTop.insert(name)
            }
        }
        let top = entries(modules)
        guard top == expectedTop else {
            return "node_modules holds other entries: " + top.symmetricDifference(expectedTop).sorted().joined(separator: ", ")
        }
        for (scope, expected) in scopes {
            let found = entries(modules + "/" + scope)
            guard found == expected else {
                return "node_modules/\(scope) holds other entries: "
                    + found.symmetricDifference(expected).sorted().joined(separator: ", ")
            }
        }
        let bin = entries(modules + "/.bin")
        guard bin == Set(binLinks.keys) else {
            return "node_modules/.bin holds other entries: "
                + bin.symmetricDifference(Set(binLinks.keys)).sorted().joined(separator: ", ")
        }
        for (name, target) in binLinks {
            let path = modules + "/.bin/" + name
            var info = stat()
            guard lstat(path, &info) == 0, info.st_mode & S_IFMT == S_IFLNK,
                (try? FileManager.default.destinationOfSymbolicLink(atPath: path)) == target
            else { return "node_modules/.bin/\(name) is not the link to \(target)" }
        }
        return nil
    }

    /// The npm cache file of an `sha512-…` integrity (cacache content-v2).
    static func cacheFile(_ integrity: String, cache: String) -> String? {
        guard integrity.hasPrefix("sha512-"), let raw = Data(base64Encoded: String(integrity.dropFirst(7))),
            raw.count == 64
        else { return nil }
        let hex = raw.map { String(format: "%02x", $0) }.joined()
        let start = hex.startIndex
        let first = hex[start..<hex.index(start, offsetBy: 2)]
        let second = hex[hex.index(start, offsetBy: 2)..<hex.index(start, offsetBy: 4)]
        return "\(cache)/content-v2/sha512/\(first)/\(second)/\(hex.dropFirst(4))"
    }

    static func verifyInstalled(_ package: Package, prefix: String, cache: String) -> String? {
        guard let file = cacheFile(package.integrity, cache: cache),
            let tarball = FileManager.default.contents(atPath: file)
        else { return "\(package.path): its npm tarball is not in the npm cache (needed to verify offline)" }
        let digest = Data(SHA512.hash(data: tarball))
        guard "sha512-" + digest.base64EncodedString() == package.integrity else {
            return "\(package.path): the cached tarball does not match the pinned integrity"
        }
        guard let scratch = try? TempDirectory.make(prefix: "friday-srt-verify-") else { return "temp dir failed" }
        defer { TempDirectory.remove(scratch) }
        let archive = scratch + "/package.tgz"
        guard FileManager.default.createFile(atPath: archive, contents: tarball) else { return "temp write failed" }
        let unpacked = scratch + "/x"
        try? FileManager.default.createDirectory(atPath: unpacked, withIntermediateDirectories: false)
        guard let outcome = try? BoundedRun.run(
            executable: "/usr/bin/tar", argv: ["tar", "-xzf", archive, "-C", unpacked], environment: ["LANG": "C"],
            timeout: 60), outcome.exitCode == 0
        else { return "\(package.path): the tarball could not be unpacked" }
        let expected = files(under: unpacked + "/package")
        let installed = files(under: prefix + "/" + package.path)
        guard Set(expected.keys) == Set(installed.keys) else {
            let differ = Set(expected.keys).symmetricDifference(Set(installed.keys)).sorted().prefix(5)
            return "\(package.path): installed files differ from the npm package (\(differ.joined(separator: ", ")))"
        }
        for (name, data) in expected where installed[name] != data {
            return "\(package.path): \(name) differs from the npm package"
        }
        return nil
    }

    /// Regular files under `root` by relative path (symlinks and others as
    /// a marker, so they never equal a file).
    static func files(under root: String) -> [String: Data] {
        var out: [String: Data] = [:]
        guard let enumerator = FileManager.default.enumerator(atPath: root) else { return out }
        for case let relative as String in enumerator {
            let path = root + "/" + relative
            var info = stat()
            guard lstat(path, &info) == 0 else { continue }
            switch info.st_mode & S_IFMT {
            case S_IFREG: out[relative] = FileManager.default.contents(atPath: path) ?? Data()
            case S_IFDIR: continue
            default: out[relative] = Data("\u{0}not a regular file".utf8)
            }
        }
        return out
    }
}
