import SwiftUI
import AppKit

struct SecretsView: View {
    @EnvironmentObject var appState: AppState
    @StateObject private var runner = ShellRunner()
    @State private var viewMode: SecretsMode = .groups
    @State private var storeScope: StoreScope = .shared
    @State private var containerScope: ContainerScope?
    @State private var parsedSecrets: [SecretEntry] = []
    @State private var showSetSheet = false
    @State private var showCreateGroupSheet = false
    @State private var newSecretName = ""
    @State private var newSecretValue = ""
    @State private var newGroupName = ""
    @State private var attachGroupName = ""
    @State private var groupEditError: String?
    @State private var pendingRemoval: SecretEntry?
    @FocusState private var createGroupFieldFocused: Bool

    enum SecretsMode: String, CaseIterable, Hashable {
        case groups
        case containers

        var label: String {
            switch self {
            case .groups:     return "Groups"
            case .containers: return "Containers"
            }
        }
    }

    enum StoreScope: Hashable {
        case shared, group(String)

        var label: String {
            switch self {
            case .shared:           return "Shared"
            case .group(let group): return "Group \(group)"
            }
        }
    }

    struct ContainerScope: Hashable {
        let vmid: String
        var label: String { "Container \(vmid)" }
    }

    struct SecretEntry: Identifiable {
        let id = UUID()
        let source: String
        let scopeLabel: String
        let name: String
    }

    private var activeLabel: String {
        viewMode == .groups ? storeScope.label : selectedContainerScope.label
    }

    var body: some View {
        VStack(spacing: 0) {
            controlBar
            Divider()
            mainContent
        }
        .background(Color(nsColor: .controlBackgroundColor))
        .navigationTitle("Secrets")
        .sheet(isPresented: $showSetSheet) { setSecretSheet }
        .sheet(isPresented: $showCreateGroupSheet) { createGroupSheet }
        .alert("Remove Secret",
               isPresented: Binding(
                   get: { pendingRemoval != nil },
                   set: { if !$0 { pendingRemoval = nil } }
               )) {
            Button("Remove", role: .destructive) {
                if let entry = pendingRemoval {
                    Task { await removeSecret(entry) }
                }
            }
            Button("Cancel", role: .cancel) {}
        } message: {
            if let entry = pendingRemoval {
                Text("Remove \"\(entry.name)\" from \(entry.scopeLabel)?")
            }
        }
        .onChange(of: viewMode) { _ in
            parsedSecrets = []
            groupEditError = nil
        }
        .onChange(of: storeScope) { _ in
            parsedSecrets = []
        }
        .onChange(of: containerScope) { _ in
            parsedSecrets = []
        }
        .onAppear {
            if containerScope == nil, let first = appState.containers.first {
                containerScope = ContainerScope(vmid: first.vmid)
            }
        }
    }

    // MARK: - Control Bar

    private var controlBar: some View {
        VStack(alignment: .leading, spacing: 12) {
            // Row 1: Mode toggle + scope picker + current label
            HStack(spacing: 14) {
                Picker("Mode", selection: $viewMode) {
                    ForEach(SecretsMode.allCases, id: \.self) { mode in
                        Text(mode.label).tag(mode)
                    }
                }
                .pickerStyle(.segmented)
                .frame(maxWidth: 200)

                if viewMode == .groups {
                    Picker("Store", selection: $storeScope) {
                        Text("Shared").tag(StoreScope.shared)
                        let defined = appState.definedSecretGroups
                        if !defined.isEmpty {
                            Divider()
                            ForEach(defined, id: \.self) { group in
                                Text("Group: \(group)").tag(StoreScope.group(group))
                            }
                        }
                        let refOnly = referencedOnlyGroups
                        if !refOnly.isEmpty {
                            Divider()
                            ForEach(refOnly, id: \.self) { group in
                                Text("(attached only) \(group)").tag(StoreScope.group(group))
                            }
                        }
                    }
                    .help("Select the active shared or group scope for listing and editing secrets.")
                    .frame(maxWidth: 220)
                } else {
                    Picker("Container", selection: $containerScope) {
                        ForEach(appState.containers) { c in
                            Text("CT \(c.vmid)").tag(Optional(ContainerScope(vmid: c.vmid)))
                        }
                    }
                    .frame(maxWidth: 220)
                }

                Spacer(minLength: 0)

                if appState.secretsScript == nil {
                    Label("proxnix-secrets not found", systemImage: "exclamationmark.triangle.fill")
                        .font(.caption)
                        .foregroundStyle(ProxnixTheme.statusWarn)
                        .help("Check Scripts Dir in Settings")
                }
            }

            // Row 2: Group management + actions
            HStack(spacing: 14) {
                if viewMode == .groups {
                    HStack(spacing: 8) {
                        Button {
                            newGroupName = ""
                            groupEditError = nil
                            showCreateGroupSheet = true
                        } label: {
                            Label("Create Group", systemImage: "plus.circle")
                        }
                        .help("Create a new backing store under private/groups and switch this view to it.")
                    }
                } else {
                    // Container mode: show attached groups + attach field
                    if !selectedContainer.secretGroups.isEmpty {
                        HStack(spacing: 6) {
                            Text("Groups:")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                            ForEach(selectedContainer.secretGroups, id: \.self) { group in
                                HStack(spacing: 3) {
                                    Text(group)
                                        .font(.system(.caption, design: .monospaced))
                                    Button {
                                        removeAttachedGroup(group)
                                    } label: {
                                        Image(systemName: "xmark.circle.fill")
                                            .font(.system(size: 9))
                                    }
                                    .buttonStyle(.plain)
                                    .help("Remove \(group)")
                                }
                                .padding(.horizontal, 8)
                                .padding(.vertical, 4)
                                .background(ProxnixTheme.accentSubtle, in: Capsule())
                                .foregroundStyle(ProxnixTheme.accent)
                            }
                        }
                    }

                    HStack(spacing: 8) {
                        TextField("Attach group", text: $attachGroupName)
                            .textFieldStyle(.roundedBorder)
                            .font(.system(.caption, design: .monospaced))
                            .frame(maxWidth: 160)
                            .onSubmit { attachGroup() }

                        if !attachableGroups.isEmpty {
                            Menu {
                                ForEach(attachableGroups, id: \.self) { group in
                                    Button(group) { attachGroup(group) }
                                }
                            } label: {
                                Label("Quick attach", systemImage: "plus.circle")
                                    .font(.caption)
                            }
                        }
                    }
                }

                Spacer(minLength: 0)

                // Action buttons
                if canInit {
                    Button { Task { await initialize() } } label: {
                        Label("Init", systemImage: "wand.and.stars")
                    }
                    .disabled(runner.isRunning || appState.secretsScript == nil)
                }

                if canRotate {
                    Button { Task { await rotate() } } label: {
                        Label("Rotate", systemImage: "arrow.triangle.2.circlepath")
                    }
                    .disabled(runner.isRunning || appState.secretsScript == nil)
                }

                if canSetSecret {
                    Button {
                        newSecretName = ""
                        newSecretValue = ""
                        showSetSheet = true
                    } label: {
                        Label("Set", systemImage: "plus.circle")
                    }
                    .disabled(runner.isRunning || appState.secretsScript == nil)
                }

                if !runner.output.isEmpty && !runner.isRunning {
                    Button("Clear") {
                        runner.clear()
                        parsedSecrets = []
                    }
                    .font(.caption)
                    .foregroundStyle(.secondary)
                }

                if runner.isRunning {
                    Button("Cancel", role: .destructive) { runner.cancel() }
                    ProgressView().controlSize(.small)
                } else {
                    Button {
                        Task { await listSecrets() }
                    } label: {
                        Label("List Secrets", systemImage: "list.bullet")
                    }
                    .buttonStyle(.borderedProminent)
                    .disabled(appState.secretsScript == nil)
                }
            }

            if let groupEditError {
                Label(groupEditError, systemImage: "exclamationmark.triangle.fill")
                    .font(.caption)
                    .foregroundStyle(ProxnixTheme.statusFail)
            }
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 10)
        .background(Color(nsColor: .windowBackgroundColor))
    }

    // MARK: - Main Content

    @ViewBuilder
    private var mainContent: some View {
        if !parsedSecrets.isEmpty {
            secretsTable
        } else {
            LogView(
                output: runner.output,
                isRunning: runner.isRunning,
                exitCode: runner.lastExitCode
            )
            .overlay {
                if runner.output.isEmpty && !runner.isRunning {
                    emptyState
                }
            }
        }
    }

    private var secretsTable: some View {
        List {
            Section {
                ForEach(parsedSecrets) { entry in
                    ViewThatFits(in: .horizontal) {
                        HStack(spacing: 14) {
                            VStack(alignment: .leading, spacing: 2) {
                                Text(entry.name)
                                    .font(.system(.body, design: .monospaced))
                                    .lineLimit(1)
                                    .truncationMode(.middle)
                                Text(entry.scopeLabel)
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                            }

                            Spacer()

                            Text(entry.source)
                                .font(.system(.caption, design: .monospaced))
                                .foregroundStyle(.secondary)
                                .padding(.horizontal, 8)
                                .padding(.vertical, 5)
                                .background(.quaternary.opacity(0.55), in: Capsule())

                            Button(role: .destructive) {
                                pendingRemoval = entry
                            } label: {
                                Label("Remove", systemImage: "trash")
                            }
                            .buttonStyle(.borderless)
                        }

                        VStack(alignment: .leading, spacing: 2) {
                            Text(entry.name)
                                .font(.system(.body, design: .monospaced))
                                .lineLimit(2)
                                .truncationMode(.middle)
                            HStack(spacing: 8) {
                                Text(entry.scopeLabel)
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                                Text(entry.source)
                                    .font(.system(.caption, design: .monospaced))
                                    .foregroundStyle(.secondary)
                                    .padding(.horizontal, 8)
                                    .padding(.vertical, 5)
                                    .background(.quaternary.opacity(0.55), in: Capsule())
                            }
                            Button(role: .destructive) {
                                pendingRemoval = entry
                            } label: {
                                Label("Remove", systemImage: "trash")
                            }
                            .buttonStyle(.borderless)
                            .padding(.top, 4)
                        }
                    }
                    .padding(.vertical, 6)
                }
            } header: {
                HStack {
                    Text("\(parsedSecrets.count) secret" + (parsedSecrets.count == 1 ? "" : "s"))
                    Spacer()
                    Text(activeLabel)
                }
                .font(.caption.weight(.semibold))
                .foregroundStyle(.secondary)
            }
        }
        .listStyle(.inset)
    }

    private var emptyState: some View {
        VStack(spacing: 12) {
            Image(systemName: "lock.doc")
                .font(.system(size: 28, weight: .semibold))
                .foregroundStyle(.secondary)
            Text("No secrets listed yet")
                .font(.system(.headline, design: .rounded))
            Text("Choose a scope and run List Secrets to inspect what is currently available.")
                .font(.caption)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
                .frame(maxWidth: 320)
        }
        .padding(24)
        .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 20, style: .continuous))
    }

    private var setSecretSheet: some View {
        VStack(alignment: .leading, spacing: 18) {
            Text("Set Secret")
                .font(.system(.title3, design: .rounded).bold())

            Text("Scope: \(activeLabel)")
                .font(.subheadline)
                .foregroundStyle(.secondary)

            Form {
                TextField("Secret name", text: $newSecretName)
                    .font(.system(.body, design: .monospaced))
                SecureField("Secret value", text: $newSecretValue)
                    .font(.system(.body, design: .monospaced))
            }
            .frame(width: 380)

            HStack {
                Button("Cancel") { showSetSheet = false }
                    .keyboardShortcut(.cancelAction)
                Spacer()
                Button("Set") {
                    showSetSheet = false
                    Task { await setSecret() }
                }
                .buttonStyle(.borderedProminent)
                .keyboardShortcut(.defaultAction)
                .disabled(newSecretName.isEmpty || newSecretValue.isEmpty)
            }
        }
        .padding(20)
        .frame(minWidth: 420)
    }

    private var createGroupSheet: some View {
        VStack(alignment: .leading, spacing: 18) {
            Text("Create Group")
                .font(.system(.title3, design: .rounded).bold())

            Text("Create a group store under private/groups and switch the current scope to it.")
                .font(.subheadline)
                .foregroundStyle(.secondary)

            TextField("Group name", text: $newGroupName)
                .textFieldStyle(.roundedBorder)
                .font(.system(.body, design: .monospaced))
                .focused($createGroupFieldFocused)
                .onSubmit {
                    if createGroup() {
                        showCreateGroupSheet = false
                    }
                }

            if let groupEditError {
                Label(groupEditError, systemImage: "exclamationmark.triangle.fill")
                    .font(.caption)
                    .foregroundStyle(ProxnixTheme.statusFail)
            }

            HStack {
                Button("Cancel") { showCreateGroupSheet = false }
                    .keyboardShortcut(.cancelAction)
                Spacer()
                Button("Create") {
                    if createGroup() {
                        showCreateGroupSheet = false
                    }
                }
                .buttonStyle(.borderedProminent)
                .keyboardShortcut(.defaultAction)
                .disabled(newGroupName.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            }
        }
        .padding(20)
        .frame(minWidth: 420)
        .onAppear {
            createGroupFieldFocused = true
        }
    }

    // MARK: - Computed Properties

    private var referencedOnlyGroups: [String] {
        appState.attachedSecretGroups.filter { !appState.definedSecretGroups.contains($0) }
    }

    private var selectedContainerScope: ContainerScope {
        containerScope ?? ContainerScope(vmid: appState.containers.first?.vmid ?? "")
    }

    private var selectedContainer: ContainerInfo {
        appState.containers.first(where: { $0.vmid == selectedContainerScope.vmid })
            ?? ContainerInfo(vmid: selectedContainerScope.vmid, dropins: [], hasSecretStore: false, hasIdentity: false, secretGroups: [])
    }

    private var attachableGroups: [String] {
        appState.secretGroups.filter { !selectedContainer.secretGroups.contains($0) }
    }

    private var canSetSecret: Bool {
        true // always available in both modes
    }

    private var canRotate: Bool {
        true
    }

    private var canInit: Bool {
        if viewMode == .groups {
            if case .shared = storeScope { return true }
            return false
        }
        return true // containers can init
    }

    private var actionHint: String {
        activeLabel
    }

    // MARK: - Actions

    @discardableResult
    private func createGroup() -> Bool {
        let group = newGroupName.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !group.isEmpty else { return false }

        do {
            try appState.createDefinedSecretGroup(group)
            storeScope = .group(group)
            newGroupName = ""
            groupEditError = nil
            return true
        } catch {
            groupEditError = error.localizedDescription
            return false
        }
    }

    private func attachGroup() {
        let group = attachGroupName.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !group.isEmpty, !selectedContainerScope.vmid.isEmpty else { return }
        attachGroup(group)
    }

    private func attachGroup(_ group: String) {
        do {
            try appState.addSecretGroup(group, to: selectedContainerScope.vmid)
            attachGroupName = ""
            groupEditError = nil
        } catch {
            groupEditError = error.localizedDescription
        }
    }

    private func removeAttachedGroup(_ group: String) {
        do {
            try appState.removeSecretGroup(group, from: selectedContainerScope.vmid)
            groupEditError = nil
        } catch {
            groupEditError = error.localizedDescription
        }
    }

    private func listSecrets() async {
        guard let script = appState.secretsScript else { return }
        var args: [String]
        if viewMode == .groups {
            switch storeScope {
            case .shared:           args = ["ls-shared"]
            case .group(let group): args = ["ls-group", group]
            }
        } else {
            args = ["ls", selectedContainerScope.vmid]
        }
        await runner.run(script: script, args: args)
        parsedSecrets = parseSecretsList(runner.output)
    }

    private func setSecret() async {
        guard let script = appState.secretsScript else { return }
        var args: [String]
        if viewMode == .groups {
            switch storeScope {
            case .shared:           args = ["set-shared", newSecretName]
            case .group(let group): args = ["set-group", group, newSecretName]
            }
        } else {
            args = ["set", selectedContainerScope.vmid, newSecretName]
        }
        await runner.run(script: script, args: args, stdin: newSecretValue)
        await listSecrets()
    }

    private func removeSecret(_ entry: SecretEntry) async {
        guard let script = appState.secretsScript else { return }
        var args: [String]
        if entry.source == "shared" {
            args = ["rm-shared", entry.name]
        } else if entry.source.hasPrefix("group:") {
            args = ["rm-group", String(entry.source.dropFirst("group:".count)), entry.name]
        } else {
            args = ["rm", entry.source, entry.name]
        }
        await runner.run(script: script, args: args)
        await listSecrets()
    }

    private func rotate() async {
        guard let script = appState.secretsScript else { return }
        var args: [String]
        if viewMode == .groups {
            switch storeScope {
            case .shared:           args = ["rotate-shared"]
            case .group(let group): args = ["rotate-group", group]
            }
        } else {
            args = ["rotate", selectedContainerScope.vmid]
        }
        await runner.run(script: script, args: args)
    }

    private func initialize() async {
        guard let script = appState.secretsScript else { return }
        var args: [String]
        if viewMode == .groups {
            args = ["init-shared"] // only shared supports init in groups mode
        } else {
            args = ["init-container", selectedContainerScope.vmid]
        }
        await runner.run(script: script, args: args)
    }

    // MARK: - Parsing

    private func parseSecretsList(_ output: String) -> [SecretEntry] {
        let lines = output.components(separatedBy: "\n")
            .map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
            .filter { !$0.isEmpty && !$0.hasPrefix("#") && !$0.hasPrefix("=") }

        var entries: [SecretEntry] = []

        for line in lines {
            let tabParts = line.components(separatedBy: "\t")
            if tabParts.count == 2 {
                let first = tabParts[0].trimmingCharacters(in: .whitespaces)
                let second = tabParts[1].trimmingCharacters(in: .whitespaces)
                if let source = normalizedSourceToken(first) {
                    entries.append(SecretEntry(source: source, scopeLabel: displayLabel(for: source), name: second))
                } else if let source = normalizedSourceToken(second) {
                    entries.append(SecretEntry(source: source, scopeLabel: displayLabel(for: source), name: first))
                }
                continue
            }

            if line.contains("/") {
                let parts = line.components(separatedBy: "/")
                if parts.count >= 2 {
                    let source = parts[0]
                    entries.append(
                        SecretEntry(
                            source: source,
                            scopeLabel: displayLabel(for: source),
                            name: parts.dropFirst().joined(separator: "/")
                        )
                    )
                }
                continue
            }

            let inferredSource = inferredSourceForCurrentScope()
            entries.append(SecretEntry(source: inferredSource, scopeLabel: displayLabel(for: inferredSource), name: line))
        }
        return entries
    }

    private func inferredSourceForCurrentScope() -> String {
        if viewMode == .groups {
            switch storeScope {
            case .shared: return "shared"
            case .group(let group): return "group:\(group)"
            }
        }
        return selectedContainerScope.vmid
    }

    private func normalizedSourceToken(_ value: String) -> String? {
        if value == "shared" || value.hasPrefix("group:") || value.allSatisfy(\.isNumber) {
            return value
        }
        if value == "container" {
            return selectedContainerScope.vmid
        }
        return nil
    }

    private func displayLabel(for source: String) -> String {
        if source == "shared" {
            return "Shared"
        }
        if source.hasPrefix("group:") {
            return source.replacingOccurrences(of: "group:", with: "Group ")
        }
        if source.allSatisfy(\.isNumber) {
            return "CT \(source)"
        }
        return source
    }
}
