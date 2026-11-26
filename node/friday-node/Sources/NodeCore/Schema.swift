// The protocol's strict JSON Schema subset (backend/nodes/protocol/messages.py
// `StrictValidator`): Draft 2020-12 semantics for the keywords the v1
// schemas and capability contracts use, with the two deliberate,
// stricter deviations the Python side documents and the Swift port must
// mirror:
// - `pattern` is a full match of the whole string (a trailing newline never
//   slips past a `$` anchor);
// - numbers are integers only (the strict parser already refuses any other).
// Plus the v2 keyword `x-friday-max-bytes` (UTF-8 byte bound of a string),
// and the three standard keywords the v2 `event` schema uses in exactly one
// place (Phase 11 D15; messages.py docstring): a local `$ref` into the root
// schema's `$defs`, `allOf`, and `if`/`then`/`else`.
//
// Supported: type, enum, const, properties, required, additionalProperties,
// minProperties, maxProperties, items, minItems, maxItems, uniqueItems,
// minLength, maxLength (code points), pattern, minimum, maximum,
// x-friday-max-bytes, $ref (`#...` JSON pointers into the root only; any
// other reference fails closed), allOf, if/then/else, boolean schemas.
// Unknown keywords (title, description, x-friday-opaque, ...) are ignored,
// as in JSON Schema: an `x-friday-opaque` hole is validated elsewhere.

import Foundation

public enum Schema {
    /// True when `instance` is valid against `schema`.
    public static func isValid(_ instance: JSONValue, against schema: JSONValue) -> Bool {
        validate(instance, schema, root: schema, depth: 0)
    }

    /// Nesting (and `$ref` chains) deeper than this fail closed: no schema
    /// here comes close, and a reference cycle can never loop.
    private static let maxDepth = 64

    private static func validate(_ instance: JSONValue, _ schema: JSONValue, root: JSONValue, depth: Int) -> Bool {
        guard depth < maxDepth else { return false }
        switch schema {
        case .bool(let allowed): return allowed
        case .object(let keywords): return validate(instance, keywords, root: root, depth: depth)
        default: return false  // not a schema: fail closed
        }
    }

    /// A local reference (`#`, `#/$defs/name`), resolved in `root`.
    private static func resolve(_ reference: String, in root: JSONValue) -> JSONValue? {
        guard reference.hasPrefix("#") else { return nil }
        let pointer = reference.dropFirst()
        if pointer.isEmpty { return root }
        guard pointer.hasPrefix("/") else { return nil }
        var node = root
        for token in pointer.dropFirst().split(separator: "/", omittingEmptySubsequences: false) {
            let key = token.replacingOccurrences(of: "~1", with: "/").replacingOccurrences(of: "~0", with: "~")
            guard let next = node[key] else { return nil }
            node = next
        }
        return node
    }

    private static func validate(
        _ instance: JSONValue, _ s: [String: JSONValue], root: JSONValue, depth: Int
    ) -> Bool {
        func valid(_ value: JSONValue, _ schema: JSONValue) -> Bool {
            validate(value, schema, root: root, depth: depth + 1)
        }
        if let reference = s["$ref"] {
            guard let text = reference.stringValue, let target = resolve(text, in: root), valid(instance, target)
            else { return false }
        }
        if let all = s["allOf"] {
            guard let schemas = all.arrayValue, !schemas.isEmpty else { return false }
            for schema in schemas where !valid(instance, schema) { return false }
        }
        if let condition = s["if"] {
            if valid(instance, condition) {
                if let then = s["then"], !valid(instance, then) { return false }
            } else if let otherwise = s["else"], !valid(instance, otherwise) {
                return false
            }
        }
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
                for item in items where !valid(item, itemSchema) { return false }
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
                    if !valid(value, propertySchema) { return false }
                } else if let additional = s["additionalProperties"] {
                    if !valid(value, additional) { return false }
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
