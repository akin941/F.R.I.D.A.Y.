// The menu-bar mic indicator (ADR-015 decision 2; Phase 11 brief D12, D15;
// Mac session M1). The icon and the menu's "Mikrofon" line follow the host
// core's `indicator` (VoiceHostCore): the child's state while the local
// mode, mute and link allow it, else the host's own fail-closed reading
// (`off`, `muted`). The Phase 9 reserved "off" state is live from here on.
//
// `micLive` is true for every state in which the host may hold the mic
// (`VoiceHostCore.acquireStates`): the icon then always shows a microphone,
// so the owner can see that audio may be captured (decision 2).

import Foundation

public struct MicIndicator: Equatable, Sendable {
    public let state: String
    /// SF Symbol for the status item.
    public let symbol: String
    /// The menu line, in Turkish (the app's language).
    public let label: String
    public let micLive: Bool

    public static let states = VoiceProtocol.states

    public static func presentation(for state: String) -> MicIndicator {
        switch state {
        case "armed_ptt":
            return MicIndicator(state: state, symbol: "mic", label: "Bas-konuş hazır", micLive: true)
        case "armed_wake":
            return MicIndicator(state: state, symbol: "mic.badge.plus", label: "\"Hey Friday\" bekleniyor", micLive: true)
        case "listening":
            return MicIndicator(state: state, symbol: "mic.fill", label: "Dinliyor", micLive: true)
        case "thinking":
            return MicIndicator(state: state, symbol: "ellipsis.bubble", label: "Düşünüyor", micLive: true)
        case "speaking":
            return MicIndicator(state: state, symbol: "speaker.wave.2.fill", label: "Konuşuyor", micLive: true)
        case "muted":
            return MicIndicator(state: state, symbol: "mic.slash", label: "Sessiz", micLive: false)
        case "storm_muted":
            return MicIndicator(
                state: state, symbol: "mic.slash.circle",
                label: "Fırtına nedeniyle sessiz (sessize al → aç ile yeniden etkinleşir)", micLive: false)
        case "error":
            return MicIndicator(state: state, symbol: "exclamationmark.triangle", label: "Hata", micLive: false)
        default:
            return MicIndicator(state: "off", symbol: "mic.slash.fill", label: "Kapalı", micLive: false)
        }
    }
}
