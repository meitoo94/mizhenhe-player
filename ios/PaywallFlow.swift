import SwiftUI
import AVKit
import AVFoundation
import UIKit
import CoreMedia

/// 付费墙态状态机(逐项对齐录屏):
///   冷启动开屏 6s「进入」 → 15s 视频试看 + 倒计时 → 试看结束弹窗
///   → 棱镜会员三档(198/158/98) → 自建订单页 → 支付宝 H5 收银台
///   → 手机号免验证码登录 → 合规内页。
///
/// 付费墙闭环(退出静默回片段, 全程无提示):
///   登录 ≠ 会员; 注册/登录成功 1s 后弹充值页。
///   点「暂不开通 / 稍后再说 / 返回」静默回试看片段, 不弹 toast / alert,
///   也不进合规内页; 片段看完后静默回到充值页, 切 Tab / 点会员横幅同样回充值页。
///   退出保留原试看进度: 剩 5s 就还是 5s, 已看完就还是结束态, 不重播不重置。
///   RootView 的 fullScreenCover 承载这一层, 只有支付成功才 dismiss 并解锁。
/// 所有文案、秒数、视频地址、套餐价格、重弹间隔都来自 RemoteConfig。
enum PaywallPhase {
    case splash, trial, vip, unlocked
}

struct PaywallFlow: View {
    let config: PaywallConfig
    @ObservedObject var store: ConfigStore
    /// true = 这不是冷启动, 而是强制闭环的再次弹出, 直接落在充值页(录屏 16.5s / 21s / 37.8s)
    let startAtVIP: Bool
    /// unlocked=true 仅表示支付成功; false = 只是 dismiss 这一层, 稍后还会被重新弹出
    var onExitToHome: (Bool) -> Void

    @State private var phase: PaywallPhase
    // 离开试看页时的现场, 回来原样恢复(不重播、不重置倒计时)
    @State private var trialLeft: Int? = nil   // nil = 从未进入过试看
    @State private var trialSeek: Double = 0   // 播放器当前位置(秒)
    @State private var trialEnded = false      // 是否已经看完
    @State private var quietLeft = 0          // 回片段后静默回充值页的倒计时
    private let quietTick = Timer.publish(every: 1, on: .main, in: .common).autoconnect()
    @State private var isPaying = false
    @State private var orderNo: String?
    @State private var errMessage: String?
    @State private var showLogin = false
    @State private var loginToast: String?
    @AppStorage("phone_login_ticket") private var loginTicket: String = ""
    @Environment(\.scenePhase) private var scenePhase

    private var login: LoginConfig { config.loginOrDefaults }
    private var gate: GateConfig { config.gateOrDefaults }

    /// 按钮触摸埋点：上报到 /v1/app/diag，用来区分「触摸压根没到」
    /// 与「触摸到了但下单/状态机失败」，不用再靠用户截图猜。
    private func reportTap(_ name: String) {
        let body: [String: Any] = [
            "diag": "TAP " + name,
            "mode": "paywall",
            "build": "8"
        ]
        Task {
            _ = try? await ChannelAttribution.shared.postJSON(
                path: "/v1/app/diag", body: body, timeout: 6)
        }
    }

    init(config: PaywallConfig, store: ConfigStore, startAtVIP: Bool = false,
         onExitToHome: @escaping (Bool) -> Void) {
        self.config = config
        self.store = store
        self.startAtVIP = startAtVIP
        self.onExitToHome = onExitToHome
        _phase = State(initialValue: startAtVIP ? .vip : .splash)
    }

    var body: some View {
        Group {
            switch phase {
            case .splash:
                SplashView(config: config.splash) {
                    reportTap("splash_enter"); phase = .trial
                }
            case .trial:
                TrialPlayerView(config: config.trial, theme: config.themeOrDefaults,
                                initialLeft: trialLeft ?? config.trial.countdownSeconds,
                                seekTo: trialSeek,
                                ended: trialEnded,
                                onProgress: { left, seek, ended in
                                    trialLeft = left
                                    trialSeek = seek
                                    trialEnded = ended
                                },
                                onVIP: { reportTap("trial_to_vip"); phase = .vip },
                                onSkip: { reportTap("trial_skip"); requestExit() })
            case .vip:
                VIPPackagesView(config: config.vip, theme: config.themeOrDefaults,
                                isPaying: isPaying,
                                onCheckout: { pkg in
                                    reportTap("vip_checkout")
                                    Task { await checkout(pkg) }
                                },
                                onLogin: { reportTap("vip_login_entry"); showLogin = true },
                                onExit: { reportTap("vip_later"); requestExit() })
            case .unlocked:
                UnlockedView(videoURL: config.trial.videoURL, orderNo: orderNo,
                             onExit: { onExitToHome(true) })
            }
        }
        .alert("下单失败", isPresented: Binding(
            get: { errMessage != nil },
            set: { if !$0 { errMessage = nil } }
        )) {
            Button("知道了", role: .cancel) { }
        } message: {
            Text(errMessage ?? "")
        }
        .sheet(isPresented: $showLogin) {
            LoginSheet(config: login) { ticket in
                loginTicket = ticket
                showLogin = false
                loginToast = login.successToast
                Task { await afterLogin() }
            }
        }
        .alert(login.successToast, isPresented: Binding(
            get: { loginToast != nil },
            set: { if !$0 { loginToast = nil } }
        )) {
            Button("好的", role: .cancel) { }
        } message: {
            Text("账号已创建，开通会员后解锁完整内容")
        }
        // 从支付宝 H5 / 自建订单页回到前台时查会员状态
        .onChange(of: scenePhase) { _, new in
            if new == .active, phase == .vip { Task { await checkVIPStatus() } }
        }
        // 退出回片段后, 先让用户完整看完这一段, 再静默回到充值页(不弹提示)
        .onReceive(quietTick) { _ in
            guard gate.forces, quietLeft > 0 else { return }
            quietLeft -= 1
            if quietLeft == 0, phase == .trial {
                withAnimation { phase = .vip }
            }
        }
    }

    /// 「暂不开通 / 稍后再说 / 返回」统一走这里: 静默回到原来的试看现场。
    /// 剩 5s 就还是 5s, 已看完就还是结束态; 不重播、不重置、不弹 toast / alert。
    /// 付费墙这层 fullScreenCover 仍常驻, 只有支付成功(unlocked) 才放行到合规内页。
    private func requestExit() {
        if gate.forces {
            let remaining = trialEnded ? 0 : (trialLeft ?? config.trial.countdownSeconds)
            quietLeft = max(1, remaining + Int(gate.reopenInterval))
            withAnimation { phase = .trial }
        } else {
            onExitToHome(false)
        }
    }

    // MARK: 下单 + 打开自建订单页(订单页再跳收银台)

    @MainActor
    private func checkout(_ pkg: VIPPackage) async {
        isPaying = true
        defer { isPaying = false }
        let body: [String: Any] = [
            "channel": store.channel,
            "package_id": pkg.id,
            "idfv": UIDevice.current.identifierForVendor?.uuidString ?? "",
            "amount": pkg.price,
            // 登录票据给 /site 后台下单用（/api/pay/vip-create 要正式账号令牌）
            "ticket": loginTicket
        ]
        do {
            let data = try await ChannelAttribution.shared.postJSON(
                path: "/v1/order", body: body, timeout: 12)
            let order = try JSONDecoder().decode(OrderResponse.self, from: data)
            orderNo = order.orderNo
            // 直接打开 /site 后台生成的支付宝 H5 收银台，
            // 不再经过本地「交易进行中 / 重新支付」占位订单页。
            let target = order.cashierURL.isEmpty ? (order.orderPageURL ?? "") : order.cashierURL
            if let url = URL(string: target), !target.isEmpty {
                await UIApplication.shared.open(url, options: [:])
            }
        } catch {
            errMessage = error.localizedDescription
        }
    }

    @MainActor
    private func checkVIPStatus() async {
        let body: [String: Any] = [
            "idfv": UIDevice.current.identifierForVendor?.uuidString ?? ""
        ]
        guard let data = try? await ChannelAttribution.shared.postJSON(
                path: "/v1/vip/status", body: body, timeout: 4),
              let status = try? JSONDecoder().decode(VIPStatus.self, from: data),
              status.isVIP else { return }
        // 录屏 42.66s: 付款流程之后要求手机号登录, 未登录先弹登录
        if login.enabled && loginTicket.isEmpty {
            showLogin = true
        } else {
            phase = .unlocked
        }
    }

    @MainActor
    private func afterLogin() async {
        let body: [String: Any] = [
            "idfv": UIDevice.current.identifierForVendor?.uuidString ?? ""
        ]
        guard let data = try? await ChannelAttribution.shared.postJSON(
                path: "/v1/vip/status", body: body, timeout: 4),
              let status = try? JSONDecoder().decode(VIPStatus.self, from: data),
              status.isVIP else {
            // 录屏 12.36s「登录成功」只代表账号注册, 不是会员。
            // 按 gate.login_to_vip_delay_seconds 延迟后回到充值页并常驻。
            DispatchQueue.main.asyncAfter(deadline: .now() + gate.loginDelay) {
                withAnimation { phase = .vip }
            }
            return
        }
        phase = .unlocked
    }
}

// MARK: - 1. 开屏倒计时(6s + 「进入」)

private struct SplashView: View {
    let config: SplashConfig
    let onDone: () -> Void
    @State private var left: Int
    private let timer = Timer.publish(every: 1, on: .main, in: .common).autoconnect()

    init(config: SplashConfig, onDone: @escaping () -> Void) {
        self.config = config
        self.onDone = onDone
        _left = State(initialValue: config.countdownSeconds)
    }

    var body: some View {
        ZStack {
            LinearGradient(colors: [Color(hex: "#0B1020"), Color(hex: "#1B2740")],
                           startPoint: .top, endPoint: .bottom).ignoresSafeArea()
            VStack(spacing: 20) {
                Spacer()
                ZStack {
                    Circle().fill(Color(hex: "#35E0C8").opacity(0.14)).frame(width: 116, height: 116)
                    Image(systemName: "play.rectangle.fill").font(.system(size: 52))
                        .foregroundStyle(Color(hex: "#35E0C8"))
                }
                Text(config.title).font(.title.bold()).foregroundStyle(.white)
                Text(config.subtitle).font(.subheadline).foregroundStyle(.white.opacity(0.65))
                Spacer()
                Button {
                    onDone()
                } label: {
                    Text("\(config.button)  \(left)s")
                        .font(.headline)
                        .frame(maxWidth: .infinity).padding(.vertical, 15)
                        .background(Color(hex: "#35E0C8"), in: RoundedRectangle(cornerRadius: 26))
                        .foregroundStyle(Color(hex: "#06121A"))
                }
                .padding(.horizontal, 40).padding(.bottom, 54)
            }
        }
        .onReceive(timer) { _ in
            guard left > 0 else { return }
            left -= 1
            if left == 0 { onDone() }
        }
    }
}

// MARK: - 2. 视频试看（点画面直接进会员充值，不出现播放器暂停控件）

/// 只用 AVPlayerLayer 渲染画面：AVKit 的 VideoPlayer 自带播放/暂停、±10s 控件，
/// 点一下先弹控件再点才播放，跟竞品「点试看片段直接出会员充值」不一致。
private struct SilentPlayerLayer: UIViewRepresentable {
    let player: AVPlayer
    let urlString: String
    let seekTo: Double

    func makeUIView(context: Context) -> PlayerLayerView {
        let v = PlayerLayerView()
        v.playerLayer.player = player
        v.playerLayer.videoGravity = .resizeAspect
        return v
    }

    func updateUIView(_ uiView: PlayerLayerView, context: Context) {
        guard player.currentItem == nil, let url = URL(string: urlString) else { return }
        player.replaceCurrentItem(with: AVPlayerItem(url: url))
        if seekTo > 0 {          // 回到原来看到的那一帧, 不从头播
            player.seek(to: CMTime(seconds: seekTo, preferredTimescale: 600))
        }
        player.play()
    }

    final class PlayerLayerView: UIView {
        override class var layerClass: AnyClass { AVPlayerLayer.self }
        var playerLayer: AVPlayerLayer { layer as! AVPlayerLayer }
    }
}

private struct TrialPlayerView: View {
    let config: TrialConfig
    let theme: PaywallTheme
    let initialLeft: Int
    let seekTo: Double
    let ended: Bool
    let onProgress: (Int, Double, Bool) -> Void
    let onVIP: () -> Void
    let onSkip: () -> Void
    @State private var player = AVPlayer()
    @State private var left: Int
    @State private var showEnd: Bool
    private let timer = Timer.publish(every: 1, on: .main, in: .common).autoconnect()

    init(config: TrialConfig, theme: PaywallTheme, initialLeft: Int, seekTo: Double,
         ended: Bool,
         onProgress: @escaping (Int, Double, Bool) -> Void,
         onVIP: @escaping () -> Void, onSkip: @escaping () -> Void) {
        self.config = config
        self.theme = theme
        self.initialLeft = initialLeft
        self.seekTo = seekTo
        self.ended = ended
        self.onProgress = onProgress
        self.onVIP = onVIP
        self.onSkip = onSkip
        _left = State(initialValue: initialLeft)
        _showEnd = State(initialValue: ended)
    }

    var body: some View {
        ZStack {
            Color.black.ignoresSafeArea()
            SilentPlayerLayer(player: player, urlString: config.videoURL, seekTo: seekTo)
                .ignoresSafeArea()
                .allowsHitTesting(false)
                .onDisappear {
                    player.pause()
                    let sec = player.currentTime().seconds
                    onProgress(left, sec.isFinite ? sec : 0, showEnd)
                }

            // 画面任意位置点一下 → 直接进会员充值页（不再弹播放/暂停控件）。
            // 用 0.001 alpha 而不是 Color.clear：完全透明的 Color 在部分
            // iOS 版本上会被命中测试优化掉；Spacer/文字一律禁命中，避免吞掉点击。
            Color.white.opacity(0.001)
                .ignoresSafeArea()
                .contentShape(Rectangle())
                .onTapGesture { showEnd = false; onVIP() }
                .allowsHitTesting(!showEnd)

            VStack {
                HStack {
                    Spacer().allowsHitTesting(false)
                    Button { showEnd = false; onVIP() } label: {
                        Text(config.vipButton)
                            .font(.subheadline.bold())
                            .padding(.horizontal, 16).padding(.vertical, 9)
                            .background(Color(hex: theme.trialButtonBackground).opacity(0.94),
                                        in: Capsule())
                            .overlay(Capsule().stroke(Color(hex: theme.ctaBorder), lineWidth: 1.6))
                            .foregroundStyle(Color(hex: theme.ctaText))
                    }
                    .padding(.top, 52).padding(.trailing, 16)
                }
                Spacer().allowsHitTesting(false)
                VStack(spacing: 8) {
                    Text("剩余 \(left)s")
                        .font(.caption.monospacedDigit())
                        .foregroundStyle(.white.opacity(0.8))
                        .allowsHitTesting(false)
                    Text(config.hint)
                        .font(.caption2)
                        .foregroundStyle(.white.opacity(0.6))
                        .multilineTextAlignment(.center)
                        .padding(.horizontal, 24)
                        .allowsHitTesting(false)
                }
                .padding(.bottom, 44)
            }

            if showEnd { endCard }
        }
        .onReceive(timer) { _ in
            guard left > 0, !showEnd else { return }
            left -= 1
            if left == 0 { showEnd = true }
        }
    }

    /// 试看结束卡片：自定义视图才能把「开通会员」做成深色渐变按钮，
    /// 系统 alert 的按钮背景改不了。
    private var endCard: some View {
        ZStack {
            Color.black.opacity(0.62).ignoresSafeArea()
                .allowsHitTesting(false)
            VStack(spacing: 16) {
                Text(config.endTitle).font(.title3.bold())
                    .foregroundStyle(.white)
                Text(config.endBody).font(.subheadline)
                    .foregroundStyle(.white.opacity(0.78))
                    .multilineTextAlignment(.center)
                HStack(spacing: 12) {
                    Button {
                        showEnd = false
                        onSkip()
                    } label: {
                        Text(config.endSecondary).font(.subheadline.bold())
                            .frame(maxWidth: .infinity).padding(.vertical, 13)
                            .background(Color.white.opacity(0.14), in: Capsule())
                            .foregroundStyle(.white)
                    }
                    .buttonStyle(.plain)
                    Button {
                        showEnd = false
                        onVIP()
                    } label: {
                        Text(config.endPrimary).font(.subheadline.bold())
                            .frame(maxWidth: .infinity).padding(.vertical, 13)
                            .background(
                                LinearGradient(colors: [Color(hex: theme.ctaBackground),
                                                        Color(hex: theme.ctaGradientEnd)],
                                               startPoint: .leading, endPoint: .trailing),
                                in: Capsule())
                            .overlay(Capsule().stroke(Color(hex: theme.ctaBorder), lineWidth: 1.5))
                            .foregroundStyle(Color(hex: theme.ctaText))
                    }
                    .buttonStyle(.plain)
                }
            }
            .padding(22)
            .background(Color(hex: theme.ctaBackground).opacity(0.94),
                        in: RoundedRectangle(cornerRadius: 22))
            .padding(.horizontal, 28)
        }
    }
}

// MARK: - 3. 棱镜会员三档

private struct VIPPackagesView: View {
    let config: VIPConfig
    let theme: PaywallTheme
    let isPaying: Bool
    let onCheckout: (VIPPackage) -> Void
    let onLogin: () -> Void
    let onExit: () -> Void
    @State private var selectedID: String

    init(config: VIPConfig, theme: PaywallTheme, isPaying: Bool,
         onCheckout: @escaping (VIPPackage) -> Void,
         onLogin: @escaping () -> Void,
         onExit: @escaping () -> Void) {
        self.config = config
        self.theme = theme
        self.isPaying = isPaying
        self.onCheckout = onCheckout
        self.onLogin = onLogin
        self.onExit = onExit
        _selectedID = State(initialValue: config.packages.first?.id ?? "")
    }

    var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                ScrollView {
                    VStack(alignment: .leading, spacing: 16) {
                        Text(config.title).font(.largeTitle.bold())
                        Text(config.subtitle ?? "开通会员，解锁完整内容")
                            .font(.subheadline).foregroundStyle(.secondary)

                        ForEach(config.packages) { pkg in
                            Button {
                                selectedID = pkg.id
                            } label: {
                                HStack(alignment: .center, spacing: 12) {
                                    VStack(alignment: .leading, spacing: 5) {
                                        HStack(spacing: 8) {
                                            Text(pkg.name).font(.headline)
                                            if !pkg.tag.isEmpty {
                                                Text(pkg.tag).font(.caption2.bold())
                                                    .padding(.horizontal, 7).padding(.vertical, 3)
                                                    .background(Color(hex: "#FF9F5A"), in: Capsule())
                                                    .foregroundStyle(.white)
                                            }
                                        }
                                        Text(pkg.perk).font(.caption).foregroundStyle(.secondary)
                                    }
                                    Spacer()
                                    VStack(alignment: .trailing, spacing: 2) {
                                        Text("¥\(String(format: "%.2f", pkg.price))")
                                            .font(.title3.bold())
                                        Text("¥\(String(format: "%.2f", pkg.originPrice))")
                                            .font(.caption).strikethrough().foregroundStyle(.secondary)
                                    }
                                }
                                .padding(16)
                                .background(
                                    RoundedRectangle(cornerRadius: 20)
                                        .fill(selectedID == pkg.id ?
                                              Color(hex: theme.ctaGradientEnd).opacity(0.20) :
                                              Color.secondary.opacity(0.08))
                                )
                                .overlay(
                                    RoundedRectangle(cornerRadius: 20)
                                        .stroke(selectedID == pkg.id ?
                                                Color(hex: theme.selectedBorder) : Color.clear,
                                                lineWidth: 2)
                                )
                            }
                            .buttonStyle(.plain)
                        }
                    }
                    .padding()
                }

                VStack(spacing: 10) {
                    Button {
                        if let pkg = config.packages.first(where: { $0.id == selectedID }) {
                            onCheckout(pkg)
                        }
                    } label: {
                        HStack {
                            if isPaying { ProgressView().tint(.white) }
                            Text(config.button).font(.headline)
                        }
                        .frame(maxWidth: .infinity).padding(.vertical, 16)
                        .background(
                            LinearGradient(colors: [Color(hex: theme.ctaBackground),
                                                    Color(hex: theme.ctaGradientEnd)],
                                           startPoint: .leading, endPoint: .trailing),
                            in: RoundedRectangle(cornerRadius: 26))
                        .overlay(RoundedRectangle(cornerRadius: 26)
                            .stroke(Color(hex: theme.ctaBorder), lineWidth: 1.6))
                        .foregroundStyle(Color(hex: theme.ctaText))
                        .shadow(color: Color(hex: theme.ctaShadow).opacity(0.55),
                                radius: 14, y: 6)
                    }
                    .disabled(isPaying)
                    HStack(spacing: 14) {
                        Button(config.loginEntry ?? "登录") { onLogin() }
                        Button("暂不开通 · 看片段") { onExit() }
                    }
                    .font(.caption)
                    Text("支付即表示同意" + config.agreements.joined())
                        .font(.caption2).foregroundStyle(.secondary)
                }
                .padding(.horizontal).padding(.bottom, 18)
            }
            .navigationTitle(config.title)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Button { onExit() } label: {
                        Image(systemName: "chevron.left").font(.body.weight(.semibold))
                    }
                }
            }
        }
    }
}

// MARK: - 4. 手机号免验证码登录(录屏 42.66–54.04s)

private struct LoginSheet: View {
    let config: LoginConfig
    let onSuccess: (String) -> Void
    @State private var phone = ""
    @State private var confirm = ""
    @State private var agreed = false
    @State private var error: String?
    @State private var busy = false
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            VStack(alignment: .leading, spacing: 18) {
                Text("手机号登录").font(.title2.bold())
                Text("无需验证码，两次输入一致即可")
                    .font(.caption).foregroundStyle(.secondary)

                TextField(config.placeholder, text: $phone)
                    .keyboardType(.numberPad).textFieldStyle(.roundedBorder)
                if config.confirmPhoneInput {
                    TextField(config.confirmPlaceholder, text: $confirm)
                        .keyboardType(.numberPad).textFieldStyle(.roundedBorder)
                }
                Toggle(isOn: $agreed) {
                    Text("已阅读并同意" + config.agreements.joined()).font(.caption)
                }
                if let error {
                    Text(error).font(.caption).foregroundStyle(.red)
                }
                Button {
                    Task { await submit() }
                } label: {
                    HStack {
                        if busy { ProgressView().tint(.black) }
                        Text(config.submit).font(.headline)
                    }
                    .frame(maxWidth: .infinity).padding(.vertical, 14)
                    .background(Color(hex: "#35E0C8"), in: RoundedRectangle(cornerRadius: 26))
                    .foregroundStyle(Color(hex: "#06121A"))
                }
                .disabled(busy)
                Spacer()
            }
            .padding()
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("关闭") { dismiss() }
                }
            }
        }
    }

    @MainActor
    private func submit() async {
        error = nil
        guard phone.count == 11, phone.hasPrefix("1") else {
            error = "请输入 11 位手机号"; return
        }
        if config.confirmPhoneInput && confirm != phone {
            error = "两次输入的手机号不一致"; return
        }
        guard agreed else { error = "请先勾选协议"; return }
        busy = true
        defer { busy = false }
        let body: [String: Any] = [
            "phone": phone,
            "confirm_phone": confirm,
            "agreed": agreed,
            "idfv": UIDevice.current.identifierForVendor?.uuidString ?? "",
            "channel": ConfigChannel.current
        ]
        do {
            let data = try await ChannelAttribution.shared.postJSON(
                path: "/v1/login", body: body, timeout: 6)
            let res = try JSONDecoder().decode(LoginResponse.self, from: data)
            if res.ok {
                // 保存登录票据，注销时用它在服务端做身份校验
                AccountDeletion.saveLogin(
                    ticket: res.ticket ?? "ok",
                    maskedPhone: String(phone.prefix(3)) + "****" + String(phone.suffix(4)))
                onSuccess(res.ticket ?? "ok")
            } else { error = "登录失败" }
        } catch {
            self.error = error.localizedDescription
        }
    }
}

/// 登录请求带渠道号，订单/会员/登录三条日志按同一渠道维度可对齐。
private enum ConfigChannel {
    static var current: String { UserDefaults.standard.string(forKey: "resolved_channel") ?? "organic" }
}

// MARK: - 5. 已解锁

private struct UnlockedView: View {
    let videoURL: String
    let orderNo: String?
    let onExit: () -> Void
    @State private var player = AVPlayer()

    var body: some View {
        ZStack(alignment: .top) {
            Color.black.ignoresSafeArea()
            VideoPlayer(player: player).ignoresSafeArea()
                .onAppear {
                    if let url = URL(string: videoURL) {
                        player.replaceCurrentItem(with: AVPlayerItem(url: url))
                        player.play()
                    }
                }
            HStack {
                Label("会员已开通", systemImage: "checkmark.seal.fill")
                    .font(.caption.bold()).foregroundStyle(.white)
                    .padding(.horizontal, 12).padding(.vertical, 7)
                    .background(.green.opacity(0.85), in: Capsule())
                Spacer()
                Button("回到首页") { onExit() }
                    .font(.caption.bold()).foregroundStyle(.white)
                    .padding(.horizontal, 12).padding(.vertical, 7)
                    .background(.ultraThinMaterial, in: Capsule())
            }.padding(.top, 52).padding(.horizontal, 16)
            if let no = orderNo {
                VStack {
                    Spacer()
                    Text("订单 \(no)").font(.caption2).foregroundStyle(.white.opacity(0.5))
                        .padding(.bottom, 20)
                }
            }
        }
    }
}

// MARK: - Hex 颜色(SwiftUI)

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

// MARK: - 渠道形态防截屏（截屏保存下来是一张白图）

/// 内容 layer 挂在 isSecureTextEntry 的 canvas layer 下：系统截屏/录屏会剔除
/// 该子树，露出容器纯白底，于是保存下来是一张白图。
///
/// build 7 回归原因：field 被设成 isUserInteractionEnabled=false，且
/// host.view 的 layer 被挪进 secure layer 后，视图树(container→host.view)与
/// layer 树(container→field→secure→host.view.layer)不一致，UIKit 默认
/// hitTest 的坐标转换走 layer 树，所有 SwiftUI 按钮都收不到触摸。
/// build 8 修法：容器显式把命中测试转发给内容视图(用 frame 而非 layer 转换)，
/// field 保持可交互且不抢 firstResponder，hosting controller 挂进父 VC 层级。
struct SecureScreen<Content: View>: View {
    private let content: Content
    @State private var hideForSnapshot = false
    @Environment(\.scenePhase) private var scenePhase

    init(@ViewBuilder content: () -> Content) { self.content = content() }

    var body: some View {
        SecureLayer(content: content)
            .ignoresSafeArea()
            .overlay {
                // 白遮罩绝不能吞点击（build 7 的第二个风险点）。
                if hideForSnapshot {
                    Color.white.ignoresSafeArea().allowsHitTesting(false)
                }
            }
            .onChange(of: scenePhase) { _, phase in
                hideForSnapshot = phase != .active
            }
            .onReceive(NotificationCenter.default.publisher(
                for: UIApplication.userDidTakeScreenshotNotification)) { _ in
                hideForSnapshot = true
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.7) {
                    hideForSnapshot = false
                }
            }
            .onReceive(NotificationCenter.default.publisher(
                for: UIScreen.capturedDidChangeNotification)) { _ in
                hideForSnapshot = UIScreen.main.isCaptured
            }
    }
}

/// secure 容器：显式把触摸转发给内容视图，绕开 layer 树与视图树不一致。
private final class SecureContainerView: UIView {
    weak var contentHost: UIView?

    override func hitTest(_ point: CGPoint, with event: UIEvent?) -> UIView? {
        if let v = contentHost, !v.isHidden, v.alpha > 0.01,
           v.isUserInteractionEnabled {
            // 用 frame 做坐标换算，不依赖 CALayer 的 superlayer 链。
            let p = CGPoint(x: point.x - v.frame.origin.x,
                            y: point.y - v.frame.origin.y)
            if let hit = v.hitTest(p, with: event) { return hit }
        }
        return super.hitTest(point, with: event)
    }
}

/// 只做渲染遮挡，绝不变成第一响应者、绝不把点击吞在自己身上。
private final class SecureTextField: UITextField {
    override var canBecomeFirstResponder: Bool { false }
    override func becomeFirstResponder() -> Bool { false }
    override func hitTest(_ point: CGPoint, with event: UIEvent?) -> UIView? {
        for v in subviews.reversed() {
            if let hit = v.hitTest(v.convert(point, from: self), with: event) {
                return hit
            }
        }
        return nil
    }
}

private extension UIView {
    var owningViewController: UIViewController? {
        var r: UIResponder? = next
        while let cur = r {
            if let vc = cur as? UIViewController { return vc }
            r = cur.next
        }
        return nil
    }
}

private struct SecureLayer<Content: View>: UIViewRepresentable {
    let content: Content

    func makeUIView(context: Context) -> SecureContainerView {
        let b = UIScreen.main.bounds
        let container = SecureContainerView(frame: b)
        container.backgroundColor = .white
        container.clipsToBounds = true
        container.isUserInteractionEnabled = true

        let field = SecureTextField(frame: b)
        field.text = " "                 // 让系统确实创建 secure canvas layer
        field.isSecureTextEntry = true
        field.isUserInteractionEnabled = true   // 关掉会让整条 layer 分支不参与命中
        field.backgroundColor = .clear
        field.textColor = .clear
        container.addSubview(field)
        field.layoutIfNeeded()

        let host = UIHostingController(rootView: content)
        host.view.backgroundColor = .clear
        host.view.frame = b
        host.view.autoresizingMask = [.flexibleWidth, .flexibleHeight]
        host.view.isUserInteractionEnabled = true
        container.addSubview(host.view)
        container.contentHost = host.view

        let co = context.coordinator
        co.controller = host
        co.contentView = host.view
        co.field = field
        // 把内容 layer 放进 secure canvas：截屏时该子树被剔除 → 白图。
        // 命中测试已由 container 显式转发，不再依赖这棵 layer 树。
        if let secure = field.layer.sublayers?.first {
            secure.frame = field.bounds
            secure.addSublayer(host.view.layer)
            co.secureLayer = secure
        }
        return container
    }

    func updateUIView(_ uiView: SecureContainerView, context: Context) {
        let b = uiView.bounds.isEmpty ? UIScreen.main.bounds : uiView.bounds
        let co = context.coordinator
        co.field?.frame = b
        co.contentView?.frame = b
        co.secureLayer?.frame = b
        // 挂进父 VC 层级：sheet / alert / NavigationStack 才能正常弹出。
        // makeUIView 时视图还没进 window，拿不到父 VC，只能在这里补。
        if let host = co.controller, host.parent == nil,
           let parent = uiView.owningViewController {
            parent.addChild(host)
            host.didMove(toParent: parent)
        }
        // 故意不重设 rootView：否则会重建整棵 SwiftUI 树，把付费墙的
        // phase / 试看进度等 @State 打回初始值。
    }

    func makeCoordinator() -> Coordinator { Coordinator() }

    final class Coordinator {
        var controller: UIHostingController<Content>?
        var contentView: UIView?
        var field: SecureTextField?
        var secureLayer: CALayer?
    }
}
