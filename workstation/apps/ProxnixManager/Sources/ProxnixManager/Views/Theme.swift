import SwiftUI

// MARK: - Proxnix Color Palette

enum ProxnixTheme {
    // Core accent — electric teal, distinct from default blue
    static let accent = Color(hue: 0.49, saturation: 0.72, brightness: 0.78)
    static let accentSubtle = accent.opacity(0.12)
    static let accentGlow = accent.opacity(0.25)

    // Surface tints
    static let surfaceTint = Color.white.opacity(0.03)
    static let cardBorder = Color.white.opacity(0.08)
    static let divider = Color.white.opacity(0.06)

    // Status palette — slightly more vivid than defaults
    static let statusOk = Color(hue: 0.38, saturation: 0.65, brightness: 0.72)
    static let statusWarn = Color(hue: 0.1, saturation: 0.75, brightness: 0.88)
    static let statusFail = Color(hue: 0.0, saturation: 0.65, brightness: 0.82)
    static let statusInfo = Color(hue: 0.58, saturation: 0.55, brightness: 0.82)

    // Sidebar
    static let sidebarHeader = Color.white.opacity(0.5)

    // Gradients
    static var heroGradient: LinearGradient {
        LinearGradient(
            colors: [
                Color(nsColor: .windowBackgroundColor),
                accent.opacity(0.06),
                Color(hue: 0.55, saturation: 0.3, brightness: 0.2).opacity(0.15)
            ],
            startPoint: .topLeading,
            endPoint: .bottomTrailing
        )
    }

    static var subtleGradient: LinearGradient {
        LinearGradient(
            colors: [
                Color(nsColor: .windowBackgroundColor),
                accent.opacity(0.04)
            ],
            startPoint: .topLeading,
            endPoint: .bottomTrailing
        )
    }
}

// MARK: - Card Modifier

struct ProxnixCard: ViewModifier {
    var tint: Color
    var cornerRadius: CGFloat

    func body(content: Content) -> some View {
        content
            .background(
                RoundedRectangle(cornerRadius: cornerRadius, style: .continuous)
                    .fill(Color(nsColor: .underPageBackgroundColor))
                    .overlay(
                        RoundedRectangle(cornerRadius: cornerRadius, style: .continuous)
                            .fill(tint)
                    )
            )
            .overlay(
                RoundedRectangle(cornerRadius: cornerRadius, style: .continuous)
                    .strokeBorder(ProxnixTheme.cardBorder, lineWidth: 0.5)
            )
    }
}

extension View {
    func proxnixCard(tint: Color = .clear, cornerRadius: CGFloat = 20) -> some View {
        modifier(ProxnixCard(tint: tint, cornerRadius: cornerRadius))
    }
}

// MARK: - Staggered Appearance Animation

struct StaggeredAppear: ViewModifier {
    let index: Int
    let baseDelay: Double
    @State private var isVisible = false

    func body(content: Content) -> some View {
        content
            .opacity(isVisible ? 1 : 0)
            .offset(y: isVisible ? 0 : 12)
            .onAppear {
                withAnimation(.easeOut(duration: 0.4).delay(baseDelay + Double(index) * 0.06)) {
                    isVisible = true
                }
            }
    }
}

extension View {
    func staggeredAppear(index: Int, baseDelay: Double = 0.1) -> some View {
        modifier(StaggeredAppear(index: index, baseDelay: baseDelay))
    }
}

// MARK: - Eyebrow Label

struct EyebrowLabel: View {
    let text: String
    let icon: String?

    init(_ text: String, icon: String? = nil) {
        self.text = text
        self.icon = icon
    }

    var body: some View {
        HStack(spacing: 5) {
            if let icon {
                Image(systemName: icon)
                    .font(.system(size: 9, weight: .bold))
            }
            Text(text.uppercased())
                .font(.system(size: 10, weight: .heavy, design: .rounded))
                .tracking(1.2)
        }
        .foregroundStyle(ProxnixTheme.accent)
    }
}

// MARK: - Metric Tile

struct MetricTile: View {
    let value: String
    let label: String
    let icon: String?

    init(_ value: String, label: String, icon: String? = nil) {
        self.value = value
        self.label = label
        self.icon = icon
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            if let icon {
                Image(systemName: icon)
                    .font(.caption2)
                    .foregroundStyle(ProxnixTheme.accent.opacity(0.6))
            }
            Text(value)
                .font(.system(.title3, design: .monospaced).bold())
            Text(label)
                .font(.system(size: 11, weight: .medium))
                .foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(14)
        .proxnixCard(cornerRadius: 14)
    }
}
