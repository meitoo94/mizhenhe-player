import Foundation
import SwiftUI
import AVFoundation
import PhotosUI
import Photos
import Vision
import UIKit
import UniformTypeIdentifiers

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

final class MediaStore: ObservableObject {
    static let shared = MediaStore()

    @Published private(set) var videos: [LocalVideo] = []
    @Published private(set) var works: [LocalWork] = []

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

    @discardableResult
    func importFile(_ src: URL, suggestedName: String? = nil) -> LocalVideo? {
        let scoped = src.startAccessingSecurityScopedResource()
        defer { if scoped { src.stopAccessingSecurityScopedResource() } }
        guard FileManager.default.fileExists(atPath: src.path) else { return nil }

        let ext = src.pathExtension.isEmpty ? "mov" : src.pathExtension
        let name = UUID().uuidString + "." + ext
        let dst = mediaDir.appendingPathComponent(name)
        do {
            if FileManager.default.fileExists(atPath: dst.path) {
                try FileManager.default.removeItem(at: dst)
            }
            try FileManager.default.copyItem(at: src, to: dst)
        } catch { return nil }

        let asset = AVURLAsset(url: dst)
        let duration = CMTimeGetSeconds(asset.duration)
        let bytes = (try? FileManager.default.attributesOfItem(atPath: dst.path)[.size] as? NSNumber)?
            .int64Value ?? 0
        let title = suggestedName ?? (src.deletingPathExtension().lastPathComponent)

        var v = LocalVideo(id: UUID().uuidString, title: title, fileName: name,
                           duration: duration.isFinite ? max(0, duration) : 0,
                           bytes: bytes, addedAt: Date().timeIntervalSince1970,
                           lastPlayedAt: nil, category: "其他", grade: nil,
                           tempo: nil, thumbName: nil)
        v.thumbName = makeThumbnail(videoURL: dst, duration: v.duration, id: v.id)
        v.category = SceneClassifier.classify(imageURL: thumbDir.appendingPathComponent(v.thumbName ?? ""))
            ?? "其他"
        videos.insert(v, at: 0)
        save()
        return v
    }

    func importFiles(_ urls: [URL]) -> Int {
        var n = 0
        for u in urls { if importFile(u) != nil { n += 1 } }
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
        works.removeAll { ids.contains(w.id) }
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

    func makeCoordinator() -> Coordinator { Coordinator(onPick: onPick) }

    final class Coordinator: NSObject, PHPickerViewControllerDelegate {
        let onPick: ([URL]) -> Void
        init(onPick: @escaping ([URL]) -> Void) { self.onPick = onPick }

        func picker(_ picker: PHPickerViewController, didFinishPicking results: [PHPickerResult]) {
            picker.dismiss(animated: true)
            guard !results.isEmpty else { return }
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
        UIActivityViewController(activityItems: items, applicationServices: nil)
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
