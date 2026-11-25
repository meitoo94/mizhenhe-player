import Foundation
import Security

/// 渠道号与首次安装时间放在 Keychain: UserDefaults 会随卸载清空,
/// Keychain 在本机保留, 卸载重装后仍能恢复到原来的渠道态。
enum KeychainStore {
    private static let service = "com.yourcompany.channelswitch"

    static func get(_ key: String) -> String? {
        let q: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: key,
            kSecReturnData as String: true,
            kSecMatchLimit as String: kSecMatchLimitOne
        ]
        var item: CFTypeRef?
        guard SecItemCopyMatching(q as CFDictionary, &item) == errSecSuccess,
              let data = item as? Data else { return nil }
        return String(data: data, encoding: .utf8)
    }

    @discardableResult
    static func set(_ value: String, for key: String) -> Bool {
        guard let data = value.data(using: .utf8) else { return false }
        let base: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: key,
            kSecAttrAccessible as String: kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
        ]
        var query = base
        let attrs: [String: Any] = [kSecValueData as String: data]
        if SecItemCopyMatching(query as CFDictionary, nil) == errSecSuccess {
            query.removeValue(forKey: kSecClass as String)
            return SecItemUpdate(base as CFDictionary, attrs as CFDictionary) == errSecSuccess
        }
        var add = base
        add[kSecValueData as String] = data
        return SecItemAdd(add as CFDictionary, nil) == errSecSuccess
    }

    static func remove(_ key: String) {
        let q: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: key
        ]
        SecItemDelete(q as CFDictionary)
    }

    // MARK: 安装时间(用于服务端 start_after_hours 守卫)
    static func installTimestamp() -> Int {
        let k = "install_ts"
        if let s = get(k), let t = Int(s) { return t }
        let t = Int(Date().timeIntervalSince1970)
        set(String(t), for: k)
        return t
    }
}
