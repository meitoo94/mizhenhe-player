import SwiftUI
import AVFoundation
import AVKit
import PhotosUI
import Speech
import UIKit
import UniformTypeIdentifiers

// MARK: - 四 Tab 主壳（研究报告 3：动界 / 万象 / 创研台 / 我的）

struct MainTabs: View {
    let config: NormalConfig
    @StateObject private var store = MediaStore.shared
    @State private var selection = 0
    @State private var playerVideo: LocalVideo?
    @State private var playerTool: Toolbox?
    @State private var categoryFilter: String?
    @State private var showPicker = false
    @State private var showFiles = false
    @State private var queue: [LocalVideo] = []
    @State private var toast = ""
    @State private var showToast = false

    var body: some View {
        TabView(selection: $selection) {
            HomeTabReal(store: store, categoryFilter: $categoryFilter,
                        openVideo: { v, t in open(v, t) },
                        openQueue: { queue = $0 },
                        importAction: { showPicker = true },
                        importFiles: { showFiles = true })
                .tabItem { Label(config.tabs[safe: 0] ?? "首页", systemImage: "play.rectangle") }
                .tag(0)
            LibraryTabReal(store: store, filter: $categoryFilter,
                           openVideo: { v, t in open(v, t) },
                           importAction: { showPicker = true },
                           importFiles: { showFiles = true })
                .tabItem { Label(config.tabs[safe: 1] ?? "媒体库", systemImage: "square.grid.2x2") }
                .tag(1)
            CraftTabReal(store: store, config: config,
                         openVideo: { v, t in open(v, t) }, toast: notify)
                .tabItem { Label(config.tabs[safe: 2] ?? "创作", systemImage: "wand.and.stars") }
                .tag(2)
            MineTabReal(store: store, toast: notify)
                .tabItem { Label(config.tabs[safe: 3] ?? "我的", systemImage: "person.crop.circle") }
                .tag(3)
        }
        .environmentObject(store)
        .sheet(item: $playerVideo) { v in
            PlayerScreen(video: v, initialTool: playerTool).environmentObject(store)
        }
        .sheet(isPresented: $showPicker) {
            VideoPicker { urls in
                let n = store.importFiles(urls)
                notify(n > 0 ? "已导入 \(n) 个视频并完成端侧归类" : "导入失败")
                selection = 1
            }
        }
        .fileImporter(isPresented: $showFiles, allowedContentTypes: [.movie, .video, .mpeg4Movie],
                      allowsMultipleSelection: true) { result in
            guard case .success(let urls) = result else { return }
            let n = store.importFiles(urls)
            notify(n > 0 ? "已导入 \(n) 个视频并完成端侧归类" : "导入失败")
            selection = 1
        }
        .fullScreenCover(isPresented: Binding(get: { !queue.isEmpty },
                                              set: { if !$0 { queue = [] } })) {
            QueuePlayerScreen(videos: queue, store: store)
        }
        .overlay(alignment: .top) {
            if showToast {
                Text(toast).font(.caption).padding(.horizontal, 14).padding(.vertical, 9)
                    .background(.ultraThinMaterial, in: Capsule())
                    .padding(.top, 12)
            }
        }
    }

    private func open(_ v: LocalVideo, _ t: Toolbox?) {
        playerTool = t
        playerVideo = v
    }

    private func notify(_ s: String) {
        toast = s; showToast = true
        DispatchQueue.main.asyncAfter(deadline: .now() + 2.4) { showToast = false }
    }
}

// MARK: - Tab 1 动界 · 首页

private struct HomeTabReal: View {
    @ObservedObject var store: MediaStore
    @Binding var categoryFilter: String?
    let openVideo: (LocalVideo, Toolbox?) -> Void
    let openQueue: ([LocalVideo]) -> Void
    let importAction: () -> Void
    let importFiles: () -> Void
    @State private var showImportMenu = false

    private var greeting: String {
        let h = Calendar.current.component(.hour, from: Date())
        if h < 6 { return "夜深了，来点轻松内容" }
        if h < 11 { return "早上好，来点轻松内容" }
        if h < 14 { return "午间好，来点轻松内容" }
        if h < 18 { return "下午好，来点轻松内容" }
        return "晚上好，来点轻松内容"
    }
    private var collections: Int {
        Set(store.videos.map(\.category)).filter { $0 != "其他" }.count
    }
    private var watching: Int {
        store.videos.filter { $0.lastPlayedAt != nil }.count
    }
    private var filtered: [LocalVideo] {
        guard let c = categoryFilter else { return store.videos }
        return store.videos.filter { $0.category == c }
    }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 18) {
                    VStack(alignment: .leading, spacing: 6) {
                        Text(greeting).font(.title2.bold())
                        Text("本地影像的动感玩法 · 不联网也能播、能调、能剪")
                            .font(.caption).foregroundStyle(.secondary)
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(18)
                    .background(LinearGradient(colors: [.blue.opacity(0.18), .purple.opacity(0.12)],
                                               startPoint: .topLeading, endPoint: .bottomTrailing),
                               in: RoundedRectangle(cornerRadius: 18))

                    HStack(spacing: 0) {
                        stat("\(store.videos.count)", "影片")
                        stat("\(collections)", "合集")
                        stat("\(watching)", "在看")
                    }
                    .padding(.vertical, 14)
                    .background(Color(.systemGray6), in: RoundedRectangle(cornerRadius: 16))

                    HStack {
                        Text("精选频道").font(.headline)
                        Spacer()
                        if categoryFilter != nil {
                            Button("清除筛选") { categoryFilter = nil }.font(.caption)
                        }
                    }
                    ScrollView(.horizontal, showsIndicators: false) {
                        HStack(spacing: 10) {
                            ForEach(MediaCategories.all, id: \.self) { c in
                                Button {
                                    categoryFilter = c
                                } label: {
                                    HStack(spacing: 6) {
                                        Image(systemName: icon(for: c))
                                        Text(c)
                                        Text("\(store.videos.filter { $0.category == c }.count)")
                                            .font(.caption2).foregroundStyle(.secondary)
                                    }
                                    .padding(.horizontal, 14).padding(.vertical, 10)
                                    .background(categoryFilter == c ? Color.blue.opacity(0.18) : Color(.systemGray6),
                                               in: Capsule())
                                }
                                .buttonStyle(.plain)
                            }
                        }
                    }

                    Text("今日放映").font(.headline)
                    if store.videos.isEmpty {
                        emptyCard("还没有可放映的影像", "导入后为你随机放一部")
                    } else {
                        Button {
                            if let v = store.videos.randomElement() { openVideo(v, nil) }
                        } label: {
                            HStack(spacing: 12) {
                                thumb(store.videos[0])
                                VStack(alignment: .leading, spacing: 4) {
                                    Text("随机放一部").font(.subheadline.bold())
                                    Text(store.videos[0].title).font(.caption).lineLimit(1)
                                    Text("本地片库共 \(store.videos.count) 部").font(.caption2)
                                        .foregroundStyle(.secondary)
                                }
                                Spacer()
                                Image(systemName: "play.circle.fill").font(.title2)
                            }
                            .padding(12).background(Color(.systemGray6), in: RoundedRectangle(cornerRadius: 14))
                        }
                        .buttonStyle(.plain)
                    }

                    Text("连看影单").font(.headline)
                    if filtered.isEmpty {
                        emptyCard("暂无影单", "导入视频后可一键连看同类影像")
                    } else {
                        VStack(spacing: 0) {
                            ForEach(Array(filtered.prefix(5))) { v in
                                Button { openVideo(v, nil) } label: {
                                    HStack(spacing: 10) {
                                        thumb(v).frame(width: 62, height: 42)
                                        VStack(alignment: .leading, spacing: 2) {
                                            Text(v.title).font(.subheadline).lineLimit(1)
                                            Text("\(v.category) · \(Format.duration(v.duration))")
                                                .font(.caption2).foregroundStyle(.secondary)
                                        }
                                        Spacer()
                                        Image(systemName: "chevron.right").font(.caption)
                                            .foregroundStyle(.secondary)
                                    }
                                    .padding(.vertical, 8)
                                }
                                .buttonStyle(.plain)
                                Divider()
                            }
                            Button {
                                openQueue(filtered)
                            } label: {
                                Label("一键连看（按影单顺序连续播放）", systemImage: "list.and.film")
                                    .frame(maxWidth: .infinity)
                            }
                            .buttonStyle(.borderedProminent).padding(.top, 10)
                        }
                        .padding(12).background(Color(.systemGray6), in: RoundedRectangle(cornerRadius: 14))
                    }

                    Button { showImportMenu = true } label: {
                        Label("导入视频", systemImage: "plus.circle.fill").frame(maxWidth: .infinity)
                    }
                    .buttonStyle(.borderedProminent)
                    .confirmationDialog("从哪里导入", isPresented: $showImportMenu) {
                        Button("从相册选择") { importAction() }
                        Button("从文件 App 选择") { importFiles() }
                        Button("取消", role: .cancel) {}
                    }
                    Text("导入只在本机建立索引，不上传服务器。")
                        .font(.caption2).foregroundStyle(.secondary)
                }
                .padding()
            }
            .navigationTitle("动界")
        }
    }

    private func stat(_ v: String, _ k: String) -> some View {
        VStack(spacing: 2) {
            Text(v).font(.title2.bold().monospacedDigit())
            Text(k).font(.caption).foregroundStyle(.secondary)
        }.frame(maxWidth: .infinity)
    }

    private func emptyCard(_ title: String, _ sub: String) -> some View {
        VStack(spacing: 8) {
            Image(systemName: "film.stack").font(.title).foregroundStyle(.secondary)
            Text(title).font(.subheadline.bold())
            Text(sub).font(.caption).foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity).padding(.vertical, 26)
        .background(Color(.systemGray6), in: RoundedRectangle(cornerRadius: 14))
    }

    private func icon(for c: String) -> String {
        switch c {
        case "人物": return "person.2"
        case "风景": return "mountain.2"
        case "宠物": return "pawprint"
        case "美食": return "fork.knife"
        default: return "questionmark.folder"
        }
    }

    @ViewBuilder
    private func thumb(_ v: LocalVideo) -> some View {
        if let u = store.thumbURL(for: v), let img = UIImage(contentsOfFile: u.path) {
            Image(uiImage: img).resizable().scaledToFill()
                .frame(width: 72, height: 50).clipped().cornerRadius(8)
        } else {
            Image(systemName: "film").frame(width: 72, height: 50)
                .background(.gray.opacity(0.2), in: RoundedRectangle(cornerRadius: 8))
        }
    }
}

// MARK: - Tab 2 万象 · 本地媒体库

private struct LibraryTabReal: View {
    @ObservedObject var store: MediaStore
    @Binding var filter: String?
    let openVideo: (LocalVideo, Toolbox?) -> Void
    let importAction: () -> Void
    let importFiles: () -> Void
    @State private var query = ""
    @State private var grid = true
    @State private var multiSelect = false
    @State private var picked: Set<String> = []
    @State private var renaming: LocalVideo?
    @State private var newTitle = ""
    @State private var showImportMenu = false
    @State private var showDeleteConfirm = false

    private var list: [LocalVideo] {
        var out = store.videos
        if let f = filter { out = out.filter { $0.category == f } }
        if !query.isEmpty { out = out.filter { $0.title.localizedCaseInsensitiveContains(query) } }
        return out
    }

    var body: some View {
        NavigationStack {
            VStack(spacing: 10) {
                HStack(spacing: 0) {
                    overview("\(store.videos.count)", "视频数")
                    overview(Format.duration(store.totalDuration), "总时长")
                    overview(Format.bytes(store.totalBytes), "占用空间")
                }
                .padding(.vertical, 10)
                .background(Color(.systemGray6), in: RoundedRectangle(cornerRadius: 12))
                .padding(.horizontal)

                HStack {
                    Image(systemName: "magnifyingglass").foregroundStyle(.secondary)
                    TextField("搜索视频标题", text: $query)
                    if !query.isEmpty {
                        Button { query = "" } label: { Image(systemName: "xmark.circle.fill") }
                            .foregroundStyle(.secondary)
                    }
                }
                .padding(10).background(Color(.systemGray6), in: RoundedRectangle(cornerRadius: 12))
                .padding(.horizontal)

                HStack {
                    Picker("", selection: $grid) {
                        Text("网格").tag(true)
                        Text("时间线").tag(false)
                    }
                    .pickerStyle(.segmented).frame(width: 170)
                    Spacer()
                    Button(multiSelect ? "完成" : "多选") {
                        multiSelect.toggle()
                        if !multiSelect { picked = [] }
                    }.font(.caption)
                    if filter != nil {
                        Button("清除归类") { filter = nil }.font(.caption)
                    }
                }
                .padding(.horizontal)

                if multiSelect && !picked.isEmpty {
                    HStack {
                        Text("已选 \(picked.count) 项").font(.caption)
                        Spacer()
                        Button(role: .destructive) { showDeleteConfirm = true } label: {
                            Label("删除", systemImage: "trash").font(.caption)
                        }
                    }.padding(.horizontal)
                }

                if list.isEmpty {
                    Spacer()
                    VStack(spacing: 10) {
                        Image(systemName: "square.grid.3x3.topleft.filled")
                            .font(.system(size: 42)).foregroundStyle(.secondary)
                        Text("万象库还是空的").font(.headline)
                        Text("点右上角 + 从相册或文件导入视频").font(.caption)
                            .foregroundStyle(.secondary)
                    }
                    Spacer()
                } else if grid {
                    ScrollView {
                        let cols = [GridItem(.adaptive(minimum: 108), spacing: 10)]
                        LazyVGrid(columns: cols, spacing: 12) {
                            ForEach(list) { v in
                                cell(v)
                            }
                        }
                        .padding(.horizontal)
                    }
                } else {
                    List {
                        ForEach(list) { v in
                            HStack(spacing: 10) {
                                thumb(v).frame(width: 78, height: 52)
                                VStack(alignment: .leading, spacing: 3) {
                                    Text(v.title).font(.subheadline).lineLimit(1)
                                    Text("\(v.category) · \(Format.duration(v.duration)) · \(Format.bytes(v.bytes))")
                                        .font(.caption2).foregroundStyle(.secondary)
                                }
                                Spacer()
                                if multiSelect { check(v) }
                            }
                            .contentShape(Rectangle())
                            .onTapGesture { tap(v) }
                            .contextMenu {
                                Button { renaming = v; newTitle = v.title } label: {
                                    Label("改名", systemImage: "pencil")
                                }
                                ForEach(MediaCategories.all, id: \.self) { c in
                                    Button {
                                        store.setCategory(id: v.id, category: c)
                                    } label: { Label("调整归类：\(c)", systemImage: "folder") }
                                }
                                Button(role: .destructive) {
                                    store.remove(ids: [v.id])
                                } label: { Label("移出片库", systemImage: "trash") }
                            }
                        }
                    }
                    .listStyle(.plain)
                }
            }
            .navigationTitle("万象")
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button { showImportMenu = true } label: { Image(systemName: "plus") }
                }
            }
            .confirmationDialog("从哪里导入", isPresented: $showImportMenu) {
                Button("从相册选择") { importAction() }
                Button("从文件 App 选择") { importFiles() }
                Button("取消", role: .cancel) {}
            }
            .alert("改名", isPresented: Binding(get: { renaming != nil },
                                               set: { if !$0 { renaming = nil } })) {
                TextField("新名称", text: $newTitle)
                Button("保存") {
                    if let v = renaming, !newTitle.isEmpty { store.rename(id: v.id, title: newTitle) }
                    renaming = nil
                }
                Button("取消", role: .cancel) { renaming = nil }
            }
            .alert("移出片库", isPresented: $showDeleteConfirm) {
                Button("删除", role: .destructive) {
                    store.remove(ids: picked); picked = []; multiSelect = false
                }
                Button("取消", role: .cancel) {}
            } message: {
                Text("将删除本机副本与索引，系统相册原片不受影响。")
            }
        }
    }

    private func overview(_ v: String, _ k: String) -> some View {
        VStack(spacing: 2) {
            Text(v).font(.subheadline.bold().monospacedDigit()).lineLimit(1).minimumScaleFactor(0.7)
            Text(k).font(.caption2).foregroundStyle(.secondary)
        }.frame(maxWidth: .infinity)
    }

    @ViewBuilder
    private func cell(_ v: LocalVideo) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            ZStack(alignment: .topTrailing) {
                thumb(v).frame(maxWidth: .infinity).frame(height: 80)
                if multiSelect { check(v).padding(4) }
            }
            Text(v.title).font(.caption2).lineLimit(1)
            Text("\(v.category) · \(Format.duration(v.duration))")
                .font(.caption2).foregroundStyle(.secondary)
        }
        .contentShape(Rectangle())
        .onTapGesture { tap(v) }
        .contextMenu {
            Button { renaming = v; newTitle = v.title } label: { Label("改名", systemImage: "pencil") }
            ForEach(MediaCategories.all, id: \.self) { c in
                Button { store.setCategory(id: v.id, category: c) } label: {
                    Label("调整归类：\(c)", systemImage: "folder")
                }
            }
            Button(role: .destructive) { store.remove(ids: [v.id]) } label: {
                Label("移出片库", systemImage: "trash")
            }
        }
    }

    private func check(_ v: LocalVideo) -> some View {
        Image(systemName: picked.contains(v.id) ? "checkmark.circle.fill" : "circle")
            .foregroundStyle(picked.contains(v.id) ? .blue : .secondary)
    }

    private func tap(_ v: LocalVideo) {
        if multiSelect {
            if picked.contains(v.id) { picked.remove(v.id) } else { picked.insert(v.id) }
        } else {
            openVideo(v, nil)
        }
    }

    @ViewBuilder
    private func thumb(_ v: LocalVideo) -> some View {
        if let u = store.thumbURL(for: v), let img = UIImage(contentsOfFile: u.path) {
            Image(uiImage: img).resizable().scaledToFill().clipped().cornerRadius(8)
        } else {
            Image(systemName: "film").frame(maxWidth: .infinity)
                .background(.gray.opacity(0.18), in: RoundedRectangle(cornerRadius: 8))
        }
    }
}

// MARK: - 连看影单：AVQueuePlayer 连续播放

private struct QueuePlayerScreen: View {
    let videos: [LocalVideo]
    let store: MediaStore
    @Environment(\.dismiss) private var dismiss
    @State private var player: AVQueuePlayer?

    var body: some View {
        ZStack(alignment: .topLeading) {
            if let p = player {
                AVPlayerController(player: p).ignoresSafeArea()
            }
            Button { dismiss() } label: {
                Image(systemName: "xmark.circle.fill").font(.title).foregroundStyle(.white).padding()
            }
        }
        .onAppear {
            let items = videos.map { AVPlayerItem(url: store.url(for: $0)) }
            let q = AVQueuePlayer(items: items)
            q.play()
            player = q
        }
        .onDisappear { player?.pause() }
    }
}

private struct AVPlayerController: UIViewControllerRepresentable {
    let player: AVPlayer
    func makeUIViewController(context: Context) -> AVPlayerViewController {
        let vc = AVPlayerViewController()
        vc.player = player
        return vc
    }
    func updateUIViewController(_ uiViewController: AVPlayerViewController, context: Context) {}
}

// MARK: - Tab 3 创研台（报告 3：4 张卡是直达播放器工具箱的入口）

private struct CraftTabReal: View {
    @ObservedObject var store: MediaStore
    let config: NormalConfig
    let openVideo: (LocalVideo, Toolbox?) -> Void
    let toast: (String) -> Void
    @State private var choosing: Toolbox?
    @State private var showWorks = false
    @State private var showSubtitle = false

    private struct Feature {
        let name: String
        let desc: String
        let icon: String
        let tool: Toolbox
    }

    private var features: [Feature] {
        [
            Feature(name: "视界识档", desc: "端侧识别人物 / 风景 / 宠物 / 美食，自动归拢成智能合集，一键连看", icon: "square.stack.3d.up", tool: .frames),
            Feature(name: "光谱调色台", desc: "胶片 / 赛博 / 暮色预设，色温对比饱和颗粒微调，导出渲染成片", icon: "slider.horizontal.3", tool: .grade),
            Feature(name: "动能曲线", desc: "时间轴分段变速 0.25x–4x，预览 / 重置 / 方案随视频保存", icon: "waveform.path.ecg", tool: .tempo),
            Feature(name: "帧廊胶片", desc: "等距取 2–12 帧，竖排 / 横排 / 九宫格长图，自带片名时间码", icon: "film", tool: .frames)
        ]
    }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    Text("创作中心").font(.title2.bold())
                    Text("入口在播放器工具箱，选一部影片即可直达对应玩法")
                        .font(.caption).foregroundStyle(.secondary)

                    let cols = [GridItem(.flexible(), spacing: 12), GridItem(.flexible(), spacing: 12)]
                    LazyVGrid(columns: cols, spacing: 12) {
                        ForEach(features, id: \.name) { f in
                            Button {
                                if store.videos.isEmpty {
                                    toast("先到「万象」导入一段本地视频")
                                } else if f.name == "视界识档" {
                                    openVideo(store.videos[0], nil)
                                    toast("已按归类打开片库，长按影片可调整归类")
                                } else {
                                    choosing = f.tool
                                }
                            } label: {
                                VStack(alignment: .leading, spacing: 8) {
                                    Image(systemName: f.icon).font(.title2)
                                        .foregroundStyle(.blue)
                                    Text(f.name).font(.subheadline.bold())
                                    Text(f.desc).font(.caption2).foregroundStyle(.secondary)
                                        .lineLimit(4)
                                    Spacer(minLength: 0)
                                }
                                .frame(maxWidth: .infinity, minHeight: 150, alignment: .leading)
                                .padding(14)
                                .background(Color(.systemGray6), in: RoundedRectangle(cornerRadius: 16))
                            }
                            .buttonStyle(.plain)
                        }
                    }

                    VStack(alignment: .leading, spacing: 10) {
                        Label("云端工坊", systemImage: "cloud.fill").font(.headline)
                        Text("云字幕任务与成片管理。临时文件最长 72 小时自动清理。")
                            .font(.caption2).foregroundStyle(.secondary)
                        Button { showSubtitle = true } label: {
                            Label("云字幕工坊 · 本机语音识别生成字幕", systemImage: "captions.bubble")
                                .frame(maxWidth: .infinity)
                        }.buttonStyle(.borderedProminent)
                        Button { showWorks = true } label: {
                            Label("我的云作品（\(store.works.count)）", systemImage: "folder")
                                .frame(maxWidth: .infinity)
                        }.buttonStyle(.bordered)
                    }
                    .padding(14)
                    .background(Color(.systemGray6), in: RoundedRectangle(cornerRadius: 16))
                }
                .padding()
            }
            .navigationTitle("创研台")
            .sheet(isPresented: Binding(get: { choosing != nil },
                                        set: { if !$0 { choosing = nil } })) {
                PickVideoSheet(store: store, tool: choosing ?? .grade) { v, t in
                    choosing = nil
                    DispatchQueue.main.asyncAfter(deadline: .now() + 0.35) { openVideo(v, t) }
                }
            }
            .sheet(isPresented: $showWorks) { WorksView(store: store) }
            .sheet(isPresented: $showSubtitle) { SubtitleStudioView(store: store) }
        }
    }
}

private struct PickVideoSheet: View {
    @ObservedObject var store: MediaStore
    let tool: Toolbox
    let pick: (LocalVideo, Toolbox) -> Void
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            List(store.videos) { v in
                Button {
                    pick(v, tool)
                    dismiss()
                } label: {
                    HStack {
                        Image(systemName: "film")
                        VStack(alignment: .leading) {
                            Text(v.title).lineLimit(1)
                            Text("\(v.category) · \(Format.duration(v.duration))")
                                .font(.caption2).foregroundStyle(.secondary)
                        }
                        Spacer()
                        Text(tool.rawValue).font(.caption).foregroundStyle(.blue)
                    }
                }
            }
            .navigationTitle("选一部影片 · 打开\(tool.rawValue)")
            .toolbar { ToolbarItem(placement: .topBarTrailing) { Button("取消") { dismiss() } } }
        }
    }
}

// MARK: - 我的云作品

private struct WorksView: View {
    @ObservedObject var store: MediaStore
    @Environment(\.dismiss) private var dismiss
    @State private var previewURL: URL?
    @State private var share: [Any] = []
    @State private var showShare = false

    var body: some View {
        NavigationStack {
            Group {
                if store.works.isEmpty {
                    VStack(spacing: 12) {
                        Image(systemName: "tray").font(.largeTitle).foregroundStyle(.secondary)
                        Text("还没有作品").font(.headline)
                        Text("调色成片、变速成片、胶片长图、字幕文件都会存到这里")
                            .font(.caption).foregroundStyle(.secondary).multilineTextAlignment(.center)
                    }.padding()
                } else {
                    List {
                        ForEach(store.works) { w in
                            HStack {
                                Image(systemName: icon(w.kind)).foregroundStyle(.blue)
                                VStack(alignment: .leading) {
                                    Text(w.title).font(.subheadline).lineLimit(1)
                                    Text("\(w.kind) · \(Date(timeIntervalSince1970: w.createdAt).formatted(date: .abbreviated, time: .shortened))")
                                        .font(.caption2).foregroundStyle(.secondary)
                                }
                                Spacer()
                                Button { share = [store.workURL(for: w)]; showShare = true } label: {
                                    Image(systemName: "square.and.arrow.up")
                                }
                            }
                            .contentShape(Rectangle())
                            .onTapGesture {
                                if w.fileName.hasSuffix(".jpg") || w.fileName.hasSuffix(".png") {
                                    previewURL = store.workURL(for: w)
                                }
                            }
                        }
                        .onDelete { idx in
                            let ids = Set(idx.map { store.works[$0].id })
                            store.removeWorks(ids: ids)
                        }
                    }
                }
            }
            .navigationTitle("我的云作品")
            .toolbar { ToolbarItem(placement: .topBarTrailing) { Button("完成") { dismiss() } } }
            .sheet(isPresented: $showShare) { ShareSheet(items: share) }
            .sheet(isPresented: Binding(get: { previewURL != nil },
                                        set: { if !$0 { previewURL = nil } })) {
                if let u = previewURL, let img = UIImage(contentsOfFile: u.path) {
                    ZStack(alignment: .topTrailing) {
                        Color.black.ignoresSafeArea()
                        Image(uiImage: img).resizable().scaledToFit()
                        Button { previewURL = nil } label: {
                            Image(systemName: "xmark.circle.fill").font(.title).foregroundStyle(.white).padding()
                        }
                    }
                }
            }
        }
    }

    private func icon(_ kind: String) -> String {
        if kind.contains("图") { return "photo" }
        if kind.contains("字幕") { return "captions.bubble" }
        return "film"
    }
}

// MARK: - 云字幕工坊：本机语音识别 → SRT（报告 2.3 的任务形态）

private struct SubtitleStudioView: View {
    @ObservedObject var store: MediaStore
    @Environment(\.dismiss) private var dismiss
    @State private var target: LocalVideo?
    @State private var status = "待开始"
    @State private var running = false
    @State private var task: SFSpeechRecognitionTask?
    @State private var srt = ""
    @State private var showPick = false

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    Text("云字幕工坊").font(.title2.bold())
                    Text("视频不出设备：在本机提取音轨 → 系统语音识别 → 生成 SRT 字幕，临时音频任务结束即删除，云端副本最长 72 小时自动清理。")
                        .font(.caption).foregroundStyle(.secondary)

                    Button {
                        if store.videos.isEmpty { status = "请先导入视频" } else { showPick = true }
                    } label: {
                        Label(target?.title ?? "选择要生成字幕的视频", systemImage: "film")
                            .frame(maxWidth: .infinity)
                    }.buttonStyle(.bordered)

                    HStack(spacing: 12) {
                        Button { start() } label: {
                            Label(running ? "识别中…" : "开始识别", systemImage: "waveform")
                                .frame(maxWidth: .infinity)
                        }.buttonStyle(.borderedProminent).disabled(running || target == nil)
                        Button { task?.cancel(); running = false; status = "已终止" } label: {
                            Label("终止", systemImage: "stop.circle").frame(maxWidth: .infinity)
                        }.buttonStyle(.bordered).disabled(!running)
                    }

                    HStack {
                        Text(status).font(.subheadline)
                        Spacer()
                        if running { ProgressView() }
                    }
                    .padding(12).background(Color(.systemGray6), in: RoundedRectangle(cornerRadius: 12))

                    if !srt.isEmpty {
                        Text("字幕预览（SRT）").font(.headline)
                        ScrollView {
                            Text(srt).font(.caption.monospaced())
                                .frame(maxWidth: .infinity, alignment: .leading)
                        }
                        .frame(height: 180).padding(10)
                        .background(Color(.systemGray6), in: RoundedRectangle(cornerRadius: 10))
                        Button {
                            store.addWork(title: target?.title ?? "字幕", kind: "字幕文件",
                                          data: Data(srt.utf8), ext: "srt")
                            status = "字幕已存入「我的云作品」"
                        } label: { Label("保存到我的云作品", systemImage: "square.and.arrow.down").frame(maxWidth: .infinity) }
                            .buttonStyle(.borderedProminent)
                    }
                }
                .padding()
            }
            .navigationTitle("云字幕")
            .toolbar { ToolbarItem(placement: .topBarTrailing) { Button("完成") { dismiss() } } }
            .sheet(isPresented: $showPick) {
                NavigationStack {
                    List(store.videos) { v in
                        Button(v.title) { target = v; showPick = false }
                    }
                    .navigationTitle("选影片")
                }
            }
        }
    }

    private func start() {
        guard let v = target else { return }
        SFSpeechRecognizer.requestAuthorization { auth in
            DispatchQueue.main.async {
                guard auth == .authorized else { status = "需要允许语音识别权限"; return }
                guard let rec = SFSpeechRecognizer(locale: Locale(identifier: "zh-CN")), rec.isAvailable else {
                    status = "当前设备语音识别不可用"; return
                }
                running = true
                status = "正在提取音轨…"
                let src = MediaStore.shared.url(for: v)
                let audio = FileManager.default.temporaryDirectory
                    .appendingPathComponent(UUID().uuidString + ".m4a")
                guard let exp = AVAssetExportSession(asset: AVURLAsset(url: src),
                                                     presetName: AVAssetExportPresetAppleM4A) else {
                    running = false; status = "音轨提取失败"; return
                }
                exp.outputURL = audio
                exp.outputFileType = .m4a
                exp.exportAsynchronously {
                    DispatchQueue.main.async {
                        guard exp.status == .completed else {
                            running = false; status = "音轨提取失败"; return
                        }
                        status = "正在本机识别语音…"
                        let req = SFSpeechURLRecognitionRequest(url: audio)
                        req.shouldReportPartialResults = false
                        task = rec.recognitionTask(with: req) { result, error in
                            DispatchQueue.main.async {
                                try? FileManager.default.removeItem(at: audio)
                                running = false
                                if let r = result, r.isFinal {
                                    srt = makeSRT(r.bestTranscription, duration: v.duration)
                                    status = "识别完成，共 \(r.bestTranscription.segments.count) 段"
                                } else if error != nil {
                                    status = "识别未完成，可重试或换一段人声清晰的视频"
                                }
                            }
                        }
                    }
                }
            }
        }
    }

    private func makeSRT(_ t: SFTranscription, duration: Double) -> String {
        var out = ""
        for (i, s) in t.segments.enumerated() {
            let start = s.timestamp
            let end = min(duration, start + max(0.8, s.duration))
            out += "\(i + 1)\n\(srtTime(start)) --> \(srtTime(end))\n\(s.substring)\n\n"
        }
        if out.isEmpty && !t.formattedString.isEmpty {
            out = "1\n\(srtTime(0)) --> \(srtTime(max(2, duration)))\n\(t.formattedString)\n"
        }
        return out
    }

    private func srtTime(_ s: Double) -> String {
        let ms = Int(max(0, s) * 1000)
        return String(format: "%02d:%02d:%02d,%03d", ms / 3_600_000,
                      (ms / 60_000) % 60, (ms / 1000) % 60, ms % 1000)
    }
}
