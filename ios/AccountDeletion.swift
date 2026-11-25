import Foundation
import SwiftUI
import UIKit

/// App 内账号注销 —— App Store 审核指南 5.1.1(v)。
/// 支持注册 / 登录的 App 必须允许用户在 App 内发起账号删除，且要真正删除
/// 账号与关联个人数据；只做「停用账号」或跳到网页客服都不满足这条。
/// 服务端 POST /v1/account/delete 删除登录记录、会员权益与设备归因绑定，
/// 交易流水仅做去标识化保留，注销台账只记哈希，不存手机号明文。
@MainActor
enum AccountDeletion {
    private static let ticketKey = "login_ticket"
    private static let maskedKey = "login_phone_masked"

    static var ticket: String { UserDefaults.standard.string(forKey: ticketKey) ?? "" }
    static var maskedPhone: String { UserDefaults.standard.string(forKey: maskedKey) ?? "" }
    static var isLoggedIn: Bool { !ticket.isEmpty }

    static func saveLogin(ticket: String, maskedPhone: String) {
        UserDefaults.standard.set(ticket, forKey: ticketKey)
        UserDefaults.standard.set(maskedPhone, forKey: maskedKey)
    }

    static func clearLogin() {
        UserDefaults.standard.removeObject(forKey: ticketKey)
        UserDefaults.standard.removeObject(forKey: maskedKey)
    }

    struct Response: Decodable {
        let ok: Bool
        let receipt: String?
        let error: String?
    }

    enum DeletionError: LocalizedError {
        case loginRequired
        case server(String)
        var errorDescription: String? {
            switch self {
            case .loginRequired: return "请先登录后再注销账号"
            case .server(let m): return m
            }
        }
    }

    /// 返回注销回执编号。身份靠登录票据校验，匿名请求服务端会拒。
    static func deleteAccount(channel: String) async throws -> String {
        guard isLoggedIn else { throw DeletionError.loginRequired }
        let idfv = UIDevice.current.identifierForVendor?.uuidString ?? ""
        let body: [String: Any] = [
            "ticket": ticket, "idfv": idfv,
            "confirm": "DELETE", "channel": channel
        ]
        let data = try await ChannelAttribution.shared.postJSON(
            path: "/v1/account/delete", body: body, timeout: 8)
        let res = try JSONDecoder().decode(Response.self, from: data)
        guard res.ok else { throw DeletionError.server(res.error ?? "注销失败") }
        clearLogin()
        return res.receipt ?? ""
    }
}

/// 注销入口视图。三处可点：会员页、登录页、合规态账户 Tab；
/// 付费墙态不必先进内页，审核员在未付费状态下也能走完整个流程。
struct AccountDeletionView: View {
    let channel: String
    var onDeleted: () -> Void = {}
    @Environment(\.dismiss) private var dismiss
    @State private var word = ""
    @State private var errorText: String?
    @State private var busy = false
    @State private var receipt = ""

    var body: some View {
        NavigationStack {
            VStack(alignment: .leading, spacing: 16) {
                Text("注销账号").font(.title2.bold())
                Text("当前账号：" + (AccountDeletion.isLoggedIn
                     ? (AccountDeletion.maskedPhone.isEmpty ? "已登录" : AccountDeletion.maskedPhone)
                     : "未登录"))
                    .font(.caption).foregroundStyle(.secondary)

                VStack(alignment: .leading, spacing: 6) {
                    Text("注销后将永久删除：")
                    Text("· 登录记录与手机号关联")
                    Text("· 会员权益与云端作品索引")
                    Text("· 本设备的广告渠道归因绑定")
                    Text("依法需保留的匿名交易流水会去标识化，不含账号标识。此操作不可撤销。")
                }
                .font(.caption).foregroundStyle(.secondary)
                .padding(12)
                .background(.quaternary, in: RoundedRectangle(cornerRadius: 12))

                if AccountDeletion.isLoggedIn {
                    TextField("输入「注销」两个字确认", text: $word)
                        .textFieldStyle(.roundedBorder)
                } else {
                    Text("注销需要登录态校验，请先登录后再回来注销。")
                        .font(.caption).foregroundStyle(.orange)
                }
                if let errorText {
                    Text(errorText).font(.caption).foregroundStyle(.red)
                }
                if !receipt.isEmpty {
                    Text("账号已注销 · 回执 " + receipt).font(.caption).foregroundStyle(.green)
                }

                Button {
                    Task { await submit() }
                } label: {
                    HStack {
                        if busy { ProgressView().tint(.white) }
                        Text(AccountDeletion.isLoggedIn ? "确认注销" : "知道了")
                    }
                    .frame(maxWidth: .infinity).padding(.vertical, 13)
                    .background(Color(hex: "#35E0C8"), in: RoundedRectangle(cornerRadius: 24))
                    .foregroundStyle(Color(hex: "#06121A"))
                }
                .disabled(busy)
                Spacer()
            }
            .padding()
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("关闭") { dismiss() }
                }
            }
        }
    }

    @MainActor
    private func submit() async {
        if !AccountDeletion.isLoggedIn { dismiss(); return }
        guard word == "注销" else {
            errorText = "请输入「注销」两个字确认"
            return
        }
        errorText = nil
        busy = true
        defer { busy = false }
        do {
            receipt = try await AccountDeletion.deleteAccount(channel: channel)
            onDeleted()
        } catch {
            errorText = error.localizedDescription
        }
    }
}

// MARK: - Hex 颜色（本文件内用；其它视图各自有一份同实现）
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
