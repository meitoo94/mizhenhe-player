import SwiftUI
import Foundation
import UIKit
import Combine

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
    @State private var memberFromHome = false
    @State private var hasShownOnce = false

    private var normal: NormalConfig {
        config.payload.normal ?? .neutral(tabs: SkinDefaults.tabs,
                                          headline: SkinDefaults.headline)
    }

    var body: some View {
        Group {
            if !config.ready {
                LaunchPlaceholder()
            } else if config.isPaywall, let pw = config.payload.paywall, !vipUnlocked {
                // 渠道命中后把付费墙直接作为根视图。
                // 旧实现用 fullScreenCover：config.ready 与 showPaywall 在同一帧变化，
                // iOS 会忽略挂在刚插入视图树上的 cover，结果是「服务端已切 paywall、
                // 真机仍只见合规首页」，而且 isPaywall 已是 true，连诊断横幅都不显示。
                // 直接换根视图后不依赖任何呈现时机，切渠道必定可见。
                // 渠道形态下整层做防截屏：截屏保存为一张白图。
                SecureScreen {
                    PaywallFlow(config: pw, store: config,
                                startAtVIP: hasShownOnce) { unlocked in
                        if unlocked {
                            vipUnlocked = true
                        } else {
                            // 「稍后再说 / 暂不开通」静默回片段并保留进度，由 PaywallFlow 内部处理；
                            // 走到这里只表示异常退出，重新进入直接落充值页。
                            hasShownOnce = true
                            gateShown = true
                        }
                    }
                }
            } else {
                NormalHomeView(config: normal) { memberFromHome = true }
                    .overlay(alignment: .top) { diagBanner }
                    .fullScreenCover(isPresented: Binding(
                        // 正常渠道没有付费墙配置时绝不呈现空视图（旧版点会员白屏的原因）
                        get: { memberFromHome && !vipUnlocked && config.payload.paywall != nil },
                        set: { if !$0 { memberFromHome = false } }
                    )) {
                        if let p = config.payload.paywall {
                            PaywallFlow(config: p, store: config,
                                        startAtVIP: true) { unlocked in
                                memberFromHome = false
                                if unlocked { vipUnlocked = true }
                            }
                        }
                    }
            }
        }
        .mzhScreen()
        .task {
            await config.bootstrap()
        }
        // 5G / WiFi 切换后链路恢复时自动重新拉一次配置：
        // 首启若恰好撞上 -1009，用户切出去再回来就会补上，不用重装。
        .onReceive(NotificationCenter.default.publisher(
            for: UIApplication.didBecomeActiveNotification)) { _ in
            Task { await config.refresh() }
        }
    }

    /// 合规首页常驻一行醒目诊断：请求次数 / 响应大小 / 验签 / 解码 / 最终形态。
    /// 点一下复制到剪贴板，可以直接把 App 端真实状态带回后台核对。
    private var diagBanner: some View {
        Text("诊断 · \(config.diag) ｜ 形态 \(config.payload.mode)/\(config.payload.channel)")
            .font(.caption2.weight(.medium))
            .foregroundStyle(.white)
            .lineLimit(3)
            .multilineTextAlignment(.leading)
            .padding(.horizontal, 12).padding(.vertical, 7)
            .background(Color(hex: "#B4461F").opacity(0.94),
                        in: RoundedRectangle(cornerRadius: 10))
            .padding(.horizontal, 10).padding(.top, 8)
            .onTapGesture {
                // 网络抖动导致留在合规首页时，点一下橙色横幅立即重新检测，
                // 不用杀进程、不用重装。
                Task { await config.refresh() }
            }
            .onLongPressGesture {
                UIPasteboard.general.string =
                    "\(config.diag) | \(config.payload.mode)/\(config.payload.channel)"
            }
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
