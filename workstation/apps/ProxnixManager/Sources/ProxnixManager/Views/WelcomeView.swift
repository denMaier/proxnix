import SwiftUI
import AppKit

struct WelcomeView: View {
    @EnvironmentObject var appState: AppState
    @State private var appeared = false

    var body: some View {
        ZStack {
            // Atmospheric background
            ZStack {
                ProxnixTheme.heroGradient
                    .ignoresSafeArea()

                // Subtle radial glow behind the logo
                RadialGradient(
                    colors: [
                        ProxnixTheme.accent.opacity(0.08),
                        Color.clear
                    ],
                    center: .topLeading,
                    startRadius: 40,
                    endRadius: 500
                )
                .ignoresSafeArea()
            }

            VStack(spacing: 28) {
                VStack(alignment: .leading, spacing: 22) {
                    // App icon area
                    HStack {
                        ZStack {
                            RoundedRectangle(cornerRadius: 22, style: .continuous)
                                .fill(
                                    LinearGradient(
                                        colors: [
                                            ProxnixTheme.accent,
                                            ProxnixTheme.accent.opacity(0.7)
                                        ],
                                        startPoint: .topLeading,
                                        endPoint: .bottomTrailing
                                    )
                                )
                                .frame(width: 72, height: 72)
                                .shadow(color: ProxnixTheme.accent.opacity(0.3), radius: 16, y: 4)

                            Image(systemName: "shippingbox.and.arrow.backward.fill")
                                .font(.system(size: 32, weight: .semibold))
                                .foregroundStyle(.white)
                        }
                        .staggeredAppear(index: 0, baseDelay: 0.15)

                        Spacer()
                    }

                    VStack(alignment: .leading, spacing: 10) {
                        Text("Welcome to Proxnix")
                            .font(.system(size: 34, weight: .bold, design: .rounded))
                            .staggeredAppear(index: 1, baseDelay: 0.15)

                        Text("Connect your site repository to discover containers, manage secrets, and publish configurations — all from one workspace.")
                            .font(.system(size: 14))
                            .foregroundStyle(.secondary)
                            .frame(maxWidth: 520, alignment: .leading)
                            .lineSpacing(2)
                            .staggeredAppear(index: 2, baseDelay: 0.15)
                    }

                    LazyVGrid(columns: [GridItem(.adaptive(minimum: 180), spacing: 14, alignment: .top)], alignment: .leading, spacing: 14) {
                        onboardingStep(
                            icon: "folder",
                            title: "Choose repo",
                            detail: "Select your proxnix site repository.",
                            index: 0
                        )
                        onboardingStep(
                            icon: "shippingbox",
                            title: "Scan containers",
                            detail: "The sidebar populates from containers/.",
                            index: 1
                        )
                        onboardingStep(
                            icon: "arrow.up.circle",
                            title: "Manage changes",
                            detail: "Publish, inspect drop-ins, and work with secrets.",
                            index: 2
                        )
                    }
                }

                VStack(alignment: .leading, spacing: 16) {
                    Button {
                        pickSiteDir()
                    } label: {
                        Label("Choose Site Directory", systemImage: "folder.badge.plus")
                            .font(.system(.body, design: .rounded).weight(.semibold))
                            .frame(maxWidth: .infinity)
                    }
                    .buttonStyle(.borderedProminent)
                    .tint(ProxnixTheme.accent)
                    .controlSize(.large)

                    if !appState.configStore.config.siteDir.isEmpty {
                        let dir = appState.configStore.config.siteDir
                        HStack(spacing: 10) {
                            Image(systemName: "checkmark.circle.fill")
                                .foregroundStyle(ProxnixTheme.statusOk)
                            VStack(alignment: .leading, spacing: 2) {
                                Text("Current directory")
                                    .font(.system(size: 11, weight: .medium))
                                    .foregroundStyle(.secondary)
                                Text(dir)
                                    .font(.system(.body, design: .monospaced))
                                    .lineLimit(2)
                                    .truncationMode(.middle)
                                    .fixedSize(horizontal: false, vertical: true)
                            }
                        }
                        .padding(.horizontal, 14)
                        .padding(.vertical, 12)
                        .background(ProxnixTheme.statusOk.opacity(0.08), in: RoundedRectangle(cornerRadius: 14, style: .continuous))
                        .overlay(
                            RoundedRectangle(cornerRadius: 14, style: .continuous)
                                .strokeBorder(ProxnixTheme.statusOk.opacity(0.15))
                        )
                    }

                    HStack(spacing: 6) {
                        Image(systemName: "gearshape")
                        Text("You can change the site directory later in Settings.")
                    }
                    .font(.caption)
                    .foregroundStyle(.tertiary)
                }
                .padding(24)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 24, style: .continuous))
                .overlay(
                    RoundedRectangle(cornerRadius: 24, style: .continuous)
                        .strokeBorder(ProxnixTheme.cardBorder)
                )
                .staggeredAppear(index: 4, baseDelay: 0.15)
            }
            .padding(32)
            .frame(maxWidth: 760)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    private func onboardingStep(icon: String, title: String, detail: String, index: Int) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            Image(systemName: icon)
                .font(.title3.weight(.semibold))
                .foregroundStyle(ProxnixTheme.accent)
            Text(title)
                .font(.system(.headline, design: .rounded))
            Text(detail)
                .font(.caption)
                .foregroundStyle(.secondary)
                .frame(maxWidth: .infinity, alignment: .leading)
        }
        .padding(16)
        .frame(maxWidth: .infinity, minHeight: 132, alignment: .topLeading)
        .proxnixCard(tint: ProxnixTheme.surfaceTint, cornerRadius: 18)
        .staggeredAppear(index: 3 + index, baseDelay: 0.15)
    }

    private func pickSiteDir() {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.allowsMultipleSelection = false
        panel.message = "Select your proxnix site repo directory"
        panel.prompt = "Choose"

        guard panel.runModal() == .OK, let url = panel.url else { return }

        appState.configStore.config.siteDir = url.path
        try? appState.configStore.save()
        appState.refresh()
    }
}
