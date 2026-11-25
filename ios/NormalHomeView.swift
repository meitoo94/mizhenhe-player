import SwiftUI

/// 合规态外壳。真实功能在 MainTabs（AppViews.swift）+ PlayerToolbox.swift +
/// LocalMediaStore.swift + MineViews.swift，按《万能云播放器·全量功能与组织架构
/// 研究报告》的 4 Tab / 播放器工具箱 / 云端工坊 / 账号体系逐项实现。
///
/// 旧版这里是四个纯静态占位页面（会员点击白屏、协议无内容、导入不落库、
/// 创作格不可点），已整体替换。
struct NormalHomeView: View {
    let config: NormalConfig
    var onOpenVIP: (() -> Void)? = nil

    var body: some View {
        MainTabs(config: config)
    }
}

extension Array {
    subscript(safe i: Int) -> Element? { indices.contains(i) ? self[i] : nil }
}
