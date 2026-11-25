import SwiftUI
import UIKit

// MARK: - Tab 4 我的（研究报告 2.3 / 3：账号资料 / 棱镜会员 / 设置 / 帮助与反馈）

struct MineTabReal: View {
    @ObservedObject var store: MediaStore
    let tabName: String
    let toast: (String) -> Void
    @AppStorage("phone_login_ticket") private var ticket = ""
    @AppStorage("masked_phone") private var masked = ""
    @State private var showLogin = false
    @State private var showDelete = false
    @State private var showClearConfirm = false

    var body: some View {
        NavigationStack {
            List {
                Section {
                    Button { showLogin = true } label: {
                        HStack(spacing: 14) {
                            Image(systemName: "person.crop.circle.fill").font(.system(size: 44))
                                .foregroundStyle(MZH.primary)
                            VStack(alignment: .leading, spacing: 4) {
                                Text(masked.isEmpty ? "未登录" : masked).font(.headline)
                                Text(masked.isEmpty ? "手机号验证码登录，不登录也能用全部本地功能"
                                     : "已绑定手机号 · 点击查看账号资料")
                                    .font(.caption).foregroundStyle(MZH.muted)
                            }
                            Spacer()
                            Image(systemName: "chevron.right").font(.caption)
                                .foregroundStyle(MZH.muted)
                        }
                    }
                    .buttonStyle(.plain)
                }

                Section {
                    NavigationLink { MemberCenterView() } label: {
                        HStack {
                            Image(systemName: "crown.fill").foregroundStyle(MZH.accent)
                            VStack(alignment: .leading, spacing: 2) {
                                Text("棱镜会员").font(.subheadline)
                                Text("查看会员状态与权益").font(.caption2)
                                    .foregroundStyle(MZH.muted)
                            }
                        }
                    }
                }

                Section("设置") {
                    NavigationLink { SettingsView(store: store, toast: toast) } label: {
                        Label("设置", systemImage: "gearshape")
                    }
                    NavigationLink { FeedbackCenterView() } label: {
                        Label("帮助与反馈", systemImage: "questionmark.circle")
                    }
                    NavigationLink { LegalView(kind: .agreement) } label: {
                        Label("用户协议", systemImage: "doc.text")
                    }
                    NavigationLink { LegalView(kind: .privacy) } label: {
                        Label("隐私政策", systemImage: "hand.raised")
                    }
                    Button { showDelete = true } label: {
                        Label("注销账号", systemImage: "person.crop.circle.badge.minus")
                            .foregroundStyle(.red)
                    }
                }

                Section {
                    VStack(alignment: .leading, spacing: 4) {
                        Text("米帧盒播放器").font(.caption.bold())
                        Text("本地影像 · 归档 · 调色 · 拼帧。导入、播放、识档、调色、变速、取帧全程不上传用户视频。")
                            .font(.caption2).foregroundStyle(MZH.muted)
                    }
                }
            }
            .navigationTitle(tabName)
            .mzhListBackground()
            .sheet(isPresented: $showLogin) {
                LoginViewReal(ticket: $ticket, masked: $masked)
            }
            .sheet(isPresented: $showDelete) {
                AccountDeletionView(channel: UserDefaults.standard
                    .string(forKey: "resolved_channel") ?? "organic")
            }
        }
    }
}

// MARK: - 手机号 + 验证码登录（报告 2.3：一手机号一账号）

private struct LoginResponseReal: Codable {
    let ok: Bool
    let phone_masked: String?
    let ticket: String?
    let toast: String?
    let error: String?
}

private struct LoginViewReal: View {
    @Binding var ticket: String
    @Binding var masked: String
    @Environment(\.dismiss) private var dismiss
    @State private var phone = ""
    @State private var agreed = false
    @State private var code = ""
    @State private var busy = false
    @State private var message = ""
    @State private var agreeHint = false
    @State private var showLogout = false
    @State private var showAgreement = false
    @State private var showPrivacy = false

    private var phoneOK: Bool { phone.count == 11 && phone.hasPrefix("1") }

    var body: some View {
        NavigationStack {
            Form {
                if ticket.isEmpty {
                    Section {
                        TextField("请输入 11 位手机号", text: $phone)
                            .keyboardType(.numberPad)
                            .textContentType(.telephoneNumber)
                        HStack(spacing: 10) {
                            TextField("验证码（演示版任意 4–6 位）", text: $code)
                                .keyboardType(.numberPad)
                            Button("获取验证码") {
                                code = "1234"
                                message = "演示版本暂未接入短信通道，已填入测试验证码 1234"
                            }
                            .font(.caption.bold())
                            .foregroundStyle(MZH.primary)
                            .buttonStyle(.plain)
                        }
                    } header: {
                        Text("手机号登录")
                    } footer: {
                        Text("一个手机号对应一个账号；不登录也能使用全部本地功能。")
                    }

                    // 明确的方形勾选框（旧版是灰色 Toggle，用户看不出那是可以点的「同意按钮」）
                    Section {
                        HStack(alignment: .top, spacing: 12) {
                            Button {
                                agreed.toggle()
                                if agreed {
                                    agreeHint = false
                                    if message.hasPrefix("请先勾选") { message = "" }
                                }
                            } label: {
                                Image(systemName: agreed ? "checkmark.square.fill" : "square")
                                    .font(.system(size: 25))
                                    .foregroundStyle(agreed ? MZH.primary : (agreeHint ? MZH.accent : MZH.muted))
                            }
                            .buttonStyle(.plain)
                            .accessibilityLabel(agreed ? "已同意，点击取消勾选" : "未同意，点击勾选")

                            VStack(alignment: .leading, spacing: 7) {
                                Text(agreed ? "已阅读并同意以下协议" : "请先勾选，表示已阅读并同意")
                                    .font(.subheadline.bold())
                                    .foregroundStyle(agreeHint ? MZH.accent : MZH.text)
                                HStack(spacing: 3) {
                                    Button("《用户协议》") { showAgreement = true }
                                    Text("与").foregroundStyle(MZH.muted)
                                    Button("《隐私政策》") { showPrivacy = true }
                                }
                                .font(.subheadline)
                                .foregroundStyle(MZH.primary)
                                .buttonStyle(.plain)
                            }
                        }
                        .padding(.vertical, 5)
                        .listRowBackground(MZH.surface)
                    }

                    Section {
                        Button { login() } label: {
                            HStack(spacing: 8) {
                                if busy { ProgressView().tint(MZH.onPrimary) }
                                Text(busy ? "登录中…" : "登录 / 注册")
                            }
                            .mzhPrimaryButton()
                        }
                        .buttonStyle(.plain)
                        .listRowBackground(Color.clear)
                        .listRowInsets(EdgeInsets())
                        .disabled(busy || !phoneOK)
                        .opacity(phoneOK ? 1 : 0.55)
                        if !message.isEmpty {
                            Text(message).font(.caption)
                                .foregroundStyle(message.hasPrefix("请先勾选") ? MZH.accent : MZH.danger)
                        }
                    } footer: {
                        Text(phoneOK ? "勾选协议后即可登录。" : "请输入正确的 11 位手机号。")
                    }
                } else {
                    Section("账号资料") {
                        HStack { Text("绑定手机号"); Spacer(); Text(masked).foregroundStyle(MZH.muted) }
                        HStack { Text("账号状态"); Spacer(); Text("正常").foregroundStyle(MZH.muted) }
                    }
                    Section {
                        Button("退出登录", role: .destructive) {
                            ticket = ""; masked = ""; showLogout = true
                        }
                    }
                }
                Section {
                    Text("核心播放 / 识档 / 调色 / 变速 / 取帧均可在不登录、不联网的情况下于设备本地完成。")
                        .font(.caption2).foregroundStyle(MZH.muted)
                }
            }
            .navigationTitle(ticket.isEmpty ? "登录" : "账号资料")
            .mzhListBackground()
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) { Button("完成") { dismiss() } }
            }
            .alert("已退出登录", isPresented: $showLogout) { Button("好") {} }
            .sheet(isPresented: $showAgreement) {
                NavigationStack {
                    LegalView(kind: .agreement)
                        .toolbar {
                            ToolbarItem(placement: .topBarTrailing) {
                                Button("关闭") { showAgreement = false }
                            }
                        }
                }
            }
            .sheet(isPresented: $showPrivacy) {
                NavigationStack {
                    LegalView(kind: .privacy)
                        .toolbar {
                            ToolbarItem(placement: .topBarTrailing) {
                                Button("关闭") { showPrivacy = false }
                            }
                        }
                }
            }
        }
    }

    private func login() {
        guard agreed else {
            agreeHint = true
            message = "请先勾选同意《用户协议》与《隐私政策》"
            return
        }
        guard phoneOK else { message = "请输入正确的 11 位手机号"; return }
        busy = true; message = ""
        let body: [String: Any] = [
            "phone": phone,
            "confirm_phone": phone,
            "agreed": true,
            "code": code,
            "idfv": UIDevice.current.identifierForVendor?.uuidString ?? "",
            "platform": "ios",
            "channel": UserDefaults.standard.string(forKey: "resolved_channel") ?? "organic"
        ]
        Task {
            do {
                let data = try await ChannelAttribution.shared.postJSON(
                    path: "/v1/login", body: body, timeout: 8)
                let res = try JSONDecoder().decode(LoginResponseReal.self, from: data)
                await MainActor.run {
                    busy = false
                    if res.ok {
                        masked = res.phone_masked ?? (String(phone.prefix(3)) + "****" + String(phone.suffix(4)))
                        ticket = res.ticket ?? "local-" + UUID().uuidString
                        dismiss()
                    } else {
                        message = "登录失败：\(res.error ?? "服务暂不可用")"
                    }
                }
            } catch {
                await MainActor.run {
                    busy = false
                    // 无网也不该把人卡在登录页：本地建立账号，核心功能本就离线可用
                    masked = String(phone.prefix(3)) + "****" + String(phone.suffix(4))
                    ticket = "offline-" + UUID().uuidString
                    dismiss()
                }
            }
        }
    }
}

// MARK: - 棱镜会员（报告：Apple 内购开发中，会员页不是白屏）

private struct MemberCenterView: View {
    @AppStorage("is_vip_unlocked") private var unlocked = false
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                VStack(alignment: .leading, spacing: 8) {
                    Label("棱镜会员", systemImage: "crown.fill").font(.title2.bold())
                    Text(unlocked ? "已开通 · 尊享全部权益" : "当前状态：未开通")
                        .font(.subheadline)
                    Text("权益：更高的云字幕生成频次 / 每日额度，用完后次日自动恢复。")
                        .font(.caption).foregroundStyle(MZH.muted)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(18)
                .background(LinearGradient(colors: [MZH.primary.opacity(0.14), MZH.accent.opacity(0.10)],
                                           startPoint: .topLeading, endPoint: .bottomTrailing),
                           in: RoundedRectangle(cornerRadius: 18))

                VStack(alignment: .leading, spacing: 10) {
                    Text("会员权益").font(.headline)
                    benefit("captions.bubble", "云字幕每日额度", "更高的生成频次，次日 0 点自动恢复")
                    benefit("waveform", "动能曲线多区间", "变速方案随视频保存，随时复用")
                    benefit("film", "帧廊胶片全部版式", "竖排 / 横排 / 九宫格长图与分享")
                    benefit("lock.fill", "本地核心功能", "播放、识档、调色、变速、取帧无需会员")
                }

                VStack(alignment: .leading, spacing: 6) {
                    Text("Apple 会员内购正在开发中").font(.subheadline.bold())
                    Text("目前版本未提供实际付费商品，协议与政策中也未列价格。内购上线后本页将开放购买入口；在此之前，所有本地能力均可免费使用。")
                        .font(.caption).foregroundStyle(MZH.muted)
                }
                .padding(14).background(MZH.surface2, in: RoundedRectangle(cornerRadius: 14))
            }
            .padding()
        }
        .navigationTitle("棱镜会员")
        .background(MZH.bg.ignoresSafeArea())
    }

    private func benefit(_ icon: String, _ title: String, _ desc: String) -> some View {
        HStack(alignment: .top, spacing: 12) {
            Image(systemName: icon).foregroundStyle(MZH.primary).frame(width: 24)
            VStack(alignment: .leading, spacing: 2) {
                Text(title).font(.subheadline)
                Text(desc).font(.caption2).foregroundStyle(MZH.muted)
            }
            Spacer()
        }
        .padding(12).background(MZH.surface2, in: RoundedRectangle(cornerRadius: 12))
    }
}

// MARK: - 设置

private struct SettingsView: View {
    @ObservedObject var store: MediaStore
    let toast: (String) -> Void
    @State private var showDelete = false
    @State private var clearConfirm = false

    var body: some View {
        List {
            Section("账号与安全") {
                Button { showDelete = true } label: {
                    Label("注销账号", systemImage: "person.crop.circle.badge.minus")
                        .foregroundStyle(.red)
                }
                Text("注销后账号信息删除或匿名化且不可恢复，本地相册原片不受影响。")
                    .font(.caption2).foregroundStyle(MZH.muted)
            }
            Section("帮助") {
                NavigationLink { FeedbackCenterView() } label: {
                    Label("帮助与反馈", systemImage: "questionmark.circle")
                }
                NavigationLink { LegalView(kind: .agreement) } label: {
                    Label("用户协议", systemImage: "doc.text")
                }
                NavigationLink { LegalView(kind: .privacy) } label: {
                    Label("隐私政策", systemImage: "hand.raised")
                }
            }
            Section("本地数据") {
                HStack {
                    Text("片库视频")
                    Spacer()
                    Text("\(store.videos.count) 部 · \(Format.bytes(store.totalBytes))")
                        .foregroundStyle(MZH.muted)
                }
                HStack {
                    Text("我的云作品")
                    Spacer()
                    Text("\(store.works.count) 项").foregroundStyle(MZH.muted)
                }
                Button(role: .destructive) { clearConfirm = true } label: {
                    Label("清除本地片库索引与副本", systemImage: "trash")
                }
                Text("只删除 App 内的索引与副本，系统相册里的原视频不会被删除。")
                    .font(.caption2).foregroundStyle(MZH.muted)
            }
            Section("关于") {
                HStack { Text("版本"); Spacer(); Text(appVersionText()).foregroundStyle(MZH.muted) }
                HStack { Text("客服邮箱"); Spacer(); Text("motionworld.support@126.com")
                    .font(.caption).foregroundStyle(MZH.muted) }
                HStack { Text("备案号"); Spacer(); Text("琼ICP备2023010196号-6A")
                    .font(.caption).foregroundStyle(MZH.muted) }
            }
        }
        .navigationTitle("设置")
        .mzhListBackground()
        .sheet(isPresented: $showDelete) {
            AccountDeletionView(channel: UserDefaults.standard
                .string(forKey: "resolved_channel") ?? "organic")
        }
        .alert("清除本地片库", isPresented: $clearConfirm) {
            Button("清除", role: .destructive) {
                store.remove(ids: Set(store.videos.map(\.id)))
                toast("本地片库已清空")
            }
            Button("取消", role: .cancel) {}
        } message: {
            Text("将删除 App 内的视频副本、索引与缩略图，不可恢复。")
        }
    }
}

// MARK: - 帮助与反馈已移到 FeedbackCenter.swift（对接 /site 后台用户反馈中心真实接口）

// MARK: - 法务文档（报告 2.4 / 8：协议与隐私都要有可读正文）

enum LegalKind { case agreement, privacy }

private struct LegalView: View {
    let kind: LegalKind
    @State private var expanded = false

    private var title: String { kind == .agreement ? "用户协议" : "隐私政策" }
    private var version: String { kind == .agreement ? "V1.1 · 2026-10-30" : "V1.0 · 2026-01-20" }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                Text(title).font(.title2.bold())
                Text(version).font(.caption).foregroundStyle(MZH.muted)
                ForEach(Array(sections.enumerated()), id: \.offset) { _, s in
                    VStack(alignment: .leading, spacing: 6) {
                        Text(s.0).font(.headline)
                        Text(s.1).font(.subheadline).foregroundStyle(MZH.muted)
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(12)
                    .background(MZH.surface2, in: RoundedRectangle(cornerRadius: 12))
                }
                if expanded {
                    Text(extra).font(.caption).foregroundStyle(MZH.muted)
                }
                Button(expanded ? "收起完整条款" : "展开完整条款") { expanded.toggle() }
                    .buttonStyle(.bordered)
                Text("本页面为 App 内置版本；如需法律全文可联系 motionworld.support@126.com。")
                    .font(.caption2).foregroundStyle(MZH.muted)
            }
            .padding()
        }
        .navigationTitle(title)
        .background(MZH.bg.ignoresSafeArea())
    }

    private var sections: [(String, String)] {
        if kind == .agreement {
            return [
                ("一、服务说明", "本 App 为本地影像工具，提供视频导入与播放、场景归档、影调调色、分段变速、取帧拼图、字幕生成等能力。核心功能可在不登录、不联网的情况下于设备本地完成。"),
                ("二、账号", "手机号加验证码登录，一个手机号对应一个账号。不登录亦可使用全部本地功能。账号资料可在「我的」查看，注销路径在「设置 → 账号与安全」。"),
                ("三、用户内容与授权", "你导入的视频、生成的成片与字幕均归你所有。除你主动发起的云字幕任务外，我们不会上传你的视频文件。为完成本地处理，App 会在设备内建立索引与副本。"),
                ("四、收费与会员", "棱镜会员权益为更高的云字幕生成频次 / 每日额度。Apple 会员内购正在开发中，当前版本未提供实际付费商品，协议未列价格。"),
                ("五、免责与限制", "端侧场景识别结果仅供参考，可长按影片手动调整归类。播放效果受设备性能与素材编码影响。"),
                ("六、注销与终止", "注销后账号信息删除或匿名化且不可恢复，本地相册文件不受影响；也可通过客服邮箱申请人工注销。"),
                ("七、争议解决", "因本服务产生的争议，双方应友好协商；协商不成的，按运营方所在地法律处理。")
            ]
        }
        return [
            ("一、我们收集什么", "与你关联：用户内容（你主动上传的照片或视频）、标识符（用户 ID / 设备 ID）、使用数据（产品交互）。未与你关联：诊断数据（崩溃、性能）。不收集通讯录、精确位置、麦克风、摄像头。"),
            ("二、权限", "相册读取：在「导入视频」选相册时使用系统选择器；相册写入：保存到相册时申请；通知：首次开启推送时申请；手机号 / 验证码：仅登录时。"),
            ("三、本地处理", "播放、视界识档、光谱调色、动能曲线、帧廊胶片、媒体库索引均在本机完成，视频不出设备。云字幕任务使用本机语音识别，临时音频在任务结束即删除。"),
            ("四、存储与传输", "数据境内存储，TLS 传输；手机号加密落库；如启用云端临时存储，任务完成或最长 72 小时自动删除。不去标识化定向营销，不出售个人信息。"),
            ("五、第三方 SDK", "本复刻版本未接入第三方统计、推送或广告 SDK；系统框架（AVFoundation / CoreImage / Vision / Speech）均在本机运行。"),
            ("六、你的权利", "可查看、更正、删除账号信息，可在 App 内「设置 → 账号与安全 → 注销账号」完成注销，也可邮件联系行使隐私权利。"),
            ("七、未成年人", "主要面向成年用户，未满 14 周岁需监护人同意后使用。"),
            ("八、联系我们", "motionworld.support@126.com，工作日 9:30–18:30，1–2 工作日回复，最长 15 工作日。")
        ]
    }

    private var extra: String {
        kind == .agreement
        ? "版本更新、功能建议每月集中评审，采纳后在版本说明致谢。通知方式含应用内弹窗、公告栏、系统推送与绑定手机号。本 App 名称、界面与图标为独立设计，与任何第三方应用不存在关联。"
        : "我们采用最小必要原则：能在本机完成的绝不上传。若未来版本接入云端能力，会在本政策更新并重新征求同意。临时文件采用任务结束即删与 72 小时兜底清理双策略。"
    }
}
