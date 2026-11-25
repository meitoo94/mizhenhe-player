import Foundation

// MARK: - 归因结果

struct AttributionResult: Codable {
    let channel: String
    let confidence: Double
    let method: String
    let resolvedAt: Int

    enum CodingKeys: String, CodingKey {
        case channel, confidence, method
        case resolvedAt = "resolved_at"
    }
}

// MARK: - 远程配置信封

/// 服务端返回信封: config 是规范化 JSON 字符串原文, sig 是这段原文的 HMAC-SHA256。
/// 直接对字符串字节验签, 规避跨语言序列化差异。
struct ConfigEnvelope: Codable {
    let config: String
    let sig: String
}

struct ConfigPayload: Codable {
    let schema: Int
    let mode: String                  // "normal" | "paywall"
    let channel: String
    let issuedAt: Int
    let ttl: Int
    let normal: NormalConfig?
    let paywall: PaywallConfig?

    enum CodingKeys: String, CodingKey {
        case schema, mode, channel, ttl, normal, paywall
        case issuedAt = "issued_at"
    }

    var isPaywall: Bool { mode == "paywall" && paywall != nil }

    /// 任何异常情况下的兜底: 合规态, 且不含任何付费墙资源。
    /// Tab 名优先取构建时注入的 Info.plist 键, 未注入时保持中性文案,
    /// 避免换皮肤后兜底态仍显示上一套皮肤的模块名。
    static var fallbackNormal: ConfigPayload {
        ConfigPayload(
            schema: 1, mode: "normal", channel: "organic",
            issuedAt: 0, ttl: 0,
            normal: NormalConfig.neutral(tabs: SkinDefaults.tabs,
                                         headline: SkinDefaults.headline),
            paywall: nil
        )
    }
}

/// 皮肤级兜底文案(构建时从 Info.plist 注入)。
enum SkinDefaults {
    static var tabs: [String] {
        (Bundle.main.object(forInfoDictionaryKey: "SkinTabs") as? [String])
            ?? ["首页", "媒体库", "创作", "我的"]
    }
    static var headline: String {
        (Bundle.main.object(forInfoDictionaryKey: "SkinHeadline") as? String)
            ?? "本地影像工具"
    }
}

// MARK: - 合规态皮肤: 全部字段由服务端配置驱动

struct ThemeConfig: Codable {
    let primary: String
    let accent: String
    let background: String
    let surface: String
    let text: String
    let muted: String
    let corner: Double
    let tabIcons: [String]

    enum CodingKeys: String, CodingKey {
        case primary, accent, background, surface, text, muted, corner
        case tabIcons = "tab_icons"
    }
}

struct HomeConfig: Codable {
    let stats: [String]
    let emptyLibrary: [String: Int]
    let channels: [String]
    let sections: [String]
    let emptyTips: [String]

    enum CodingKeys: String, CodingKey {
        case stats, channels, sections
        case emptyLibrary = "empty_library"
        case emptyTips = "empty_tips"
    }
}

struct LibraryConfig: Codable {
    let searchPlaceholder: String
    let viewModes: [String]
    let bulkActions: [String]
    let emptyTitle: String
    let importHint: String
    let importButton: String

    enum CodingKeys: String, CodingKey {
        case viewModes, bulkActions
        case searchPlaceholder = "search_placeholder"
        case emptyTitle = "empty_title"
        case importHint = "import_hint"
        case importButton = "import_button"
    }
}

struct CraftFeature: Codable, Identifiable {
    let id: String
    let name: String
    let desc: String
    let icon: String
    let mapsTo: String

    enum CodingKeys: String, CodingKey {
        case id, name, desc, icon
        case mapsTo = "maps_to"
    }
}

struct CloudConfig: Codable {
    let name: String
    let desc: String
    let items: [String]
}

struct CraftConfig: Codable {
    let headline: String
    let features: [CraftFeature]
    let cloud: CloudConfig
}

struct MembershipConfig: Codable {
    let name: String
    let placeholder: String
    let mapsTo: String

    enum CodingKeys: String, CodingKey {
        case name, placeholder
        case mapsTo = "maps_to"
    }
}

struct MineConfig: Codable {
    let account: String
    let accountHint: String
    let membership: MembershipConfig
    let links: [String]

    enum CodingKeys: String, CodingKey {
        case account, membership, links
        case accountHint = "account_hint"
    }
}

struct NormalConfig: Codable {
    let brand: String
    let tagline: String
    let headline: String
    let tabs: [String]
    let theme: ThemeConfig
    let home: HomeConfig
    let library: LibraryConfig
    let craft: CraftConfig
    let mine: MineConfig

    /// 皮肤中性兜底: 不含任何具体皮肤名, 保证验签失败/超时/缓存过期时也是合规态。
    static func neutral(tabs: [String], headline: String) -> NormalConfig {
        NormalConfig(
            brand: "", tagline: "", headline: headline, tabs: tabs,
            theme: ThemeConfig(primary: "#4F8CFF", accent: "#FF9F5A",
                               background: "#FFFFFF", surface: "#F2F4F8",
                               text: "#101828", muted: "#8A94A6", corner: 16,
                               tabIcons: ["play.rectangle", "square.grid.2x2",
                                          "wand.and.stars", "person.crop.circle"]),
            home: HomeConfig(stats: ["影片", "合集", "创作"],
                             emptyLibrary: ["影片": 0, "合集": 0, "创作": 0],
                             channels: [], sections: ["今日放映"],
                             emptyTips: ["导入本地视频后，这里会生成今日片单"]),
            library: LibraryConfig(searchPlaceholder: "搜索", viewModes: ["网格", "时间线"],
                                   bulkActions: [], emptyTitle: "媒体库为空",
                                   importHint: "导入只建立索引，不复制、不上传文件",
                                   importButton: "导入本地视频"),
            craft: CraftConfig(headline: "创作", features: [],
                               cloud: CloudConfig(name: "", desc: "", items: [])),
            mine: MineConfig(account: "未登录", accountHint: "",
                             membership: MembershipConfig(name: "会员", placeholder: "", mapsTo: ""),
                             links: ["设置", "用户协议", "隐私政策"])
        )
    }
}

// MARK: - 付费墙态: 开屏 / 试看 / 会员 / 收银台 / 登录

struct GateConfig: Codable {
    let oncePerInstall: Bool
    let allowSkip: Bool
    let skipButton: String
    let homeAfterSkip: Bool
    let reopenFrom: String

    /// 只有一种形态: 退出静默回试看片段, 不给任何提示。
    /// 注册/登录成功后 1s 弹出充值页; 点「暂不开通 / 稍后再说 / 返回」静默回片段,
    /// 片段看完、切 Tab、点功能或会员横幅会静默回到充值页, 只有支付成功才放行。
    let forcePaywallUntilVIP: Bool?
    let loginToVIPDelaySeconds: Double?
    let reopenIntervalSeconds: Double?
    let persistentBanner: Bool?
    let bannerText: String?

    enum CodingKeys: String, CodingKey {
        case oncePerInstall = "once_per_install"
        case allowSkip = "allow_skip"
        case skipButton = "skip_button"
        case homeAfterSkip = "home_after_skip"
        case reopenFrom = "reopen_from"
        case forcePaywallUntilVIP = "force_paywall_until_vip"
        case loginToVIPDelaySeconds = "login_to_vip_delay_seconds"
        case reopenIntervalSeconds = "reopen_interval_seconds"
        case persistentBanner = "persistent_banner"
        case bannerText = "banner_text"
    }

    var forces: Bool { forcePaywallUntilVIP ?? true }
    var loginDelay: Double { loginToVIPDelaySeconds ?? 1.0 }
    var reopenInterval: Double { reopenIntervalSeconds ?? 2.0 }
    var showBanner: Bool { persistentBanner ?? true }
    var bannerTitle: String { bannerText ?? "开通会员 解锁完整内容" }

    static let defaults = GateConfig(
        oncePerInstall: false, allowSkip: false,
        skipButton: "稍后再说", homeAfterSkip: false, reopenFrom: "",
        forcePaywallUntilVIP: true, loginToVIPDelaySeconds: 1.0,
        reopenIntervalSeconds: 2.0, persistentBanner: true,
        bannerText: "开通会员 解锁完整内容")
}

struct PaywallConfig: Codable {
    let splash: SplashConfig
    let trial: TrialConfig
    let vip: VIPConfig
    let checkout: CheckoutConfig
    let gate: GateConfig?
    let login: LoginConfig?

    var gateOrDefaults: GateConfig { gate ?? .defaults }
    var loginOrDefaults: LoginConfig { login ?? .defaults }
}

struct SplashConfig: Codable {
    let title: String
    let subtitle: String
    let button: String
    let countdownSeconds: Int
    enum CodingKeys: String, CodingKey {
        case title, subtitle, button
        case countdownSeconds = "countdown_seconds"
    }
}

struct TrialConfig: Codable {
    let videoURL: String
    let posterURL: String
    let countdownSeconds: Int
    let vipButton: String
    let hint: String
    let endTitle: String
    let endBody: String
    let endPrimary: String
    let endSecondary: String
    enum CodingKeys: String, CodingKey {
        case hint
        case videoURL = "video_url"
        case posterURL = "poster_url"
        case countdownSeconds = "countdown_seconds"
        case vipButton = "vip_button"
        case endTitle = "end_title"
        case endBody = "end_body"
        case endPrimary = "end_primary"
        case endSecondary = "end_secondary"
    }
}

struct VIPPackage: Codable, Identifiable {
    let id: String
    let name: String
    let price: Double
    let originPrice: Double
    let perk: String
    let tag: String
    enum CodingKeys: String, CodingKey {
        case id, name, price, perk, tag
        case originPrice = "origin_price"
    }
}

struct VIPConfig: Codable {
    let title: String
    let subtitle: String?
    let packages: [VIPPackage]
    let button: String
    let agreements: [String]
    let loginEntry: String?

    enum CodingKeys: String, CodingKey {
        case title, subtitle, packages, button, agreements
        case loginEntry = "login_entry"
    }
}

struct LoginConfig: Codable {
    let enabled: Bool
    let method: String
    let confirmPhoneInput: Bool
    let placeholder: String
    let confirmPlaceholder: String
    let submit: String
    let agreements: [String]
    let successToast: String

    enum CodingKeys: String, CodingKey {
        case enabled, method, placeholder, submit, agreements
        case confirmPhoneInput = "confirm_phone_input"
        case confirmPlaceholder = "confirm_placeholder"
        case successToast = "success_toast"
    }

    static let defaults = LoginConfig(enabled: true, method: "phone_no_sms",
                                      confirmPhoneInput: true,
                                      placeholder: "请输入手机号",
                                      confirmPlaceholder: "请再次输入手机号",
                                      submit: "登录",
                                      agreements: ["《用户协议》", "《隐私政策》"],
                                      successToast: "登录成功")
}

struct CheckoutConfig: Codable {
    let method: String
    let orderEndpoint: String
    let cashierBase: String
    let supportURL: String
    let orderPagePath: String?
    let orderPageBase: String?
    let orderPageTitle: String?
    let repayButton: String?
    let supportPrefix: String?

    enum CodingKeys: String, CodingKey {
        case method
        case orderEndpoint = "order_endpoint"
        case cashierBase = "cashier_base"
        case supportURL = "support_url"
        case orderPagePath = "order_page_path"
        case orderPageBase = "order_page_base"
        case orderPageTitle = "order_page_title"
        case repayButton = "repay_button"
        case supportPrefix = "support_prefix"
    }
}

// MARK: - 订单 / 登录 / 会员状态

struct OrderResponse: Codable {
    let orderNo: String
    let amount: Double
    let status: String
    let cashierURL: String
    let orderPageURL: String?
    let packageName: String?

    enum CodingKeys: String, CodingKey {
        case amount, status
        case orderNo = "order_no"
        case cashierURL = "cashier_url"
        case orderPageURL = "order_page_url"
        case packageName = "package_name"
    }
}

struct LoginResponse: Codable {
    let ok: Bool
    let ticket: String?
    let phoneMasked: String?
    let isVIP: Bool?
    let toast: String?

    enum CodingKeys: String, CodingKey {
        case ok, ticket, toast
        case phoneMasked = "phone_masked"
        case isVIP = "is_vip"
    }
}

struct VIPStatus: Codable {
    let isVIP: Bool
    enum CodingKeys: String, CodingKey { case isVIP = "is_vip" }
}

// MARK: - 通用网络错误

enum ChannelError: Error {
    case invalidURL
    case badResponse(Int)
    case timeout
    case invalidSignature
}
