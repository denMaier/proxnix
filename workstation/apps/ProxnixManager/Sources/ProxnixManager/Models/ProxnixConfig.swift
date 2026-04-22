import Foundation

struct ProxnixConfig: Equatable {
    var siteDir: String = ""
    var masterIdentity: String = "~/.ssh/id_ed25519"
    var hosts: String = ""
    var sshIdentity: String = ""
    var remoteDir: String = "/var/lib/proxnix"
    var remotePrivDir: String = "/var/lib/proxnix/private"
    var remoteHostRelayIdentity: String = "/etc/proxnix/host_relay_identity"
    var secretProvider: String = "embedded-sops"
    var secretProviderCommand: String = ""
    var scriptsDir: String = ""

    var hostList: [String] {
        hosts.split(separator: " ").map(String.init).filter { !$0.isEmpty }
    }
}
