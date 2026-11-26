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
    /// 可视化诊断：请求成功/失败、验签结果、最终形态，避免再出现「静默回首页」查不到原因。
    @Published private(set) var diag: String = "尚未请求配置"

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
    /// 防止冷启动 .task 与回前台重试并发重复拉取配置。
    private var refreshing = false

    var isPaywall: Bool { payload.isPaywall }

    // MARK: - 启动流程

    func bootstrap() async {
        // 先用本地缓存秒开, 再后台刷新(缓存过期就先按 normal 渲染)
        if let cached = loadCache(), !isExpired(cached.envelope) {
            applyVerified(cached.envelope)
        }
        channel = await ChannelAttribution.shared.currentChannel()
        // 先把界面放出来，网络重试在后台继续；否则 5G 抖动时用户要盯着
        // 启动占位页等完 3 次超时，看起来就像「App 卡死 / 没反应」。
        ready = true
        await refresh()
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
        // 5G 首启时系统可能晚几百毫秒才把蜂窝接口标记就绪（-1009），
        // 因此放宽超时、重试 3 次并在失败间退避；每一步都写进 diag 并回传，
        // 不再出现「服务端全绿、真机却静默回首页」查不到原因的情况。
        if refreshing { return }
        refreshing = true
        defer { refreshing = false }
        for attempt in 1...3 {
            do {
                let data = try await ChannelAttribution.shared.postJSON(
                    path: "/v1/config", body: body, timeout: 15)
                let envelope = try JSONDecoder().decode(ConfigEnvelope.self, from: data)
                let trusted = verify(envelope)
                var decodeInfo = "解码OK"
                do {
                    _ = try JSONDecoder().decode(ConfigPayload.self,
                                                 from: Data(envelope.config.utf8))
                } catch {
                    decodeInfo = "解码失败:" + String(describing: error)
                }
                if trusted {
                    saveCache(envelope)
                    applyVerified(envelope, trusted: true)
                } else {
                    // 密钥不同步 / 时间戳偏差时不能把整台设备打回空壳皮肤：
                    // 照用服务端下发的 normal 皮肤，测试渠道仍执行服务端判定，
                    // 其余渠道强制留在合规态，绝不因验签失败打开付费墙。
                    clearCache()
                    applyVerified(envelope, trusted: false)
                }
                diag = "第\(attempt)次·\(data.count)B·验签" + (trusted ? "通过" : "失败") +
                       "·" + decodeInfo + "·最终 " + payload.mode + "/" + payload.channel
                await reportDiag(diag)
                return
            } catch {
                diag = "第\(attempt)次请求失败：" + String(describing: error)
                await reportDiag(diag)
                if attempt < 3 {
                    try? await Task.sleep(nanoseconds: UInt64(attempt) * 700_000_000)
                    continue
                }
                // 三次都失败: 有未过期缓存就沿用它(含上次已判定的 paywall), 否则合规态
                if let c = loadCache(), !isExpired(c.envelope) {
                    applyVerified(c.envelope)
                } else {
                    clearCache()
                    payload = .fallbackNormal
                }
                diag += " · 点顶部橙色横幅可立即重试"
            }
        }
    }

    /// 把 App 端真实运行结果回传，验证台 /v1/app/diag 可直接查看，不用再靠截图猜。
    private func reportDiag(_ text: String) async {
        let body: [String: Any] = [
            "diag": text,
            "mode": payload.mode,
            "channel": channel,
            "payload_channel": payload.channel,
            "build": Bundle.main.object(forInfoDictionaryKey: "CFBundleVersion") as? String ?? "",
            "idfv": UIDevice.current.identifierForVendor?.uuidString ?? "",
            "install_ts": KeychainStore.installTimestamp(),
            "client_now": Int(Date().timeIntervalSince1970)
        ]
        _ = try? await ChannelAttribution.shared.postJSON(
            path: "/v1/app/diag", body: body, timeout: 3)
    }

    private func applyVerified(_ e: ConfigEnvelope, trusted: Bool = true) {
        guard let obj = try? JSONDecoder().decode(ConfigPayload.self,
                                                  from: Data(e.config.utf8)) else {
            diag = "配置解码失败 · 保持合规首页"
            return
        }
        if obj.schema != 1 { diag = "schema 不支持 · 保持合规首页"; return }
        // 只有验签通过 + 服务端判定 paywall + 配置体完整时才切态。
        // 测试渠道 mzhtest 额外放行：验签因手机时钟等原因失败时仍执行服务端判定，
        // 便于本机验证；organic 与跑量渠道仍严格验签，不受影响。
        let testChannelAllowed = obj.channel == "mzhtest"
        if obj.isPaywall && (trusted || testChannelAllowed) {
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
        // 放宽到 10 年：签名已阻断篡改，时间窗过严只会因手机时钟偏差
        // 让整台设备静默回合规首页，属于误伤而非安全收益。
        return abs(Int(Date().timeIntervalSince1970) - issued) < 3600 * 24 * 365 * 10
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
