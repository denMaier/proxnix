import Foundation

struct GitFileEntry: Identifiable, Hashable {
    let id = UUID()
    let flag: String
    let path: String
}

struct GitRepoStatus {
    var isRepo = false
    var branch = ""
    var staged: [GitFileEntry] = []
    var unstaged: [GitFileEntry] = []
    var untracked: [GitFileEntry] = []
    var ahead = 0
    var behind = 0
    var hasRemote = false
    var error: String?

    var isClean: Bool {
        staged.isEmpty && unstaged.isEmpty && untracked.isEmpty
    }

    var totalChanges: Int {
        staged.count + unstaged.count + untracked.count
    }
}

/// Runs git commands against the proxnix site directory.
@MainActor
class GitService: ObservableObject {
    @Published var status = GitRepoStatus()
    @Published var isRunning = false
    @Published var lastOutput = ""

    private let siteDir: String

    init(siteDir: String) {
        self.siteDir = (siteDir as NSString).expandingTildeInPath
    }

    // MARK: - Public API

    func initRepo() async -> (Bool, String) {
        let result = await runGit("init")
        let output = result.output.trimmingCharacters(in: .whitespacesAndNewlines)
        return (result.exitCode == 0, output)
    }

    func refresh() async {
        guard !isRunning else { return }
        isRunning = true
        defer { isRunning = false }

        var st = GitRepoStatus()

        // Check if it's a git repo
        let checkResult = await runGit("rev-parse", "--is-inside-work-tree")
        guard checkResult.exitCode == 0 else {
            st.error = "Not a git repository"
            status = st
            return
        }
        st.isRepo = true

        // Branch
        let branchResult = await runGit("branch", "--show-current")
        if branchResult.exitCode == 0 {
            st.branch = branchResult.output.trimmingCharacters(in: .whitespacesAndNewlines)
        }

        // Porcelain status
        let statusResult = await runGit("status", "--porcelain=v1", "-u")
        if statusResult.exitCode == 0 {
            for line in statusResult.output.components(separatedBy: .newlines) {
                guard line.count >= 3 else { continue }
                let indexFlag = line[line.startIndex]
                let worktreeFlag = line[line.index(after: line.startIndex)]
                let path = String(line.dropFirst(3))

                if indexFlag == "?" {
                    st.untracked.append(GitFileEntry(flag: "?", path: path))
                } else {
                    if indexFlag != " " {
                        st.staged.append(GitFileEntry(flag: String(indexFlag), path: path))
                    }
                    if worktreeFlag != " " {
                        st.unstaged.append(GitFileEntry(flag: String(worktreeFlag), path: path))
                    }
                }
            }
        }

        // Ahead/behind
        let upstreamResult = await runGit("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
        if upstreamResult.exitCode == 0 {
            st.hasRemote = true
            let upstream = upstreamResult.output.trimmingCharacters(in: .whitespacesAndNewlines)
            let countResult = await runGit("rev-list", "--left-right", "--count", "HEAD...\(upstream)")
            if countResult.exitCode == 0 {
                let parts = countResult.output.trimmingCharacters(in: .whitespacesAndNewlines)
                    .components(separatedBy: .whitespaces)
                    .filter { !$0.isEmpty }
                if parts.count == 2 {
                    st.ahead = Int(parts[0]) ?? 0
                    st.behind = Int(parts[1]) ?? 0
                }
            }
        }

        status = st
    }

    func diffSummary() async -> String {
        var parts: [String] = []

        let staged = await runGit("diff", "--cached", "--stat")
        let trimmedStaged = staged.output.trimmingCharacters(in: .whitespacesAndNewlines)
        if staged.exitCode == 0 && !trimmedStaged.isEmpty {
            parts.append("Staged changes:\n\(trimmedStaged)")
        }

        let unstaged = await runGit("diff", "--stat")
        let trimmedUnstaged = unstaged.output.trimmingCharacters(in: .whitespacesAndNewlines)
        if unstaged.exitCode == 0 && !trimmedUnstaged.isEmpty {
            parts.append("Unstaged changes:\n\(trimmedUnstaged)")
        }

        return parts.isEmpty ? "No changes." : parts.joined(separator: "\n\n")
    }

    func stageAll() async -> (Bool, String) {
        let result = await runGit("add", "-A")
        if result.exitCode != 0 {
            return (false, result.output.trimmingCharacters(in: .whitespacesAndNewlines))
        }
        return (true, "All changes staged.")
    }

    func commit(message: String) async -> (Bool, String) {
        guard !message.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            return (false, "Commit message cannot be empty.")
        }
        let result = await runGit("commit", "-m", message)
        let output = result.output.trimmingCharacters(in: .whitespacesAndNewlines)
        return (result.exitCode == 0, output)
    }

    func push() async -> (Bool, String) {
        let result = await runGit("push")
        let output = (result.output + result.stderr).trimmingCharacters(in: .whitespacesAndNewlines)
        return (result.exitCode == 0, output.isEmpty ? "Pushed successfully." : output)
    }

    // MARK: - Private

    private struct GitResult {
        let exitCode: Int32
        let output: String
        let stderr: String
    }

    private nonisolated func runGit(_ args: String...) async -> GitResult {
        let siteDir = self.siteDir
        return await withCheckedContinuation { continuation in
            DispatchQueue.global(qos: .userInitiated).async {
                let task = Process()
                let outPipe = Pipe()
                let errPipe = Pipe()

                task.executableURL = URL(fileURLWithPath: "/usr/bin/git")
                task.arguments = Array(args)
                task.currentDirectoryURL = URL(fileURLWithPath: siteDir)
                task.standardOutput = outPipe
                task.standardError = errPipe
                task.environment = Self.gitEnvironment()

                do {
                    try task.run()
                } catch {
                    continuation.resume(returning: GitResult(exitCode: -1, output: error.localizedDescription, stderr: ""))
                    return
                }

                task.waitUntilExit()

                let outData = outPipe.fileHandleForReading.readDataToEndOfFile()
                let errData = errPipe.fileHandleForReading.readDataToEndOfFile()
                let output = String(data: outData, encoding: .utf8) ?? ""
                let stderr = String(data: errData, encoding: .utf8) ?? ""

                continuation.resume(returning: GitResult(exitCode: task.terminationStatus, output: output, stderr: stderr))
            }
        }
    }

    private nonisolated static func gitEnvironment() -> [String: String] {
        var env = ProcessInfo.processInfo.environment
        let extraPaths = ["/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin"]
        let existing = (env["PATH"] ?? "").components(separatedBy: ":").filter { !$0.isEmpty }
        let merged = (extraPaths + existing).reduce(into: [String]()) { acc, p in
            if !acc.contains(p) { acc.append(p) }
        }
        env["PATH"] = merged.joined(separator: ":")
        env["HOME"] = FileManager.default.homeDirectoryForCurrentUser.path
        return env
    }
}
