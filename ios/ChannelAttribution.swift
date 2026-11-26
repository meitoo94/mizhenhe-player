import Foundation
import UIKit
import Darwin
#if canImport(AdServices)
import AdServices
#endif
// Release 上架包通过 MZH_NO_AD_TRACKING 排除 IDFA 引用，隐私问卷无需声明追踪。
#if canImport(AdSupport) && !MZH_NO_AD_TRACKING
import AdSupport
#endif

/// 首次启动归因: 把「百度推广广告点击 → App Store 下载 → 首启」接起来。
/// 主链路不是剪贴板: 百度后台通过 API 回传「哪些 IP / 设备号 来自广告点击」,
/// 我方后台按「计划ID → 我方渠道号(例如 bbdd1)」建点击库; App 首启只上报
/// 设备上下文, 服务端用请求出口 IP / 设备号匹配点击库并绑定渠道号。
/// 优先级: Keychain 缓存 > 百度 API 设备绑定(IP/设备号) > 未命中 organic。
/// ASA / IDFA / CAID 只是提高匹配精度的辅助信号, 不作为渠道号来源。
final class ChannelAttribution {

    static let shared = ChannelAttribution()
    static var baseURL = "http://127.0.0.1:8787"   // 生产换 https://<your-domain>

    private let kChannel = "attributed_channel"
    private let kMethod  = "attributed_method"
    private init() {}

    // MARK: - 对外入口

    /// 有缓存直接返回; 无缓存走一次网络归因。任何异常都回落 organic(合规态)。
    func currentChannel() async -> String {
        if let cached = KeychainStore.get(kChannel), !cached.isEmpty {
            UserDefaults.standard.set(cached, forKey: "resolved_channel")
            return cached
        }
        let result = await resolveOnce()
        KeychainStore.set(result.channel, for: kChannel)
        KeychainStore.set(result.method, for: kMethod)
        // 供登录 / 订单请求带同一渠道维度, 便于三条日志对齐
        UserDefaults.standard.set(result.channel, forKey: "resolved_channel")
        return result.channel
    }

    func debugMethod() -> String {
        KeychainStore.get(kMethod) ?? "not_resolved"
    }

    func resetForTesting() {
        KeychainStore.remove(kChannel)
        KeychainStore.remove(kMethod)
    }

    // MARK: - 归因主流程

    private func resolveOnce() async -> AttributionResult {
        var body: [String: Any] = deviceContext()
        // 百度推广回传的点击里带 IP 和设备号; 客户端把这些辅助标识交给服务端做
        // 精确匹配(服务端只存加盐哈希)。客户端不传、也不知道自己的渠道号。
        if let idfa = advertisingIdentifier() { body["idfa"] = idfa }
        if let caid = caidIdentifier() { body["caid"] = caid }
        // Apple Search Ads 官方归因 token 作为补充信号(服务端可换 campaignId)
        if let asa = appleSearchAdsToken() { body["asa_token"] = asa }

        do {
            // 主链路: 服务端按百度 API 回传的 IP / 设备号解析并绑定渠道号
            let data = try await postJSON(path: "/v1/attribution/resolve",
                                         body: body, timeout: 6)
            let decoded = try JSONDecoder().decode(AttributionResult.self, from: data)
            return decoded
        } catch {
            return AttributionResult(channel: "organic", confidence: 0,
                                     method: "fallback_error", resolvedAt: 0)
        }
    }

    // MARK: - 设备上下文

    private func deviceContext() -> [String: Any] {
        let screen = UIScreen.main.bounds
        var model = "unknown"
        var sysinfo = utsname()
        uname(&sysinfo)
        model = withUnsafePointer(to: &sysinfo.machine) { p in
            p.withMemoryRebound(to: CChar.self, capacity: 1) { String(cString: $0) }
        }
        return [
            "idfv": UIDevice.current.identifierForVendor?.uuidString ?? "",
            "model": model,
            "os": UIDevice.current.systemVersion,
            "tz": TimeZone.current.identifier,
            "lang": Locale.preferredLanguages.first ?? "",
            "screen": "\(Int(screen.width))x\(Int(screen.height))@\(Int(UIScreen.main.scale))",
            "country": Locale.current.region?.identifier.lowercased() ?? "cn",
            "build": Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "",
            "install_ts": KeychainStore.installTimestamp(),
            "app_version": Bundle.main.object(forInfoDictionaryKey: "CFBundleVersion") as? String ?? ""
        ]
    }

    // MARK: - 设备号辅助信号(不落明文, 只用于服务端哈希匹配)

    /// IDFA 需要 AppTrackingTransparency 授权; 未授权/全零时返回 nil,
    /// 服务端会退回到「出口 IP + 多特征」匹配, 不阻断主链路。
    private func advertisingIdentifier() -> String? {
        #if canImport(AdSupport) && !MZH_NO_AD_TRACKING
        let v = ASIdentifierManager.shared().advertisingIdentifier.uuidString
        return (v.isEmpty || v == "00000000-0000-0000-0000-000000000000") ? nil : v
        #else
        return nil
        #endif
    }

    /// CAID(中国广告协会匿名设备标识)由第三方 SDK 提供; 接入后在这里返回。
    /// 未接入时返回 nil, 服务端仍可用 IP / 其它设备号匹配。
    private func caidIdentifier() -> String? { nil }

    // MARK: - Apple Search Ads

    private func appleSearchAdsToken() -> String? {
        #if canImport(AdServices)
        if #available(iOS 14.3, *) {
            return try? AAAttribution.attributionToken()
        }
        #endif
        return nil
    }

    // MARK: - 网络工具

    /// 蜂窝网络首启常见 NSURLErrorNotConnectedToInternet(-1009)：系统还没把
    /// 5G 接口标记就绪，URLSession.shared 会立刻失败而不是等待。这里用独立
    /// session 显式放行蜂窝 / 低数据模式，并让任务等待链路就绪。
    private static let configSession: URLSession = {
        let cfg = URLSessionConfiguration.default
        cfg.waitsForConnectivity = true
        cfg.allowsCellularAccess = true
        if #available(iOS 13.0, *) {
            cfg.allowsExpensiveNetworkAccess = true
            cfg.allowsConstrainedNetworkAccess = true
        }
        cfg.timeoutIntervalForRequest = 20
        cfg.timeoutIntervalForResource = 30
        return URLSession(configuration: cfg)
    }()

    func postJSON(path: String, body: [String: Any], timeout: TimeInterval) async throws -> Data {
        guard let url = URL(string: ChannelAttribution.baseURL + path) else {
            throw ChannelError.invalidURL
        }
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try JSONSerialization.data(withJSONObject: body, options: [])
        req.timeoutInterval = timeout
        req.allowsCellularAccess = true
        if #available(iOS 13.0, *) {
            req.allowsExpensiveNetworkAccess = true
            req.allowsConstrainedNetworkAccess = true
        }
        let (data, resp) = try await ChannelAttribution.configSession.data(for: req)
        guard let http = resp as? HTTPURLResponse, 200..<300 ~= http.statusCode else {
            throw ChannelError.badResponse((resp as? HTTPURLResponse)?.statusCode ?? -1)
        }
        return data
    }
}
