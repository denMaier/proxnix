import SwiftUI
import AppKit

struct LogView: View {
    let output: String
    let isRunning: Bool
    let exitCode: Int32?

    @State private var pinToBottom = true

    private var cleanOutput: String {
        Self.stripANSI(output)
    }

    var body: some View {
        VStack(spacing: 0) {
            statusBar
            Divider()
            scrollArea
        }
    }

    // MARK: - Status Bar

    private var statusBar: some View {
        HStack(spacing: 10) {
            HStack(spacing: 8) {
                if isRunning {
                    ProgressView()
                        .controlSize(.mini)
                    Text("Running")
                        .foregroundStyle(.secondary)
                } else if let code = exitCode {
                    let ok = code == 0
                    Image(systemName: ok ? "checkmark.circle.fill" : "xmark.circle.fill")
                        .foregroundStyle(ok ? .green : .red)
                    Text(ok ? "Success" : "Failed (exit \(code))")
                        .foregroundStyle(ok ? .green : .red)
                } else {
                    Image(systemName: "terminal")
                        .foregroundStyle(.tertiary)
                    Text("Ready")
                        .foregroundStyle(.tertiary)
                }
            }
            .font(.system(.caption, design: .monospaced))
            .padding(.horizontal, 10)
            .padding(.vertical, 6)
            .background(.quaternary.opacity(0.5), in: Capsule())

            if !cleanOutput.isEmpty {
                Text("\(cleanOutput.split(separator: "\n").count) lines")
                    .font(.system(.caption, design: .monospaced))
                    .foregroundStyle(.secondary)
            }

            Spacer()

            HStack(spacing: 8) {
                if !output.isEmpty {
                    Button {
                        NSPasteboard.general.clearContents()
                        NSPasteboard.general.setString(cleanOutput, forType: .string)
                    } label: {
                        Label("Copy", systemImage: "doc.on.doc")
                    }
                    .buttonStyle(.borderless)
                    .help("Copy log to clipboard")
                }

                Button {
                    pinToBottom.toggle()
                } label: {
                    Label(pinToBottom ? "Follow" : "Manual", systemImage: pinToBottom ? "arrow.down.to.line.compact" : "arrow.down.to.line")
                        .foregroundStyle(pinToBottom ? .blue : .secondary)
                }
                .buttonStyle(.borderless)
                .help(pinToBottom ? "Auto-scroll: on" : "Auto-scroll: off")
            }
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 8)
        .background(Color(nsColor: .windowBackgroundColor))
    }

    // MARK: - Scroll Area

    private var scrollArea: some View {
        ScrollViewReader { proxy in
            ScrollView(.vertical) {
                Text(cleanOutput.isEmpty ? " " : cleanOutput)
                    .font(.system(size: 12, design: .monospaced))
                    .foregroundStyle(cleanOutput.isEmpty ? .clear : .primary)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .textSelection(.enabled)
                    .padding(10)
                    .id("end")
            }
            .onChange(of: output) { _ in
                if pinToBottom {
                    withAnimation(.easeOut(duration: 0.15)) {
                        proxy.scrollTo("end", anchor: .bottom)
                    }
                }
            }
        }
        .background(Color(nsColor: .textBackgroundColor).opacity(0.5))
    }

    // MARK: - ANSI Stripping

    private static let ansiPattern = try! NSRegularExpression(
        pattern: "\\x1B\\[[0-9;]*[A-Za-z]",
        options: []
    )

    static func stripANSI(_ text: String) -> String {
        let range = NSRange(text.startIndex..., in: text)
        return ansiPattern.stringByReplacingMatches(in: text, range: range, withTemplate: "")
    }
}
