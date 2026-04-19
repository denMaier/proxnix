import Foundation

struct ContainerInfo: Identifiable, Comparable {
    var id: String { vmid }
    let vmid: String
    let dropins: [String]
    let hasSecretStore: Bool
    let hasIdentity: Bool
    let secretGroups: [String]

    static func < (lhs: ContainerInfo, rhs: ContainerInfo) -> Bool {
        // Sort numerically if possible, else lexicographically
        if let l = Int(lhs.vmid), let r = Int(rhs.vmid) { return l < r }
        return lhs.vmid < rhs.vmid
    }
}
