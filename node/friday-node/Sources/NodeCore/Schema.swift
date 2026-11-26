// The protocol's strict JSON Schema subset (backend/nodes/protocol/messages.py
// `StrictValidator`): Draft 2020-12 semantics for the keywords the v1
// schemas and capability contracts use, with the two deliberate,
// stricter deviations the Python side documents and the Swift port must
// mirror:
// - `pattern` is a full match of the whole string (a trailing newline never
//   slips past a `$` anchor);
// - numbers are integers only (the strict parser already refuses any other).
// Plus the v2 keyword `x-friday-max-bytes` (UTF-8 byte bound of a string).
//
// Supported: type, enum, const, properties, required, additionalProperties,
// minProperties, maxProperties, items, minItems, maxItems, uniqueItems,
// minLength, maxLength (code points), pattern, minimum, maximum,
// x-friday-max-bytes, boolean schemas. Unknown keywords (title,
// description, x-friday-opaque, ...) are ignored, as in JSON Schema: an
// `x-friday-opaque` hole is validated elsewhere.

import Foundation

public enum Schema {
    /// True when `instance` is valid against `schema`.
    public static func isValid(_ instance: JSONValue, against schema: JSONValue) -> Bool {
        validate(instance, schema)
    }

    private static func validate(_ instance: JSONValue, _ schema: JSONValue) -> Bool {
        switch schema {
        case .bool(let allowed): return allowed
        case .object(let keywords): return validate(instance, keywords)
        default: return false  // not a schema: fail closed
        }
    }

    private static func validate(_ instance: JSONValue, _ s: [String: JSONValue]) -> Bool {
        if let type = s["type"], !matchesType(instance, type) { return false }
        if let options = s["enum"]?.arrayValue, !options.contains(instance) { return false }
        if let constant = s["const"], constant != instance { return false }

        switch instance {
        case .string(let text):
            let length = text.unicodeScalars.count
            if let minimum = s["minLength"]?.intValue, length < minimum { return false }
            if let maximum = s["maxLength"]?.intValue, length > maximum { return false }
            if let bytes = s["x-friday-max-bytes"]?.intValue, text.utf8.count > bytes { return false }
            if let pattern = s["pattern"]?.stringValue, !fullMatch(text, pattern) { return false }
        case .int(let number):
            if let minimum = s["minimum"]?.intValue, number < minimum { return false }
            if let maximum = s["maximum"]?.intValue, number > maximum { return false }
        case .array(let items):
            if let minimum = s["minItems"]?.intValue, items.count < minimum { return false }
            if let maximum = s["maxItems"]?.intValue, items.count > maximum { return false }
            if s["uniqueItems"]?.boolValue == true {
                for (index, item) in items.enumerated() where items[(index + 1)...].contains(item) {
                    return false
                }
            }
            if let itemSchema = s["items"] {
                for item in items where !validate(item, itemSchema) { return false }
            }
        case .object(let fields):
            if let minimum = s["minProperties"]?.intValue, fields.count < minimum { return false }
            if let maximum = s["maxProperties"]?.intValue, fields.count > maximum { return false }
            if let required = s["required"]?.arrayValue {
                for name in required {
                    guard let key = name.stringValue, fields[key] != nil else { return false }
                }
            }
            let properties = s["properties"]?.objectValue ?? [:]
            for (key, value) in fields {
                if let propertySchema = properties[key] {
                    if !validate(value, propertySchema) { return false }
                } else if let additional = s["additionalProperties"] {
                    if !validate(value, additional) { return false }
                }
            }
        default:
            break
        }
        return true
    }

    private static func matchesType(_ instance: JSONValue, _ type: JSONValue) -> Bool {
        if let name = type.stringValue { return matches(instance, name) }
        if let names = type.arrayValue {
            return names.contains { $0.stringValue.map { matches(instance, $0) } ?? false }
        }
        return false
    }

    private static func matches(_ instance: JSONValue, _ name: String) -> Bool {
        switch (name, instance) {
        case ("null", .null), ("boolean", .bool), ("integer", .int), ("number", .int),
            ("string", .string), ("array", .array), ("object", .object):
            return true
        default:
            return false
        }
    }

    // MARK: Full-match patterns

    nonisolated(unsafe) private static var cache: [String: NSRegularExpression] = [:]
    private static let lock = NSLock()

    private static func regex(_ pattern: String) -> NSRegularExpression? {
        lock.lock()
        defer { lock.unlock() }
        if let cached = cache[pattern] { return cached }
        // `\z` (true end of input, unlike `$`) makes the engine backtrack to
        // a match that ends at the end, as `re.fullmatch` does.
        guard let compiled = try? NSRegularExpression(pattern: "^(?:" + pattern + ")\\z") else { return nil }
        cache[pattern] = compiled
        return compiled
    }

    /// Python `re.fullmatch`: the match must start at 0 and cover the whole
    /// string. A pattern that does not compile fails closed.
    static func fullMatch(_ text: String, _ pattern: String) -> Bool {
        guard let expression = regex(pattern) else { return false }
        let whole = NSRange(text.startIndex..<text.endIndex, in: text)
        guard let match = expression.firstMatch(in: text, options: [.anchored], range: whole) else {
            return false
        }
        return match.range == whole
    }
}
