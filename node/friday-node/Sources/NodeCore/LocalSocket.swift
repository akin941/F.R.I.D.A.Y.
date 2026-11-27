// The node's user-owned Unix domain sockets (HookSocket, VoiceSelftestSocket):
// one place for the binding rules and the bounded reads and writes.
//
// - The socket is 0600 inside a 0700 directory owned by this user (an
//   existing directory with another owner, or a symlink, is refused; a
//   stale socket file is replaced, any other file is refused).
// - Reads and writes are bounded in size and time; SIGPIPE never fires.

import Foundation

enum LocalSocket {
    /// Bind and listen on `path` under the rules above.
    static func listen(path: String, backlog: Int32 = 8) throws -> Int32 {
        let directory = (path as NSString).deletingLastPathComponent
        try? FileManager.default.createDirectory(
            atPath: directory, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
        var info = stat()
        guard lstat(directory, &info) == 0, (info.st_mode & S_IFMT) == S_IFDIR, info.st_uid == getuid(),
            chmod(directory, 0o700) == 0
        else { throw HookSocketError.unsafeDirectory }
        if lstat(path, &info) == 0 {
            guard (info.st_mode & S_IFMT) == S_IFSOCK else { throw HookSocketError.unsafePath }
            unlink(path)
        }
        let socketFD = socket(AF_UNIX, SOCK_STREAM, 0)
        guard socketFD >= 0 else { throw HookSocketError.socket }
        guard var address = address(path) else {
            close(socketFD)
            throw HookSocketError.unsafePath
        }
        let previous = umask(0o177)
        let bound = withUnsafePointer(to: &address) {
            $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                bind(socketFD, $0, socklen_t(MemoryLayout<sockaddr_un>.size))
            }
        }
        umask(previous)
        guard bound == 0, chmod(path, 0o600) == 0, Darwin.listen(socketFD, backlog) == 0 else {
            close(socketFD)
            throw HookSocketError.socket
        }
        return socketFD
    }

    /// Remove the socket file (only when it is one).
    static func unlinkSocket(_ path: String) {
        var info = stat()
        if lstat(path, &info) == 0, (info.st_mode & S_IFMT) == S_IFSOCK { unlink(path) }
    }

    /// A connected client socket, or nil. `timeoutSeconds` bounds every
    /// single send and receive.
    static func connect(path: String, timeoutSeconds: Int) -> Int32? {
        let socketFD = socket(AF_UNIX, SOCK_STREAM, 0)
        guard socketFD >= 0 else { return nil }
        setTimeouts(socketFD, seconds: timeoutSeconds)
        guard var address = address(path) else {
            close(socketFD)
            return nil
        }
        let connected = withUnsafePointer(to: &address) {
            $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                Darwin.connect(socketFD, $0, socklen_t(MemoryLayout<sockaddr_un>.size))
            }
        }
        guard connected == 0 else {
            close(socketFD)
            return nil
        }
        return socketFD
    }

    static func setTimeouts(_ socketFD: Int32, seconds: Int) {
        var timeout = timeval(tv_sec: seconds, tv_usec: 0)
        setsockopt(socketFD, SOL_SOCKET, SO_SNDTIMEO, &timeout, socklen_t(MemoryLayout<timeval>.size))
        setsockopt(socketFD, SOL_SOCKET, SO_RCVTIMEO, &timeout, socklen_t(MemoryLayout<timeval>.size))
        var noSigPipe: Int32 = 1
        setsockopt(socketFD, SOL_SOCKET, SO_NOSIGPIPE, &noSigPipe, socklen_t(MemoryLayout<Int32>.size))
    }

    private static func address(_ path: String) -> sockaddr_un? {
        var address = sockaddr_un()
        address.sun_family = sa_family_t(AF_UNIX)
        let bytes = Array(path.utf8)
        guard bytes.count < MemoryLayout.size(ofValue: address.sun_path) else { return nil }
        withUnsafeMutableBytes(of: &address.sun_path) { $0.copyBytes(from: bytes) }
        return address
    }

    /// Send all of `data`; false on a timeout or a closed peer.
    static func sendAll(_ socketFD: Int32, _ data: Data) -> Bool {
        let bytes = [UInt8](data)
        var offset = 0
        while offset < bytes.count {
            let sent = bytes[offset...].withUnsafeBytes { send(socketFD, $0.baseAddress, $0.count, 0) }
            if sent <= 0 { return false }
            offset += sent
        }
        return true
    }

    /// Bytes up to (not including) the first newline, at most `limit`, before
    /// `deadline`; nil otherwise. Reads one byte at a time, so nothing after
    /// the line is consumed.
    static func readLine(_ socketFD: Int32, limit: Int, deadline: Date) -> Data? {
        var line = Data()
        var byte: UInt8 = 0
        while true {
            if Date() > deadline { return nil }
            let count = recv(socketFD, &byte, 1, 0)
            if count <= 0 { return nil }
            if byte == 0x0A { return line }
            line.append(byte)
            if line.count > limit { return nil }
        }
    }

    /// Exactly `count` bytes before `deadline`, or nil.
    static func readExactly(_ socketFD: Int32, count: Int, deadline: Date) -> Data? {
        var data = Data()
        data.reserveCapacity(count)
        var buffer = [UInt8](repeating: 0, count: 65_536)
        while data.count < count {
            if Date() > deadline { return nil }
            let got = recv(socketFD, &buffer, min(buffer.count, count - data.count), 0)
            if got <= 0 { return nil }
            data.append(contentsOf: buffer[0..<got])
        }
        return data
    }
}
