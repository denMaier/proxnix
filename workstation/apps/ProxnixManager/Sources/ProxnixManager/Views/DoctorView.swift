import SwiftUI
import AppKit

struct DoctorView: View {
    @EnvironmentObject var appState: AppState
    @StateObject private var runner = ShellRunner()

    @State private var siteOnly = true
    @State private var hostOnly = false
    @State private var configOnly = false
    @State private var targetVmid = ""
    @State private var parsedResults: [DoctorSection] = []
    @State private var hasRunOnce = false
    @State private var filterLevel: DoctorLevel? = nil

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 24) {
                header
                    .staggeredAppear(index: 0)

                if runner.isRunning {
                    runningState
                        .staggeredAppear(index: 1)
                } else if !parsedResults.isEmpty {
                    summaryMetrics
                        .staggeredAppear(index: 1)
                    resultsPanel
                        .staggeredAppear(index: 2)
                } else if hasRunOnce {
                    emptyResultState
                        .staggeredAppear(index: 1)
                }
            }
            .padding(24)
        }
        .background(Color(nsColor: .controlBackgroundColor))
        .navigationTitle("Doctor")
        .onAppear {
            if !hasRunOnce && appState.doctorScript != nil {
                Task { await runDoctor() }
            }
        }
    }

    // MARK: - Header

    private var header: some View {
        VStack(alignment: .leading, spacing: 16) {
            ViewThatFits(in: .horizontal) {
                HStack(alignment: .top, spacing: 18) {
                    headerCopy
                    Spacer(minLength: 0)
                    headerActions
                }

                VStack(alignment: .leading, spacing: 12) {
                    headerCopy
                    headerActions
                }
            }

            filterControls
        }
        .padding(24)
        .proxnixCard(tint: ProxnixTheme.accentSubtle.opacity(0.4))
        .overlay(alignment: .topTrailing) {
            headerStatusBadge
                .padding(.top, 16)
                .padding(.trailing, 16)
        }
    }

    private var headerCopy: some View {
        VStack(alignment: .leading, spacing: 8) {
            EyebrowLabel("Health", icon: "stethoscope")

            Text("Doctor")
                .font(.system(size: 32, weight: .bold, design: .rounded))

            Text("Lint your site repo, compare against remote hosts, and surface misconfigurations before they bite.")
                .foregroundStyle(.secondary)
                .frame(maxWidth: 520, alignment: .leading)
        }
    }

    @ViewBuilder
    private var headerActions: some View {
        VStack(alignment: .trailing, spacing: 10) {
            HStack(spacing: 10) {
                if !parsedResults.isEmpty && !runner.isRunning {
                    Button {
                        NSPasteboard.general.clearContents()
                        NSPasteboard.general.setString(LogView.stripANSI(runner.output), forType: .string)
                    } label: {
                        Label("Copy", systemImage: "doc.on.doc")
                    }
                    .buttonStyle(.borderless)
                    .help("Copy raw output")
                }

                if runner.isRunning {
                    Button("Cancel", role: .destructive) { runner.cancel() }
                    ProgressView().controlSize(.small)
                } else {
                    Button {
                        Task { await runDoctor() }
                    } label: {
                        Label("Run Doctor", systemImage: "stethoscope")
                    }
                    .buttonStyle(.borderedProminent)
                    .disabled(appState.doctorScript == nil)
                }
            }
        }
    }

    @ViewBuilder
    private var headerStatusBadge: some View {
        if appState.doctorScript == nil {
            makeStatusBadge(
                "proxnix-doctor not found",
                systemImage: "exclamationmark.triangle.fill",
                color: ProxnixTheme.statusWarn
            )
            .help("Check Scripts Dir in Settings")
        } else if let code = runner.lastExitCode, !runner.isRunning {
            makeStatusBadge(
                code == 0 ? "All clear" : (code == 1 ? "Warnings" : "Issues found"),
                systemImage: code == 0 ? "checkmark.seal.fill" : "exclamationmark.triangle.fill",
                color: code == 0 ? ProxnixTheme.statusOk : (code == 1 ? ProxnixTheme.statusWarn : ProxnixTheme.statusFail)
            )
        }
    }

    private func makeStatusBadge(_ title: String, systemImage: String, color: Color) -> some View {
        Label(title, systemImage: systemImage)
            .font(.system(size: 12, weight: .semibold, design: .rounded))
            .padding(.horizontal, 12)
            .padding(.vertical, 8)
            .background(color.opacity(0.12), in: Capsule())
            .foregroundStyle(color)
    }

    private var filterControls: some View {
        ViewThatFits(in: .horizontal) {
            HStack(alignment: .center, spacing: 16) {
                filterToggles
                Spacer(minLength: 0)
                containerPicker
            }

            VStack(alignment: .leading, spacing: 12) {
                filterToggles
                containerPicker
            }
        }
    }

    private var filterToggles: some View {
        HStack(spacing: 16) {
            Toggle("Site only", isOn: $siteOnly)
                .help("Lint the local site repo only; skip remote host checks")
                .onChange(of: siteOnly) { on in
                    if on { hostOnly = false }
                }
            Toggle("Host only", isOn: $hostOnly)
                .help("Skip local lint; only compare against remote hosts")
                .onChange(of: hostOnly) { on in
                    if on { siteOnly = false }
                }
            Toggle("Config only", isOn: $configOnly)
                .help("Check config publish scope only; skip secret stores and identities")
        }
        .toggleStyle(.checkbox)
        .font(.system(.caption, design: .rounded).weight(.medium))
    }

    private var containerPicker: some View {
        Picker("Container", selection: $targetVmid) {
            Text("All").tag("")
            ForEach(appState.containers) { c in
                Text(c.vmid).tag(c.vmid)
            }
        }
        .frame(maxWidth: 180)
    }

    // MARK: - Running State

    private var runningState: some View {
        VStack(spacing: 16) {
            ProgressView()
                .controlSize(.large)

            VStack(spacing: 6) {
                Text("Running checks\u{2026}")
                    .font(.system(.headline, design: .rounded))
                let lineCount = runner.output.split(separator: "\n").count
                if lineCount > 0 {
                    Text("\(lineCount) lines so far")
                        .font(.system(.caption, design: .monospaced))
                        .foregroundStyle(.secondary)
                }
            }
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 48)
    }

    // MARK: - Summary Metrics

    private var summaryMetrics: some View {
        let allEntries = parsedResults.flatMap(\.entries)
        let okCount = allEntries.filter { $0.level == .ok }.count
        let infoCount = allEntries.filter { $0.level == .info }.count
        let warnCount = allEntries.filter { $0.level == .warn }.count
        let failCount = allEntries.filter { $0.level == .fail }.count

        return ViewThatFits(in: .horizontal) {
            summaryMetricsGrid(columns: 4, okCount: okCount, infoCount: infoCount, warnCount: warnCount, failCount: failCount)
            summaryMetricsGrid(columns: 2, okCount: okCount, infoCount: infoCount, warnCount: warnCount, failCount: failCount)
            summaryMetricsGrid(columns: 1, okCount: okCount, infoCount: infoCount, warnCount: warnCount, failCount: failCount)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private func summaryMetricsGrid(columns: Int, okCount: Int, infoCount: Int, warnCount: Int, failCount: Int) -> some View {
        LazyVGrid(
            columns: Array(repeating: GridItem(.flexible(minimum: 0, maximum: .infinity), spacing: 12, alignment: .leading), count: columns),
            alignment: .leading,
            spacing: 12
        ) {
            metricButton(count: okCount, label: "Passed", icon: "checkmark.circle.fill", color: ProxnixTheme.statusOk, level: .ok)
            metricButton(count: infoCount, label: "Info", icon: "info.circle.fill", color: ProxnixTheme.statusInfo, level: .info)
            metricButton(count: warnCount, label: "Warnings", icon: "exclamationmark.triangle.fill", color: ProxnixTheme.statusWarn, level: .warn)
            metricButton(count: failCount, label: "Failures", icon: "xmark.circle.fill", color: ProxnixTheme.statusFail, level: .fail)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private func metricButton(count: Int, label: String, icon: String, color: Color, level: DoctorLevel) -> some View {
        Button {
            withAnimation(.easeInOut(duration: 0.2)) {
                filterLevel = filterLevel == level ? nil : level
            }
        } label: {
            VStack(alignment: .leading, spacing: 4) {
                Image(systemName: icon)
                    .font(.caption2)
                    .foregroundStyle(count > 0 ? color : .secondary)
                Text("\(count)")
                    .font(.system(.title3, design: .monospaced).bold())
                    .foregroundStyle(count > 0 ? .primary : .secondary)
                Text(label)
                    .font(.system(size: 11, weight: .medium))
                    .foregroundStyle(.secondary)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(14)
            .proxnixCard(
                tint: filterLevel == level ? color.opacity(0.1) : .clear,
                cornerRadius: 14
            )
            .overlay(
                RoundedRectangle(cornerRadius: 14, style: .continuous)
                    .strokeBorder(filterLevel == level ? color.opacity(0.3) : .clear, lineWidth: 1)
            )
        }
        .buttonStyle(.plain)
    }

    // MARK: - Results

    private var filteredResults: [DoctorSection] {
        guard let level = filterLevel else { return parsedResults }
        return parsedResults.compactMap { section in
            let filtered = section.entries.filter { $0.level == level }
            return filtered.isEmpty ? nil : DoctorSection(name: section.name, entries: filtered)
        }
    }

    private var resultsPanel: some View {
        VStack(alignment: .leading, spacing: 0) {
            ForEach(filteredResults) { section in
                sectionView(section)
            }
        }
        .proxnixCard()
    }

    private func sectionView(_ section: DoctorSection) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(spacing: 8) {
                Text(section.name)
                    .font(.system(.caption, design: .monospaced).bold())
                    .foregroundStyle(ProxnixTheme.accent)

                Spacer()

                Text("\(section.entries.count)")
                    .font(.system(size: 10, weight: .bold, design: .monospaced))
                    .foregroundStyle(.secondary)
                    .padding(.horizontal, 7)
                    .padding(.vertical, 2)
                    .background(.quaternary.opacity(0.5), in: Capsule())
            }
            .padding(.horizontal, 18)
            .padding(.vertical, 10)
            .background(Color(nsColor: .windowBackgroundColor))

            ForEach(section.entries) { entry in
                entryRow(entry)
            }
        }
    }

    private func entryRow(_ entry: DoctorEntry) -> some View {
        HStack(spacing: 10) {
            Image(systemName: entry.level.icon)
                .font(.caption)
                .foregroundStyle(entry.level.color)
                .frame(width: 16)

            Text(entry.level.rawValue)
                .font(.system(.caption, design: .monospaced).bold())
                .foregroundStyle(entry.level.color)
                .frame(width: 36, alignment: .leading)

            Text(entry.message)
                .font(.system(.caption, design: .monospaced))
                .textSelection(.enabled)
                .frame(maxWidth: .infinity, alignment: .leading)
        }
        .padding(.horizontal, 18)
        .padding(.vertical, 5)
    }

    // MARK: - Empty State

    private var emptyResultState: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("No issues found")
                .font(.system(.headline, design: .rounded))
            Text("The doctor run completed but produced no parseable results. Check the raw output if this seems wrong.")
                .font(.caption)
                .foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(16)
        .background(.quaternary.opacity(0.28), in: RoundedRectangle(cornerRadius: 16, style: .continuous))
    }

    // MARK: - Actions

    private func runDoctor() async {
        guard let script = appState.doctorScript else { return }
        parsedResults = []
        filterLevel = nil

        var args: [String] = []
        if siteOnly   { args.append("--site-only") }
        if hostOnly   { args.append("--host-only") }
        if configOnly { args.append("--config-only") }
        if !targetVmid.isEmpty { args += ["--vmid", targetVmid] }

        await runner.run(script: script, args: args)
        parsedResults = Self.parseOutput(runner.output)
        hasRunOnce = true
    }

    // MARK: - Parsing

    static func parseOutput(_ raw: String) -> [DoctorSection] {
        let cleaned = LogView.stripANSI(raw)
        var sections: [DoctorSection] = []
        var currentName = "general"
        var currentEntries: [DoctorEntry] = []

        for line in cleaned.components(separatedBy: "\n") {
            let trimmed = line.trimmingCharacters(in: .whitespaces)

            if trimmed.hasPrefix("[") && trimmed.hasSuffix("]") {
                if !currentEntries.isEmpty {
                    sections.append(DoctorSection(name: currentName, entries: currentEntries))
                    currentEntries = []
                }
                currentName = String(trimmed.dropFirst().dropLast())
                continue
            }

            if let entry = parseLine(trimmed) {
                currentEntries.append(entry)
            }
        }

        if !currentEntries.isEmpty {
            sections.append(DoctorSection(name: currentName, entries: currentEntries))
        }

        return sections
    }

    private static func parseLine(_ line: String) -> DoctorEntry? {
        for level in DoctorLevel.allCases {
            let prefix = level.rawValue
            if line.hasPrefix(prefix) {
                let message = String(line.dropFirst(prefix.count))
                    .trimmingCharacters(in: .whitespaces)
                guard !message.isEmpty else { continue }
                return DoctorEntry(level: level, message: message)
            }
        }
        return nil
    }
}

// MARK: - Models

struct DoctorSection: Identifiable {
    let id = UUID()
    let name: String
    let entries: [DoctorEntry]
}

struct DoctorEntry: Identifiable {
    let id = UUID()
    let level: DoctorLevel
    let message: String
}

enum DoctorLevel: String, CaseIterable {
    case ok   = "OK"
    case info = "INFO"
    case warn = "WARN"
    case fail = "FAIL"

    var icon: String {
        switch self {
        case .ok:   return "checkmark.circle.fill"
        case .info: return "info.circle.fill"
        case .warn: return "exclamationmark.triangle.fill"
        case .fail: return "xmark.circle.fill"
        }
    }

    var color: Color {
        switch self {
        case .ok:   return ProxnixTheme.statusOk
        case .info: return ProxnixTheme.statusInfo
        case .warn: return ProxnixTheme.statusWarn
        case .fail: return ProxnixTheme.statusFail
        }
    }
}
