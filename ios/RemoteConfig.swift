import Foundation
import UIKit
import CryptoKit

/// 远程配置: 决定这一台设备此刻跑哪套业务 UI。
///
/// 设计原则(关键):
///  1. 默认态永远是 normal。无网 / 超时 / 验签失败 / 渠道未知 / 服务端 5xx
///     全部回落 normal, 不会「因为出错反而打开付费墙」。
///  2. 二进制里两套 UI 都编译进去, 但没有任何硬编码的渠道判断、
///     没有写死的视频 URL / 价格 / 倒计时秒数 —— 全部由配置下发。
///  3. 配置带 HMAC 签名, 防止中间人篡改或重放旧配置。
///  4. 决策在服务端, 客户端只执行结果。改渠道开关不需要发版。
@MainActor
final class ConfigStore: ObservableObject {

    @Published private(set) var payload: ConfigPayload = .fallbackNormal
    @Published private(set) var channel: String = "organic"
    @Published private(set) var ready: Bool = false

    // 演示用共享密钥。生产改用「服务端私钥签 Ed25519 + 客户端内置公钥验」,
    // 避免对称密钥被从二进制里抠出来伪造配置。
    /// 签名密钥可由 Info.plist 的 ConfigHMACSecret 注入；缺省与服务端 MZH_CONFIG_SECRET 默认值一致。
    private let hmacSecret: String = {
        if let s = Bundle.main.object(forInfoDictionaryKey: "ConfigHMACSecret") as? String,
           !s.isEmpty, !s.hasPrefix("$(") { return s }
        return "demo-secret-change-in-production"
    }()

    private let cacheKey = "remote_config_envelope"
    private let cacheTimeKey = "remote_config_cached_at"

    var isPaywall: Bool { payload.isPaywall }

    // MARK: - 启动流程

    func bootstrap() async {
        // 先用本地缓存秒开, 再后台刷新(缓存过期就先按 normal 渲染)
        if let cached = loadCache(), !isExpired(cached.envelope) {
            applyVerified(cached.envelope)
        }
        channel = await ChannelAttribution.shared.currentChannel()
        await refresh()
        ready = true
    }

    func refresh() async {
        let body: [String: Any] = [
            "idfv": UIDevice.current.identifierForVendor?.uuidString ?? "",
            "channel": channel,
            // 让服务端用百度推广回传的 IP / 设备号复核渠道号, 以服务端绑定为准
            "auto_attribution": true,
            "country": Locale.current.region?.identifier.lowercased() ?? "cn",
            "build": Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "",
            "install_ts": KeychainStore.installTimestamp()
        ]
        do {
            let data = try await ChannelAttribution.shared.postJSON(
                path: "/v1/config", body: body, timeout: 4)
            let envelope = try JSONDecoder().decode(ConfigEnvelope.self, from: data)
            let trusted = verify(envelope)
            if trusted {
                saveCache(envelope)
                applyVerified(envelope, trusted: true)
            } else {
                // 密钥不同步 / 时间戳偏差时不能把整台设备打回空壳皮肤：
                // 照用服务端下发的 normal 皮肤，但强制留在合规态，绝不打开付费墙。
                clearCache()
                applyVerified(envelope, trusted: false)
            }
        } catch {
            // 超时/无网: 保留未过期缓存, 否则合规态
            if let c = loadCache(), isExpired(c.envelope) {
                clearCache()
                payload = .fallbackNormal
            }
        }
    }

    private func applyVerified(_ e: ConfigEnvelope, trusted: Bool = true) {
        guard let obj = try? JSONDecoder().decode(ConfigPayload.self,
                                                  from: Data(e.config.utf8)) else { return }
        if obj.schema != 1 { return }
        // 只有验签通过 + 服务端判定 paywall + 配置体完整时才切态
        if obj.isPaywall && trusted {
            payload = obj
        } else {
            payload = ConfigPayload(schema: obj.schema, mode: "normal", channel: obj.channel,
                                    issuedAt: obj.issuedAt, ttl: obj.ttl,
                                    normal: obj.normal ?? .neutral(tabs: SkinDefaults.tabs,
                                                                   headline: SkinDefaults.headline),
                                    paywall: nil)
        }
    }

    // MARK: - 验签

    private func verify(_ e: ConfigEnvelope) -> Bool {
        let key = SymmetricKey(data: Data(hmacSecret.utf8))
        let mac = HMAC<SHA256>.authenticationCode(for: Data(e.config.utf8), using: key)
        let hex = mac.map { String(format: "%02x", $0) }.joined()
        guard hex == e.sig else { return false }
        // 时间戳新鲜度, 阻断旧配置重放
        guard let obj = try? JSONSerialization.jsonObject(with: Data(e.config.utf8)) as? [String: Any],
              let issued = obj["issued_at"] as? Int else { return false }
        return abs(Int(Date().timeIntervalSince1970) - issued) < 3600
    }

    // MARK: - 缓存

    private func saveCache(_ e: ConfigEnvelope) {
        UserDefaults.standard.set(e.config, forKey: cacheKey)
        UserDefaults.standard.set(e.sig, forKey: cacheKey + "_sig")
        UserDefaults.standard.set(Int(Date().timeIntervalSince1970), forKey: cacheTimeKey)
    }

    private func loadCache() -> (envelope: ConfigEnvelope, at: Int)? {
        let d = UserDefaults.standard
        guard let c = d.string(forKey: cacheKey), let s = d.string(forKey: cacheKey + "_sig") else {
            return nil
        }
        return (ConfigEnvelope(config: c, sig: s), d.integer(forKey: cacheTimeKey))
    }

    private func isExpired(_ e: ConfigEnvelope) -> Bool {
        guard let obj = try? JSONSerialization.jsonObject(with: Data(e.config.utf8)) as? [String: Any],
              let ttl = obj["ttl"] as? Int else { return true }
        let at = UserDefaults.standard.integer(forKey: cacheTimeKey)
        return Int(Date().timeIntervalSince1970) - at > ttl
    }

    private func clearCache() {
        let d = UserDefaults.standard
        d.removeObject(forKey: cacheKey)
        d.removeObject(forKey: cacheKey + "_sig")
        d.removeObject(forKey: cacheTimeKey)
    }
}
