import Foundation

enum SiteScanner {
    static func scan(siteDir: String) -> [ContainerInfo] {
        let fm = FileManager.default
        let expandedSite = (siteDir as NSString).expandingTildeInPath
        let containersURL = URL(fileURLWithPath: expandedSite).appendingPathComponent("containers")
        let privateContainersURL = URL(fileURLWithPath: expandedSite)
            .appendingPathComponent("private/containers")

        // Collect numeric VMIDs from both public and private dirs
        var vmids = Set<String>()
        for base in [containersURL, privateContainersURL] {
            guard let entries = try? fm.contentsOfDirectory(
                at: base,
                includingPropertiesForKeys: [.isDirectoryKey]
            ) else { continue }
            for entry in entries {
                let name = entry.lastPathComponent
                guard name.allSatisfy(\.isNumber), !name.isEmpty else { continue }
                guard (try? entry.resourceValues(forKeys: [.isDirectoryKey]).isDirectory) == true else { continue }
                vmids.insert(name)
            }
        }

        return vmids.sorted { (a, b) -> Bool in
            if let ia = Int(a), let ib = Int(b) { return ia < ib }
            return a < b
        }.map { vmid in
            buildContainer(
                vmid: vmid,
                containersURL: containersURL,
                privateContainersURL: privateContainersURL
            )
        }
    }

    static func scanDefinedSecretGroups(siteDir: String) -> [String] {
        let fm = FileManager.default
        let expandedSite = (siteDir as NSString).expandingTildeInPath
        let privateGroupsURL = URL(fileURLWithPath: expandedSite).appendingPathComponent("private/groups")

        var groups = Set<String>()

        if let entries = try? fm.contentsOfDirectory(
            at: privateGroupsURL,
            includingPropertiesForKeys: [.isDirectoryKey]
        ) {
            for entry in entries {
                let name = entry.lastPathComponent
                guard isValidSecretGroupName(name) else { continue }
                guard (try? entry.resourceValues(forKeys: [.isDirectoryKey]).isDirectory) == true else { continue }
                groups.insert(name)
            }
        }

        return groups.sorted()
    }

    static func scanAttachedSecretGroups(siteDir: String) -> [String] {
        let groups = scan(siteDir: siteDir)
            .flatMap(\.secretGroups)
        return Array(Set(groups)).sorted()
    }

    static func scanAllSecretGroups(siteDir: String) -> [String] {
        let defined = scanDefinedSecretGroups(siteDir: siteDir)
        let attached = scanAttachedSecretGroups(siteDir: siteDir)
        return Array(Set(defined).union(attached)).sorted()
    }

    static func isValidSecretGroupName(_ group: String) -> Bool {
        guard !group.isEmpty else { return false }
        return group.allSatisfy { char in
            char.isLetter || char.isNumber || char == "_" || char == "." || char == "-"
        }
    }

    private static func buildContainer(
        vmid: String,
        containersURL: URL,
        privateContainersURL: URL
    ) -> ContainerInfo {
        let fm = FileManager.default

        // Dropins
        let dropinsURL = containersURL.appendingPathComponent("\(vmid)/dropins")
        let dropins: [String]
        if let entries = try? fm.contentsOfDirectory(at: dropinsURL, includingPropertiesForKeys: nil) {
            dropins = entries.map(\.lastPathComponent).sorted()
        } else {
            dropins = []
        }

        // Secret store & identity
        let secretStorePath = privateContainersURL
            .appendingPathComponent("\(vmid)/secrets.sops.yaml").path
        let identityPath = privateContainersURL
            .appendingPathComponent("\(vmid)/age_identity.sops.yaml").path

        // Secret groups
        let groupsFile = containersURL
            .appendingPathComponent("\(vmid)/secret-groups.list")
        let groups = parseSecretGroups(from: groupsFile)

        return ContainerInfo(
            vmid: vmid,
            dropins: dropins,
            hasSecretStore: fm.fileExists(atPath: secretStorePath),
            hasIdentity: fm.fileExists(atPath: identityPath),
            secretGroups: groups
        )
    }

    private static func parseSecretGroups(from fileURL: URL) -> [String] {
        guard let content = try? String(contentsOf: fileURL, encoding: .utf8) else {
            return []
        }

        var groups: [String] = []
        var seen = Set<String>()

        for rawLine in content.components(separatedBy: .newlines) {
            let line = rawLine
                .split(separator: "#", maxSplits: 1, omittingEmptySubsequences: false)
                .first
                .map(String.init)?
                .trimmingCharacters(in: .whitespacesAndNewlines) ?? ""

            guard isValidSecretGroupName(line), !seen.contains(line) else { continue }
            seen.insert(line)
            groups.append(line)
        }

        return groups
    }
}
