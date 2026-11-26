import SwiftUI
import Foundation

@main
struct ChannelSwitchApp: App {
    init() {
        ChannelAttribution.baseURL = Self.resolvedBaseURL()
    }

    /// 后端地址只在构建配置里注入，二进制里不再写死域名：
    /// Release 用 Config-Release.xcconfig 的 HTTPS 域名；
    /// Debug 用 Config-Debug.xcconfig 的局域网地址，也可用下条命令临时覆盖：
    ///   xcrun simctl spawn booted defaults write com.mizhenhe.player \
    ///     debug_config_base_url -string http://192.168.11.14:8790
    private static func resolvedBaseURL() -> String {
        #if DEBUG
        if let override = UserDefaults.standard.string(forKey: "debug_config_base_url"),
           !override.isEmpty { return override }
        #endif
        if let v = Bundle.main.object(forInfoDictionaryKey: "ConfigBaseURL") as? String,
           !v.isEmpty, !v.hasPrefix("$(") { return v }
        return "https://config.yourdomain.com"
    }

    var body: some Scene {
        WindowGroup {
            RootView()
        }
    }
}

struct RootView: View {
    @StateObject private var config = ConfigStore()
    /// 付费成功才置 true; 注册/登录不算会员(录屏: 登录成功后仍反复弹充值)
    @AppStorage("is_vip_unlocked") private var vipUnlocked = false
    @AppStorage("paywall_gate_shown") private var gateShown = false
    @State private var showPaywall = false
    @State private var hasShownOnce = false
    @State private var secondsSinceDismiss = 0
    @State private var reopening = false
    private let tick = Timer.publish(every: 1, on: .main, in: .common).autoconnect()

    private var normal: NormalConfig {
        config.payload.normal ?? .neutral(tabs: SkinDefaults.tabs,
                                          headline: SkinDefaults.headline)
    }
    private var gate: GateConfig? { config.payload.paywall?.gateOrDefaults }
    /// 付费墙层常驻: 未付费时退出按钮只静默回试看片段, 不 dismiss 这一层;
    /// 下面的计时是异常落到合规内页时的兜底拉回(同样不弹提示)。
    private var forced: Bool { config.isPaywall && (gate?.forces ?? false) && !vipUnlocked }

    var body: some View {
        Group {
            if !config.ready {
                LaunchPlaceholder()
            } else {
                NormalHomeView(config: normal) { showPaywall = true }
                    .overlay(alignment: .top) {
                        if !config.isPaywall {
                            Text(config.diag)
                                .font(.caption2)
                                .foregroundStyle(Color.white.opacity(0.72))
                                .padding(.horizontal, 10).padding(.vertical, 4)
                                .background(Color.black.opacity(0.42), in: Capsule())
                                .padding(.top, 6)
                                .allowsHitTesting(false)
                        }
                    }
                    .overlay(alignment: .bottom) {
                        if forced && (gate?.showBanner ?? false) {
                            memberBanner.padding(.bottom, 78).padding(.horizontal, 12)
                        }
                    }
                    .fullScreenCover(isPresented: Binding(
                        // 正常渠道没有付费墙配置时绝不呈现空视图（旧版点会员白屏的原因）
                        get: { showPaywall && config.payload.paywall != nil },
                        set: { if !$0 { showPaywall = false } }
                    )) {
                        if let p = config.payload.paywall {
                            PaywallFlow(config: p, store: config,
                                        startAtVIP: hasShownOnce) { unlocked in
                                showPaywall = false
                                hasShownOnce = true
                                secondsSinceDismiss = 0
                                if unlocked {
                                    vipUnlocked = true
                                    reopening = false
                                } else {
                                    gateShown = true
                                    // 异常落到合规内页时由计时器静默拉回充值页
                                    reopening = forced
                                }
                            }
                        }
                    }
            }
        }
        .mzhScreen()
        .onReceive(tick) { _ in
            guard reopening, forced, !showPaywall else { return }
            secondsSinceDismiss += 1
            let interval = max(1, Int(gate?.reopenInterval ?? 2.0))
            if secondsSinceDismiss >= interval {
                secondsSinceDismiss = 0
                showPaywall = true        // 只弹充值页: PaywallFlow 的 startAtVIP=true
            }
        }
        .task {
            await config.bootstrap()
            guard config.isPaywall else { return }
            let g = config.payload.paywall?.gateOrDefaults
            if (g?.forces ?? false) && !vipUnlocked {
                showPaywall = true        // 冷启动: 开屏 → 试看 → 充值
            } else if !(g?.oncePerInstall ?? true) || !gateShown {
                showPaywall = true
                gateShown = true
            }
        }
    }

    /// 录屏 37–40s: 合规首页底部常驻紫色「开通会员 解锁完整内容」条, 点了也进充值页
    private var memberBanner: some View {
        Button {
            showPaywall = true
            hasShownOnce = true
        } label: {
            HStack(spacing: 10) {
                Image(systemName: "crown.fill").font(.title3)
                VStack(alignment: .leading, spacing: 2) {
                    Text(gate?.bannerTitle ?? "开通会员 解锁完整内容")
                        .font(.subheadline.bold())
                    Text("解锁完整内容与专属权益").font(.caption2)
                }
                Spacer()
                Text("开通 ›").font(.subheadline.bold())
            }
            .foregroundStyle(.white)
            .padding(.horizontal, 16).padding(.vertical, 12)
            .background(
                LinearGradient(colors: [Color(hex: "#7B3FE4"), Color(hex: "#A24BE8")],
                               startPoint: .leading, endPoint: .trailing),
                in: RoundedRectangle(cornerRadius: 16))
        }
        .buttonStyle(.plain)
        .shadow(color: .purple.opacity(0.35), radius: 12, y: 4)
    }
}

/// 配置拉取完成前的占位，避免启动瞬间闪出错误形态
private struct LaunchPlaceholder: View {
    var body: some View {
        ZStack {
            Color(hex: "#0B1020").ignoresSafeArea()
            VStack(spacing: 14) {
                Image(systemName: "square.grid.3x3")
                    .font(.system(size: 50)).foregroundStyle(Color(hex: "#35E0C8"))
                ProgressView().tint(Color(hex: "#35E0C8"))
            }
        }
    }
}

private extension Color {
    init(hex: String) {
        let s = hex.trimmingCharacters(in: CharacterSet(charactersIn: "#"))
        var v: UInt64 = 0
        Scanner(string: s).scanHexInt64(&v)
        self.init(.sRGB,
                  red: Double((v >> 16) & 0xff) / 255,
                  green: Double((v >> 8) & 0xff) / 255,
                  blue: Double(v & 0xff) / 255, opacity: 1)
    }
}
