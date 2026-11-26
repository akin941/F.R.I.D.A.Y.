// PTSpike — throwaway Personal Team signing spike (runbook mac-session-handoff A1).
// Not friday-node. Shows whether TCC grants and a Keychain item survive rebuilds
// signed with the free Apple ID "Apple Development" identity.
// Build only through ../build.sh: it generates the gitignored BuildMarker.swift
// (`buildMarker`), so a fresh clone does not compile from Xcode until it has run.

import SwiftUI

@main
struct PTSpikeApp: App {
    @StateObject private var model = SpikeModel()

    var body: some Scene {
        WindowGroup("PTSpike") {
            ContentView()
                .environmentObject(model)
                .frame(minWidth: 720, minHeight: 480)
        }
    }
}

struct ContentView: View {
    @EnvironmentObject var model: SpikeModel

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("PTSpike build \(model.buildVersion)").font(.title2).bold()
            Text("Marker: \(buildMarker)").font(.callout).foregroundStyle(.secondary)
            Text("Status file: \(model.statusPath)").font(.caption).textSelection(.enabled)

            List(model.results) { result in
                HStack {
                    Image(systemName: result.ok ? "checkmark.circle.fill" : "xmark.circle")
                        .foregroundStyle(result.ok ? .green : .orange)
                    VStack(alignment: .leading) {
                        Text(result.name).bold()
                        Text(result.state).font(.caption).textSelection(.enabled)
                    }
                    Spacer()
                    ForEach(model.actions(for: result.id), id: \.title) { action in
                        Button(action.title, action: action.run)
                    }
                }
            }

            HStack {
                Button("Refresh") { model.refresh() }
                Spacer()
                Button("Delete spike Keychain items", role: .destructive) { model.deleteKeychainItems() }
            }
        }
        .padding()
    }
}
