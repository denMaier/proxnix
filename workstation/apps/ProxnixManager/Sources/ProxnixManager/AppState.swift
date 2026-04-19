import Foundation
import Combine

@MainActor
class AppState: ObservableObject {
    @Published var configStore = ConfigStore()
    @Published var containers: [ContainerInfo] = []
    @Published var definedSecretGroups: [String] = []
    @Published var attachedSecretGroups: [String] = []
    @Published var containerSidebarMetadata: [String: ContainerSidebarMetadata] = [:]

    private let sidebarMetadataStore = ContainerSidebarMetadataStore()

    var secretGroups: [String] {
        Array(Set(definedSecretGroups).union(attachedSecretGroups)).sorted()
    }

    init() {
        refresh()
    }

    func refresh() {
        let siteDir = configStore.config.siteDir
        guard !siteDir.isEmpty else {
            containers = []
            definedSecretGroups = []
            attachedSecretGroups = []
            containerSidebarMetadata = [:]
            return
        }
        containers = SiteScanner.scan(siteDir: siteDir)
        definedSecretGroups = SiteScanner.scanDefinedSecretGroups(siteDir: siteDir)
        attachedSecretGroups = SiteScanner.scanAttachedSecretGroups(siteDir: siteDir)
        containerSidebarMetadata = sidebarMetadataStore.metadata(forSiteDir: siteDir)
    }

    // MARK: - Script resolution

    private var bundledScriptsDir: String? {
        guard let resourceURL = Bundle.main.resourceURL else { return nil }
        let path = resourceURL.appendingPathComponent("bin").path
        guard FileManager.default.fileExists(atPath: path) else { return nil }
        return path
    }

    func scriptPath(named name: String) -> String? {
        var dirs: [String] = []

        if !configStore.config.scriptsDir.isEmpty {
            dirs.append(configStore.config.scriptsDir)
        }
        if let bundledScriptsDir {
            dirs.append(bundledScriptsDir)
        }

        dirs.append(contentsOf: [
            "/run/current-system/sw/bin",
            "/usr/local/bin",
            "/opt/homebrew/bin",
        ])

        for dir in dirs {
            let expanded = (dir as NSString).expandingTildeInPath
            let path = URL(fileURLWithPath: expanded).appendingPathComponent(name).path
            if FileManager.default.isExecutableFile(atPath: path) { return path }
        }
        return nil
    }

    var publishScript: String? { scriptPath(named: "proxnix-publish") }
    var secretsScript: String? { scriptPath(named: "proxnix-secrets") }
    var doctorScript: String? { scriptPath(named: "proxnix-doctor") }

    func createDefinedSecretGroup(_ group: String) throws {
        let trimmed = group.trimmingCharacters(in: .whitespacesAndNewlines)
        guard SiteScanner.isValidSecretGroupName(trimmed) else {
            throw AppStateError.invalidSecretGroupName
        }

        let path = definedSecretGroupDirectoryPath(for: trimmed)
        try FileManager.default.createDirectory(
            at: URL(fileURLWithPath: path),
            withIntermediateDirectories: true
        )
        refresh()
    }

    func addSecretGroup(_ group: String, to vmid: String) throws {
        let trimmed = group.trimmingCharacters(in: .whitespacesAndNewlines)
        guard SiteScanner.isValidSecretGroupName(trimmed) else {
            throw AppStateError.invalidSecretGroupName
        }

        let currentGroups = containers.first(where: { $0.vmid == vmid })?.secretGroups ?? []
        guard !currentGroups.contains(trimmed) else { return }

        let path = secretGroupsFilePath(for: vmid)
        let parent = URL(fileURLWithPath: path).deletingLastPathComponent()
        try FileManager.default.createDirectory(at: parent, withIntermediateDirectories: true)

        var updatedGroups = currentGroups
        updatedGroups.append(trimmed)
        try writeSecretGroups(updatedGroups, to: path)
        refresh()
    }

    func removeSecretGroup(_ group: String, from vmid: String) throws {
        let path = secretGroupsFilePath(for: vmid)
        let currentGroups = containers.first(where: { $0.vmid == vmid })?.secretGroups ?? []
        let updatedGroups = currentGroups.filter { $0 != group }

        if updatedGroups.isEmpty {
            if FileManager.default.fileExists(atPath: path) {
                try FileManager.default.removeItem(atPath: path)
            }
        } else {
            try writeSecretGroups(updatedGroups, to: path)
        }
        refresh()
    }

    func sidebarMetadata(for vmid: String) -> ContainerSidebarMetadata {
        containerSidebarMetadata[vmid]?.normalized ?? ContainerSidebarMetadata()
    }

    func saveSidebarMetadata(_ metadata: ContainerSidebarMetadata, for vmid: String) throws {
        let siteDir = configStore.config.siteDir
        guard !siteDir.isEmpty else { return }

        try sidebarMetadataStore.save(metadata, for: vmid, siteDir: siteDir)
        containerSidebarMetadata = sidebarMetadataStore.metadata(forSiteDir: siteDir)
    }

    private func secretGroupsFilePath(for vmid: String) -> String {
        let siteDir = (configStore.config.siteDir as NSString).expandingTildeInPath
        return URL(fileURLWithPath: siteDir)
            .appendingPathComponent("containers/\(vmid)/secret-groups.list")
            .path
    }

    private func definedSecretGroupDirectoryPath(for group: String) -> String {
        let siteDir = (configStore.config.siteDir as NSString).expandingTildeInPath
        return URL(fileURLWithPath: siteDir)
            .appendingPathComponent("private/groups/\(group)")
            .path
    }

    private func writeSecretGroups(_ groups: [String], to path: String) throws {
        let contents = groups.joined(separator: "\n") + "\n"
        try contents.write(toFile: path, atomically: true, encoding: .utf8)
    }
}

enum AppStateError: LocalizedError {
    case invalidSecretGroupName

    var errorDescription: String? {
        switch self {
        case .invalidSecretGroupName:
            return "Group names may only contain letters, numbers, underscores, dots, and dashes."
        }
    }
}
