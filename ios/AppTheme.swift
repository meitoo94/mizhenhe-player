import SwiftUI

// MARK: - 米帧盒统一视觉（与 H5 皮肤 demo/index.html 的 CSS 变量逐项对齐）
//
// 图 1 的深色「本地影像 · 归档 · 调色 · 拼帧」是产品既定风格。
// 之前四个 Tab 全用 SwiftUI 系统默认色，真机上变成白底（图 2），
// 这里把配色抽成全局常量，所有页面统一引用，不再依赖系统浅色。

private func mzhRGB(_ r: Double, _ g: Double, _ b: Double, _ a: Double = 1) -> Color {
    Color(.sRGB, red: r / 255.0, green: g / 255.0, blue: b / 255.0, opacity: a)
}

func appVersionText() -> String {
    let v = Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "1.0"
    let b = Bundle.main.object(forInfoDictionaryKey: "CFBundleVersion") as? String ?? "1"
    return "\(v) (\(b))"
}

enum MZH {
    /// 页面背景 #0B1020
    static let bg = mzhRGB(11, 16, 32)
    /// 卡片 #151C30
    static let surface = mzhRGB(21, 28, 48)
    /// 次级卡片 / 输入框 #1B2440
    static let surface2 = mzhRGB(27, 36, 64)
    /// 描边 rgba(255,255,255,.09)
    static let line = mzhRGB(255, 255, 255, 0.09)
    /// 主色 #35E0C8
    static let primary = mzhRGB(53, 224, 200)
    /// 点缀色 #FF9F5A
    static let accent = mzhRGB(255, 159, 90)
    /// 主文字 #F2F6FF
    static let text = mzhRGB(242, 246, 255)
    /// 次要文字 #8A96B4
    static let muted = mzhRGB(138, 150, 180)
    /// 主色按钮上的文字 #06121A
    static let onPrimary = mzhRGB(6, 18, 26)
    /// 成功 #4BD6A0
    static let green = mzhRGB(75, 214, 160)
    static let warn = mzhRGB(255, 159, 90)
    static let danger = mzhRGB(255, 112, 112)
    static let corner: CGFloat = 20
    static let cardCorner: CGFloat = 16

    /// 首页 hero 背景：与 demo 的 radial-gradient 一致的深色青橙光晕
    static var heroGradient: LinearGradient {
        LinearGradient(colors: [mzhRGB(20, 40, 56), mzhRGB(24, 28, 50), bg],
                       startPoint: .topTrailing, endPoint: .bottomLeading)
    }
}

extension View {
    /// 任何页面的根修饰器：深色 + 统一 tint + 背景色，sheet / fullScreenCover 也继承
    func mzhScreen() -> some View {
        self.background(MZH.bg.ignoresSafeArea())
            .preferredColorScheme(.dark)
            .tint(MZH.primary)
    }

    /// List / Form 统一深色：隐藏系统默认背景后铺自己的底色
    func mzhListBackground() -> some View {
        self.scrollContentBackground(.hidden)
            .background(MZH.bg)
    }

    /// 深色卡片（带细描边，对齐 demo .card）
    func mzhCard(padding p: CGFloat = 14, radius r: CGFloat = MZH.cardCorner) -> some View {
        self.padding(p)
            .background(MZH.surface, in: RoundedRectangle(cornerRadius: r))
            .overlay(RoundedRectangle(cornerRadius: r).stroke(MZH.line, lineWidth: 1))
    }

    /// 主色实心按钮
    func mzhPrimaryButton() -> some View {
        self.font(.subheadline.bold())
            .foregroundStyle(MZH.onPrimary)
            .frame(maxWidth: .infinity)
            .padding(.vertical, 13)
            .background(MZH.primary, in: RoundedRectangle(cornerRadius: 14))
    }
}
