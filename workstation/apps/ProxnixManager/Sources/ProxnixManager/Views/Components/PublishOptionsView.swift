import SwiftUI

/// Reusable publish controls: toggles, target list, run/cancel button, and log output.
struct PublishOptionsView: View {
    @EnvironmentObject var appState: AppState
    @ObservedObject var runner: ShellRunner

    let vmid: String?  // nil = publish all

    @State private var dryRun = false
    @State private var configOnly = false
    @State private var reportChanges = true

    private var hosts: [String] {
        appState.configStore.config.hostList
    }

    var body: some View {
        VStack(spacing: 0) {
            controls
            Divider()
            LogView(
                output: runner.output,
                isRunning: runner.isRunning,
                exitCode: runner.lastExitCode
            )
            .frame(minHeight: 240)
        }
        .background(Color(nsColor: .windowBackgroundColor))
    }

    private var controls: some View {
        VStack(alignment: .leading, spacing: 18) {
            ViewThatFits(in: .horizontal) {
                HStack(alignment: .top) {
                    VStack(alignment: .leading, spacing: 6) {
                        Text(vmid == nil ? "Publish the full site" : "Publish container \(vmid!)")
                            .font(.system(.title3, design: .rounded).bold())
                        Text(vmid == nil ? "Run a full publish across configured hosts." : "Run a narrower publish against only this container.")
                            .foregroundStyle(.secondary)
                    }
                    Spacer()
                    runButtonCluster
                }

                VStack(alignment: .leading, spacing: 6) {
                    VStack(alignment: .leading, spacing: 6) {
                        Text(vmid == nil ? "Publish the full site" : "Publish container \(vmid!)")
                            .font(.system(.title3, design: .rounded).bold())
                        Text(vmid == nil ? "Run a full publish across configured hosts." : "Run a narrower publish against only this container.")
                            .foregroundStyle(.secondary)
                    }
                    runButtonCluster
                }
            }

            LazyVGrid(columns: [GridItem(.adaptive(minimum: 280), spacing: 16, alignment: .top)], alignment: .leading, spacing: 16) {
                VStack(alignment: .leading, spacing: 12) {
                    Text("Options")
                        .font(.headline)

                    Toggle("Dry run", isOn: $dryRun)
                        .help("Preview actions without writing anything remote")
                    Toggle("Config only", isOn: $configOnly)
                        .help("Sync Nix config; skip secrets and identities")
                    Toggle("Report changes", isOn: $reportChanges)
                        .help("Show which remote files changed")
                }
                .toggleStyle(.checkbox)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(16)
                .proxnixCard(tint: ProxnixTheme.surfaceTint, cornerRadius: 18)

                VStack(alignment: .leading, spacing: 12) {
                    Text(vmid == nil ? "Targets" : "Scope")
                        .font(.headline)

                    if vmid == nil {
                        if hosts.isEmpty {
                            Label("No hosts configured", systemImage: "exclamationmark.triangle.fill")
                                .foregroundStyle(.orange)
                            Text("Set `SSH Hosts` in Settings before running a full publish.")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        } else {
                            Label("\(hosts.count) host" + (hosts.count == 1 ? "" : "s"), systemImage: "server.rack")
                                .foregroundStyle(.secondary)
                            Text(hosts.joined(separator: "\n"))
                                .font(.system(.caption, design: .monospaced))
                                .frame(maxWidth: .infinity, alignment: .leading)
                                .textSelection(.enabled)
                        }
                    } else {
                        Label("Container \(vmid!)", systemImage: "shippingbox.fill")
                            .foregroundStyle(.secondary)
                        Text("This run will pass `--vmid \(vmid!)` to `proxnix-publish`.")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }

                    if appState.publishScript == nil {
                        Label("`proxnix-publish` not found", systemImage: "exclamationmark.triangle.fill")
                            .font(.caption)
                            .foregroundStyle(.orange)
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(16)
                .proxnixCard(tint: ProxnixTheme.surfaceTint, cornerRadius: 18)
            }
        }
        .padding(18)
    }

    private var runButtonCluster: some View {
        HStack(spacing: 10) {
            if !runner.output.isEmpty && !runner.isRunning {
                Button("Clear") { runner.clear() }
                    .foregroundStyle(.secondary)
            }

            if runner.isRunning {
                Button("Cancel", role: .destructive) { runner.cancel() }
                ProgressView()
                    .controlSize(.small)
            } else {
                Button {
                    Task { await publish() }
                } label: {
                    Label(vmid == nil ? "Publish All" : "Publish", systemImage: "arrow.up.circle.fill")
                }
                .buttonStyle(.borderedProminent)
                .disabled(appState.publishScript == nil)
            }
        }
    }

    private func publish() async {
        guard let script = appState.publishScript else { return }
        var args: [String] = []
        if dryRun        { args.append("--dry-run") }
        if configOnly    { args.append("--config-only") }
        if reportChanges { args.append("--report-changes") }
        if let vmid {
            args += ["--vmid", vmid]
        }
        await runner.run(script: script, args: args)
    }
}
