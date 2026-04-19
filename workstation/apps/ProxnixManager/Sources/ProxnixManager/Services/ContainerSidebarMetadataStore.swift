import Foundation

struct ContainerSidebarMetadata: Codable, Equatable {
    var displayName: String = ""
    var group: String = ""
    var labels: [String] = []

    var normalized: ContainerSidebarMetadata {
        ContainerSidebarMetadata(
            displayName: displayName.trimmingCharacters(in: .whitespacesAndNewlines),
            group: group.trimmingCharacters(in: .whitespacesAndNewlines),
            labels: Self.normalizeLabels(labels)
        )
    }

    var isEmpty: Bool {
        let metadata = normalized
        return metadata.displayName.isEmpty && metadata.group.isEmpty && metadata.labels.isEmpty
    }

    func title(for vmid: String) -> String {
        let metadata = normalized
        return metadata.displayName.isEmpty ? vmid : metadata.displayName
    }

    static func parseLabels(from raw: String) -> [String] {
        normalizeLabels(
            raw.split(whereSeparator: { $0 == "," || $0 == "\n" })
                .map(String.init)
        )
    }

    private static func normalizeLabels(_ labels: [String]) -> [String] {
        var normalized: [String] = []
        var seen = Set<String>()

        for label in labels {
            let trimmed = label.trimmingCharacters(in: .whitespacesAndNewlines)
            guard !trimmed.isEmpty else { continue }

            let key = trimmed.lowercased()
            guard !seen.contains(key) else { continue }

            seen.insert(key)
            normalized.append(trimmed)
        }

        return normalized
    }
}

private struct ContainerSidebarMetadataFile: Codable {
    var sites: [String: SiteContainerSidebarMetadata] = [:]
}

private struct SiteContainerSidebarMetadata: Codable {
    var containers: [String: ContainerSidebarMetadata] = [:]
}

final class ContainerSidebarMetadataStore {
    private let fileURL: URL
    private let encoder: JSONEncoder
    private let decoder = JSONDecoder()

    init() {
        let base = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent(".config/proxnix")
        fileURL = base.appendingPathComponent("manager-sidebar-state.json")

        encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
    }

    func metadata(forSiteDir siteDir: String) -> [String: ContainerSidebarMetadata] {
        let siteKey = normalizedSiteKey(siteDir)
        return loadState().sites[siteKey]?.containers ?? [:]
    }

    func save(_ metadata: ContainerSidebarMetadata, for vmid: String, siteDir: String) throws {
        let siteKey = normalizedSiteKey(siteDir)
        var state = loadState()
        var siteState = state.sites[siteKey] ?? SiteContainerSidebarMetadata()
        let normalized = metadata.normalized

        if normalized.isEmpty {
            siteState.containers.removeValue(forKey: vmid)
        } else {
            siteState.containers[vmid] = normalized
        }

        if siteState.containers.isEmpty {
            state.sites.removeValue(forKey: siteKey)
        } else {
            state.sites[siteKey] = siteState
        }

        try persist(state)
    }

    private func loadState() -> ContainerSidebarMetadataFile {
        guard let data = try? Data(contentsOf: fileURL),
              let decoded = try? decoder.decode(ContainerSidebarMetadataFile.self, from: data) else {
            return ContainerSidebarMetadataFile()
        }

        return decoded
    }

    private func persist(_ state: ContainerSidebarMetadataFile) throws {
        let directory = fileURL.deletingLastPathComponent()
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        let data = try encoder.encode(state)
        try data.write(to: fileURL, options: .atomic)
    }

    private func normalizedSiteKey(_ siteDir: String) -> String {
        let expanded = (siteDir as NSString).expandingTildeInPath
        return URL(fileURLWithPath: expanded).standardizedFileURL.path
    }
}
