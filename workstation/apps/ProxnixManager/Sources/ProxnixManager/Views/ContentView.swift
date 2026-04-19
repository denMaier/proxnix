import SwiftUI

enum SidebarItem: Hashable {
    case settings
    case publish
    case secrets
    case doctor
    case git
    case container(String)
}

private struct SidebarContainerGroup: Identifiable {
    let id: String
    let title: String
    let containers: [ContainerInfo]
    let isPrimary: Bool
}

struct ContentView: View {
    @EnvironmentObject var appState: AppState
    @State private var selection: SidebarItem? = nil
    @State private var expandedGroups: Set<String> = []
    @State private var expandedGroupsInitialized = false

    private var needsOnboarding: Bool {
        appState.configStore.config.siteDir.isEmpty
    }

    private var sidebarContainerGroups: [SidebarContainerGroup] {
        guard !appState.containers.isEmpty else { return [] }

        let grouped = Dictionary(grouping: appState.containers) { container in
            appState.sidebarMetadata(for: container.vmid).group
        }
        let hasCustomGroups = grouped.keys.contains { !$0.isEmpty }
        let orderedKeys = grouped.keys.sorted { lhs, rhs in
            switch (lhs.isEmpty, rhs.isEmpty) {
            case (true, true):
                return false
            case (true, false):
                return false
            case (false, true):
                return true
            case (false, false):
                return lhs.localizedCaseInsensitiveCompare(rhs) == .orderedAscending
            }
        }

        return orderedKeys.map { key in
            SidebarContainerGroup(
                id: key.isEmpty ? "_ungrouped" : key,
                title: key.isEmpty ? (hasCustomGroups ? "Ungrouped" : "Containers") : key,
                containers: grouped[key, default: []].sorted(by: sidebarContainerSort),
                isPrimary: key.isEmpty && !hasCustomGroups
            )
        }
    }

    private var defaultSidebarContainer: ContainerInfo? {
        sidebarContainerGroups.first?.containers.first
    }

    var body: some View {
        NavigationSplitView {
            sidebar
        } detail: {
            detail
        }
        .navigationSplitViewStyle(.balanced)
        .background(Color(nsColor: .controlBackgroundColor))
        .onChange(of: appState.containers) { containers in
            if !expandedGroupsInitialized && !containers.isEmpty {
                expandedGroups = Set(sidebarContainerGroups.map(\.id))
                expandedGroupsInitialized = true
            }
            if selection == nil && !containers.isEmpty {
                if let first = defaultSidebarContainer {
                    selection = .container(first.vmid)
                }
            }
        }
        .onChange(of: needsOnboarding) { onboarding in
            if !onboarding && selection == nil {
                if let first = defaultSidebarContainer {
                    selection = .container(first.vmid)
                } else {
                    selection = .settings
                }
            }
        }
    }

    // MARK: - Sidebar

    private var sidebar: some View {
        List(selection: $selection) {
            Section {
                sidebarAction("Git", icon: "arrow.triangle.branch", tag: .git, tint: .orange)
                sidebarAction("Doctor", icon: "stethoscope", tag: .doctor, tint: .blue)
                sidebarAction("Publish All", icon: "arrow.up.circle.fill", tag: .publish, tint: ProxnixTheme.accent)
                sidebarAction("Secrets", icon: "lock.fill", tag: .secrets, tint: .purple)
            } header: {
                EyebrowLabel("Actions", icon: "bolt.fill")
                    .padding(.bottom, 4)
            }

            if appState.containers.isEmpty {
                Section {
                    if needsOnboarding {
                        sidebarEmptyHint(
                            icon: "folder.badge.questionmark",
                            title: "Set a site directory",
                            detail: "Point at your site repo to discover containers."
                        )
                    } else {
                        sidebarEmptyHint(
                            icon: "shippingbox",
                            title: "No containers found",
                            detail: "Check your site directory or create containers/ entries."
                        )
                    }
                } header: {
                    containerSectionHeader(title: "Containers", count: 0, isPrimary: true)
                }
            } else {
                ForEach(sidebarContainerGroups) { group in
                    Section {
                        DisclosureGroup(isExpanded: groupExpandedBinding(for: group.id)) {
                            ForEach(group.containers) { container in
                                containerRow(container)
                                    .tag(SidebarItem.container(container.vmid))
                            }
                        } label: {
                            containerSectionHeader(
                                title: group.title,
                                count: group.containers.count,
                                isPrimary: group.isPrimary
                            )
                        }
                        .animation(nil, value: expandedGroups)
                    }
                }
            }

            Section {
                sidebarAction("Settings", icon: "gear", tag: .settings, tint: .gray)
            } header: {
                EyebrowLabel("App", icon: "app.badge.checkmark")
                    .padding(.bottom, 4)
            }
        }
        .listStyle(.sidebar)
        .navigationTitle("Proxnix")
        .toolbar {
            ToolbarItem(placement: .automatic) {
                Button { appState.refresh() } label: {
                    Image(systemName: "arrow.clockwise")
                }
                .help("Refresh containers (Cmd+R)")
            }
        }
    }

    private func containerSectionHeader(title: String, count: Int, isPrimary: Bool) -> some View {
        HStack {
            if isPrimary {
                EyebrowLabel(title, icon: "shippingbox.fill")
            } else {
                Text(title)
                    .font(.system(size: 12, weight: .semibold, design: .rounded))
                    .foregroundStyle(.secondary)
            }

            Spacer()

            if count > 0 {
                Text("\(count)")
                    .font(.system(size: 10, weight: .bold, design: .monospaced))
                    .foregroundStyle(isPrimary ? ProxnixTheme.accent : .secondary)
                    .padding(.horizontal, 7)
                    .padding(.vertical, 2)
                    .background((isPrimary ? ProxnixTheme.accentSubtle : Color.secondary.opacity(0.12)), in: Capsule())
            }
        }
        .padding(.bottom, 4)
    }

    private func groupExpandedBinding(for id: String) -> Binding<Bool> {
        Binding(
            get: { expandedGroups.contains(id) },
            set: { isExpanded in
                if isExpanded {
                    expandedGroups.insert(id)
                } else {
                    expandedGroups.remove(id)
                }
            }
        )
    }

    private func sidebarAction(_ title: String, icon: String, tag: SidebarItem, tint: Color) -> some View {
        Label {
            Text(title)
                .font(.system(.body, design: .rounded).weight(.medium))
        } icon: {
            Image(systemName: icon)
                .foregroundStyle(tint)
        }
        .tag(tag)
    }

    private func sidebarEmptyHint(icon: String, title: String, detail: String) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Label(title, systemImage: icon)
                .font(.caption.weight(.semibold))
            Text(detail)
                .font(.caption2)
                .foregroundStyle(.secondary)
        }
        .padding(.vertical, 4)
    }

    @ViewBuilder
    private func containerRow(_ c: ContainerInfo) -> some View {
        let metadata = appState.sidebarMetadata(for: c.vmid)
        let title = metadata.title(for: c.vmid)

        HStack(spacing: 8) {
            VStack(alignment: .leading, spacing: 2) {
                HStack(spacing: 8) {
                    // Status dot with subtle glow
                    Circle()
                        .fill(c.hasSecretStore ? ProxnixTheme.statusOk : ProxnixTheme.statusInfo)
                        .frame(width: 7, height: 7)
                        .shadow(color: (c.hasSecretStore ? ProxnixTheme.statusOk : ProxnixTheme.statusInfo).opacity(0.5), radius: 3)
                        .help(c.hasSecretStore ? "Has secrets" : "No secrets")
                    Text(title)
                        .font(metadata.displayName.isEmpty
                              ? .system(.body, design: .monospaced).bold()
                              : .system(.body, design: .rounded).weight(.semibold))
                        .lineLimit(1)
                }
                if let detail = containerSidebarDetail(for: c, metadata: metadata) {
                    Text(detail)
                        .font(.system(size: 10, weight: .medium, design: .rounded))
                        .foregroundStyle(.tertiary)
                        .lineLimit(1)
                }
            }

            Spacer()

            HStack(spacing: 5) {
                if c.hasIdentity {
                    statusBadge(icon: "key.fill", color: .orange)
                        .help("Has age identity")
                }
                if c.hasSecretStore {
                    statusBadge(icon: "lock.fill", color: ProxnixTheme.statusOk)
                        .help("Has secret store")
                }
                if !c.secretGroups.isEmpty {
                    statusBadge(icon: "person.3.fill", color: .purple)
                        .help("Groups: \(c.secretGroups.joined(separator: ", "))")
                }
            }
        }
        .help(containerSidebarHelp(for: c, metadata: metadata))
    }

    private func statusBadge(icon: String, color: Color) -> some View {
        Image(systemName: icon)
            .font(.system(size: 9, weight: .semibold))
            .foregroundStyle(color)
            .frame(width: 18, height: 18)
            .background(color.opacity(0.12), in: RoundedRectangle(cornerRadius: 5, style: .continuous))
    }

    private func sidebarContainerSort(_ lhs: ContainerInfo, _ rhs: ContainerInfo) -> Bool {
        let lhsTitle = appState.sidebarMetadata(for: lhs.vmid).title(for: lhs.vmid)
        let rhsTitle = appState.sidebarMetadata(for: rhs.vmid).title(for: rhs.vmid)
        let comparison = lhsTitle.localizedCaseInsensitiveCompare(rhsTitle)

        if comparison != .orderedSame {
            return comparison == .orderedAscending
        }

        return lhs < rhs
    }

    private func containerSidebarDetail(for container: ContainerInfo, metadata: ContainerSidebarMetadata) -> String? {
        let normalized = metadata.normalized
        var parts: [String] = []

        if !normalized.displayName.isEmpty {
            parts.append("VMID \(container.vmid)")
        }

        if !normalized.labels.isEmpty {
            let preview = Array(normalized.labels.prefix(2))
            var labelSummary = preview.joined(separator: ", ")
            if normalized.labels.count > preview.count {
                labelSummary += " +\(normalized.labels.count - preview.count)"
            }
            parts.append(labelSummary)
        }

        if !container.dropins.isEmpty && parts.count < 2 {
            parts.append("\(container.dropins.count) drop-in\(container.dropins.count == 1 ? "" : "s")")
        }

        return parts.isEmpty ? nil : parts.joined(separator: " • ")
    }

    private func containerSidebarHelp(for container: ContainerInfo, metadata: ContainerSidebarMetadata) -> String {
        let normalized = metadata.normalized
        var parts = ["VMID \(container.vmid)"]

        if !normalized.group.isEmpty {
            parts.append("Group: \(normalized.group)")
        }

        if !normalized.labels.isEmpty {
            parts.append("Labels: \(normalized.labels.joined(separator: ", "))")
        }

        return parts.joined(separator: "\n")
    }

    // MARK: - Detail

    @ViewBuilder
    private var detail: some View {
        if needsOnboarding {
            WelcomeView()
        } else {
            switch selection {
            case .settings:
                SettingsView()
            case .git:
                GitView(siteDir: appState.configStore.config.siteDir)
            case .doctor:
                DoctorView()
            case .publish:
                PublishView()
            case .secrets:
                SecretsView()
            case .container(let vmid):
                if let c = appState.containers.first(where: { $0.vmid == vmid }) {
                    ContainerDetailView(container: c)
                } else {
                    dashboardEmptyState(
                        title: "Container not found",
                        message: "Refresh the site scan or choose a different container from the sidebar.",
                        icon: "questionmark.circle"
                    )
                }
            case nil:
                dashboardEmptyState(
                    title: "Select a workspace",
                    message: "Choose a container to inspect details, or use the global publish and secrets tools.",
                    icon: "sidebar.left"
                )
            }
        }
    }

    private func dashboardEmptyState(title: String, message: String, icon: String) -> some View {
        ZStack {
            ProxnixTheme.heroGradient
                .ignoresSafeArea()

            VStack(alignment: .leading, spacing: 18) {
                Image(systemName: icon)
                    .font(.system(size: 34, weight: .semibold))
                    .foregroundStyle(ProxnixTheme.accent)

                VStack(alignment: .leading, spacing: 6) {
                    Text(title)
                        .font(.system(size: 28, weight: .bold, design: .rounded))
                    Text(message)
                        .foregroundStyle(.secondary)
                        .frame(maxWidth: 420, alignment: .leading)
                }

                if !appState.containers.isEmpty {
                    LazyVGrid(columns: [GridItem(.adaptive(minimum: 120), spacing: 12, alignment: .leading)], alignment: .leading, spacing: 12) {
                        MetricTile("\(appState.containers.count)", label: "containers", icon: "shippingbox")
                        MetricTile("\(appState.containers.filter(\.hasSecretStore).count)", label: "with secrets", icon: "lock.fill")
                        MetricTile("\(appState.containers.reduce(0) { $0 + $1.dropins.count })", label: "drop-ins", icon: "doc.text")
                    }
                }
            }
            .padding(32)
            .frame(maxWidth: 560, alignment: .leading)
            .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 28, style: .continuous))
            .overlay(
                RoundedRectangle(cornerRadius: 28, style: .continuous)
                    .strokeBorder(ProxnixTheme.cardBorder)
            )
            .padding(32)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}
