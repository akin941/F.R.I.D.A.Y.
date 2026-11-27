// Clocks and the reconnect backoff (Phase 9 brief D13 item 2: exponential,
// 1 s -> 60 s, full jitter; mirrors ReferenceNode._schedule_reconnect).

import Foundation

/// The node clock, in integer Unix seconds (every protocol time is one).
public protocol NodeClock: AnyObject {
    func now() -> Int64
}

public final class SystemClock: NodeClock {
    public init() {}
    public func now() -> Int64 { Int64(Date().timeIntervalSince1970) }
}

/// A clock tests move by hand.
public final class ManualClock: NodeClock {
    public private(set) var current: Int64
    public init(_ start: Int64) { current = start }
    public func now() -> Int64 { current }
    public func advance(_ seconds: Int64) { current += seconds }
}

/// Full-jitter exponential backoff: attempt n waits uniform(0, min(60, 2^n)).
public struct Backoff {
    public static let minimumSeconds = 1.0
    public static let maximumSeconds = 60.0

    public private(set) var attempts = 0
    private var random: () -> Double

    /// `random` returns a value in [0, 1); tests inject a fixed sequence.
    public init(random: @escaping () -> Double = { Double.random(in: 0..<1) }) {
        self.random = random
    }

    public mutating func reset() { attempts = 0 }

    /// The delay before the next reconnect attempt, in seconds.
    public mutating func nextDelay() -> Double {
        let ceiling = min(Self.maximumSeconds, Self.minimumSeconds * pow(2, Double(min(attempts, 16))))
        attempts += 1
        return random() * ceiling
    }
}
