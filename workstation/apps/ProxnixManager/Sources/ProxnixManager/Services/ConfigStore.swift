import Foundation
import Combine

class ConfigStore: ObservableObject {
    @Published var config = ProxnixConfig()

    let configPath: URL

    init() {
        let base = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent(".config/proxnix")
        configPath = base.appendingPathComponent("config")
        load()
    }

    func load() {
        guard FileManager.default.fileExists(atPath: configPath.path) else { return }

        let task = Process()
        let outputPipe = Pipe()
        let shellScript = """
        set -a
        source \(shellSingleQuoted(configPath.path))
        printf 'PROXNIX_SITE_DIR=%s\\n' "$PROXNIX_SITE_DIR"
        printf 'PROXNIX_MASTER_IDENTITY=%s\\n' "$PROXNIX_MASTER_IDENTITY"
        printf 'PROXNIX_HOSTS=%s\\n' "$PROXNIX_HOSTS"
        printf 'PROXNIX_SSH_IDENTITY=%s\\n' "$PROXNIX_SSH_IDENTITY"
        printf 'PROXNIX_REMOTE_DIR=%s\\n' "$PROXNIX_REMOTE_DIR"
        printf 'PROXNIX_REMOTE_PRIV_DIR=%s\\n' "$PROXNIX_REMOTE_PRIV_DIR"
        printf 'PROXNIX_REMOTE_HOST_RELAY_IDENTITY=%s\\n' "$PROXNIX_REMOTE_HOST_RELAY_IDENTITY"
        printf 'PROXNIX_SECRET_PROVIDER=%s\\n' "$PROXNIX_SECRET_PROVIDER"
        printf 'PROXNIX_SECRET_PROVIDER_COMMAND=%s\\n' "$PROXNIX_SECRET_PROVIDER_COMMAND"
        printf 'PROXNIX_SCRIPTS_DIR=%s\\n' "$PROXNIX_SCRIPTS_DIR"
        """

        task.executableURL = URL(fileURLWithPath: "/bin/bash")
        task.arguments = ["-lc", shellScript]
        task.standardOutput = outputPipe
        task.standardError = Pipe()

        do {
            try task.run()
            task.waitUntilExit()
        } catch {
            return
        }

        guard task.terminationStatus == 0,
              let content = String(data: outputPipe.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8)
        else {
            return
        }

        var cfg = ProxnixConfig()
        for raw in content.components(separatedBy: .newlines) {
            guard let separator = raw.firstIndex(of: "=") else { continue }
            let key = String(raw[..<separator])
            let value = String(raw[raw.index(after: separator)...])
            switch key {
            case "PROXNIX_SITE_DIR": cfg.siteDir = value
            case "PROXNIX_MASTER_IDENTITY": cfg.masterIdentity = value
            case "PROXNIX_HOSTS": cfg.hosts = value
            case "PROXNIX_SSH_IDENTITY": cfg.sshIdentity = value
            case "PROXNIX_REMOTE_DIR": cfg.remoteDir = value
            case "PROXNIX_REMOTE_PRIV_DIR": cfg.remotePrivDir = value
            case "PROXNIX_REMOTE_HOST_RELAY_IDENTITY": cfg.remoteHostRelayIdentity = value
            case "PROXNIX_SECRET_PROVIDER": cfg.secretProvider = value
            case "PROXNIX_SECRET_PROVIDER_COMMAND": cfg.secretProviderCommand = value
            case "PROXNIX_SCRIPTS_DIR": cfg.scriptsDir = value
            default: break
            }
        }

        self.config = cfg
    }

    func save() throws {
        let dir = configPath.deletingLastPathComponent()
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)

        var lines = ["# proxnix workstation config"]
        func emit(_ key: String, _ value: String) {
            if !value.isEmpty { lines.append("\(key)=\(shellSingleQuoted(value))") }
        }
        emit("PROXNIX_SITE_DIR",                  config.siteDir)
        emit("PROXNIX_MASTER_IDENTITY",            config.masterIdentity)
        emit("PROXNIX_HOSTS",                      config.hosts)
        emit("PROXNIX_SSH_IDENTITY",               config.sshIdentity)
        emit("PROXNIX_REMOTE_DIR",                 config.remoteDir)
        emit("PROXNIX_REMOTE_PRIV_DIR",            config.remotePrivDir)
        emit("PROXNIX_REMOTE_HOST_RELAY_IDENTITY", config.remoteHostRelayIdentity)
        emit("PROXNIX_SECRET_PROVIDER",            config.secretProvider)
        emit("PROXNIX_SECRET_PROVIDER_COMMAND",    config.secretProviderCommand)
        emit("PROXNIX_SCRIPTS_DIR",                config.scriptsDir)

        try (lines.joined(separator: "\n") + "\n")
            .write(to: configPath, atomically: true, encoding: .utf8)
    }

    private func shellSingleQuoted(_ value: String) -> String {
        "'\(value.replacingOccurrences(of: "'", with: "'\"'\"'"))'"
    }
}
