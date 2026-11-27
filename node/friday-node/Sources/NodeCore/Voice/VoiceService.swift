// The voice host as the app runs it (Phase 11 brief D3, D6, D12, D15; Mac
// session M1; runbook B7): VoiceChildHost (the child process and its
// protocol), the audio engine, the local settings, the node WebSocket and
// the voice credential, wired together.
//
// - The child runs while voice is wanted: mode is not `off`, the node
//   WebSocket is up (`hello.ack`), the local kill switch is released and the
//   node is paired. WS loss sends `link: down` (the child releases) and then
//   stops the child; the kill switch kills it at once (ADR-013 decision 6).
// - The mic follows the host core: capture runs only while the core holds
//   the mic; captured frames go through `VoiceHostCore.feedAudio`.
// - Each child `state` is relayed to the node WebSocket as `voice.state`
//   (NodeSession's rules); `play` / `stop_playback` / earcons reach the
//   audio engine as the core's effects.
// - The credential: minted with the node secret when the child starts,
//   re-minted `VoiceCredential.refreshLeadSeconds` before expiry and when the
//   child reports `release_mic(credential_refused)`. A 401 releases the mic
//   (indicator `error`), forgets the token and backs off 1 s -> 60 s.
// - Mute is re-read every second (stored setting and the input device's
//   switch); a read failure is `mute: unreadable`.

import Foundation
import os

/// What the voice host needs from the node runner (NodeRunner; a fake in
/// tests).
public protocol VoiceNodeLink: AnyObject {
    func relayVoiceState(_ state: JSONValue)
    func credentialForVoice() -> NodeCredential?
}

extension NodeRunner: VoiceNodeLink {}

public final class VoiceService: VoiceChildHostDelegate, @unchecked Sendable {
    public typealias Mint = (NodeCredential) async throws -> VoiceToken

    public let host: VoiceChildHost
    private let audio: VoiceAudioIO
    private let settingsStore: VoiceSettingsStore
    private let muteSource: InputMuteSource
    private weak var link: VoiceNodeLink?
    private let mint: Mint
    private let wallClock: () -> Double
    static let log = Logger(subsystem: "dev.friday.friday-node", category: "voice")

    // Host-queue state.
    private var linkUp = false
    private var killSwitch = false
    private var settings = VoiceSettings()
    private var token: VoiceToken?
    private var minting = false
    private var mintBackoff = 1.0
    /// After a failed mint: no new attempt before this (the backoff).
    private var retryAt: Double?
    private var muteTimer: DispatchSourceTimer?
    private var capturing = false

    /// Main-queue observers (the menu).
    public var onIndicator: ((MicIndicator) -> Void)?

    public init(
        host: VoiceChildHost, audio: VoiceAudioIO, settingsStore: VoiceSettingsStore,
        muteSource: InputMuteSource = NoInputMute(), link: VoiceNodeLink,
        mint: @escaping Mint = { try await VoiceCredential.mint($0) },
        wallClock: @escaping () -> Double = { Date().timeIntervalSince1970 }
    ) {
        self.host = host
        self.audio = audio
        self.settingsStore = settingsStore
        self.muteSource = muteSource
        self.link = link
        self.mint = mint
        self.wallClock = wallClock
        host.delegate = self
        audio.onCapture = { [weak self] pcm, aec in
            guard let self else { return }
            self.host.perform { core in core.feedAudio(pcm, aec: aec) }
        }
    }

    /// Build the app's service: the installed child, paced playback, the
    /// real clock.
    public static func app(link: VoiceNodeLink, audio: VoiceAudioIO, settingsStore: VoiceSettingsStore) -> VoiceService {
        let core = VoiceHostCore(
            clock: SystemVoiceClock(), mode: "off", muted: .unreadable, linkUp: false,
            hostVersion: NodeInfo.version, pacedPlayback: true)
        return VoiceService(
            host: VoiceChildHost(core: core), audio: audio, settingsStore: settingsStore,
            muteSource: CoreAudioInputMute(), link: link)
    }

    // MARK: Inputs (any thread)

    /// Load the settings and start polling mute.
    public func start(autoPoll: Bool = true) {
        host.perform { core in
            self.settings = (try? self.settingsStore.load()) ?? VoiceSettings()
            core.setMode(self.settings.mode)
            core.setMute(VoiceMute.read(store: self.settingsStore, device: self.muteSource))
            if autoPoll { self.startMutePoll() }
            self.reconcile(core)
        }
    }

    /// The node WebSocket went up or down.
    public func linkChanged(_ up: Bool) {
        host.perform { core in
            self.linkUp = up
            core.setLink(up)
            self.reconcile(core)
        }
    }

    public func killSwitchChanged(engaged: Bool) {
        host.perform { core in
            self.killSwitch = engaged
            if engaged {
                self.token = nil
                core.dropCredential()
            }
            self.reconcile(core)
        }
    }

    /// The settings changed (VoiceSettingsEditor.onChange).
    public func settingsChanged(_ new: VoiceSettings) {
        host.perform { core in
            let old = self.settings
            self.settings = new
            if new.mode != old.mode { core.setMode(new.mode) }
            self.pollMute(core)
            self.reconcile(core)
        }
    }

    /// The push-to-talk hotkey.
    public func ptt(_ down: Bool) {
        host.perform { core in core.ptt(down ? "down" : "up") }
    }

    /// Quit: stop the child and the audio; blocks until the child got its
    /// `shutdown` (the kill ladder runs on).
    public func shutdown() {
        muteTimer?.cancel()
        host.stop(wait: true)
        audio.shutdown()
    }

    public var indicator: MicIndicator {
        MicIndicator.presentation(for: host.performSync { $0.indicator })
    }

    // MARK: Host queue

    private func wanted() -> Bool {
        settings.mode != "off" && linkUp && !killSwitch && link?.credentialForVoice() != nil
    }

    private func reconcile(_ core: VoiceHostCore) {
        if killSwitch {
            host.kill()
            return
        }
        if wanted() {
            if token == nil || needsRefresh() { mintSoon() }
            host.start()
        } else if core.childRunning || core.linkOpen {
            host.stop()
        }
    }

    private func needsRefresh() -> Bool {
        guard let token else { return true }
        return wallClock() >= VoiceCredential.refreshAt(expiresAt: token.expiresAtSeconds, now: 0)
    }

    private func startMutePoll() {
        guard muteTimer == nil else { return }
        let timer = DispatchSource.makeTimerSource(queue: host.queue)
        timer.schedule(deadline: .now() + 1, repeating: 1)
        timer.setEventHandler { [weak self] in
            guard let self else { return }
            self.pollMute(self.host.core)
            self.credentialTick(self.host.core)
            self.host.perform { _ in }  // publish what the poll changed
        }
        muteTimer = timer
        timer.resume()
    }

    private func pollMute(_ core: VoiceHostCore) {
        let muted = VoiceMute.read(store: settingsStore, device: muteSource)
        if muted != core.muted { core.setMute(muted) }
    }

    /// Re-mint when due: no token, or its refresh time came (the backoff
    /// after a failure permitting).
    func credentialTick(_ core: VoiceHostCore) {
        guard wanted(), needsRefresh() else { return }
        mintSoon()
    }

    private func mintSoon() {
        guard !minting, let credential = link?.credentialForVoice() else { return }
        if let retryAt, wallClock() < retryAt { return }
        minting = true
        retryAt = nil
        let mint = self.mint
        Task {
            let outcome: Result<VoiceToken, Error>
            do {
                outcome = .success(try await mint(credential))
            } catch {
                outcome = .failure(error)
            }
            self.host.perform { core in self.minted(outcome, core) }
        }
    }

    private func minted(_ outcome: Result<VoiceToken, Error>, _ core: VoiceHostCore) {
        minting = false
        switch outcome {
        case .success(let token):
            mintBackoff = 1
            guard !killSwitch else { return }
            self.token = token
            core.sendCredential(token.frame)
        case .failure(VoiceCredentialError.refused):
            Self.log.error("voice credential: refused (401); the mic is released")
            token = nil
            core.refuseCredential()
            scheduleRetry()
        case .failure:
            Self.log.error("voice credential: mint failed; retrying")
            scheduleRetry()
        }
    }

    private func scheduleRetry() {
        retryAt = wallClock() + mintBackoff
        mintBackoff = min(mintBackoff * 2, 60)
    }

    // MARK: VoiceChildHostDelegate (host queue)

    public func voiceHost(_ host: VoiceChildHost, effects: [VoiceHostEffect]) {
        for effect in effects {
            switch effect {
            case .playbackBegan(let id): audio.beginPlayback(id)
            case .playbackAudio(let id, let pcm): audio.enqueue(id, pcm: pcm)
            case .playbackStopped(let id): audio.stopPlayback(id)
            case .earcon(let name): audio.playEarcon(name)
            case .voiceState(let item): link?.relayVoiceState(item)
            case .childReleasedMic(let reason):
                if reason == "credential_refused" || reason == "credential_expired" || reason == "credential_missing" {
                    // The child holds no usable token: a refused one is
                    // re-minted (backoff permitting, never in a loop); a
                    // missing one is re-sent while it is still fresh.
                    if reason == "credential_refused" { token = nil }
                    if let token, !needsRefresh() {
                        host.core.sendCredential(token.frame)
                    } else {
                        mintSoon()
                    }
                }
            case .log(let code):
                if code.utf8.count <= 64 { Self.log.info("voice child log: \(code, privacy: .public)") }
            }
        }
    }

    public func voiceHost(_ host: VoiceChildHost, micHeld: Bool) {
        if micHeld && !capturing {
            do {
                try audio.startCapture()
                capturing = true
            } catch {
                Self.log.error("voice: the microphone could not be opened")
                host.core.releaseMic("error", indicator: "error")
            }
        } else if !micHeld && capturing {
            audio.stopCapture()
            capturing = false
        }
    }

    public func voiceHost(_ host: VoiceChildHost, indicator: String) {
        let presentation = MicIndicator.presentation(for: indicator)
        DispatchQueue.main.async { self.onIndicator?(presentation) }
    }
}
