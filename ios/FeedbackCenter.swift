import SwiftUI
import PhotosUI
import UIKit

// MARK: - 帮助与反馈 · 对接 /site 后台「用户反馈中心」
//
// 后台真实接口（Yii2 models/UserFeedback.php）：
//   GET  /api/feedback/types    投诉类型字典
//   POST /api/feedback/upload   截图
//   POST /api/feedback/submit   {types[], content, images[], contact, device_no}
// 米帧盒服务端 /v1/feedback/* 负责游客 token 与转发，App 只认这一层。
//
// 设计取舍：后台字典是给小说/影视 App 用的（充值、剧集、误充等），
// 直接照搬到本地播放器里既莫名其妙又不适合送审。所以 App 展示一套
// 贴合本机功能的分类，每个分类的提交值都取自后台允许的原值字典
// （TYPES + LEGACY_TYPES），保证后台 validateType 一定放行。

private struct FeedbackOption: Identifiable {
    let id: String          // 提交给后台的合法原值
    let title: String       // App 内短标签
    let desc: String
    let icon: String
}

private let kFeedbackOptions: [FeedbackOption] = [
    FeedbackOption(id: "功能异常", title: "播放 / 导入异常",
                   desc: "视频打不开、导入失败、缩略图不对", icon: "play.slash"),
    FeedbackOption(id: "闪退/卡顿", title: "闪退 / 卡顿",
                   desc: "闪退、卡死、发热或耗电异常", icon: "exclamationmark.triangle"),
    FeedbackOption(id: "需要更多功能", title: "想要新功能",
                   desc: "格式、归档、调色、变速、取帧的建议", icon: "lightbulb"),
    FeedbackOption(id: "功能建议", title: "界面 / 体验建议",
                   desc: "操作不顺、看不懂、按钮不好点", icon: "hand.tap"),
    FeedbackOption(id: "内容举报", title: "内容举报",
                   desc: "违规、侵权或令人不适的内容", icon: "flag"),
    FeedbackOption(id: "其他", title: "其他问题",
                   desc: "上面都没覆盖到的问题", icon: "ellipsis.circle")
]

private struct FeedbackLast: Decodable {
    let id: Int?
    let at: Int?
    let types: [String]?
    let content: String?
    let contact: String?
    let images: [String]?
    let status: String?
}

private struct FeedbackEnvelope: Decodable {
    let ok: Bool
    let types: [String]?
    let source: String?
    let id: Int?
    let msg: String?
    let error: String?
    let url: String?
    let pending_id: Int?
    let pending_at: String?
    let last: FeedbackLast?
    let feedback: FeedbackLast?
}

private enum FeedbackErr: LocalizedError {
    case badURL
    case http(Int)
    case server(String)

    var errorDescription: String? {
        switch self {
        case .badURL: return "服务地址无效"
        case .http(let c): return "服务返回 \(c)"
        case .server(let s): return s
        }
    }
}

private enum FeedbackAPI {
    static var base: String { ChannelAttribution.baseURL }

    /// 设备号只用于后台「同一设备一条处理中」判重，服务端会再哈希一次
    static var device: String {
        let d = UserDefaults.standard
        if let s = d.string(forKey: "mzh_feedback_device"), !s.isEmpty { return s }
        let s = UIDevice.current.identifierForVendor?.uuidString ?? UUID().uuidString
        d.set(s, forKey: "mzh_feedback_device")
        return s
    }

    static func types() async -> (Bool, [String]) {
        guard let url = URL(string: base + "/v1/feedback/types") else { return (false, []) }
        do {
            var req = URLRequest(url: url)
            req.timeoutInterval = 10
            let (data, resp) = try await URLSession.shared.data(for: req)
            guard (resp as? HTTPURLResponse)?.statusCode == 200 else { return (false, []) }
            let e = try JSONDecoder().decode(FeedbackEnvelope.self, from: data)
            return (e.ok, e.types ?? [])
        } catch { return (false, []) }
    }

    static func last() async -> FeedbackLast? {
        guard var comps = URLComponents(string: base + "/v1/feedback/status") else { return nil }
        comps.queryItems = [URLQueryItem(name: "device_no", value: device)]
        guard let url = comps.url else { return nil }
        do {
            var req = URLRequest(url: url)
            req.timeoutInterval = 10
            let (data, _) = try await URLSession.shared.data(for: req)
            return try JSONDecoder().decode(FeedbackEnvelope.self, from: data).last
        } catch { return nil }
    }

    static func upload(_ jpeg: Data) async throws -> String {
        guard let url = URL(string: base + "/v1/feedback/upload") else { throw FeedbackErr.badURL }
        let boundary = "MZH" + UUID().uuidString.replacingOccurrences(of: "-", with: "")
        var body = Data()
        func put(_ s: String) { body.append(Data(s.utf8)) }
        put("--\(boundary)\r\n")
        put("Content-Disposition: form-data; name=\"file\"; filename=\"feedback.jpg\"\r\n")
        put("Content-Type: image/jpeg\r\n\r\n")
        body.append(jpeg)
        put("\r\n--\(boundary)--\r\n")
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.timeoutInterval = 25
        req.setValue("multipart/form-data; boundary=\(boundary)",
                     forHTTPHeaderField: "Content-Type")
        req.setValue(device, forHTTPHeaderField: "X-Device-No")
        let (data, resp) = try await URLSession.shared.upload(for: req, from: body)
        guard (resp as? HTTPURLResponse)?.statusCode == 200 else {
            throw FeedbackErr.http((resp as? HTTPURLResponse)?.statusCode ?? -1)
        }
        let e = try JSONDecoder().decode(FeedbackEnvelope.self, from: data)
        guard e.ok, let u = e.url, !u.isEmpty else {
            throw FeedbackErr.server(e.msg ?? "截图上传失败，请重试")
        }
        return u
    }

    static func submit(types: [String], content: String, contact: String,
                       images: [String]) async throws -> FeedbackEnvelope {
        guard let url = URL(string: base + "/v1/feedback/submit") else { throw FeedbackErr.badURL }
        let payload: [String: Any] = [
            "types": types, "content": content, "contact": contact,
            "images": images, "device_no": device
        ]
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.timeoutInterval = 25
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try JSONSerialization.data(withJSONObject: payload, options: [])
        let (data, resp) = try await URLSession.shared.data(for: req)
        guard (resp as? HTTPURLResponse)?.statusCode == 200 else {
            throw FeedbackErr.http((resp as? HTTPURLResponse)?.statusCode ?? -1)
        }
        return try JSONDecoder().decode(FeedbackEnvelope.self, from: data)
    }
}

private struct FeedbackImage: Identifiable {
    let id = UUID()
    let image: UIImage
    let jpeg: Data
}

// MARK: - 页面

struct FeedbackCenterView: View {
    @State private var selected: Set<String> = []
    @State private var text = ""
    @State private var contact = ""
    @State private var pickerItems: [PhotosPickerItem] = []
    @State private var attachments: [FeedbackImage] = []
    @State private var includeDiagnostics = false
    @State private var serviceOK: Bool?
    @State private var remoteTypes: [String] = []
    @State private var last: FeedbackLast?
    @State private var busy = false
    @State private var busyHint = ""
    @State private var result = ""
    @State private var resultOK = false
    @State private var pendingLock = false
    @State private var expandedFAQ: String?

    private var canSubmit: Bool {
        !selected.isEmpty && text.trimmingCharacters(in: .whitespacesAndNewlines).count >= 5
        && text.count <= 2000 && !busy && !pendingLock
    }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 18) {
                    serviceCard
                    if let last { lastCard(last) }

                    title("1 · 问题类型", hint: "可多选，\(selected.count) 项")
                    LazyVGrid(columns: [GridItem(.adaptive(minimum: 150), spacing: 10)],
                              spacing: 10) {
                        ForEach(kFeedbackOptions) { o in
                            optionChip(o)
                        }
                    }
                    Text(kFeedbackOptions
                        .filter { selected.contains($0.id) }
                        .map(\.desc).joined(separator: " · "))
                        .font(.caption2).foregroundStyle(MZH.muted)
                        .opacity(selected.isEmpty ? 0 : 1)

                    title("2 · 问题描述", hint: "\(text.count) / 2000")
                    ZStack(alignment: .topLeading) {
                        TextEditor(text: $text)
                            .frame(minHeight: 140)
                            .padding(10)
                            .scrollContentBackground(.hidden)
                            .background(MZH.surface2, in: RoundedRectangle(cornerRadius: 14))
                        if text.isEmpty {
                            Text("在哪一步出问题？当时在做什么？期望的结果是什么？")
                                .font(.caption).foregroundStyle(MZH.muted)
                                .padding(.horizontal, 16).padding(.vertical, 18)
                                .allowsHitTesting(false)
                        }
                    }
                    .overlay(RoundedRectangle(cornerRadius: 14).stroke(MZH.line, lineWidth: 1))
                    if text.count > 2000 {
                        Text("内容超过 2000 字，请精简后再提交")
                            .font(.caption).foregroundStyle(MZH.danger)
                    }

                    title("3 · 截图", hint: "选填，最多 3 张")
                    PhotosPicker(selection: $pickerItems, maxSelectionCount: 3,
                                 matching: .images) {
                        Label("添加问题截图", systemImage: "photo.badge.plus")
                            .font(.subheadline)
                            .frame(maxWidth: .infinity)
                            .padding(.vertical, 12)
                            .background(MZH.surface, in: RoundedRectangle(cornerRadius: 12))
                            .overlay(RoundedRectangle(cornerRadius: 12)
                                .stroke(MZH.line, lineWidth: 1))
                    }
                    .buttonStyle(.plain)
                    .onChange(of: pickerItems) { _, items in
                        Task { await loadPicked(items) }
                    }
                    if !attachments.isEmpty {
                        ScrollView(.horizontal, showsIndicators: false) {
                            HStack(spacing: 10) {
                                ForEach(attachments) { a in
                                    ZStack(alignment: .topTrailing) {
                                        Image(uiImage: a.image).resizable().scaledToFill()
                                            .frame(width: 78, height: 78)
                                            .clipShape(RoundedRectangle(cornerRadius: 10))
                                        Button {
                                            attachments.removeAll { $0.id == a.id }
                                            pickerItems = []
                                        } label: {
                                            Image(systemName: "xmark.circle.fill")
                                                .foregroundStyle(MZH.danger)
                                                .background(Circle().fill(MZH.bg))
                                        }.buttonStyle(.plain).padding(3)
                                    }
                                }
                            }
                        }
                    }
                    Text("截图只用于定位问题；不会读取你的视频、相册原片、通讯录或位置。")
                        .font(.caption2).foregroundStyle(MZH.muted)

                    title("4 · 联系方式", hint: "选填")
                    TextField("手机号或邮箱，方便客服回复你", text: $contact)
                        .keyboardType(.emailAddress)
                        .textInputAutocapitalization(.never)
                        .padding(12)
                        .background(MZH.surface2, in: RoundedRectangle(cornerRadius: 12))

                    privacyCard
                    submitButton
                    if !result.isEmpty { resultCard }

                    title("常见问题", hint: "点开看答案")
                    ForEach(kFAQs) { f in
                        DisclosureGroup(isExpanded: Binding(
                            get: { expandedFAQ == f.q },
                            set: { expandedFAQ = $0 ? f.q : nil })) {
                            Text(f.a).font(.caption).foregroundStyle(MZH.muted)
                                .padding(.top, 6).frame(maxWidth: .infinity, alignment: .leading)
                        } label: {
                            Text(f.q).font(.subheadline)
                        }
                        .padding(12)
                        .background(MZH.surface, in: RoundedRectangle(cornerRadius: 12))
                    }

                    Link(destination: URL(string: "mailto:motionworld.support@126.com")!) {
                        Label("给客服发邮件：motionworld.support@126.com",
                              systemImage: "envelope")
                            .font(.caption).foregroundStyle(MZH.primary)
                            .frame(maxWidth: .infinity, alignment: .center)
                    }
                    .padding(.bottom, 30)
                }
                .padding(16)
            }
            .navigationTitle("帮助与反馈")
            .background(MZH.bg.ignoresSafeArea())
            .task { await load() }
        }
    }

    // MARK: 组件

    private var serviceCard: some View {
        HStack(spacing: 12) {
            Image(systemName: serviceOK == true ? "checkmark.circle.fill" : "wifi.exclamationmark")
                .font(.title3)
                .foregroundStyle(serviceOK == true ? MZH.green : MZH.accent)
            VStack(alignment: .leading, spacing: 2) {
                Text(serviceOK == true ? "已连接用户反馈中心" : "反馈服务暂不可用")
                    .font(.subheadline.bold())
                Text(serviceOK == true
                     ? "后台字典 \(remoteTypes.count) 项 · 提交后进入客服工单队列"
                     : "可先写好内容，点右侧重试；也可直接发邮件")
                    .font(.caption2).foregroundStyle(MZH.muted)
            }
            Spacer()
            if serviceOK != true {
                Button("重试") { Task { await load() } }
                    .font(.caption.bold()).foregroundStyle(MZH.primary)
                    .buttonStyle(.plain)
            }
        }
        .padding(14)
        .background(MZH.surface, in: RoundedRectangle(cornerRadius: 14))
        .overlay(RoundedRectangle(cornerRadius: 14).stroke(MZH.line, lineWidth: 1))
    }

    private func lastCard(_ l: FeedbackLast) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Label("上次提交", systemImage: "clock.arrow.circlepath")
                    .font(.subheadline.bold()).foregroundStyle(MZH.primary)
                Spacer()
                Text("工单 #\(l.id ?? 0)").font(.caption2).foregroundStyle(MZH.muted)
            }
            Text("时间：\(formatTime(l.at)) · 状态：\(l.status ?? "处理中")")
                .font(.caption).foregroundStyle(MZH.muted)
            if let c = l.content, !c.isEmpty {
                Text(c).font(.caption).lineLimit(2).foregroundStyle(MZH.text)
            }
            Text("客服回复本条之后即可再次提交；紧急问题可直接发邮件。")
                .font(.caption2).foregroundStyle(MZH.muted)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(14)
        .background(MZH.surface2, in: RoundedRectangle(cornerRadius: 14))
    }

    private func title(_ t: String, hint: String) -> some View {
        HStack(alignment: .firstTextBaseline) {
            Text(t).font(.headline)
            Spacer()
            Text(hint).font(.caption2).foregroundStyle(MZH.muted)
        }
    }

    private func optionChip(_ o: FeedbackOption) -> some View {
        let on = selected.contains(o.id)
        return Button {
            if on { selected.remove(o.id) } else { selected.insert(o.id) }
            if !result.isEmpty { result = "" }
        } label: {
            HStack(spacing: 6) {
                Image(systemName: on ? "checkmark.circle.fill" : o.icon)
                    .font(.caption)
                Text(o.title).font(.caption.bold()).lineLimit(1)
            }
            .frame(maxWidth: .infinity)
            .padding(.horizontal, 10).padding(.vertical, 10)
            .background(on ? MZH.primary.opacity(0.16) : MZH.surface,
                        in: Capsule())
            .overlay(Capsule().stroke(on ? MZH.primary : MZH.line, lineWidth: 1))
            .foregroundStyle(on ? MZH.primary : MZH.muted)
        }
        .buttonStyle(.plain)
    }

    private var privacyCard: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("隐私保护").font(.subheadline.bold())
            Text("只发送你选的类型、写下的描述、所选截图与联系方式。不上传视频、相册原片、通讯录、精确位置。")
                .font(.caption2).foregroundStyle(MZH.muted)
            Toggle(isOn: $includeDiagnostics) {
                Text("附带诊断信息（App 版本 / 机型 / 系统版本）")
                    .font(.caption).foregroundStyle(MZH.text)
            }
            .tint(MZH.primary)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(14)
        .background(MZH.surface, in: RoundedRectangle(cornerRadius: 14))
    }

    private var submitButton: some View {
        Button {
            Task { await submitFeedback() }
        } label: {
            HStack(spacing: 8) {
                if busy { ProgressView().tint(MZH.onPrimary) }
                Text(busy ? busyHint : (pendingLock ? "等待客服回复后可再次提交" : "提交反馈"))
            }
            .mzhPrimaryButton()
        }
        .buttonStyle(.plain)
        .disabled(!canSubmit)
        .opacity(canSubmit ? 1 : 0.5)
    }

    private var resultCard: some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: resultOK ? "checkmark.circle.fill" : "exclamationmark.circle.fill")
                .foregroundStyle(resultOK ? MZH.green : MZH.accent)
            Text(result).font(.caption).foregroundStyle(MZH.text)
            Spacer(minLength: 0)
        }
        .padding(14)
        .background(resultOK ? MZH.green.opacity(0.10) : MZH.accent.opacity(0.10),
                    in: RoundedRectangle(cornerRadius: 14))
    }

    // MARK: 动作

    private func load() async {
        let (ok, types) = await FeedbackAPI.types()
        let rec = await FeedbackAPI.last()
        await MainActor.run {
            serviceOK = ok
            remoteTypes = types
            last = rec
        }
    }

    private func loadPicked(_ items: [PhotosPickerItem]) async {
        var got: [FeedbackImage] = []
        for item in items.prefix(3) {
            guard let data = try? await item.loadTransferable(type: Data.self),
                  let img = UIImage(data: data),
                  let jpg = img.jpegData(compressionQuality: 0.8) else { continue }
            got.append(FeedbackImage(image: img, jpeg: jpg))
        }
        await MainActor.run { attachments = got }
    }

    private func submitFeedback() async {
        guard !selected.isEmpty else {
            await MainActor.run { resultOK = false; result = "请先选择至少一个问题类型" }
            return
        }
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard trimmed.count >= 5 else {
            await MainActor.run { resultOK = false; result = "请再具体描述一下遇到的问题（至少 5 个字）" }
            return
        }
        // 先快照到本地值，避免异步过程中反复读 @State
        let pickedTypes = Array(selected)
        let withDiagnostics = includeDiagnostics
        let snaps = attachments
        let contactText = contact.trimmingCharacters(in: .whitespacesAndNewlines)
        await MainActor.run { busy = true; busyHint = "准备提交…"; result = "" }

        var content = trimmed
        if withDiagnostics {
            let v = Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "1.0"
            content += "\n\n[诊断信息]\napp=米帧盒播放器\nversion=\(v)\nios=\(UIDevice.current.systemVersion)\nmodel=\(UIDevice.current.model)"
        }

        var urls: [String] = []
        for (i, a) in snaps.enumerated() {
            await MainActor.run { busyHint = "正在上传截图 \(i + 1)/\(snaps.count)…" }
            if let u = try? await FeedbackAPI.upload(a.jpeg) { urls.append(u) }
        }

        await MainActor.run { busyHint = "正在提交…" }
        do {
            let e = try await FeedbackAPI.submit(types: pickedTypes, content: content,
                                                 contact: contactText, images: urls)
            await MainActor.run {
                busy = false
                if e.ok {
                    resultOK = true
                    pendingLock = true
                    result = "已提交（工单 #\(e.id ?? 0)）。客服回复前不能重复提交，通常 1–2 个工作日内处理。"
                    if let f = e.feedback { last = f }
                    text = ""
                    selected = []
                    attachments = []
                    pickerItems = []
                } else {
                    resultOK = false
                    pendingLock = (e.error == "pending")
                    if pendingLock, let at = e.pending_at {
                        result = "\(e.msg ?? "您的问题正在处理中")（提交于 \(at)）"
                    } else {
                        result = e.msg ?? "提交失败，请稍后重试"
                    }
                }
            }
        } catch {
            await MainActor.run {
                busy = false
                resultOK = false
                result = "提交失败：\(error.localizedDescription)。内容已保留，可重试或直接发邮件给客服。"
            }
        }
    }

    private func formatTime(_ t: Int?) -> String {
        guard let t else { return "—" }
        let d = Date(timeIntervalSince1970: TimeInterval(t))
        let f = DateFormatter()
        f.dateFormat = "yyyy-MM-dd HH:mm"
        return f.string(from: d)
    }
}

private struct FAQItem: Identifiable {
    var id: String { q }
    let q: String
    let a: String
}

private let kFAQs: [FAQItem] = [
    FAQItem(q: "支持哪些视频格式？", a: "常见 MP4 / MOV / M4V，优先使用 H.264 / HEVC 硬解。导入时会在本机建立索引与副本，以便离线反复播放。"),
    FAQItem(q: "导入会占双倍空间吗？", a: "会在 App 文档目录留一份副本，占用约等于原片大小；可在「设置 → 清除本地片库索引与副本」一次清理，系统相册原片不受影响。"),
    FAQItem(q: "播放卡顿怎么办？", a: "关闭后台应用；4K 120fps 素材在动能曲线中建议不高于 2x；高颗粒感滤镜会增加渲染负担。"),
    FAQItem(q: "iCloud 里的视频打不开？", a: "请先在「照片」App 把视频下载到本机，再从相册导入。"),
    FAQItem(q: "不登录能用吗？", a: "可以。播放、识档、调色、变速、取帧、媒体库全部在本机完成，不登录、不联网也能用。"),
    FAQItem(q: "反馈提交后怎么知道结果？", a: "反馈会进入后台「用户反馈中心」工单队列，客服回复前同一设备不能重复提交；紧急问题可发邮件到 motionworld.support@126.com。")
]
