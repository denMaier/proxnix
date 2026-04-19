import Foundation

/// Runs a shell script as a subprocess and streams stdout+stderr in real time.
@MainActor
class ShellRunner: ObservableObject {
    @Published var output: String = ""
    @Published var isRunning: Bool = false
    @Published var lastExitCode: Int32? = nil

    private var process: Process?

    func run(script: String, args: [String] = [], stdin stdinText: String? = nil) async {
        guard !isRunning else { return }
        output = ""
        lastExitCode = nil
        isRunning = true

        let task = Process()
        let outPipe = Pipe()
        let errPipe = Pipe()

        task.executableURL = URL(fileURLWithPath: "/bin/bash")
        task.arguments = [script] + args
        task.standardOutput = outPipe
        task.standardError = errPipe
        task.environment = enrichedEnvironment()

        if let stdinText {
            let inPipe = Pipe()
            task.standardInput = inPipe
            if let data = stdinText.data(using: .utf8) {
                inPipe.fileHandleForWriting.write(data)
            }
            inPipe.fileHandleForWriting.closeFile()
        }

        process = task

        outPipe.fileHandleForReading.readabilityHandler = { [weak self] fh in
            let data = fh.availableData
            guard !data.isEmpty, let text = String(data: data, encoding: .utf8) else { return }
            Task { @MainActor [weak self] in self?.output += text }
        }
        errPipe.fileHandleForReading.readabilityHandler = { [weak self] fh in
            let data = fh.availableData
            guard !data.isEmpty, let text = String(data: data, encoding: .utf8) else { return }
            Task { @MainActor [weak self] in self?.output += text }
        }

        do {
            try task.run()
        } catch {
            output += "error: could not launch process: \(error.localizedDescription)\n"
            isRunning = false
            return
        }

        await withCheckedContinuation { (cont: CheckedContinuation<Void, Never>) in
            task.terminationHandler = { _ in cont.resume() }
        }

        outPipe.fileHandleForReading.readabilityHandler = nil
        errPipe.fileHandleForReading.readabilityHandler = nil

        // Drain any remaining buffered output
        let remaining = outPipe.fileHandleForReading.readDataToEndOfFile()
        let errRemaining = errPipe.fileHandleForReading.readDataToEndOfFile()
        if let t = String(data: remaining, encoding: .utf8), !t.isEmpty { output += t }
        if let t = String(data: errRemaining, encoding: .utf8), !t.isEmpty { output += t }

        lastExitCode = task.terminationStatus
        isRunning = false
    }

    func cancel() {
        process?.interrupt()
    }

    func clear() {
        output = ""
        lastExitCode = nil
    }

    // Ensure GUI-launched apps have tool paths that would be in a login shell.
    private func enrichedEnvironment() -> [String: String] {
        var env = ProcessInfo.processInfo.environment
        let extraPaths = [
            "/opt/homebrew/bin",
            "/opt/homebrew/sbin",
            "/usr/local/bin",
            "/usr/bin",
            "/bin",
            "/usr/sbin",
            "/sbin",
        ]
        let existing = (env["PATH"] ?? "").components(separatedBy: ":").filter { !$0.isEmpty }
        let merged = (extraPaths + existing).reduce(into: [String]()) { acc, p in
            if !acc.contains(p) { acc.append(p) }
        }
        env["PATH"] = merged.joined(separator: ":")
        env["HOME"] = FileManager.default.homeDirectoryForCurrentUser.path
        env["TERM"] = "dumb"
        return env
    }
}
