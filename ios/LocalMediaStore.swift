import Foundation
import SwiftUI
import AVFoundation
import PhotosUI
import Photos
import Vision
import UIKit
import UniformTypeIdentifiers

// MARK: - 导入 / 播放操作记录上报
// 复用 /v1/app/diag（验证台直接可读）。build 12 之前服务端只有渠道配置
// 和 VIP 触摸埋点，片仓里哪条视频卡在哪一步完全看不到，只能靠用户截图猜。
enum MZHDiag {
    static func report(_ text: String) {
        let body: [String: Any] = [
            "diag": text,
            "mode": "media",
            "build": Bundle.main.object(forInfoDictionaryKey: "CFBundleVersion") as? String ?? "",
            "client_now": Int(Date().timeIntervalSince1970)
        ]
        Task {
            _ = try? await ChannelAttribution.shared.postJSON(
                path: "/v1/app/diag", body: body, timeout: 4)
        }
    }
}

// MARK: - 数据模型（对应研究报告 2.2 / 2.1：索引、归类、调色记忆、变速方案）

struct GradePreset: Codable, Hashable {
    var name: String
    var temperature: Double   // 色温 -1...1
    var contrast: Double      // 对比度 0.5...1.8
    var saturation: Double    // 饱和度 0...2
    var grain: Double         // 颗粒感 0...1
    var brightness: Double    // 亮度 -0.3...0.3

    static let presets: [GradePreset] = [
        GradePreset(name: "原片", temperature: 0, contrast: 1, saturation: 1, grain: 0, brightness: 0),
        GradePreset(name: "胶片", temperature: 0.18, contrast: 1.14, saturation: 0.86, grain: 0.35, brightness: 0.02),
        GradePreset(name: "赛博", temperature: -0.28, contrast: 1.30, saturation: 1.48, grain: 0.12, brightness: 0),
        GradePreset(name: "暮色", temperature: 0.42, contrast: 1.08, saturation: 1.12, grain: 0.18, brightness: -0.04)
    ]
}

struct TempoSegment: Codable, Hashable, Identifiable {
    var id: String
    var start: Double
    var end: Double
    var rate: Double          // 0.25...4
    init(id: String = UUID().uuidString, start: Double, end: Double, rate: Double) {
        self.id = id; self.start = start; self.end = end; self.rate = rate
    }
}

struct LocalVideo: Codable, Identifiable, Hashable {
    var id: String
    var title: String
    var fileName: String
    var duration: Double
    var bytes: Int64
    var addedAt: TimeInterval
    var lastPlayedAt: TimeInterval?
    var category: String                  // 视界识档：人物 / 风景 / 宠物 / 美食 / 其他
    var grade: GradePreset?               // 光谱：调色随影片记忆
    var tempo: [TempoSegment]?            // 动能：方案随视频保存
    var thumbName: String?
}

struct LocalWork: Codable, Identifiable, Hashable {
    var id: String
    var title: String
    var kind: String                      // 调色成片 / 变速成片 / 胶片长图 / 字幕文件
    var fileName: String
    var createdAt: TimeInterval
}

enum MediaCategories {
    static let all = ["人物", "风景", "宠物", "美食", "其他"]
}

// MARK: - 本地片库：只在本机建立索引与副本，不上传任何视频（报告 2.2 / 4）

/// 导入进度：复制 100MB 视频 / 生成封面 / 视觉归类都要花时间，
/// 以前全在主线程同步跑完才入库，界面卡住且没有任何说明，
/// 用户就会以为「刚导入的视频点不开」。现在每步都有可见状态。
enum MediaImportStage: Equatable {
    case idle
    /// fraction 是真实的 0...1 进度（按已写入字节算），用来画线性进度条
    case running(done: Int, total: Int, stage: String, fraction: Double)
    case finished(ok: Int, failed: Int)
}

final class MediaStore: ObservableObject {
    static let shared = MediaStore()

    @Published private(set) var videos: [LocalVideo] = []
    @Published private(set) var works: [LocalWork] = []
    @Published private(set) var importStage: MediaImportStage = .idle
    /// 已经进片仓、但封面 / 归类还在后台补的条目。
    /// 片仓给它们打「上传中」角标，点开只提示「正在上传中，请稍等」。
    @Published private(set) var preparingIDs: Set<String> = []
    @Published private(set) var isImporting = false

    private let root: URL
    private let mediaDir: URL
    private let thumbDir: URL
    private let workDir: URL
    private let indexURL: URL

    init() {
        let base = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("Mizhenhe", isDirectory: true)
        root = base
        mediaDir = base.appendingPathComponent("Media", isDirectory: true)
        thumbDir = base.appendingPathComponent("Thumbs", isDirectory: true)
        workDir = base.appendingPathComponent("Works", isDirectory: true)
        indexURL = base.appendingPathComponent("library.json")
        for d in [base, mediaDir, thumbDir, workDir] {
            try? FileManager.default.createDirectory(at: d, withIntermediateDirectories: true)
        }
        load()
    }

    // MARK: 索引读写

    private struct Index: Codable {
        var videos: [LocalVideo]
        var works: [LocalWork]
    }

    private func load() {
        guard let data = try? Data(contentsOf: indexURL),
              let idx = try? JSONDecoder().decode(Index.self, from: data) else { return }
        videos = idx.videos.filter { FileManager.default.fileExists(atPath: url(for: $0).path) }
        works = idx.works.filter { FileManager.default.fileExists(atPath: workURL(for: $0).path) }
    }

    private func save() {
        let idx = Index(videos: videos, works: works)
        guard let data = try? JSONEncoder().encode(idx) else { return }
        try? data.write(to: indexURL, options: .atomic)
    }

    func url(for v: LocalVideo) -> URL { mediaDir.appendingPathComponent(v.fileName) }
    func workURL(for w: LocalWork) -> URL { workDir.appendingPathComponent(w.fileName) }
    func thumbURL(for v: LocalVideo) -> URL? {
        guard let n = v.thumbName else { return nil }
        let u = thumbDir.appendingPathComponent(n)
        return FileManager.default.fileExists(atPath: u.path) ? u : nil
    }

    // MARK: 导入（相册 / 文件双通道）

    /// 分块复制：按已写入字节回报 0...1 的真实进度。
    /// 以前用 copyItem 一次到底，92MB 期间界面只能干等，看着像卡死。
    private func copyWithProgress(from src: URL, to dst: URL,
                                  onProgress: (Double) -> Void) throws {
        let total = (try FileManager.default.attributesOfItem(atPath: src.path)[.size] as? NSNumber)?
            .doubleValue ?? 0
        if FileManager.default.fileExists(atPath: dst.path) {
            try? FileManager.default.removeItem(at: dst)
        }
        guard FileManager.default.createFile(atPath: dst.path, contents: nil) else {
            throw NSError(domain: "MZHImport", code: -1)
        }
        let input = try FileHandle(forReadingFrom: src)
        let output = try FileHandle(forWritingTo: dst)
        defer { try? input.close(); try? output.close() }
        let chunk = 1 << 20
        var written = 0.0
        var last = Date.distantPast
        while true {
            let data = input.readData(ofLength: chunk)
            if data.isEmpty { break }
            try output.write(contentsOf: data)
            written += Double(data.count)
            let now = Date()
            if total > 0, now.timeIntervalSince(last) > 0.06 {
                last = now
                onProgress(min(0.995, written / total))
            }
        }
        onProgress(1)
    }

    /// 相册给的临时文件已经在我们自己的 tmp 目录里，同盘 move 是瞬时的，
    /// 不再把 92MB 重复拷贝一遍；只有「文件」App / iCloud 的 URL 才真复制。
    private func isOwnTemporary(_ u: URL) -> Bool {
        u.standardizedFileURL.path.hasPrefix(
            FileManager.default.temporaryDirectory.standardizedFileURL.path + "/")
    }

    /// 复制副本 + 校验可播放 + 读取时长。
    /// 这一步完成视频就能播；封面 / 归类挪到 finalize 后台补，不再让大文件用户干等。
    private func makeEntry(from src: URL, suggestedName: String? = nil,
                           onProgress: (Double) -> Void) -> LocalVideo? {
        let scoped = src.startAccessingSecurityScopedResource()
        defer { if scoped { src.stopAccessingSecurityScopedResource() } }
        guard FileManager.default.fileExists(atPath: src.path) else { return nil }

        let ext = src.pathExtension.isEmpty ? "mov" : src.pathExtension
        let name = UUID().uuidString + "." + ext
        let dst = mediaDir.appendingPathComponent(name)
        do {
            if isOwnTemporary(src) {
                if FileManager.default.fileExists(atPath: dst.path) {
                    try? FileManager.default.removeItem(at: dst)
                }
                try FileManager.default.moveItem(at: src, to: dst)
                onProgress(1)
            } else {
                try copyWithProgress(from: src, to: dst, onProgress: onProgress)
            }
        } catch { return nil }

        let asset = AVURLAsset(url: dst)
        // 不再用同步 isPlayable 判定：大体积 HEVC / 杜比视界在这一步会同步阻塞，
        // 而且偶尔误判成「不可播放」把刚复制好的 92MB 直接删掉，
        // 用户看到的就是「上传了却什么都没有 / 打不开」。
        // 副本能不能解码交给播放器的诊断卡判断，导入阶段一律先入库。
        let duration = CMTimeGetSeconds(asset.duration)
        let bytes = (try? FileManager.default.attributesOfItem(atPath: dst.path)[.size] as? NSNumber)?
            .int64Value ?? 0
        var title = suggestedName ?? src.deletingPathExtension().lastPathComponent
        // 相册导出的是 UUID 临时名（会显示成 3D31B871-5C3A-… 这种乱码标题）
        if title.count > 24, title.contains("-"),
           title.range(of: "^[0-9A-Fa-f-]{24,}$", options: .regularExpression) != nil {
            let f = DateFormatter()
            f.dateFormat = "MM-dd HH:mm"
            title = "视频 · " + f.string(from: Date())
        }
        return LocalVideo(id: UUID().uuidString, title: title, fileName: name,
                          duration: duration.isFinite ? max(0, duration) : 0,
                          bytes: bytes, addedAt: Date().timeIntervalSince1970,
                          lastPlayedAt: nil, category: "其他", grade: nil,
                          tempo: nil, thumbName: nil)
    }

    /// 纯后台：生成封面 + 端侧归类，只返回结果补丁，由主线程写回索引（无竞态）。
    private func finalize(_ v: LocalVideo) -> (String?, String) {
        let thumb = makeThumbnail(videoURL: url(for: v), duration: v.duration, id: v.id)
        let cat = thumb.flatMap {
            SceneClassifier.classify(imageURL: thumbDir.appendingPathComponent($0))
        } ?? "其他"
        return (thumb, cat)
    }

    /// 在系统相册里点「完成」的那一刻立刻给可见状态：
    /// PHPicker 把 iCloud / 4K 视频导出成临时文件本身可能要几十秒，
    /// 以前这段时间界面完全没反应，用户只会觉得「还在传、卡住了」。
    func beginSelection(count: Int) {
        guard count > 0 else { return }
        isImporting = true
        importStage = .running(done: 0, total: count,
                               stage: "正在从相册读取所选视频…", fraction: 0.01)
    }

    /// 异步导入：真实字节进度条 → 复制完立刻入库并可播放 → 立刻「上传完成」。
    /// build 12 把「封面生成 + 端侧归类」串在关键路径上：copyCGImage 对大 HEVC
    /// 会同步卡住且没有超时，后面所有条目都停在 preparingIDs，
    /// 点开永远是「正在上传中，请稍等」——这就是「上传了之后打不开」。
    func importFilesAsync(_ urls: [URL]) {
        let total = urls.count
        guard total > 0 else {
            isImporting = false
            importStage = .finished(ok: 0, failed: 0)
            return
        }
        isImporting = true
        importStage = .running(done: 0, total: total,
                               stage: "正在上传中，请稍等", fraction: 0.02)
        MZHDiag.report("IMPORT 开始 · 共 \(total) 个文件")
        DispatchQueue.global(qos: .userInitiated).async {
            var ok = 0, failed = 0
            for (i, u) in urls.enumerated() {
                let base = Double(i) / Double(total)
                let span = 1.0 / Double(total)
                let report: (Double, String) -> Void = { f, s in
                    DispatchQueue.main.async {
                        self.importStage = .running(done: i, total: total, stage: s,
                                                    fraction: min(0.999, base + span * f))
                    }
                }
                report(0.02, "第 \(i + 1)/\(total) 个 · 正在上传中，请稍等")
                guard let v = self.makeEntry(from: u, onProgress: { p in
                    report(0.02 + 0.9 * p,
                           "第 \(i + 1)/\(total) 个 · 正在上传 \(Int(p * 100))%")
                }) else {
                    failed += 1
                    report(1, "第 \(i + 1)/\(total) 个 · 文件未能读取")
                    MZHDiag.report("IMPORT 失败 · 第 \(i + 1)/\(total) 个 · 文件未能读取（可能仍在 iCloud 下载）")
                    continue
                }
                ok += 1
                let mb = String(format: "%.1fMB", Double(v.bytes) / 1048576.0)
                MZHDiag.report("IMPORT 复制完成 · 第 \(i + 1)/\(total) 个 · \(mb) · 已可播放")
                // 复制完立刻入库。文件此时已完整写进沙盒，点开就该能播；
                // preparingIDs 只作为「封面生成中」角标，不再拦截点击。
                DispatchQueue.main.async {
                    self.preparingIDs.insert(v.id)
                    self.videos.insert(v, at: 0)
                    self.save()
                    self.importStage = .running(done: i + 1, total: total,
                                                stage: "第 \(i + 1)/\(total) 个上传完成 · 现在可以播放",
                                                fraction: Double(i + 1) / Double(total))
                }
                self.finalizeInBackground(v)
            }
            DispatchQueue.main.async {
                self.isImporting = false
                self.importStage = .finished(ok: ok, failed: failed)
                MZHDiag.report("IMPORT 全部完成 · 成功 \(ok) · 失败 \(failed)")
            }
        }
    }

    /// 封面 / 端侧归类只影响缩略图，放到独立后台队列，卡住也绝不牵连播放与「上传完成」。
    private func finalizeInBackground(_ v: LocalVideo) {
        let vid = v.id
        DispatchQueue.global(qos: .utility).async {
            let patch = self.finalize(v)
            DispatchQueue.main.async {
                if let idx = self.videos.firstIndex(where: { $0.id == vid }) {
                    self.videos[idx].thumbName = patch.0
                    self.videos[idx].category = patch.1
                }
                self.preparingIDs.remove(vid)
                self.save()
                MZHDiag.report("FINALIZE 完成 · \(patch.0 == nil ? "封面未生成（不影响播放）" : "封面OK") · \(patch.1)")
            }
        }
        // 看门狗：即使取帧卡死，8 秒后也清掉角标，不留「永远上传中」的条目。
        DispatchQueue.main.asyncAfter(deadline: .now() + 8) { [weak self] in
            self?.preparingIDs.remove(vid)
        }
    }

    func dismissImportResult() {
        if case .finished = importStage { importStage = .idle }
    }

    /// 兼容旧调用（同步）：新界面一律用 importFilesAsync。
    func importFiles(_ urls: [URL]) -> Int {
        var n = 0
        for u in urls {
            if var v = makeEntry(from: u, onProgress: { _ in }) {
                let patch = finalize(v)
                v.thumbName = patch.0
                v.category = patch.1
                videos.insert(v, at: 0)
                n += 1
            }
        }
        if n > 0 { save() }
        return n
    }

    private func makeThumbnail(videoURL: URL, duration: Double, id: String) -> String? {
        let gen = AVAssetImageGenerator(asset: AVURLAsset(url: videoURL))
        gen.appliesPreferredTrackTransform = true
        gen.maximumSize = CGSize(width: 480, height: 480)
        let at = CMTime(seconds: min(1.0, max(0.0, duration * 0.1)), preferredTimescale: 600)
        guard let cg = try? gen.copyCGImage(at: at, actualTime: nil) else { return nil }
        let name = id + ".jpg"
        let u = thumbDir.appendingPathComponent(name)
        guard let data = UIImage(cgImage: cg).jpegData(compressionQuality: 0.82) else { return nil }
        try? data.write(to: u, options: .atomic)
        return name
    }

    // MARK: 管理（搜索 / 改名 / 归类 / 删除 / 记忆）

    func rename(id: String, title: String) {
        guard let i = videos.firstIndex(where: { $0.id == id }) else { return }
        videos[i].title = title
        save()
    }

    func setCategory(id: String, category: String) {
        guard let i = videos.firstIndex(where: { $0.id == id }) else { return }
        videos[i].category = category
        save()
    }

    func setGrade(id: String, grade: GradePreset?) {
        guard let i = videos.firstIndex(where: { $0.id == id }) else { return }
        videos[i].grade = grade
        save()
    }

    func setTempo(id: String, segments: [TempoSegment]?) {
        guard let i = videos.firstIndex(where: { $0.id == id }) else { return }
        videos[i].tempo = segments
        save()
    }

    func markPlayed(id: String) {
        guard let i = videos.firstIndex(where: { $0.id == id }) else { return }
        videos[i].lastPlayedAt = Date().timeIntervalSince1970
        save()
    }

    func remove(ids: Set<String>) {
        for v in videos where ids.contains(v.id) {
            try? FileManager.default.removeItem(at: url(for: v))
            if let n = v.thumbName { try? FileManager.default.removeItem(at: thumbDir.appendingPathComponent(n)) }
        }
        videos.removeAll { ids.contains($0.id) }
        save()
    }

    func removeWorks(ids: Set<String>) {
        for w in works where ids.contains(w.id) {
            try? FileManager.default.removeItem(at: workURL(for: w))
        }
        works.removeAll { ids.contains($0.id) }
        save()
    }

    @discardableResult
    func addWork(title: String, kind: String, from src: URL) -> LocalWork? {
        let ext = src.pathExtension.isEmpty ? "mp4" : src.pathExtension
        let name = UUID().uuidString + "." + ext
        let dst = workDir.appendingPathComponent(name)
        do {
            if FileManager.default.fileExists(atPath: dst.path) {
                try FileManager.default.removeItem(at: dst)
            }
            try FileManager.default.copyItem(at: src, to: dst)
        } catch { return nil }
        let w = LocalWork(id: UUID().uuidString, title: title, kind: kind,
                          fileName: name, createdAt: Date().timeIntervalSince1970)
        works.insert(w, at: 0)
        save()
        return w
    }

    @discardableResult
    func addWork(title: String, kind: String, data: Data, ext: String) -> LocalWork? {
        let name = UUID().uuidString + "." + ext
        do {
            try data.write(to: workDir.appendingPathComponent(name), options: .atomic)
        } catch { return nil }
        let w = LocalWork(id: UUID().uuidString, title: title, kind: kind,
                          fileName: name, createdAt: Date().timeIntervalSince1970)
        works.insert(w, at: 0)
        save()
        return w
    }

    // 报告 2.2：统计条 视频数 / 总时长 / 占用空间
    var totalDuration: Double { videos.reduce(0) { $0 + $1.duration } }
    var totalBytes: Int64 { videos.reduce(0) { $0 + $1.bytes } }
}

// MARK: - 视界识档：端侧 Vision 场景分类，零上传（报告 2.1 / 4）

enum SceneClassifier {
    static func classify(imageURL: URL) -> String? {
        guard FileManager.default.fileExists(atPath: imageURL.path),
              let data = try? Data(contentsOf: imageURL),
              let ui = UIImage(data: data), let cg = ui.cgImage else { return nil }
        let req = VNClassifyImageRequest()
        let handler = VNImageRequestHandler(cgImage: cg, options: [:])
        try? handler.perform([req])
        let obs = (req.results as? [VNClassificationObservation]) ?? []
        for o in obs.prefix(12) {
            let s = o.identifier.lowercased()
            if s.contains("person") || s.contains("people") || s.contains("face") { return "人物" }
            if s.contains("dog") || s.contains("cat") || s.contains("animal") || s.contains("pet") { return "宠物" }
            if s.contains("food") || s.contains("meal") || s.contains("fruit") || s.contains("dish") { return "美食" }
            if s.contains("landscape") || s.contains("mountain") || s.contains("sky") ||
               s.contains("nature") || s.contains("beach") || s.contains("forest") ||
               s.contains("sunset") || s.contains("tree") || s.contains("water") { return "风景" }
        }
        return nil
    }
}

// MARK: - 相册导入：PHPicker 无需相册读取权限（报告 2.4）

struct VideoPicker: UIViewControllerRepresentable {
    /// 选完立刻回调，让界面先进入「正在从相册读取」状态
    let onStart: (Int) -> Void
    let onPick: ([URL]) -> Void

    func makeUIViewController(context: Context) -> PHPickerViewController {
        var cfg = PHPickerConfiguration()
        cfg.filter = .videos
        cfg.selectionLimit = 0
        cfg.preferredAssetRepresentationMode = .current
        let vc = PHPickerViewController(configuration: cfg)
        vc.delegate = context.coordinator
        return vc
    }

    func updateUIViewController(_ uiViewController: PHPickerViewController, context: Context) {}

    func makeCoordinator() -> Coordinator { Coordinator(onStart: onStart, onPick: onPick) }

    final class Coordinator: NSObject, PHPickerViewControllerDelegate {
        let onStart: (Int) -> Void
        let onPick: ([URL]) -> Void
        init(onStart: @escaping (Int) -> Void, onPick: @escaping ([URL]) -> Void) {
            self.onStart = onStart; self.onPick = onPick
        }

        func picker(_ picker: PHPickerViewController, didFinishPicking results: [PHPickerResult]) {
            picker.dismiss(animated: true)
            guard !results.isEmpty else { return }
            DispatchQueue.main.async { self.onStart(results.count) }
            let group = DispatchGroup()
            var urls: [URL] = []
            let lock = NSLock()
            for r in results {
                let p = r.itemProvider
                guard p.hasItemConformingToTypeIdentifier(UTType.movie.identifier) else { continue }
                group.enter()
                p.loadFileRepresentation(forTypeIdentifier: UTType.movie.identifier) { src, _ in
                    defer { group.leave() }
                    guard let src = src else { return }
                    // 系统给的临时文件在回调结束后即失效，先落到自己的临时目录
                    let tmp = FileManager.default.temporaryDirectory
                        .appendingPathComponent(UUID().uuidString + "-" + src.lastPathComponent)
                    do { try FileManager.default.copyItem(at: src, to: tmp) } catch { return }
                    lock.lock(); urls.append(tmp); lock.unlock()
                }
            }
            group.notify(queue: .main) { self.onPick(urls) }
        }
    }
}

// MARK: - 通用工具

enum Format {
    static func duration(_ s: Double) -> String {
        let t = Int(max(0, s))
        if t >= 3600 { return String(format: "%d:%02d:%02d", t / 3600, (t % 3600) / 60, t % 60) }
        return String(format: "%02d:%02d", t / 60, t % 60)
    }
    static func bytes(_ b: Int64) -> String {
        if b >= 1_073_741_824 { return String(format: "%.1f GB", Double(b) / 1_073_741_824) }
        if b >= 1_048_576 { return String(format: "%.1f MB", Double(b) / 1_048_576) }
        return String(format: "%.0f KB", Double(b) / 1024)
    }
}

extension Color {
    static func mzh(_ hex: String) -> Color {
        let s = hex.trimmingCharacters(in: CharacterSet(charactersIn: "#"))
        var v: UInt64 = 0
        Scanner(string: s).scanHexInt64(&v)
        return Color(.sRGB,
                     red: Double((v >> 16) & 0xff) / 255,
                     green: Double((v >> 8) & 0xff) / 255,
                     blue: Double(v & 0xff) / 255, opacity: 1)
    }
}

struct ShareSheet: UIViewControllerRepresentable {
    let items: [Any]
    func makeUIViewController(context: Context) -> UIActivityViewController {
        UIActivityViewController(activityItems: items, applicationActivities: nil)
    }
    func updateUIViewController(_ uiViewController: UIActivityViewController, context: Context) {}
}

enum PhotoSaver {
    static func saveImage(_ image: UIImage, completion: @escaping (Bool) -> Void) {
        UIImageWriteToSavedPhotosAlbum(image, Holder.shared,
                                       #selector(Holder.done(_:didFinishSavingWithError:contextInfo:)), nil)
        Holder.shared.completion = completion
    }
    static func saveVideo(_ url: URL, completion: @escaping (Bool) -> Void) {
        PHPhotoLibrary.shared().performChanges({
            PHAssetChangeRequest.creationRequestForAssetFromVideo(atFileURL: url)
        }) { ok, _ in DispatchQueue.main.async { completion(ok) } }
    }

    private final class Holder: NSObject {
        static let shared = Holder()
        var completion: ((Bool) -> Void)?
        @objc func done(_ image: UIImage, didFinishSavingWithError error: NSError?, contextInfo: UnsafeMutableRawPointer?) {
            DispatchQueue.main.async {
                self.completion?(error == nil)
                self.completion = nil
            }
        }
    }
}
