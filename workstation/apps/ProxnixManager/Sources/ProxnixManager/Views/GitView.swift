import SwiftUI

struct GitView: View {
    @EnvironmentObject var appState: AppState
    @StateObject private var git: GitService
    @State private var commitMessage = ""
    @State private var logOutput = ""
    @State private var logTitle = ""
    @State private var showLog = false
    @State private var alertMessage = ""
    @State private var showAlert = false

    init(siteDir: String) {
        _git = StateObject(wrappedValue: GitService(siteDir: siteDir))
    }

    var body: some View {
        VStack(spacing: 0) {
            controls
            Divider()
            if showLog {
                LogView(output: logOutput, isRunning: git.isRunning, exitCode: nil)
                    .frame(minHeight: 160)
            }
        }
        .background(Color(nsColor: .windowBackgroundColor))
        .navigationTitle("Git")
        .task { await git.refresh() }
    }

    // MARK: - Controls

    private var controls: some View {
        VStack(alignment: .leading, spacing: 18) {
            // Header with refresh
            ViewThatFits(in: .horizontal) {
                HStack(alignment: .top) {
                    headerText
                    Spacer()
                    refreshButton
                }
                VStack(alignment: .leading, spacing: 6) {
                    headerText
                    refreshButton
                }
            }

            if !git.status.isRepo {
                noRepoContent
            } else {
                repoContent
            }
        }
        .padding(18)
    }

    private var headerText: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("Site Repository")
                .font(.system(.title3, design: .rounded).bold())
            if git.status.isRepo {
                HStack(spacing: 12) {
                    Label(git.status.branch.isEmpty ? "(detached)" : git.status.branch,
                          systemImage: "arrow.triangle.branch")
                        .foregroundStyle(.secondary)
                    if git.status.hasRemote {
                        trackingBadge
                    }
                }
                .font(.caption)
            } else if git.isRunning {
                Text("Checking repository status...")
                    .foregroundStyle(.secondary)
            } else {
                Text("No git repository found")
                    .foregroundStyle(.secondary)
            }
        }
    }

    private var refreshButton: some View {
        Button {
            Task { await git.refresh() }
        } label: {
            Label("Refresh", systemImage: "arrow.clockwise")
        }
        .disabled(git.isRunning)
    }

    @ViewBuilder
    private var trackingBadge: some View {
        let st = git.status
        if st.ahead == 0 && st.behind == 0 {
            Label("Up to date", systemImage: "checkmark.circle.fill")
                .foregroundStyle(ProxnixTheme.statusOk)
        } else {
            HStack(spacing: 8) {
                if st.ahead > 0 {
                    Label("\(st.ahead) ahead", systemImage: "arrow.up")
                        .foregroundStyle(ProxnixTheme.statusWarn)
                }
                if st.behind > 0 {
                    Label("\(st.behind) behind", systemImage: "arrow.down")
                        .foregroundStyle(ProxnixTheme.statusFail)
                }
            }
        }
    }

    // MARK: - No Repo

    private var noRepoContent: some View {
        VStack(alignment: .leading, spacing: 16) {
            if let error = git.status.error, error != "Not a git repository" {
                Label(error, systemImage: "exclamationmark.triangle.fill")
                    .foregroundStyle(.orange)
            }

            VStack(alignment: .leading, spacing: 8) {
                Label("No git repository", systemImage: "folder.badge.questionmark")
                    .font(.headline)
                Text("The site directory is not tracked by git. Initialize a repository to enable commit and push workflows.")
                    .foregroundStyle(.secondary)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(16)
            .proxnixCard(tint: ProxnixTheme.surfaceTint, cornerRadius: 18)

            Button {
                Task { await initRepo() }
            } label: {
                Label("Initialize Repository", systemImage: "plus.circle.fill")
            }
            .buttonStyle(.borderedProminent)
            .disabled(git.isRunning)
        }
    }

    private func initRepo() async {
        let (ok, output) = await git.initRepo()
        logOutput = output
        logTitle = "Git Init"
        showLog = true
        if ok {
            await git.refresh()
        }
    }

    // MARK: - Repo Content

    @ViewBuilder
    private var repoContent: some View {
        let st = git.status

        // Metric tiles
        LazyVGrid(columns: [GridItem(.adaptive(minimum: 120), spacing: 12, alignment: .leading)], alignment: .leading, spacing: 12) {
            MetricTile("\(st.staged.count)", label: "staged", icon: "checkmark.circle")
            MetricTile("\(st.unstaged.count)", label: "modified", icon: "pencil.circle")
            MetricTile("\(st.untracked.count)", label: "untracked", icon: "questionmark.circle")
            MetricTile("\(st.ahead)", label: "ahead", icon: "arrow.up.circle")
        }

        // File cards
        LazyVGrid(columns: [GridItem(.adaptive(minimum: 280), spacing: 16, alignment: .top)], alignment: .leading, spacing: 16) {
            if !st.staged.isEmpty {
                fileListCard(title: "Staged", files: st.staged, color: ProxnixTheme.statusOk)
            }
            if !st.unstaged.isEmpty {
                fileListCard(title: "Modified", files: st.unstaged, color: ProxnixTheme.statusWarn)
            }
            if !st.untracked.isEmpty {
                fileListCard(title: "Untracked", files: st.untracked, color: ProxnixTheme.statusInfo)
            }
        }

        // Actions
        VStack(alignment: .leading, spacing: 12) {
            Text("Actions")
                .font(.headline)

            // Commit controls
            HStack(spacing: 10) {
                TextField("Commit message", text: $commitMessage)
                    .textFieldStyle(.roundedBorder)
                    .onSubmit { Task { await commitFlow() } }

                if !st.staged.isEmpty || !st.unstaged.isEmpty || !st.untracked.isEmpty {
                    Button {
                        Task { await stageAndCommit() }
                    } label: {
                        Label("Stage & Commit", systemImage: "plus.circle.fill")
                    }
                    .disabled(commitMessage.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || git.isRunning)
                    .help("Stage all changes and commit")
                }

                if !st.staged.isEmpty {
                    Button {
                        Task { await commitFlow() }
                    } label: {
                        Label("Commit", systemImage: "checkmark.circle.fill")
                    }
                    .buttonStyle(.borderedProminent)
                    .disabled(commitMessage.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || git.isRunning)
                }
            }

            // Push
            HStack(spacing: 10) {
                if st.hasRemote && st.ahead > 0 {
                    Button {
                        Task { await pushFlow() }
                    } label: {
                        Label("Push \(st.ahead) commit\(st.ahead == 1 ? "" : "s")", systemImage: "arrow.up.circle.fill")
                    }
                    .buttonStyle(.borderedProminent)
                    .tint(.orange)
                    .disabled(git.isRunning)
                } else if !st.hasRemote {
                    Label("No upstream remote configured", systemImage: "exclamationmark.triangle")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }

                Spacer()

                Button {
                    Task { await viewDiff() }
                } label: {
                    Label("View Diff", systemImage: "doc.text.magnifyingglass")
                }
                .disabled(git.isRunning)
            }
        }
        .padding(16)
        .proxnixCard(tint: ProxnixTheme.surfaceTint, cornerRadius: 18)

        if git.isRunning {
            HStack {
                ProgressView().controlSize(.small)
                Text("Working...")
                    .foregroundStyle(.secondary)
            }
        }
    }

    private func fileListCard(title: String, files: [GitFileEntry], color: Color) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text(title)
                    .font(.headline)
                Spacer()
                Text("\(files.count)")
                    .font(.system(.caption, design: .monospaced).bold())
                    .foregroundStyle(color)
                    .padding(.horizontal, 7)
                    .padding(.vertical, 2)
                    .background(color.opacity(0.12), in: Capsule())
            }

            ForEach(files.prefix(8)) { file in
                HStack(spacing: 6) {
                    Text(file.flag)
                        .font(.system(.caption, design: .monospaced).bold())
                        .foregroundStyle(color)
                        .frame(width: 16)
                    Text(file.path)
                        .font(.system(.caption, design: .monospaced))
                        .lineLimit(1)
                        .truncationMode(.middle)
                }
            }
            if files.count > 8 {
                Text("... and \(files.count - 8) more")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(16)
        .proxnixCard(tint: ProxnixTheme.surfaceTint, cornerRadius: 18)
    }

    // MARK: - Actions

    private func stageAndCommit() async {
        let (stageOk, stageMsg) = await git.stageAll()
        if !stageOk {
            alertMessage = "Stage failed: \(stageMsg)"
            showAlert = true
            return
        }
        await git.refresh()
        await commitFlow()
    }

    private func commitFlow() async {
        let msg = commitMessage.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !msg.isEmpty else { return }

        let (ok, output) = await git.commit(message: msg)
        logOutput = output
        logTitle = "Git Commit"
        showLog = true

        if ok {
            commitMessage = ""
        } else {
            alertMessage = "Commit failed: \(output)"
            showAlert = true
        }
        await git.refresh()
    }

    private func pushFlow() async {
        let (_, output) = await git.push()
        logOutput = output
        logTitle = "Git Push"
        showLog = true
        await git.refresh()
    }

    private func viewDiff() async {
        let diff = await git.diffSummary()
        logOutput = diff
        logTitle = "Diff Summary"
        showLog = true
    }
}
