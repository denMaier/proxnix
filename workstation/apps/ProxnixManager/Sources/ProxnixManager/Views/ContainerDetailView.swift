import SwiftUI
import AppKit

struct ContainerDetailView: View {
    @EnvironmentObject var appState: AppState
    let container: ContainerInfo
    @StateObject private var publishRunner = ShellRunner()
    @StateObject private var doctorRunner = ShellRunner()
    @State private var selectedDropin: String?
    @State private var dropinContent: String?
    @State private var newSecretGroup = ""
    @State private var groupEditError: String?
    @State private var pendingGroupRemoval: String?
    @State private var doctorResults: [DoctorSection] = []
    @State private var showMetadataPopover = false
    @State private var sidebarDisplayName = ""
    @State private var sidebarGroup = ""
    @State private var sidebarLabels = ""
    @State private var sidebarEditError: String?

    private var sidebarMetadata: ContainerSidebarMetadata {
        appState.sidebarMetadata(for: container.vmid)
    }

    private var sidebarDraft: ContainerSidebarMetadata {
        ContainerSidebarMetadata(
            displayName: sidebarDisplayName,
            group: sidebarGroup,
            labels: ContainerSidebarMetadata.parseLabels(from: sidebarLabels)
        )
    }

    private var sidebarDraftIsDirty: Bool {
        sidebarDraft.normalized != sidebarMetadata.normalized
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                header
                    .staggeredAppear(index: 0)

                statusRow
                    .staggeredAppear(index: 1)

                dropinsSection
                    .staggeredAppear(index: 2)

                secretGroupsSection
                    .staggeredAppear(index: 3)

                publishSection
                    .staggeredAppear(index: 4)
            }
            .padding(24)
        }
        .background(Color(nsColor: .controlBackgroundColor))
        .navigationTitle(sidebarMetadata.title(for: container.vmid))
        .onAppear {
            loadSidebarMetadata()
            if doctorResults.isEmpty && appState.doctorScript != nil {
                Task { await runContainerDoctor() }
            }
        }
        .onChange(of: container.vmid) { _ in
            loadSidebarMetadata()
        }
        .alert("Remove Group",
               isPresented: Binding(
                   get: { pendingGroupRemoval != nil },
                   set: { if !$0 { pendingGroupRemoval = nil } }
               )) {
            Button("Remove", role: .destructive) {
                if let group = pendingGroupRemoval {
                    removeSecretGroup(group)
                }
            }
            Button("Cancel", role: .cancel) {}
        } message: {
            if let group = pendingGroupRemoval {
                Text("Remove group \"\(group)\" from container \(container.vmid)?")
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
                    doctorBadge
                }

                VStack(alignment: .leading, spacing: 12) {
                    headerCopy
                    doctorBadge
                }
            }

            if !sidebarMetadata.normalized.labels.isEmpty {
                HStack(spacing: 6) {
                    ForEach(sidebarMetadata.normalized.labels, id: \.self) { label in
                        Text(label)
                            .font(.system(size: 10, weight: .semibold, design: .rounded))
                            .padding(.horizontal, 8)
                            .padding(.vertical, 3)
                            .background(ProxnixTheme.accentSubtle, in: Capsule())
                            .foregroundStyle(ProxnixTheme.accent)
                    }
                }
            }
        }
        .padding(24)
        .proxnixCard(tint: ProxnixTheme.accentSubtle.opacity(0.4))
    }

    private var headerCopy: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 8) {
                EyebrowLabel("Container", icon: "shippingbox.fill")

                Spacer()

                Button {
                    showMetadataPopover.toggle()
                } label: {
                    Image(systemName: "pencil.circle")
                        .font(.system(size: 16))
                        .foregroundStyle(.secondary)
                }
                .buttonStyle(.plain)
                .help("Edit display name, group, and labels")
                .popover(isPresented: $showMetadataPopover, arrowEdge: .bottom) {
                    metadataPopover
                }
            }

            if sidebarMetadata.displayName.isEmpty {
                Text(container.vmid)
                    .font(.system(size: 32, weight: .bold, design: .monospaced))
                    .lineLimit(1)
                    .minimumScaleFactor(0.8)
            } else {
                VStack(alignment: .leading, spacing: 4) {
                    Text(sidebarMetadata.displayName)
                        .font(.system(size: 30, weight: .bold, design: .rounded))
                        .lineLimit(1)
                        .minimumScaleFactor(0.8)

                    Text("VMID \(container.vmid)")
                        .font(.system(.caption, design: .monospaced).weight(.semibold))
                        .foregroundStyle(.secondary)
                }
            }

            Text("\(container.dropins.count) drop-in\(container.dropins.count == 1 ? "" : "s") configured")
                .foregroundStyle(.secondary)
        }
    }

    // MARK: - Metadata Popover

    private var metadataPopover: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Sidebar metadata")
                .font(.system(.headline, design: .rounded))

            VStack(alignment: .leading, spacing: 10) {
                VStack(alignment: .leading, spacing: 4) {
                    Text("Display name")
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(.secondary)
                    TextField("Optional friendly name", text: $sidebarDisplayName)
                        .textFieldStyle(.roundedBorder)
                        .onSubmit { saveSidebarMetadata() }
                }

                VStack(alignment: .leading, spacing: 4) {
                    Text("Group")
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(.secondary)
                    TextField("Optional sidebar group", text: $sidebarGroup)
                        .textFieldStyle(.roundedBorder)
                        .onSubmit { saveSidebarMetadata() }
                }

                VStack(alignment: .leading, spacing: 4) {
                    Text("Labels")
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(.secondary)
                    TextField("Comma-separated", text: $sidebarLabels)
                        .textFieldStyle(.roundedBorder)
                        .onSubmit { saveSidebarMetadata() }
                }
            }

            HStack(spacing: 8) {
                if let sidebarEditError {
                    Image(systemName: "exclamationmark.triangle.fill")
                        .font(.caption)
                        .foregroundStyle(ProxnixTheme.statusFail)
                        .help(sidebarEditError)
                }

                Spacer()

                if !sidebarMetadata.isEmpty {
                    Button("Clear") {
                        clearSidebarMetadata()
                    }
                    .controlSize(.small)
                }

                Button("Save") {
                    saveSidebarMetadata()
                    showMetadataPopover = false
                }
                .buttonStyle(.borderedProminent)
                .controlSize(.small)
                .disabled(!sidebarDraftIsDirty)
            }

            Text("App-local only. Does not modify your site repo.")
                .font(.caption2)
                .foregroundStyle(.tertiary)
        }
        .padding(16)
        .frame(width: 280)
    }

    // MARK: - Doctor Badge

    @ViewBuilder
    private var doctorBadge: some View {
        if doctorRunner.isRunning {
            HStack(spacing: 8) {
                ProgressView().controlSize(.small)
                Text("Checking\u{2026}")
                    .font(.system(size: 12, weight: .medium, design: .rounded))
                    .foregroundStyle(.secondary)
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 8)
            .background(.quaternary.opacity(0.5), in: Capsule())
        } else if let code = doctorRunner.lastExitCode {
            Button {
                Task { await runContainerDoctor() }
            } label: {
                Label(
                    code == 0 ? "Healthy" : (code == 1 ? "Warnings" : "Issues"),
                    systemImage: code == 0 ? "checkmark.seal.fill" : "exclamationmark.triangle.fill"
                )
                .font(.system(size: 12, weight: .semibold, design: .rounded))
            }
            .buttonStyle(.plain)
            .padding(.horizontal, 12)
            .padding(.vertical, 8)
            .background(
                (code == 0 ? ProxnixTheme.statusOk : (code == 1 ? ProxnixTheme.statusWarn : ProxnixTheme.statusFail)).opacity(0.12),
                in: Capsule()
            )
            .foregroundStyle(code == 0 ? ProxnixTheme.statusOk : (code == 1 ? ProxnixTheme.statusWarn : ProxnixTheme.statusFail))
            .help("Click to re-run doctor")
        }
    }

    // MARK: - Status Row

    private var statusRow: some View {
        LazyVGrid(
            columns: [GridItem(.adaptive(minimum: 140), spacing: 12, alignment: .leading)],
            alignment: .leading,
            spacing: 12
        ) {
            statusTile(
                title: "Identity",
                value: container.hasIdentity ? "Present" : "Missing",
                icon: "key.fill",
                color: container.hasIdentity ? .orange : .secondary
            )
            statusTile(
                title: "Secret store",
                value: container.hasSecretStore ? "Ready" : "Missing",
                icon: "lock.fill",
                color: container.hasSecretStore ? ProxnixTheme.statusOk : .secondary
            )
            statusTile(
                title: "Groups",
                value: container.secretGroups.isEmpty ? "None" : "\(container.secretGroups.count)",
                icon: "person.3.fill",
                color: container.secretGroups.isEmpty ? .secondary : .purple
            )
            statusTile(
                title: "Drop-ins",
                value: "\(container.dropins.count)",
                icon: "doc.text.fill",
                color: container.dropins.isEmpty ? .secondary : ProxnixTheme.accent
            )
        }
    }

    private func statusTile(title: String, value: String, icon: String, color: Color) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Image(systemName: icon)
                .font(.caption2)
                .foregroundStyle(color.opacity(0.7))
            Text(value)
                .font(.system(.title3, design: .monospaced).bold())
                .foregroundStyle(color == .secondary ? .secondary : .primary)
            Text(title)
                .font(.system(size: 11, weight: .medium))
                .foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(14)
        .proxnixCard(cornerRadius: 14)
    }

    // MARK: - Drop-ins

    private var dropinsSection: some View {
        VStack(alignment: .leading, spacing: 16) {
            sectionHeader(
                eyebrow: "Files",
                title: "Drop-ins",
                trailing: container.dropins.isEmpty ? nil : "\(container.dropins.count) file\(container.dropins.count == 1 ? "" : "s")"
            )

            if container.dropins.isEmpty {
                emptyState(
                    title: "No drop-ins",
                    detail: "Files will appear when the container has entries in containers/\(container.vmid)/dropins/."
                )
            } else {
                VStack(spacing: 0) {
                    ForEach(Array(container.dropins.enumerated()), id: \.element) { index, dropin in
                        if index > 0 { Divider().padding(.leading, 44) }
                        dropinRow(dropin)
                    }
                }
                .proxnixCard(cornerRadius: 14)

                if let content = dropinContent, let name = selectedDropin {
                    dropinViewer(name: name, content: content)
                }
            }
        }
        .padding(22)
        .proxnixCard()
    }

    private func dropinRow(_ dropin: String) -> some View {
        Button {
            if selectedDropin == dropin {
                selectedDropin = nil
                dropinContent = nil
            } else {
                loadDropin(dropin)
            }
        } label: {
            HStack(spacing: 12) {
                Image(systemName: selectedDropin == dropin ? "doc.text.fill" : "doc.text")
                    .font(.body)
                    .foregroundStyle(selectedDropin == dropin ? ProxnixTheme.accent : .secondary)
                    .frame(width: 24)

                Text(dropin)
                    .font(.system(.body, design: .monospaced))
                    .foregroundStyle(.primary)
                    .frame(maxWidth: .infinity, alignment: .leading)

                Image(systemName: selectedDropin == dropin ? "chevron.down" : "chevron.right")
                    .font(.caption.bold())
                    .foregroundStyle(.tertiary)
            }
            .padding(.horizontal, 14)
            .padding(.vertical, 10)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .background(selectedDropin == dropin ? ProxnixTheme.accentSubtle.opacity(0.4) : .clear)
    }

    private func dropinViewer(name: String, content: String) -> some View {
        VStack(spacing: 0) {
            HStack(spacing: 10) {
                Text(name)
                    .font(.system(.caption, design: .monospaced).bold())
                    .foregroundStyle(ProxnixTheme.accent)

                Spacer()

                Button {
                    NSPasteboard.general.clearContents()
                    NSPasteboard.general.setString(content, forType: .string)
                } label: {
                    Label("Copy", systemImage: "doc.on.doc")
                }
                .buttonStyle(.borderless)

                Button {
                    openInEditor(name)
                } label: {
                    Label("Open", systemImage: "arrow.up.forward.app")
                }
                .buttonStyle(.borderless)

                Button {
                    selectedDropin = nil
                    dropinContent = nil
                } label: {
                    Image(systemName: "xmark.circle.fill")
                }
                .buttonStyle(.borderless)
                .foregroundStyle(.secondary)
            }
            .padding(.horizontal, 14)
            .padding(.vertical, 10)
            .background(Color(nsColor: .windowBackgroundColor))

            Divider()

            ScrollView {
                Text(content)
                    .font(.system(size: 12, design: .monospaced))
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .textSelection(.enabled)
                    .padding(14)
            }
            .frame(maxHeight: 320)
            .background(Color(nsColor: .textBackgroundColor).opacity(0.55))
        }
        .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: 14, style: .continuous)
                .strokeBorder(ProxnixTheme.accent.opacity(0.15))
        )
    }

    // MARK: - Secret Groups

    private var secretGroupsSection: some View {
        VStack(alignment: .leading, spacing: 16) {
            sectionHeader(
                eyebrow: "Access",
                title: "Secret groups",
                trailing: container.secretGroups.isEmpty ? nil : "\(container.secretGroups.count) attached"
            )

            if !container.secretGroups.isEmpty {
                FlowLayout(container.secretGroups, spacing: 10) { group in
                    groupChip(group)
                }
            }

            // Attach / create
            VStack(alignment: .leading, spacing: 10) {
                ViewThatFits(in: .horizontal) {
                    HStack(spacing: 10) {
                        secretGroupField
                        addGroupButton
                    }

                    VStack(alignment: .leading, spacing: 10) {
                        secretGroupField
                        addGroupButton
                    }
                }

                if !attachableExistingGroups.isEmpty {
                    VStack(alignment: .leading, spacing: 8) {
                        Text("Available")
                            .font(.caption.weight(.semibold))
                            .foregroundStyle(.secondary)

                        FlowLayout(attachableExistingGroups, spacing: 8) { group in
                            Button {
                                addSecretGroup(named: group)
                            } label: {
                                Label(group, systemImage: "plus")
                                    .font(.system(.caption, design: .monospaced))
                            }
                            .buttonStyle(.plain)
                            .padding(.horizontal, 10)
                            .padding(.vertical, 6)
                            .background(Color(nsColor: .windowBackgroundColor), in: Capsule())
                            .overlay(Capsule().strokeBorder(ProxnixTheme.cardBorder))
                        }
                    }
                }

                if let groupEditError {
                    Label(groupEditError, systemImage: "exclamationmark.triangle.fill")
                        .font(.caption)
                        .foregroundStyle(ProxnixTheme.statusFail)
                }
            }
        }
        .padding(22)
        .proxnixCard()
    }

    private var secretGroupField: some View {
        TextField("Group name", text: $newSecretGroup)
            .textFieldStyle(.roundedBorder)
            .font(.system(.body, design: .monospaced))
            .onSubmit { addSecretGroup() }
    }

    private var addGroupButton: some View {
        Button {
            addSecretGroup()
        } label: {
            Label("Attach", systemImage: "plus.circle.fill")
        }
        .buttonStyle(.borderedProminent)
        .disabled(newSecretGroup.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
    }

    private var attachableExistingGroups: [String] {
        appState.definedSecretGroups.filter { !container.secretGroups.contains($0) }
    }

    @ViewBuilder
    private func groupChip(_ group: String) -> some View {
        let defined = appState.definedSecretGroups.contains(group)

        HStack(spacing: 6) {
            Text(group)
                .font(.system(.caption, design: .monospaced))

            if !defined {
                Image(systemName: "exclamationmark.circle")
                    .font(.system(size: 10, weight: .semibold))
                    .help("No store yet under private/groups/\(group)/")
            }

            Button(role: .destructive) {
                pendingGroupRemoval = group
            } label: {
                Image(systemName: "xmark.circle.fill")
                    .font(.caption2)
            }
            .buttonStyle(.plain)
            .help("Remove group")
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 7)
        .background((defined ? ProxnixTheme.accentSubtle : ProxnixTheme.statusWarn.opacity(0.14)), in: Capsule())
        .foregroundStyle(defined ? ProxnixTheme.accent : ProxnixTheme.statusWarn)
    }

    // MARK: - Publish

    private var publishSection: some View {
        VStack(alignment: .leading, spacing: 16) {
            ViewThatFits(in: .horizontal) {
                HStack(alignment: .top) {
                    sectionHeader(
                        eyebrow: "Deploy",
                        title: "Publish",
                        trailing: nil
                    )
                    Spacer()
                    publishActions
                }

                VStack(alignment: .leading, spacing: 12) {
                    sectionHeader(
                        eyebrow: "Deploy",
                        title: "Publish",
                        trailing: nil
                    )
                    publishActions
                }
            }

            PublishOptionsView(runner: publishRunner, vmid: container.vmid)
                .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
                .overlay(
                    RoundedRectangle(cornerRadius: 14, style: .continuous)
                        .strokeBorder(ProxnixTheme.cardBorder)
                )
        }
        .padding(22)
        .proxnixCard()
    }

    @ViewBuilder
    private var publishActions: some View {
        if appState.publishScript == nil {
            Label("proxnix-publish not found", systemImage: "exclamationmark.triangle.fill")
                .font(.system(size: 12, weight: .semibold, design: .rounded))
                .padding(.horizontal, 12)
                .padding(.vertical, 8)
                .background(ProxnixTheme.statusWarn.opacity(0.12), in: Capsule())
                .foregroundStyle(ProxnixTheme.statusWarn)
                .help("Check Scripts Dir in Settings")
        }
    }

    // MARK: - Shared Components

    private func sectionHeader(eyebrow: String, title: String, trailing: String?) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                EyebrowLabel(eyebrow)
                if let trailing {
                    Spacer()
                    Text(trailing)
                        .font(.system(size: 10, weight: .bold, design: .monospaced))
                        .foregroundStyle(.secondary)
                        .padding(.horizontal, 7)
                        .padding(.vertical, 2)
                        .background(.quaternary.opacity(0.5), in: Capsule())
                }
            }
            Text(title)
                .font(.system(.title3, design: .rounded).bold())
        }
    }

    private func emptyState(title: String, detail: String) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(title)
                .font(.system(.headline, design: .rounded))
            Text(detail)
                .font(.caption)
                .foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(16)
        .background(.quaternary.opacity(0.28), in: RoundedRectangle(cornerRadius: 16, style: .continuous))
    }

    // MARK: - Actions

    private func loadDropin(_ name: String) {
        let path = dropinPath(name)
        selectedDropin = name
        dropinContent = (try? String(contentsOfFile: path, encoding: .utf8)) ?? "Could not read file"
    }

    private func openInEditor(_ name: String) {
        let path = dropinPath(name)
        NSWorkspace.shared.open(URL(fileURLWithPath: path))
    }

    private func dropinPath(_ name: String) -> String {
        let siteDir = (appState.configStore.config.siteDir as NSString).expandingTildeInPath
        return URL(fileURLWithPath: siteDir)
            .appendingPathComponent("containers/\(container.vmid)/dropins/\(name)")
            .path
    }

    private func addSecretGroup() {
        let group = newSecretGroup.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !group.isEmpty else { return }
        addSecretGroup(named: group)
    }

    private func addSecretGroup(named group: String) {
        do {
            try appState.addSecretGroup(group, to: container.vmid)
            newSecretGroup = ""
            groupEditError = nil
        } catch {
            groupEditError = error.localizedDescription
        }
    }

    private func removeSecretGroup(_ group: String) {
        do {
            try appState.removeSecretGroup(group, from: container.vmid)
            groupEditError = nil
        } catch {
            groupEditError = error.localizedDescription
        }
    }

    private func runContainerDoctor() async {
        guard let script = appState.doctorScript else { return }
        doctorResults = []
        await doctorRunner.run(script: script, args: ["--site-only", "--vmid", container.vmid])
        doctorResults = DoctorView.parseOutput(doctorRunner.output)
    }

    private func loadSidebarMetadata() {
        let metadata = sidebarMetadata
        sidebarDisplayName = metadata.displayName
        sidebarGroup = metadata.group
        sidebarLabels = metadata.labels.joined(separator: ", ")
        sidebarEditError = nil
    }

    private func saveSidebarMetadata() {
        do {
            try appState.saveSidebarMetadata(sidebarDraft, for: container.vmid)
            loadSidebarMetadata()
            sidebarEditError = nil
        } catch {
            sidebarEditError = error.localizedDescription
        }
    }

    private func clearSidebarMetadata() {
        sidebarDisplayName = ""
        sidebarGroup = ""
        sidebarLabels = ""
        saveSidebarMetadata()
    }
}
