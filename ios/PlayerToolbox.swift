import SwiftUI
import AVFoundation
import AVKit
import CoreMedia
import CoreImage
import UIKit

// MARK: - 播放器工具箱（报告 3：光谱 / 动能 / 帧廊 都在播放页，创研台只是入口）

enum Toolbox: String, CaseIterable, Identifiable {
    case grade = "光谱"
    case tempo = "动能"
    case frames = "帧廊"
    case capture = "截帧"
    var id: String { rawValue }
    var icon: String {
        switch self {
        case .grade: return "slider.horizontal.3"
        case .tempo: return "waveform.path.ecg"
        case .frames: return "film"
        case .capture: return "camera"
        }
    }
}

// MARK: 光谱调色台：预设 + 色温/对比/饱和/颗粒/亮度 + 导出渲染

enum GradeFilter {
    static func isOriginal(_ g: GradePreset) -> Bool {
        g.temperature == 0 && g.contrast == 1 && g.saturation == 1 &&
        g.grain == 0 && g.brightness == 0
    }

    static func apply(_ input: CIImage, _ g: GradePreset) -> CIImage {
        var out = input
        if g.temperature != 0 {
            if let f = CIFilter(name: "CITemperatureAndTint") {
                f.setValue(out, forKey: kCIInputImageKey)
                f.setValue(CIVector(x: CGFloat(6500 + g.temperature * 2600), y: 0), forKey: "inputNeutral")
                f.setValue(CIVector(x: 6500, y: 0), forKey: "inputTargetNeutral")
                if let r = f.outputImage { out = r }
            }
        }
        if let f = CIFilter(name: "CIColorControls") {
            f.setValue(out, forKey: kCIInputImageKey)
            f.setValue(g.saturation, forKey: kCIInputSaturationKey)
            f.setValue(g.contrast, forKey: kCIInputContrastKey)
            f.setValue(g.brightness, forKey: kCIInputBrightnessKey)
            if let r = f.outputImage { out = r }
        }
        if g.grain > 0.01 {
            let extent = out.extent
            if let noise = CIFilter(name: "CIRandomGenerator")?.outputImage?.cropped(to: extent) {
                var layer = noise.applyingFilter("CIColorControls", parameters: [
                    kCIInputSaturationKey: 0, kCIInputBrightnessKey: -0.45
                ])
                layer = layer.applyingFilter("CIColorMatrix", parameters: [
                    "inputAVector": CIVector(x: 0, y: 0, z: 0, w: CGFloat(min(0.5, g.grain * 0.42)))
                ])
                out = layer.applyingFilter("CISourceOverCompositing", parameters: [
                    kCIInputBackgroundImageKey: out
                ]).cropped(to: extent)
            }
        }
        return out.cropped(to: input.extent)
    }

    static func composition(for asset: AVAsset, grade: GradePreset) -> AVVideoComposition? {
        guard !isOriginal(grade) else { return nil }
        return AVMutableVideoComposition(asset: asset, applyingCIFiltersWithHandler: { req in
            req.finish(with: GradeFilter.apply(req.sourceImage, grade), context: nil)
        })
    }
}

// MARK: 动能曲线：分段变速 0.25x–4x

enum CompositionBuilder {
    static func build(asset: AVURLAsset, segments: [TempoSegment]) -> AVMutableComposition? {
        let comp = AVMutableComposition()
        let full = CMTimeRange(start: .zero, duration: asset.duration)
        guard let vSrc = asset.tracks(withMediaType: .video).first,
              let vDst = comp.addMutableTrack(withMediaType: .video,
                                              preferredTrackID: kCMPersistentTrackID_Invalid) else { return nil }
        do { try vDst.insertTimeRange(full, of: vSrc, at: .zero) } catch { return nil }
        if let aSrc = asset.tracks(withMediaType: .audio).first,
           let aDst = comp.addMutableTrack(withMediaType: .audio,
                                           preferredTrackID: kCMPersistentTrackID_Invalid) {
            do { try aDst.insertTimeRange(full, of: aSrc, at: .zero) } catch { }
        }
        // 从后往前缩放，避免前面的缩放改变后面区间的时间坐标
        for s in segments.sorted(by: { $0.start > $1.start }) {
            let len = max(0.05, s.end - s.start)
            guard s.rate > 0, s.rate != 1 else { continue }
            let range = CMTimeRange(start: CMTime(seconds: s.start, preferredTimescale: 600),
                                    duration: CMTime(seconds: len, preferredTimescale: 600))
            let to = CMTime(seconds: len / s.rate, preferredTimescale: 600)
            comp.scaleTimeRange(range, toDuration: to)
        }
        return comp
    }

    static func hasEffect(_ segments: [TempoSegment]) -> Bool {
        segments.contains { $0.rate != 1 && $0.end > $0.start }
    }
}

// MARK: 帧廊胶片：等距取帧 + 竖排/横排/九宫格 + 片名时间码

enum FrameStrip {
    static func times(count n: Int, duration: Double) -> [CMTime] {
        guard n > 0 else { return [] }
        let d = max(0.2, duration)
        return (0..<n).map { i in
            CMTime(seconds: d * Double(i) / Double(max(1, n - 1)) * 0.96, preferredTimescale: 600)
        }
    }

    static func grab(url: URL, times: [CMTime]) -> [UIImage] {
        let gen = AVAssetImageGenerator(asset: AVURLAsset(url: url))
        gen.appliesPreferredTrackTransform = true
        gen.maximumSize = CGSize(width: 720, height: 720)
        gen.requestedTimeToleranceBefore = .zero
        gen.requestedTimeToleranceAfter = .zero
        var out: [UIImage] = []
        for t in times {
            if let cg = try? gen.copyCGImage(at: t, actualTime: nil) {
                out.append(UIImage(cgImage: cg))
            }
        }
        return out
    }

    static func compose(images: [UIImage], title: String, duration: Double, layout: Int) -> UIImage? {
        guard !images.isEmpty else { return nil }
        let pad: CGFloat = 36
        let titleH: CGFloat = 108
        let isGrid = layout == 2
        let cols = isGrid ? 3 : (layout == 1 ? images.count : 1)
        let rows = isGrid ? max(1, (images.count + 2) / 3) : (layout == 1 ? 1 : images.count)
        let cellW: CGFloat = isGrid ? 340 : (layout == 1 ? 420 : 1008)
        let cellH: CGFloat = cellW * 9 / 16
        let gap: CGFloat = 18
        let width = pad * 2 + CGFloat(cols) * cellW + CGFloat(max(0, cols - 1)) * gap
        let height = titleH + pad + CGFloat(rows) * (cellH + 34) + CGFloat(max(0, rows - 1)) * gap + pad
        let size = CGSize(width: min(width, 4200), height: min(height, 6000))
        let renderer = UIGraphicsImageRenderer(size: size)
        return renderer.image { _ in
            UIColor(red: 0.09, green: 0.11, blue: 0.16, alpha: 1).setFill()
            UIBezierPath(rect: CGRect(origin: .zero, size: size)).fill()
            let titleAttrs: [NSAttributedString.Key: Any] = [
                .font: UIFont.boldSystemFont(ofSize: 38), .foregroundColor: UIColor.white
            ]
            (title as NSString).draw(at: CGPoint(x: pad, y: 30), withAttributes: titleAttrs)
            let subAttrs: [NSAttributedString.Key: Any] = [
                .font: UIFont.systemFont(ofSize: 20), .foregroundColor: UIColor.lightGray
            ]
            let sub = "米帧盒播放器 · 本地取帧 · \(images.count) 帧 · \(Format.duration(duration))"
            (sub as NSString).draw(at: CGPoint(x: pad, y: 76), withAttributes: subAttrs)
            for (i, img) in images.enumerated() {
                let c = i % cols, r = i / cols
                let x = pad + CGFloat(c) * (cellW + gap)
                let y = titleH + pad + CGFloat(r) * (cellH + 34 + gap)
                let rect = CGRect(x: x, y: y, width: cellW, height: cellH)
                UIColor.black.setFill()
                UIBezierPath(rect: rect).fill()
                img.draw(in: rect)
                UIColor.white.withAlphaComponent(0.08).setStroke()
                let border = UIBezierPath(rect: rect); border.lineWidth = 2; border.stroke()
                let tc = duration * Double(i) / Double(max(1, images.count - 1)) * 0.96
                let stamp = "\(String(format: "%02d", i + 1))  ·  \(Format.duration(tc))"
                (stamp as NSString).draw(at: CGPoint(x: x + 6, y: y + cellH + 6), withAttributes: [
                    .font: UIFont.monospacedDigitSystemFont(ofSize: 18, weight: .medium),
                    .foregroundColor: UIColor.lightGray
                ])
            }
        }
    }
}

// MARK: 导出渲染（调色成片 / 变速成片，报告 2.1「点导出把影调渲染为新视频」）

enum Exporter {
    static func render(asset: AVURLAsset, grade: GradePreset?, segments: [TempoSegment],
                       completion: @escaping (URL?) -> Void) {
        let base: AVAsset = CompositionBuilder.hasEffect(segments)
            ? (CompositionBuilder.build(asset: asset, segments: segments) ?? asset) : asset
        let out = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString + ".mp4")
        try? FileManager.default.removeItem(at: out)
        guard let session = AVAssetExportSession(asset: base,
                                                 presetName: AVAssetExportPresetHighestQuality) else {
            DispatchQueue.main.async { completion(nil) }; return
        }
        if let g = grade, !GradeFilter.isOriginal(g) {
            session.videoComposition = GradeFilter.composition(for: base, grade: g)
        }
        session.outputURL = out
        session.outputFileType = .mp4
        session.shouldOptimizeForNetworkUse = true
        session.exportAsynchronously {
            DispatchQueue.main.async {
                completion(session.status == .completed ? out : nil)
            }
        }
    }
}

// MARK: - 播放器

/// 系统播放器兜底：必须新建 AVPlayer + AVPlayerItem。
/// build 10 复用了已经 failed 的 model.player，只是换了个外壳，
/// 同一个坏 item 照样出不来画面（用户点「系统播放器」也是黑屏）。
private struct SystemPlayerScreen: View {
    let url: URL
    @Environment(\.dismiss) private var dismiss
    @State private var player = AVPlayer()

    var body: some View {
        ZStack(alignment: .topTrailing) {
            SystemPlayerController(player: player).ignoresSafeArea()
            VStack(alignment: .trailing, spacing: 10) {
                Button { dismiss() } label: {
                    Image(systemName: "xmark.circle.fill").font(.title)
                        .foregroundStyle(.white)
                }
                Button {
                    player.replaceCurrentItem(with: AVPlayerItem(url: url))
                    player.play()
                } label: {
                    Text("重新载入").font(.caption2.bold()).foregroundStyle(.white)
                        .padding(.horizontal, 10).padding(.vertical, 6)
                        .background(Color.white.opacity(0.16), in: Capsule())
                }
            }.padding()
        }
        .background(Color.black.ignoresSafeArea())
        .onAppear {
            MZHDiag.report("PLAY 改用系统播放器 · \(url.lastPathComponent)")
            player.replaceCurrentItem(with: AVPlayerItem(url: url))
            player.play()
        }
        .onDisappear { player.pause() }
    }
}

private struct SystemPlayerController: UIViewControllerRepresentable {
    let player: AVPlayer
    func makeUIViewController(context: Context) -> AVPlayerViewController {
        let vc = AVPlayerViewController()
        vc.player = player
        return vc
    }
    func updateUIViewController(_ uiViewController: AVPlayerViewController,
                                context: Context) {
        uiViewController.player = player
    }
}

/// 播放器加载状态：以前只管 play()，asset 还没 ready 或解码失败时
/// 屏幕就是一片纯黑，用户只能猜「是不是还在导入」。现在每一步都可见。
enum PlayerLoadState: Equatable {
    case loading
    case buffering
    case ready
    case failed(String)
}

final class PlayerModel: ObservableObject {
    let player = AVPlayer()
    let asset: AVURLAsset
    let duration: Double
    @Published var playing = false
    @Published var loadState: PlayerLoadState = .loading
    @Published var diagnosis = ""
    /// 自定义层连续停滞重建都无效时，通知界面自动切系统播放器
    @Published var needSystemFallback = false

    /// 播放停滞看门狗：ready 不等于「画面真的在动」
    private var lastSeconds: Double = -1
    private var stallSeconds: Double = 0
    private var watchdogToken = UUID()
    private var fallbackRounds = 0

    private var itemObservation: NSKeyValueObservation?
    private var timeObservation: NSKeyValueObservation?
    private var periodic: Any?
    private var lastGrade: GradePreset?
    private var lastSegments: [TempoSegment] = []
    private var loadToken = UUID()
    /// 自动恢复期间强制走纯 URL item，不套 AVAsset / CIFilter composition
    private var forcePlain = false
    private var autoRetries = 0
    /// 一次加载只上报一条结果，避免 KVO 与 periodic observer 重复写日志
    private var reportedResult = false

    deinit {
        if let periodic { player.removeTimeObserver(periodic) }
    }

    init(url: URL) {
        asset = AVURLAsset(url: url)
        duration = CMTimeGetSeconds(asset.duration)
        player.volume = 1
        timeObservation = player.observe(\.timeControlStatus, options: [.initial, .new]) {
            [weak self] p, _ in
            DispatchQueue.main.async {
                guard let self else { return }
                switch p.timeControlStatus {
                case .waitingToPlayAtSpecifiedRate:
                    if self.loadState == .ready { self.loadState = .buffering }
                case .playing:
                    if self.loadState == .buffering { self.loadState = .ready }
                    self.playing = true
                case .paused:
                    self.playing = false
                default:
                    break
                }
            }
        }
    }

    /// 只有用户真的在光谱 / 动能里改了参数，才需要 AVAsset + composition。
    /// 单纯点开视频一律用系统最稳的 AVPlayerItem(url:)：build 11 之前用
    /// AVPlayerItem(asset:) 加载 97MB 级 HEVC 时 status 会长时间停在 unknown，
    /// 14 秒后被误判成「副本损坏」——图里那两个大视频就是这么挂的。
    private var needsStyling: Bool {
        let graded = lastGrade.map { !GradeFilter.isOriginal($0) } ?? false
        return graded || CompositionBuilder.hasEffect(lastSegments)
    }

    func rebuild(grade: GradePreset?, segments: [TempoSegment]) {
        lastGrade = grade
        lastSegments = segments
        forcePlain = false
        autoRetries = 0
        reportedResult = false
        needSystemFallback = false
        fallbackRounds = 0
        lastSeconds = -1
        stallSeconds = 0
        diagnosis = ""
        loadState = .loading
        playing = false
        MZHDiag.report("PLAY 开始加载 · \(asset.url.lastPathComponent)")
        installItem()
        startWatchdog()
    }

    private func installItem() {
        let item: AVPlayerItem
        if !forcePlain && needsStyling {
            let base: AVAsset = CompositionBuilder.hasEffect(lastSegments)
                ? (CompositionBuilder.build(asset: asset, segments: lastSegments) ?? asset) : asset
            item = AVPlayerItem(asset: base)
            if let g = lastGrade, !GradeFilter.isOriginal(g) {
                item.videoComposition = GradeFilter.composition(for: base, grade: g)
            }
        } else {
            // 不经过 AVURLAsset 同步解析，也不套 CIFilter：系统自己挑解码器。
            item = AVPlayerItem(url: asset.url)
        }
        // build 12/13 把前向缓冲限制成 1 秒，97MB HEVC 1080p 的解码管线
        // 一直在等数据，status 长期停在 unknown，界面就永远「正在准备画面…」。
        // 交回系统默认缓冲策略，不再人为掐住。

        itemObservation = item.observe(\.status, options: [.initial, .new]) {
            [weak self] it, _ in
            DispatchQueue.main.async {
                guard let self else { return }
                switch it.status {
                case .readyToPlay:
                    self.markReady()
                    self.player.play()
                case .failed:
                    self.playing = false
                    if self.forcePlain { self.fail(Self.friendlyMessage(it.error)) }
                    else { self.autoRecover() }
                default:
                    break
                }
            }
        }
        player.replaceCurrentItem(with: item)

        // status 的 KVO 偶尔不回来；播放时间真的在走就说明画面已经出来了。
        if periodic == nil {
            periodic = player.addPeriodicTimeObserver(
                forInterval: CMTime(seconds: 0.4, preferredTimescale: 600), queue: .main) {
                [weak self] t in
                guard let self else { return }
                let s = CMTimeGetSeconds(t)
                // 只认「时间真的在往前走」，不能因为一次 >0.05 就宣布成功
                guard s.isFinite, s > self.lastSeconds + 0.01 else { return }
                self.lastSeconds = s
                self.stallSeconds = 0
                switch self.loadState {
                case .loading, .buffering: self.markReady()
                default: break
                }
            }
        }
        scheduleTimeout()
    }

    private func markReady() {
        loadToken = UUID()          // 让挂起的超时闭包失效
        autoRetries = 0
        if loadState != .ready { loadState = .ready }
        // build 13 的实锤 bug：periodic observer 也会调 markReady，
        // 但只有 KVO readyToPlay 分支才 player.play()。KVO 没回来时
        // 画面出来了却永远不播 —— 用户截图那张「静止的演唱会」就是它。
        if player.rate < 0.01 { player.play() }
        playing = true
        if !reportedResult {
            reportedResult = true
            MZHDiag.report("PLAY 首帧成功 · \(asset.url.lastPathComponent)")
        }
    }

    private func fail(_ msg: String) {
        loadToken = UUID()
        watchdogToken = UUID()
        playing = false
        loadState = .failed(msg)
        if !reportedResult {
            reportedResult = true
            MZHDiag.report("PLAY 加载失败 · \(asset.url.lastPathComponent) · \(msg)")
        }
    }

    /// 超时 / 解码失败：清空旧解码会话，用纯 URL item 自动重建，最多 2 次。
    private func autoRecover() {
        guard autoRetries < 2 else {
            fail("播放器等待画面超时，已自动重试 2 次")
            return
        }
        autoRetries += 1
        forcePlain = true
        diagnosis = ""
        loadState = .loading
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.4) { [weak self] in
            guard let self else { return }
            self.player.replaceCurrentItem(with: nil)
            self.installItem()
        }
    }

    private func scheduleTimeout() {
        let token = UUID()
        loadToken = token
        let wait: Double = autoRetries > 0 ? 8 : 12
        DispatchQueue.main.asyncAfter(deadline: .now() + wait) { [weak self] in
            guard let self, self.loadToken == token else { return }
            switch self.loadState {
            case .loading, .buffering: self.autoRecover()
            default: return
            }
        }
    }

    /// ready 之后画面可能停在首帧不动（HEVC 解码会话 stall）。
    /// 每秒看一次播放时间，连续 3 秒不推进就重建解码会话，
    /// 再不行自动切系统播放器，不再让用户对着一张静止画面干等。
    private func startWatchdog() {
        let token = UUID()
        watchdogToken = token
        tickWatchdog(token: token)
    }

    private func tickWatchdog(token: UUID) {
        DispatchQueue.main.asyncAfter(deadline: .now() + 1) { [weak self] in
            guard let self, self.watchdogToken == token else { return }
            if self.loadState == .ready {
                let s = CMTimeGetSeconds(self.player.currentTime())
                if s.isFinite, s > self.lastSeconds + 0.01 {
                    self.lastSeconds = s
                    self.stallSeconds = 0
                } else {
                    self.stallSeconds += 1
                    if self.stallSeconds >= 3 { self.handleStall() }
                }
            }
            self.tickWatchdog(token: token)
        }
    }

    private func handleStall() {
        stallSeconds = 0
        fallbackRounds += 1
        MZHDiag.report("PLAY 画面停滞 · 第 \(fallbackRounds) 次自动恢复 · \(asset.url.lastPathComponent)")
        if fallbackRounds < 2 {
            forcePlain = true
            autoRetries = 0
            reportedResult = false
            lastSeconds = -1
            loadState = .loading
            player.replaceCurrentItem(with: nil)
            installItem()
        } else {
            needSystemFallback = true
            watchdogToken = UUID()   // 停掉看门狗，交给系统播放器
            MZHDiag.report("PLAY 自定义层连续停滞 · 自动切系统播放器")
        }
    }

    private static func friendlyMessage(_ err: Error?) -> String {
        let code = (err as NSError?)?.code ?? 0
        if code == -12865 { return "这个编码当前播放器不支持（错误 \(code)），请点系统播放器" }
        if code == -11800 || code == -11828 || code == -11819 {
            return "播放器报告解码错误（\(code)），请点系统播放器打开"
        }
        return "播放器报告错误（\(code)），可改用系统播放器打开"
    }

    private static func codecName(_ raw: String) -> String {
        switch raw {
        case "hvc1", "hev1": return "HEVC/H.265"
        case "avc1": return "H.264"
        case "dvh1", "dvhe": return "杜比视界"
        default: return raw
        }
    }

    /// 把本地副本的真实编码 / 分辨率摆出来，直接回答「为什么这两个不行」
    func diagnose(url: URL) {
        DispatchQueue.global(qos: .userInitiated).async {
            let attrs = try? FileManager.default.attributesOfItem(atPath: url.path)
            let size = (attrs?[.size] as? NSNumber)?.int64Value ?? 0
            let a = AVURLAsset(url: url)
            let sem = DispatchSemaphore(value: 0)
            a.loadValuesAsynchronously(forKeys: ["tracks", "playable", "duration"]) { sem.signal() }
            _ = sem.wait(timeout: .now() + 6)
            let playable = a.isPlayable
            let tracks = a.tracks(withMediaType: .video).count
            var detail = ""
            if a.statusOfValue(forKey: "tracks", error: nil) == .loaded,
               let t = a.tracks(withMediaType: .video).first {
                let sz = t.naturalSize
                var codec = "未知编码"
                if let f = t.formatDescriptions.first {
                    // formatDescriptions 是 [Any]；用描述文本读 FourCC，
                    // 避开 CMFormatDescription 的 CF 类型转换（Swift 会直接报错）。
                    let desc = String(describing: f)
                    for raw in ["hvc1", "hev1", "avc1", "dvh1", "dvhe"] where desc.contains(raw) {
                        codec = Self.codecName(raw)
                        break
                    }
                }
                detail = " · \(codec) · \(Int(sz.width))×\(Int(sz.height)) · \(Int(t.nominalFrameRate.rounded()))fps"
            }
            var s = "本地副本 \(Format.bytes(size)) · 视频轨道 \(tracks) 条" + detail
            if size < 1024 { s = "本地副本只有 \(size) 字节，导入时没写完整" }
            else if !playable { s += " · 系统判定不可播放" }
            else if tracks == 0 { s += " · 没有画面轨道" }
            else { s += " · 文件可读，问题在播放器加载" }
            MZHDiag.report("PLAY 文件诊断 · \(s)")
            DispatchQueue.main.async { self.diagnosis = s }
        }
    }

    func retry() {
        forcePlain = true
        autoRetries = 0
        reportedResult = false
        needSystemFallback = false
        fallbackRounds = 0
        lastSeconds = -1
        stallSeconds = 0
        diagnosis = ""
        loadState = .loading
        MZHDiag.report("PLAY 手动重新加载 · \(asset.url.lastPathComponent)")
        player.replaceCurrentItem(with: nil)
        installItem()
        startWatchdog()
    }

    func toggle() {
        if case .failed = loadState { retry(); return }
        if playing { player.pause() } else { player.play() }
        playing.toggle()
    }

    func currentTime() -> Double { CMTimeGetSeconds(player.currentTime()) }
}

/// 主渲染层改用系统的 AVPlayerViewController（隐藏自带控件）。
/// 自定义 AVPlayerLayer 对 97MB HEVC 会长时间停在 unknown / 首帧静止，
/// 系统播放器自己管缓冲、解码会话与重试，是「照片 App 能播」的那条路径。
private struct EmbeddedSystemPlayer: UIViewControllerRepresentable {
    let player: AVPlayer
    func makeUIViewController(context: Context) -> AVPlayerViewController {
        let vc = AVPlayerViewController()
        vc.player = player
        vc.showsPlaybackControls = false
        vc.view.backgroundColor = .black
        return vc
    }
    func updateUIViewController(_ uiViewController: AVPlayerViewController,
                                context: Context) {
        uiViewController.player = player
        uiViewController.showsPlaybackControls = false
    }
}

struct PlayerLayerView: UIViewRepresentable {
    let player: AVPlayer
    func makeUIView(context: Context) -> PlayerUIView {
        let v = PlayerUIView()
        v.playerLayer.player = player
        v.playerLayer.videoGravity = .resizeAspect
        v.backgroundColor = .black
        return v
    }
    func updateUIView(_ uiView: PlayerUIView, context: Context) {
        uiView.playerLayer.player = player
    }
    final class PlayerUIView: UIView {
        override class var layerClass: AnyClass { AVPlayerLayer.self }
        var playerLayer: AVPlayerLayer { layer as! AVPlayerLayer }
        // SwiftUI 布局后才拿到真实 bounds，显式同步一次，
        // 避免 layer frame 停留在 0 导致有声音没画面（纯黑）。
        override func layoutSubviews() {
            super.layoutSubviews()
            playerLayer.frame = bounds
            playerLayer.videoGravity = .resizeAspect
        }
    }
}

struct PlayerScreen: View {
    let video: LocalVideo
    var initialTool: Toolbox? = nil
    @Environment(\.dismiss) private var dismiss
    @EnvironmentObject private var store: MediaStore
    @StateObject private var model: PlayerModel
    @State private var tool: Toolbox?
    @State private var grade: GradePreset
    @State private var segments: [TempoSegment]
    @State private var busy = false
    @State private var toast = ""
    @State private var showToast = false
    @State private var shareItems: [Any] = []
    @State private var showShare = false
    @State private var showSystemPlayer = false

    init(video: LocalVideo, initialTool: Toolbox? = nil) {
        self.video = video
        self.initialTool = initialTool
        _model = StateObject(wrappedValue: PlayerModel(url: MediaStore.shared.url(for: video)))
        _grade = State(initialValue: video.grade ?? GradePreset.presets[0])
        _segments = State(initialValue: video.tempo ?? [])
    }

    var body: some View {
        ZStack {
            Color.black.ignoresSafeArea()
            VStack(spacing: 0) {
                HStack {
                    Button { dismiss() } label: {
                        Image(systemName: "xmark.circle.fill").font(.title2).foregroundStyle(.white)
                    }
                    VStack(alignment: .leading, spacing: 2) {
                        Text(video.title).font(.subheadline.bold()).foregroundStyle(.white).lineLimit(1)
                        Text("\(Format.duration(video.duration)) · \(video.category) · 本地")
                            .font(.caption2).foregroundStyle(MZH.muted)
                    }
                    Spacer()
                    Button { store.markPlayed(id: video.id) } label: {
                        Image(systemName: "checkmark.circle").foregroundStyle(.white)
                    }
                }
                .padding(.horizontal).padding(.vertical, 10)

                ZStack {
                    EmbeddedSystemPlayer(player: model.player)
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                        .allowsHitTesting(false)
                    // 透明点击层接管播放/暂停，避免系统 VC 把手势吞掉
                    Color.clear.contentShape(Rectangle())
                        .onTapGesture { model.toggle() }

                    // 首帧出来之前先用导入时生成的封面垫底，不再是一片纯黑。
                    if case .loading = model.loadState,
                       let u = store.thumbURL(for: video),
                       let img = UIImage(contentsOfFile: u.path) {
                        Image(uiImage: img).resizable().scaledToFit()
                            .opacity(0.3).allowsHitTesting(false)
                    }

                    switch model.loadState {
                    case .loading:
                        VStack(spacing: 10) {
                            ProgressView().tint(MZH.primary).scaleEffect(1.2)
                            Text("正在准备画面…").font(.caption).foregroundStyle(.white)
                            Text("首次打开大视频需要几秒").font(.caption2)
                                .foregroundStyle(MZH.muted)
                        }
                        .padding(18)
                        .background(.black.opacity(0.45), in: RoundedRectangle(cornerRadius: 14))
                        .allowsHitTesting(false)
                    case .buffering:
                        HStack(spacing: 8) {
                            ProgressView().tint(MZH.primary)
                            Text("正在缓冲…").font(.caption2).foregroundStyle(.white)
                        }
                        .padding(.horizontal, 14).padding(.vertical, 8)
                        .background(.black.opacity(0.55), in: Capsule())
                        .allowsHitTesting(false)
                    case .ready:
                        EmptyView()
                    case .failed(let msg):
                        // 条目出现在片仓时文件已完整，一律给可操作的失败卡；
                        // 不再用「正在上传中」把系统播放器按钮也挡掉。
                        failureCard(msg)
                    }
                }

                HStack(spacing: 0) {
                    ForEach(Toolbox.allCases) { t in
                        Button {
                            if t == .capture { captureFrame() } else { tool = t }
                        } label: {
                            VStack(spacing: 4) {
                                Image(systemName: t.icon).font(.title3)
                                Text(t.rawValue).font(.caption2)
                            }
                            .frame(maxWidth: .infinity)
                            .foregroundStyle(.white)
                        }
                        .disabled(busy)
                    }
                }
                .padding(.vertical, 12)
                .background(Color(white: 0.08))
            }
        }
        .onAppear {
            model.rebuild(grade: grade, segments: segments)
            store.markPlayed(id: video.id)
            if let t = initialTool, t != .capture { tool = t }
        }
        .onDisappear { model.player.pause() }
        .onChange(of: model.needSystemFallback) { _, v in
            if v { showSystemPlayer = true }
        }
        .sheet(item: $tool) { t in
            toolSheet(t)
                .environmentObject(store)
                .presentationDetents([.medium, .large])
        }
        .sheet(isPresented: $showShare) { ShareSheet(items: shareItems) }
        .fullScreenCover(isPresented: $showSystemPlayer) {
            SystemPlayerScreen(url: MediaStore.shared.url(for: video))
        }
        .overlay(alignment: .top) {
            if showToast {
                Text(toast).font(.caption).padding(10)
                    .background(.ultraThinMaterial, in: Capsule())
                    .padding(.top, 60)
                    .transition(.opacity)
            }
        }
        .animation(.easeInOut, value: showToast)
    }

    private func failureCard(_ msg: String) -> some View {
        VStack(spacing: 10) {
            Image(systemName: "exclamationmark.triangle.fill").font(.largeTitle)
                .foregroundStyle(MZH.warn)
            Text("播放器没能加载出画面")
                .font(.subheadline.bold()).foregroundStyle(.white)
            Text("文件已经写进片仓，不是还在上传；点系统播放器可直接换一条解码路径")
                .font(.caption2).foregroundStyle(MZH.muted)
                .multilineTextAlignment(.center)
            Text(msg).font(.caption2).foregroundStyle(MZH.muted)
                .multilineTextAlignment(.center).lineLimit(2)
            Text(model.diagnosis.isEmpty ? "正在检查视频编码…" : model.diagnosis)
                .font(.caption2.monospacedDigit()).foregroundStyle(MZH.primary)
                .multilineTextAlignment(.center).lineLimit(3)
            HStack(spacing: 10) {
                Button("系统播放器") { showSystemPlayer = true }
                    .font(.caption.bold()).foregroundStyle(MZH.onPrimary)
                    .padding(.horizontal, 16).padding(.vertical, 8)
                    .background(MZH.primary, in: Capsule())
                Button("重新加载") { model.retry() }
                    .font(.caption.bold()).foregroundStyle(MZH.primary)
                    .padding(.horizontal, 14).padding(.vertical, 8)
                    .overlay(Capsule().stroke(MZH.primary, lineWidth: 1))
            }
            Button(role: .destructive) {
                store.remove(ids: [video.id])
                dismiss()
            } label: {
                Text("删除这个副本，重新导入").font(.caption2).foregroundStyle(MZH.warn)
            }
        }
        .padding(18).frame(maxWidth: 310)
        .background(MZH.surface.opacity(0.96), in: RoundedRectangle(cornerRadius: 16))
        .overlay(RoundedRectangle(cornerRadius: 16).stroke(MZH.line, lineWidth: 1))
        .onAppear { model.diagnose(url: MediaStore.shared.url(for: video)) }
    }

    @ViewBuilder
    private func toolSheet(_ t: Toolbox) -> some View {
        NavigationStack {
            Group {
                switch t {
                case .grade:
                    GradePanel(grade: $grade, busy: $busy, onPreview: { g in
                        model.rebuild(grade: g, segments: segments)
                    }, onSave: { g in
                        store.setGrade(id: video.id, grade: g)
                        notify("调色已随本片记忆")
                    }, onCapture: { captureFrame() }, onExport: {
                        exportVideo(kind: "调色成片")
                    })
                case .tempo:
                    TempoPanel(segments: $segments, duration: video.duration, busy: $busy,
                               onPreview: { s in model.rebuild(grade: grade, segments: s) },
                               onSave: { s in
                                   store.setTempo(id: video.id, segments: s)
                                   notify("变速方案已保存，下次打开自动生效")
                               }, onExport: { exportVideo(kind: "变速成片") })
                case .frames:
                    FramesPanel(video: video, busy: $busy, onDone: { img in
                        store.addWork(title: video.title + " · 胶片长图", kind: "胶片长图",
                                      data: img.jpegData(compressionQuality: 0.9) ?? Data(), ext: "jpg")
                        notify("已存入「我的云作品」")
                    }, onSaveAlbum: { img in
                        PhotoSaver.saveImage(img) { ok in notify(ok ? "已保存到相册" : "保存失败，请检查相册权限") }
                    })
                case .capture: EmptyView()
                }
            }
            .navigationTitle(toolTitle(t))
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("完成") { tool = nil }
                }
            }
        }
    }

    private func toolTitle(_ t: Toolbox) -> String {
        switch t {
        case .grade: return "光谱调色台"
        case .tempo: return "动能曲线"
        case .frames: return "帧廊胶片"
        case .capture: return "截帧"
        }
    }

    private func captureFrame() {
        let t = CMTime(seconds: max(0, model.currentTime()), preferredTimescale: 600)
        let gen = AVAssetImageGenerator(asset: model.asset)
        gen.appliesPreferredTrackTransform = true
        guard let cg = try? gen.copyCGImage(at: t, actualTime: nil) else {
            notify("取帧失败"); return
        }
        let ci = CIImage(cgImage: cg)
        let out = GradeFilter.apply(ci, grade)
        let img = UIImage(ciImage: out)
        PhotoSaver.saveImage(img) { ok in
            notify(ok ? "已带当前影调截帧保存到相册" : "保存失败，请检查相册权限")
        }
    }

    private func exportVideo(kind: String) {
        guard !busy else { return }
        busy = true
        notify("正在渲染，请稍候…")
        Exporter.render(asset: model.asset, grade: grade, segments: segments) { url in
            busy = false
            guard let url = url else { notify("导出失败，换个短一点的视频再试"); return }
            store.addWork(title: video.title + " · " + kind, kind: kind, from: url)
            notify("已导出到「我的云作品」")
            PhotoSaver.saveVideo(url) { _ in }
        }
    }

    private func notify(_ text: String) {
        toast = text
        showToast = true
        DispatchQueue.main.asyncAfter(deadline: .now() + 2.2) { showToast = false }
    }
}

// MARK: - 光谱面板

private struct GradePanel: View {
    @Binding var grade: GradePreset
    @Binding var busy: Bool
    let onPreview: (GradePreset) -> Void
    let onSave: (GradePreset) -> Void
    let onCapture: () -> Void
    let onExport: () -> Void
    @State private var comparing = false

    private let original = GradePreset.presets[0]

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                Text("横滑选影调预设，滑杆微调后实时预览").font(.caption).foregroundStyle(MZH.muted)
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 10) {
                        ForEach(GradePreset.presets, id: \.name) { p in
                            Button(p.name) {
                                grade = p
                                onPreview(comparing ? original : p)
                                onSave(p)
                            }
                            .buttonStyle(.borderedProminent)
                            .tint(grade.name == p.name ? MZH.primary : MZH.muted)
                        }
                    }
                }
                Group {
                    slider("色温", value: $grade.temperature, range: -1...1)
                    slider("对比度", value: $grade.contrast, range: 0.5...1.8)
                    slider("饱和度", value: $grade.saturation, range: 0...2)
                    slider("颗粒感", value: $grade.grain, range: 0...1)
                    slider("亮度", value: $grade.brightness, range: -0.3...0.3)
                }
                .onChange(of: grade) { _, g in
                    onPreview(comparing ? original : g)
                    onSave(g)
                }

                Button {
                    comparing.toggle()
                    onPreview(comparing ? original : grade)
                } label: {
                    Label(comparing ? "正在查看原片，点此恢复影调" : "按住/点此对比原片",
                          systemImage: "eye")
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(.bordered)

                HStack(spacing: 12) {
                    Button { onCapture() } label: {
                        Label("带滤镜截帧", systemImage: "camera").frame(maxWidth: .infinity)
                    }.buttonStyle(.bordered)
                    Button { onExport() } label: {
                        Label(busy ? "渲染中…" : "导出成片", systemImage: "square.and.arrow.up")
                            .frame(maxWidth: .infinity)
                    }.buttonStyle(.borderedProminent).disabled(busy)
                }
                Text("导出的新视频会存入「我的云作品」，并写入系统相册。调色参数随本片记忆。")
                    .font(.caption2).foregroundStyle(MZH.muted)
            }
            .padding()
        }
    }

    private func slider(_ title: String, value: Binding<Double>, range: ClosedRange<Double>) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack {
                Text(title).font(.subheadline)
                Spacer()
                Text(String(format: "%.2f", value.wrappedValue)).font(.caption.monospacedDigit())
                    .foregroundStyle(MZH.muted)
            }
            Slider(value: value, in: range)
        }
    }
}

// MARK: - 动能面板

private struct TempoPanel: View {
    @Binding var segments: [TempoSegment]
    let duration: Double
    @Binding var busy: Bool
    let onPreview: ([TempoSegment]) -> Void
    let onSave: ([TempoSegment]) -> Void
    let onExport: () -> Void
    private let rates = [0.25, 0.5, 1.0, 2.0, 4.0]

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                Text("时间轴分段变速 0.25x–4x，方案随视频保存").font(.caption).foregroundStyle(MZH.muted)
                if segments.isEmpty {
                    Text("还没有变速段，点下方添加。").font(.subheadline).foregroundStyle(MZH.muted)
                }
                ForEach(Array(segments.enumerated()), id: \.element.id) { idx, seg in
                    VStack(alignment: .leading, spacing: 8) {
                        HStack {
                            Text("区间 \(idx + 1)  \(Format.duration(seg.start)) – \(Format.duration(seg.end))")
                                .font(.subheadline.bold())
                            Spacer()
                            Button(role: .destructive) {
                                segments.removeAll { $0.id == seg.id }
                                onPreview(segments); onSave(segments)
                            } label: { Image(systemName: "trash") }
                        }
                        Picker("速率", selection: Binding(
                            get: { seg.rate },
                            set: { r in
                                if let i = segments.firstIndex(where: { $0.id == seg.id }) {
                                    segments[i].rate = r
                                    onPreview(segments); onSave(segments)
                                }
                            })) {
                            ForEach(rates, id: \.self) { r in
                                Text(String(format: "%g×", r)).tag(r)
                            }
                        }
                        .pickerStyle(.segmented)
                    }
                    .padding(12)
                    .background(MZH.surface2, in: RoundedRectangle(cornerRadius: 12))
                }
                HStack(spacing: 10) {
                    Button {
                        let start = segments.last?.end ?? 0
                        let end = min(duration, start + max(2, duration / 3))
                        segments.append(TempoSegment(start: start, end: end, rate: 2))
                        onPreview(segments); onSave(segments)
                    } label: { Label("添加区间", systemImage: "plus").frame(maxWidth: .infinity) }
                    .buttonStyle(.bordered)
                    Button {
                        segments = []
                        onPreview(segments); onSave(segments)
                    } label: { Label("重置原速", systemImage: "arrow.counterclockwise").frame(maxWidth: .infinity) }
                    .buttonStyle(.bordered)
                }
                Button { onExport() } label: {
                    Label(busy ? "渲染中…" : "导出变速成片", systemImage: "square.and.arrow.up")
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(.borderedProminent).disabled(busy)
                Text("「预览」实时生效：改动后立即按新节奏播放。")
                    .font(.caption2).foregroundStyle(MZH.muted)
            }
            .padding()
        }
    }
}

// MARK: - 帧廊面板

private struct FramesPanel: View {
    let video: LocalVideo
    @Binding var busy: Bool
    let onDone: (UIImage) -> Void
    let onSaveAlbum: (UIImage) -> Void
    @State private var count = 6
    @State private var layout = 0
    @State private var images: [UIImage] = []
    @State private var composed: UIImage?
    @State private var hint = ""

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                Stepper("收帧数量：\(count) 帧（2–12）", value: $count, in: 2...12)
                Picker("版式", selection: $layout) {
                    Text("竖排长卷").tag(0)
                    Text("横排长卷").tag(1)
                    Text("九宫格海报").tag(2)
                }.pickerStyle(.segmented)
                Button {
                    busy = true
                    let url = MediaStore.shared.url(for: video)
                    DispatchQueue.global(qos: .userInitiated).async {
                        let times = FrameStrip.times(count: count, duration: video.duration)
                        let got = FrameStrip.grab(url: url, times: times)
                        DispatchQueue.main.async {
                            images = got
                            composed = nil
                            busy = false
                            hint = got.isEmpty ? "取帧失败，换个视频试试" : "已收集 \(got.count) 帧"
                        }
                    }
                } label: { Label(busy ? "取帧中…" : "等距取帧", systemImage: "film").frame(maxWidth: .infinity) }
                    .buttonStyle(.borderedProminent).disabled(busy)

                if !hint.isEmpty { Text(hint).font(.caption).foregroundStyle(MZH.muted) }

                if !images.isEmpty {
                    ScrollView(.horizontal, showsIndicators: false) {
                        HStack(spacing: 8) {
                            ForEach(Array(images.enumerated()), id: \.offset) { i, img in
                                VStack(spacing: 4) {
                                    Image(uiImage: img).resizable().scaledToFill()
                                        .frame(width: 100, height: 68).clipped()
                                        .cornerRadius(6)
                                    Button(role: .destructive) {
                                        images.remove(at: i)
                                    } label: { Image(systemName: "xmark.circle.fill").font(.caption) }
                                }
                            }
                        }
                    }
                    Button { compose() } label: {
                        Label("合成胶片", systemImage: "rectangle.on.rectangle").frame(maxWidth: .infinity)
                    }.buttonStyle(.bordered)
                }

                if let img = composed {
                    Image(uiImage: img).resizable().scaledToFit()
                        .frame(maxHeight: 320).cornerRadius(10)
                    HStack(spacing: 10) {
                        Button { onSaveAlbum(img) } label: {
                            Label("存相册", systemImage: "photo").frame(maxWidth: .infinity)
                        }.buttonStyle(.bordered)
                        Button { onDone(img) } label: {
                            Label("存入我的云作品", systemImage: "square.and.arrow.down").frame(maxWidth: .infinity)
                        }.buttonStyle(.borderedProminent)
                    }
                }
                Text("自带片名与时间码边框，竖排 / 横排 / 九宫格三种版式。")
                    .font(.caption2).foregroundStyle(MZH.muted)
            }
            .padding()
        }
        .onAppear {
            let url = MediaStore.shared.url(for: video)
            DispatchQueue.global(qos: .userInitiated).async {
                let got = FrameStrip.grab(url: url,
                                          times: FrameStrip.times(count: count, duration: video.duration))
                DispatchQueue.main.async { images = got }
            }
        }
    }

    private func compose() {
        guard let img = FrameStrip.compose(images: images, title: video.title,
                                           duration: video.duration, layout: layout) else { return }
        composed = img
    }
}
