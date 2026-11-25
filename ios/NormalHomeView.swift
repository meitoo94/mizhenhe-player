import SwiftUI
import AVKit
import UniformTypeIdentifiers

/// 合规态: 功能规格与「万能云播放器」逐项一致(四 Tab / 四大玩法 / 云字幕 / 会员占位),
/// 但视觉、命名、布局、组件全部由服务端 normal 配置驱动 —— 换皮肤不用改代码、不用发版。
struct NormalHomeView: View {
    let config: NormalConfig
    /// 账户页「会员中心」重新唤起付费墙的入口(录屏里首页与会员页可来回切)
    var onOpenVIP: (() -> Void)? = nil

    @State private var selection = 0
    private var theme: ThemeConfig { config.theme }

    var body: some View {
        TabView(selection: $selection) {
            HomeTab(cfg: config)
                .tabItem { Label(config.tabs[safe: 0] ?? "首页",
                                 systemImage: theme.tabIcons[safe: 0] ?? "play.rectangle") }
                .tag(0)
            LibraryTab(cfg: config)
                .tabItem { Label(config.tabs[safe: 1] ?? "媒体库",
                                 systemImage: theme.tabIcons[safe: 1] ?? "square.grid.2x2") }
                .tag(1)
            CraftTab(cfg: config)
                .tabItem { Label(config.tabs[safe: 2] ?? "创作",
                                 systemImage: theme.tabIcons[safe: 2] ?? "wand.and.stars") }
                .tag(2)
            MineTab(cfg: config, onOpenVIP: onOpenVIP)
                .tabItem { Label(config.tabs[safe: 3] ?? "我的",
                                 systemImage: theme.tabIcons[safe: 3] ?? "person.crop.circle") }
                .tag(3)
        }
        .tint(Color(hex: theme.primary))
        .preferredColorScheme(theme.background.hasPrefix("#0") ? .dark : .light)
    }
}

// MARK: - 主题工具

private extension Color {
    init(hex: String) {
        let s = hex.trimmingCharacters(in: CharacterSet(charactersIn: "#"))
        var v: UInt64 = 0
        Scanner(string: s).scanHexInt64(&v)
        let r = Double((v >> 16) & 0xff) / 255
        let g = Double((v >> 8) & 0xff) / 255
        let b = Double(v & 0xff) / 255
        self.init(.sRGB, red: r, green: g, blue: b, opacity: 1)
    }
}

private struct SkinTheme {
    let c: ThemeConfig
    var primary: Color { Color(hex: c.primary) }
    var accent: Color { Color(hex: c.accent) }
    var bg: Color { Color(hex: c.background) }
    var surface: Color { Color(hex: c.surface) }
    var text: Color { Color(hex: c.text) }
    var muted: Color { Color(hex: c.muted) }
    var radius: CGFloat { CGFloat(c.corner) }
}

/// 品牌头: 每个 Tab 顶部统一的签名条, 替代旧皮肤的导航栏标题
private struct BrandBar<C: View>: View {
    let cfg: NormalConfig
    let trailing: C
    init(cfg: NormalConfig, @ViewBuilder trailing: () -> C = { EmptyView() }) {
        self.cfg = cfg; self.trailing = trailing()
    }
    var body: some View {
        let t = SkinTheme(c: cfg.theme)
        HStack(alignment: .bottom) {
            VStack(alignment: .leading, spacing: 3) {
                Text(cfg.brand).font(.caption.bold()).tracking(3).foregroundStyle(t.primary)
                Text(cfg.tagline).font(.title3.bold()).foregroundStyle(t.text)
            }
            Spacer()
            trailing
        }
        .padding(.horizontal).padding(.top, 8).padding(.bottom, 14)
    }
}

private struct GridBackground: View {
    let c: ThemeConfig
    var body: some View {
        let t = SkinTheme(c: c)
        ZStack {
            t.bg.ignoresSafeArea()
            // 细网格底纹: 新皮肤的视觉标识
            VStack(spacing: 28) {
                ForEach(0..<20, id: \.self) { _ in
                    Rectangle().fill(t.primary.opacity(0.045)).frame(height: 1)
                }
            }.ignoresSafeArea()
        }
    }
}

private struct SkinCard<C: View>: View {
    let c: ThemeConfig
    let content: C
    init(c: ThemeConfig, @ViewBuilder content: () -> C) {
        self.c = c; self.content = content()
    }
    var body: some View {
        let t = SkinTheme(c: c)
        content
            .padding(16)
            .background(t.surface, in: RoundedRectangle(cornerRadius: t.radius))
            .overlay(RoundedRectangle(cornerRadius: t.radius)
                .stroke(t.primary.opacity(0.16), lineWidth: 1))
    }
}

// MARK: - Tab 1 今映(对应 动界)

private struct HomeTab: View {
    let cfg: NormalConfig
    private var t: SkinTheme { SkinTheme(c: cfg.theme) }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 18) {
                    BrandBar(cfg: cfg) {
                        Image(systemName: "plus.circle.fill").font(.title2).foregroundStyle(t.primary)
                    }

                    // Hero: 今日放映(空库时显示 0 影片 / 0 合集 / 0 创作)
                    VStack(alignment: .leading, spacing: 12) {
                        HStack {
                            Text(cfg.home.sections[safe: 1] ?? "今日放映")
                                .font(.headline).foregroundStyle(t.text)
                            Spacer()
                            Text("LOCAL ONLY").font(.caption2).tracking(2).foregroundStyle(t.muted)
                        }
                        HStack(spacing: 10) {
                            ForEach(cfg.home.stats, id: \.self) { k in
                                VStack(spacing: 2) {
                                    Text("\(cfg.home.emptyLibrary[k] ?? 0)")
                                        .font(.title2.bold().monospacedDigit()).foregroundStyle(t.primary)
                                    Text(k).font(.caption2).foregroundStyle(t.muted)
                                }.frame(maxWidth: .infinity)
                            }
                        }
                        .padding(.vertical, 14)
                        .background(t.bg.opacity(0.55), in: RoundedRectangle(cornerRadius: 14))
                    }
                    .padding(16)
                    .background(LinearGradient(colors: [t.primary.opacity(0.20), t.accent.opacity(0.10)],
                                               startPoint: .topLeading, endPoint: .bottomTrailing),
                               in: RoundedRectangle(cornerRadius: t.radius))

                    // 精选频道: 横向 chip 卡
                    Text(cfg.home.sections[safe: 0] ?? "精选频道")
                        .font(.headline).foregroundStyle(t.text)
                    ScrollView(.horizontal, showsIndicators: false) {
                        HStack(spacing: 10) {
                            ForEach(cfg.home.channels, id: \.self) { ch in
                                HStack(spacing: 8) {
                                    Image(systemName: "square.grid.2x2.fill").font(.caption)
                                    Text(ch).font(.subheadline)
                                }
                                .foregroundStyle(t.text)
                                .padding(.horizontal, 16).padding(.vertical, 12)
                                .background(t.surface, in: Capsule())
                                .overlay(Capsule().stroke(t.primary.opacity(0.28), lineWidth: 1))
                            }
                        }
                    }

                    Text(cfg.home.sections[safe: 2] ?? "连看影单")
                        .font(.headline).foregroundStyle(t.text)
                    SkinCard(c: cfg.theme) {
                        HStack(spacing: 12) {
                            Image(systemName: "film.stack").font(.title3).foregroundStyle(t.accent)
                            VStack(alignment: .leading, spacing: 3) {
                                Text(cfg.home.emptyTips[safe: 0] ?? "")
                                    .font(.subheadline).foregroundStyle(t.text)
                                Text(cfg.home.emptyTips[safe: 1] ?? "")
                                    .font(.caption).foregroundStyle(t.muted)
                            }
                            Spacer()
                        }
                    }
                    Text(cfg.headline).font(.caption).foregroundStyle(t.muted)
                        .padding(.top, 4)
                }
                .padding(.horizontal)
            }
            .background(GridBackground(c: cfg.theme))
            .navigationBarHidden(true)
        }
    }
}

// MARK: - Tab 2 片仓(对应 万象)

/// 合规态本地播放器: 商店名称叫「米帧盒播放器」, 审核员必须能找到一个真能播视频的入口。
/// 只从系统文件选择器读取用户自己的视频, 不上传、不联网、不写任何服务端状态。
private struct LocalPlayerView: UIViewControllerRepresentable {
    let url: URL
    func makeUIViewController(context: Context) -> AVPlayerViewController {
        let vc = AVPlayerViewController()
        vc.player = AVPlayer(url: url)
        vc.player?.play()
        return vc
    }
    func updateUIViewController(_ uiViewController: AVPlayerViewController, context: Context) { }
}

private struct LibraryTab: View {
    let cfg: NormalConfig
    @State private var query = ""
    @State private var mode = 0
    @State private var showImporter = false
    @State private var playerURL: URL?
    private var t: SkinTheme { SkinTheme(c: cfg.theme) }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    BrandBar(cfg: cfg)
                    HStack(spacing: 10) {
                        Image(systemName: "magnifyingglass").foregroundStyle(t.muted)
                        TextField(cfg.library.searchPlaceholder, text: $query)
                            .foregroundStyle(t.text)
                    }
                    .padding(12)
                    .background(t.surface, in: RoundedRectangle(cornerRadius: 14))
                    .overlay(RoundedRectangle(cornerRadius: 14)
                        .stroke(t.primary.opacity(0.22), lineWidth: 1))

                    HStack {
                        Picker("", selection: $mode) {
                            ForEach(0..<cfg.library.viewModes.count, id: \.self) { i in
                                Text(cfg.library.viewModes[i]).tag(i)
                            }
                        }
                        .pickerStyle(.segmented)
                        .frame(width: 190)
                        Spacer()
                        ForEach(cfg.library.bulkActions, id: \.self) { a in
                            Text(a).font(.caption).foregroundStyle(t.muted)
                                .padding(.horizontal, 10).padding(.vertical, 6)
                                .background(t.surface, in: Capsule())
                        }
                    }

                    VStack(spacing: 12) {
                        Image(systemName: "square.grid.3x3.topleft.filled")
                            .font(.system(size: 40)).foregroundStyle(t.primary.opacity(0.7))
                        Text(cfg.library.emptyTitle).font(.headline).foregroundStyle(t.text)
                        Text(cfg.library.importHint).font(.caption).foregroundStyle(t.muted)
                            .multilineTextAlignment(.center)
                        Button(cfg.library.importButton) { showImporter = true }
                            .buttonStyle(.borderedProminent).tint(t.primary)
                            .foregroundStyle(t.bg)
                    }
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 40)
                    .background(t.surface.opacity(0.5), in: RoundedRectangle(cornerRadius: t.radius))
                }
                .padding(.horizontal)
            }
            .background(GridBackground(c: cfg.theme))
            .navigationBarHidden(true)
            .fileImporter(isPresented: $showImporter, allowedContentTypes: [.movie]) { result in
                guard case .success(let src) = result else { return }
                let scoped = src.startAccessingSecurityScopedResource()
                defer { if scoped { src.stopAccessingSecurityScopedResource() } }
                let dst = URL(fileURLWithPath: NSTemporaryDirectory())
                    .appendingPathComponent("local-" + UUID().uuidString + "." + src.pathExtension)
                do {
                    if FileManager.default.fileExists(atPath: dst.path) {
                        try FileManager.default.removeItem(at: dst)
                    }
                    try FileManager.default.copyItem(at: src, to: dst)
                    playerURL = dst
                } catch { }
            }
            .sheet(isPresented: Binding(
                get: { playerURL != nil },
                set: { if !$0 { playerURL = nil } }
            )) {
                if let u = playerURL {
                    LocalPlayerView(url: u).ignoresSafeArea()
                }
            }
        }
    }
}

// MARK: - Tab 3 造片(对应 创研台)

private struct CraftTab: View {
    let cfg: NormalConfig
    private var t: SkinTheme { SkinTheme(c: cfg.theme) }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 18) {
                    BrandBar(cfg: cfg)
                    Text(cfg.craft.headline).font(.title2.bold()).foregroundStyle(t.text)

                    // 2x2 玩法宫格: 功能与万能云四大玩法一一对应
                    let cols = [GridItem(.flexible(), spacing: 12), GridItem(.flexible(), spacing: 12)]
                    LazyVGrid(columns: cols, spacing: 12) {
                        ForEach(cfg.craft.features) { f in
                            VStack(alignment: .leading, spacing: 10) {
                                ZStack {
                                    Circle().fill(t.primary.opacity(0.14)).frame(width: 40, height: 40)
                                    Image(systemName: f.icon).font(.title3).foregroundStyle(t.primary)
                                }
                                Text(f.name).font(.headline).foregroundStyle(t.text)
                                Text(f.desc).font(.caption2).foregroundStyle(t.muted)
                                    .lineLimit(3)
                                Spacer(minLength: 0)
                            }
                            .frame(maxWidth: .infinity, minHeight: 152, alignment: .leading)
                            .padding(14)
                            .background(t.surface, in: RoundedRectangle(cornerRadius: t.radius))
                            .overlay(RoundedRectangle(cornerRadius: t.radius)
                                .stroke(t.primary.opacity(0.14), lineWidth: 1))
                        }
                    }

                    SkinCard(c: cfg.theme) {
                        VStack(alignment: .leading, spacing: 8) {
                            HStack {
                                Image(systemName: "cloud.fill").foregroundStyle(t.accent)
                                Text(cfg.craft.cloud.name).font(.headline).foregroundStyle(t.text)
                                Spacer()
                                Text("72h").font(.caption2).foregroundStyle(t.muted)
                            }
                            Text(cfg.craft.cloud.desc).font(.caption).foregroundStyle(t.muted)
                            HStack(spacing: 8) {
                                ForEach(cfg.craft.cloud.items, id: \.self) { i in
                                    Text(i).font(.caption2).foregroundStyle(t.primary)
                                        .padding(.horizontal, 10).padding(.vertical, 5)
                                        .background(t.primary.opacity(0.10), in: Capsule())
                                }
                            }
                        }
                    }
                }
                .padding(.horizontal)
            }
            .background(GridBackground(c: cfg.theme))
            .navigationBarHidden(true)
        }
    }
}

// MARK: - Tab 4 账户(对应 我的)

private struct MineTab: View {
    let cfg: NormalConfig
    let onOpenVIP: (() -> Void)?
    @State private var showDeleteAccount = false
    private var t: SkinTheme { SkinTheme(c: cfg.theme) }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 18) {
                    BrandBar(cfg: cfg)
                    SkinCard(c: cfg.theme) {
                        HStack(spacing: 14) {
                            ZStack {
                                Circle().fill(t.primary.opacity(0.16)).frame(width: 54, height: 54)
                                Image(systemName: "person.crop.circle.fill")
                                    .font(.system(size: 34)).foregroundStyle(t.primary)
                            }
                            VStack(alignment: .leading, spacing: 4) {
                                Text(cfg.mine.account).font(.headline).foregroundStyle(t.text)
                                Text(cfg.mine.accountHint).font(.caption).foregroundStyle(t.muted)
                            }
                            Spacer()
                        }
                    }

                    Button {
                        onOpenVIP?()
                    } label: {
                        HStack {
                            VStack(alignment: .leading, spacing: 4) {
                                Text(cfg.mine.membership.name).font(.headline)
                                Text(cfg.mine.membership.placeholder).font(.caption)
                            }
                            Spacer()
                            Image(systemName: "chevron.right")
                        }
                        .foregroundStyle(t.text)
                        .padding(16)
                        .background(LinearGradient(colors: [t.primary.opacity(0.22), t.accent.opacity(0.12)],
                                                   startPoint: .leading, endPoint: .trailing),
                                   in: RoundedRectangle(cornerRadius: t.radius))
                    }
                    .buttonStyle(.plain)

                    VStack(spacing: 0) {
                        ForEach(cfg.mine.links, id: \.self) { l in
                            HStack {
                                Text(l).font(.subheadline).foregroundStyle(t.text)
                                Spacer()
                                Image(systemName: "chevron.right").font(.caption).foregroundStyle(t.muted)
                            }
                            .padding(.vertical, 14)
                            Divider().background(t.muted.opacity(0.2))
                        }
                    }
                    .padding(.horizontal, 16)
                    .background(t.surface, in: RoundedRectangle(cornerRadius: t.radius))

                    // 5.1.1(v): 支持登录就能在 App 内注销账号，不能只放一个客服链接
                    Button {
                        showDeleteAccount = true
                    } label: {
                        HStack {
                            VStack(alignment: .leading, spacing: 4) {
                                Text("注销账号").font(.headline)
                                Text("删除账号及关联个人数据，App 内即可完成").font(.caption)
                            }
                            Spacer()
                            Image(systemName: "chevron.right")
                        }
                        .foregroundStyle(t.text)
                        .padding(16)
                        .background(t.surface, in: RoundedRectangle(cornerRadius: t.radius))
                    }
                    .buttonStyle(.plain)
                }
                .padding(.horizontal)
            }
            .background(GridBackground(c: cfg.theme))
            .navigationBarHidden(true)
            .sheet(isPresented: $showDeleteAccount) {
                AccountDeletionView(channel: UserDefaults.standard
                    .string(forKey: "resolved_channel") ?? "organic")
            }
        }
    }
}

// MARK: - 安全下标

extension Array {
    subscript(safe i: Int) -> Element? { indices.contains(i) ? self[i] : nil }
}
