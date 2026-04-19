import SwiftUI
import AppKit

struct SettingsView: View {
    @EnvironmentObject var appState: AppState
    @State private var draft = ProxnixConfig()
    @State private var saveError: String?
    @State private var justSaved = false

    private var isDirty: Bool {
        draft != appState.configStore.config
    }

    var body: some View {
        Form {
            Section {
                directoryRow("Site Directory", value: $draft.siteDir,
                             placeholder: "~/path/to/site-repo",
                             validation: validateSiteDir)
                pathRow("Master Identity", value: $draft.masterIdentity,
                        placeholder: "~/.ssh/id_ed25519",
                        validation: validateFileExists)
            } header: {
                Label("Site Repo", systemImage: "folder.fill")
            }

            Section {
                row("SSH Hosts", placeholder: "root@192.168.1.10 root@192.168.1.11",
                    value: $draft.hosts,
                    help: "Space-separated SSH targets passed to proxnix-publish")
                pathRow("SSH Identity", value: $draft.sshIdentity,
                        placeholder: "Optional: ~/.ssh/proxnix_key",
                        validation: draft.sshIdentity.isEmpty ? nil : validateFileExists,
                        help: "Leave blank to use SSH agent / default key")
            } header: {
                Label("Hosts", systemImage: "network")
            }

            Section {
                row("Remote Dir", placeholder: "/var/lib/proxnix", value: $draft.remoteDir)
                row("Remote Priv Dir", placeholder: "/var/lib/proxnix/private", value: $draft.remotePrivDir)
                row("Host Relay Identity", placeholder: "/etc/proxnix/host_relay_identity",
                    value: $draft.remoteHostRelayIdentity)
            } header: {
                Label("Remote Paths", systemImage: "externaldrive.connected.to.line.below")
            }

            Section {
                directoryRow("Scripts Dir", value: $draft.scriptsDir,
                             placeholder: "Optional: custom scripts directory",
                             validation: validateScriptsDir,
                             help: "Leave blank to use bundled app scripts, /run/current-system/sw/bin, /usr/local/bin, or /opt/homebrew/bin")
            } header: {
                Label("App", systemImage: "app.badge.checkmark")
            }

            if let error = saveError {
                Section {
                    Label(error, systemImage: "exclamationmark.triangle.fill")
                        .foregroundStyle(.red)
                        .font(.caption)
                }
            }

            Section {
                HStack {
                    if isDirty {
                        Label("Unsaved changes", systemImage: "circle.fill")
                            .font(.caption)
                            .foregroundStyle(.orange)
                        Button("Revert") {
                            draft = appState.configStore.config
                            saveError = nil
                        }
                        .font(.caption)
                    }
                    Spacer()
                    if justSaved {
                        Label("Saved", systemImage: "checkmark.circle.fill")
                            .foregroundStyle(.green)
                            .transition(.opacity)
                    }
                    Button("Save") { save() }
                        .buttonStyle(.borderedProminent)
                        .controlSize(.large)
                        .keyboardShortcut("s", modifiers: .command)
                        .disabled(!isDirty)
                }
            }
        }
        .formStyle(.grouped)
        .navigationTitle("Settings")
        .onAppear { draft = appState.configStore.config }
    }

    // MARK: - Row Builders

    @ViewBuilder
    private func row(_ label: String, placeholder: String, value: Binding<String>,
                     help: String? = nil) -> some View {
        LabeledContent(label) {
            VStack(alignment: .leading, spacing: 2) {
                TextField(placeholder, text: value)
                    .textFieldStyle(.roundedBorder)
                    .font(.system(.body, design: .monospaced))
                if let help {
                    Text(help).font(.caption).foregroundStyle(.secondary)
                }
            }
        }
    }

    @ViewBuilder
    private func directoryRow(_ label: String, value: Binding<String>,
                              placeholder: String,
                              validation: ((String) -> ValidationResult)? = nil,
                              help: String? = nil) -> some View {
        LabeledContent(label) {
            VStack(alignment: .leading, spacing: 4) {
                HStack(spacing: 6) {
                    TextField(placeholder, text: value)
                        .textFieldStyle(.roundedBorder)
                        .font(.system(.body, design: .monospaced))
                    Button {
                        pickDirectory(into: value)
                    } label: {
                        Image(systemName: "folder")
                    }
                    .buttonStyle(.borderless)
                    .help("Browse...")
                }
                if let help {
                    Text(help).font(.caption).foregroundStyle(.secondary)
                }
                if let validation, !value.wrappedValue.isEmpty {
                    let result = validation(value.wrappedValue)
                    if case .warning(let msg) = result {
                        Label(msg, systemImage: "exclamationmark.triangle.fill")
                            .font(.caption).foregroundStyle(.orange)
                    } else if case .error(let msg) = result {
                        Label(msg, systemImage: "xmark.circle.fill")
                            .font(.caption).foregroundStyle(.red)
                    }
                }
            }
        }
    }

    @ViewBuilder
    private func pathRow(_ label: String, value: Binding<String>,
                         placeholder: String,
                         validation: ((String) -> ValidationResult)? = nil,
                         help: String? = nil) -> some View {
        LabeledContent(label) {
            VStack(alignment: .leading, spacing: 4) {
                TextField(placeholder, text: value)
                    .textFieldStyle(.roundedBorder)
                    .font(.system(.body, design: .monospaced))
                if let help {
                    Text(help).font(.caption).foregroundStyle(.secondary)
                }
                if let validation, !value.wrappedValue.isEmpty {
                    let result = validation(value.wrappedValue)
                    if case .warning(let msg) = result {
                        Label(msg, systemImage: "exclamationmark.triangle.fill")
                            .font(.caption).foregroundStyle(.orange)
                    } else if case .error(let msg) = result {
                        Label(msg, systemImage: "xmark.circle.fill")
                            .font(.caption).foregroundStyle(.red)
                    }
                }
            }
        }
    }

    // MARK: - Validation

    enum ValidationResult {
        case ok, warning(String), error(String)
    }

    private func validateSiteDir(_ path: String) -> ValidationResult {
        let expanded = (path as NSString).expandingTildeInPath
        var isDir: ObjCBool = false
        guard FileManager.default.fileExists(atPath: expanded, isDirectory: &isDir), isDir.boolValue else {
            return .error("Directory does not exist")
        }
        let containersPath = URL(fileURLWithPath: expanded).appendingPathComponent("containers").path
        guard FileManager.default.fileExists(atPath: containersPath, isDirectory: &isDir), isDir.boolValue else {
            return .warning("No containers/ subdirectory found")
        }
        return .ok
    }

    private func validateScriptsDir(_ path: String) -> ValidationResult {
        let expanded = (path as NSString).expandingTildeInPath
        var isDir: ObjCBool = false
        guard FileManager.default.fileExists(atPath: expanded, isDirectory: &isDir), isDir.boolValue else {
            return .error("Directory does not exist")
        }
        let publish = URL(fileURLWithPath: expanded).appendingPathComponent("proxnix-publish").path
        let secrets = URL(fileURLWithPath: expanded).appendingPathComponent("proxnix-secrets").path
        let hasPublish = FileManager.default.isExecutableFile(atPath: publish)
        let hasSecrets = FileManager.default.isExecutableFile(atPath: secrets)
        if !hasPublish && !hasSecrets {
            return .warning("Scripts not found in this directory")
        } else if !hasPublish {
            return .warning("proxnix-publish not found")
        } else if !hasSecrets {
            return .warning("proxnix-secrets not found")
        }
        return .ok
    }

    private func validateFileExists(_ path: String) -> ValidationResult {
        let expanded = (path as NSString).expandingTildeInPath
        guard FileManager.default.fileExists(atPath: expanded) else {
            return .warning("File does not exist")
        }
        return .ok
    }

    // MARK: - Actions

    private func pickDirectory(into binding: Binding<String>) {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.allowsMultipleSelection = false
        panel.prompt = "Choose"
        guard panel.runModal() == .OK, let url = panel.url else { return }
        binding.wrappedValue = url.path
    }

    private func save() {
        saveError = nil
        appState.configStore.config = draft
        do {
            try appState.configStore.save()
            appState.refresh()
            withAnimation { justSaved = true }
            DispatchQueue.main.asyncAfter(deadline: .now() + 2) {
                withAnimation { justSaved = false }
            }
        } catch {
            saveError = error.localizedDescription
        }
    }
}
