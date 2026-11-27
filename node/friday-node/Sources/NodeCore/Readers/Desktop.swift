// `apps.frontmost` and `windows.list` (Phase 9 brief M2; backend/nodes/
// reference/readers.py `_app`, `_apps_frontmost`, `_windows_list`).
//
// The release node reads `NSWorkspace` and `CGWindowListCopyWindowInfo`
// (`SystemDesktop`); the tests and the conformance build read the
// reference fixture (`FixtureDesktop`, backend/nodes/reference/fixtures/
// desktop.json). Window titles are returned only when the owner's local
// toggle is on, and the system source reads them only with the Screen
// Recording grant.

import AppKit
import CoreGraphics
import Foundation

public struct DesktopApp: Equatable, Sendable {
    public var bundleID: String?
    public var appName: String?
    public var titles: [String]
    public var windowCount: Int
    public var onScreen: Bool

    public init(bundleID: String?, appName: String?, titles: [String] = [], windowCount: Int = 0, onScreen: Bool = false) {
        self.bundleID = bundleID
        self.appName = appName
        self.titles = titles
        self.windowCount = windowCount
        self.onScreen = onScreen
    }
}

public protocol DesktopSource: Sendable {
    func frontmost() -> DesktopApp?
    func windows(withTitles: Bool) -> [DesktopApp]
}

/// The reference fixture (`window_count` = the number of fixture titles).
public struct FixtureDesktop: DesktopSource {
    let document: LooseJSON

    public init(fixturesDirectory: URL) throws {
        let data = try Data(contentsOf: fixturesDirectory.appendingPathComponent("desktop.json"))
        guard let document = LooseJSON.parse(data) else { throw ReaderError() }
        self.document = document
    }

    private static func app(_ entry: LooseJSON?) -> DesktopApp {
        let titles = entry?["titles"]?.array?.compactMap(\.string) ?? []
        var onScreen = false
        if case .bool(let flag)? = entry?["on_screen"] { onScreen = flag }
        return DesktopApp(
            bundleID: entry?["bundle_id"]?.string, appName: entry?["app_name"]?.string, titles: titles,
            windowCount: titles.count, onScreen: onScreen)
    }

    public func frontmost() -> DesktopApp? { Self.app(document["frontmost"]) }

    public func windows(withTitles: Bool) -> [DesktopApp] {
        (document["windows"]?.array ?? []).map { Self.app($0) }
    }
}

/// This Mac: NSWorkspace and the window server.
public struct SystemDesktop: DesktopSource {
    public init() {}

    public func frontmost() -> DesktopApp? {
        guard let app = NSWorkspace.shared.frontmostApplication else { return DesktopApp(bundleID: nil, appName: nil) }
        return DesktopApp(bundleID: app.bundleIdentifier, appName: app.localizedName)
    }

    public func windows(withTitles: Bool) -> [DesktopApp] {
        guard let info = CGWindowListCopyWindowInfo([.optionAll, .excludeDesktopElements], kCGNullWindowID)
            as? [[String: Any]]
        else { return [] }
        // Titles need the Screen Recording grant; without it they stay off.
        let titlesAllowed = withTitles && CGPreflightScreenCaptureAccess()
        var order: [pid_t] = []
        var apps: [pid_t: DesktopApp] = [:]
        for window in info {
            guard (window[kCGWindowLayer as String] as? Int) == 0,
                let pid = window[kCGWindowOwnerPID as String] as? pid_t
            else { continue }
            if apps[pid] == nil {
                let running = NSRunningApplication(processIdentifier: pid)
                apps[pid] = DesktopApp(
                    bundleID: running?.bundleIdentifier,
                    appName: running?.localizedName ?? window[kCGWindowOwnerName as String] as? String)
                order.append(pid)
            }
            apps[pid]!.windowCount += 1
            if (window[kCGWindowIsOnscreen as String] as? Bool) == true { apps[pid]!.onScreen = true }
            if titlesAllowed, let title = window[kCGWindowName as String] as? String, !title.isEmpty {
                apps[pid]!.titles.append(title)
            }
        }
        return order.map { apps[$0]! }
    }
}

extension NodeReaders {
    func app(_ entry: DesktopApp) -> (JSONValue, JSONValue) {
        let bundle: JSONValue = entry.bundleID.flatMap(ident).map(JSONValue.string) ?? .null
        var name: JSONValue = .null
        if let appName = entry.appName {
            let scrubbed = scrubbedText(appName, limit: 255)
            if !scrubbed.isEmpty { name = .string(scrubbed) }
        }
        return (bundle, name)
    }

    func appsFrontmost(_ args: JSONValue, now: Int64) throws -> JSONValue {
        let (bundle, name) = app(desktop.frontmost() ?? DesktopApp(bundleID: nil, appName: nil))
        return ["bundle_id": bundle, "app_name": name]
    }

    func windowsList(_ args: JSONValue, now: Int64) throws -> JSONValue {
        var rows: [JSONValue] = []
        for entry in desktop.windows(withTitles: windowTitles) {
            let (bundle, name) = app(entry)
            var row: [String: JSONValue] = [
                "bundle_id": bundle,
                "app_name": name,
                "window_count": .int(Int64(entry.windowCount)),
                "on_screen": .bool(entry.onScreen),
            ]
            if windowTitles {
                row["titles"] = .array(entry.titles.prefix(ReaderLimits.maxTitles).map {
                    .string(scrubbedText($0, limit: ReaderLimits.maxTitleChars))
                })
            }
            if rowOK("windows.list", "windows", .object(row)) { rows.append(.object(row)) }
        }
        return ["windows": .array(Array(rows.prefix(Int(args["limit"]?.intValue ?? 0))))]
    }
}
