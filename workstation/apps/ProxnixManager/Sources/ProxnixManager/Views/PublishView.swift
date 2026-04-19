import SwiftUI

struct PublishView: View {
    @StateObject private var runner = ShellRunner()

    var body: some View {
        PublishOptionsView(runner: runner, vmid: nil)
            .navigationTitle("Publish All")
    }
}
