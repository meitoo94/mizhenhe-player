<?php
/* @var $this \yii\web\View */
/* @var $content string */

use yii\helpers\Html;
use app\assets\AppAsset;

AppAsset::register($this);
$this->registerCsrfMetaTags();
$this->registerMetaTag(['charset' => Yii::$app->charset], 'charset');
// 手机端按设备宽度渲染（之前缺这条，iOS/安卓都按 980px 缩放，后台在手机上挤成一条缝）
$this->registerMetaTag(['name' => 'viewport', 'content' => 'width=device-width, initial-scale=1, viewport-fit=cover'], 'viewport');

$route = Yii::$app->controller->route;
$me = Yii::$app->user->identity;
$siteName = Yii::$app->params['siteName'] ?? '拾文阁';

// 按角色控制菜单可见性（超级管理员全开）
$can = function ($r) use ($me) {
    return $me !== null && $me->canAccessRoute($r);
};
$isSuper = $me !== null && $me->isSuperAdmin();
$displayName = $me !== null ? ($me->real_name !== '' ? $me->real_name : $me->username) : '';
$loginName = $me !== null ? (string)$me->username : '';
$roleName = $me !== null ? $me->getRoleName() : '';
$avatarText = $displayName !== '' ? mb_strtoupper(mb_substr($displayName, 0, 1)) : 'A';
?>
<?php $this->beginPage() ?>
<!DOCTYPE html>
<html lang="<?= Yii::$app->language ?>">
<head>
    <?php $this->head() ?>
    <title><?= Html::encode($this->title ?: $siteName . ' · 后台管理') ?></title>
</head>
<body class="admin-body">
<?php $this->beginBody() ?>
<div class="admin-shell">
    <header class="admin-top">
        <button type="button" class="admin-nav-btn" id="adminNavBtn" aria-label="打开菜单" onclick="swToggleNav()">
            <span></span><span></span><span></span>
        </button>
        <div class="admin-top-brand">📚 <?= Html::encode($siteName) ?>后台</div>

        <div class="admin-user" id="adminUser">
            <button type="button" class="admin-user-btn" onclick="swUserMenu(event)">
                <span class="admin-avatar"><?= Html::encode($avatarText) ?></span>
                <span class="admin-user-meta">
                    <b><?= Html::encode($displayName) ?></b>
                    <i><?= Html::encode($roleName) ?></i>
                </span>
                <span class="admin-user-arrow">▾</span>
            </button>
            <div class="admin-user-menu">
                <div class="au-head">
                    <b><?= Html::encode($displayName) ?></b>
                    <span><?= Html::encode($displayName !== $loginName ? $loginName . ' · ' : '') ?><?= Html::encode($roleName) ?></span>
                </div>
                <?= Html::a('🔑 修改密码', ['admin/manager/password']) ?>
                <div class="au-sep"></div>
                <?= Html::a('🚪 退出登录', ['/admin/auth/logout'], ['data-method' => 'post', 'class' => 'au-logout']) ?>
            </div>
        </div>
    </header>

    <!-- 手机端侧栏抽屉的遮罩，点一下收起菜单 -->
    <div class="admin-scrim" id="adminScrim" onclick="swCloseNav()"></div>

    <div class="admin-wrap">
        <aside class="admin-side">
            <?php /* 顶部登录账号：菜单整体下移，进后台第一眼就知道当前是谁在操作 */ ?>
            <div class="side-user">
                <span class="side-user-avatar"><?= Html::encode($avatarText) ?></span>
                <div class="side-user-info">
                    <b title="<?= Html::encode($loginName !== '' ? $loginName : $displayName) ?>"><?= Html::encode($displayName) ?></b>
                    <span class="side-user-sub"><?= Html::encode($roleName !== '' ? $roleName : '管理员') ?><?php if ($loginName !== '' && $loginName !== $displayName): ?> · <?= Html::encode($loginName) ?><?php endif; ?> · <?= Html::a('退出', ['/admin/auth/logout'], ['class' => 'side-user-out', 'data-method' => 'post', 'title' => '退出登录']) ?></span>
                </div>
            </div>
            <?php if ($can('admin/stat')): ?>
                <?= Html::a('📈 数据统计', ['admin/stat/index'], ['class' => (strpos($route, 'admin/stat') === 0 ? 'active' : '')]) ?>
            <?php endif; ?>

            <?php if ($can('admin/stat')): ?>
                <?php /* 百度投放控制台：外链直达，?kt= 令牌免去 Basic Auth 弹窗（nginx 命中后写 cookie） */ ?>
                <a href="/bd/?kt=ktcdaa8a68ad43078b1e28f600292a325a3e002a2a" target="_blank" rel="noopener" onclick="return swOpenBd(this)">🧭 进入百度管理后台</a>
            <?php endif; ?>

            <?php if ($can('admin/channel')): ?>
                <?php
                // 渠道管理 / 渠道展示 两个菜单都以 admin/channel 开头，这里显式区分，
                // 避免打开「渠道展示」时「渠道管理」也高亮
                $isChannelDisplay = strpos($route, 'admin/channel-display') === 0;
                $isChannelIndex = !$isChannelDisplay && strpos($route, 'admin/channel') === 0;
                ?>
                <?= Html::a('🎯 渠道管理', ['admin/channel/index'], ['class' => ($isChannelIndex ? 'active' : '')]) ?>
                <?= Html::a('🎨 渠道展示', ['admin/channel-display/index'], ['class' => ($isChannelDisplay ? 'active' : '')]) ?>
                <?php /* MZH MERGE BEGIN 米帧盒播放器入口（由 merge.py 插入，可回滚） */ ?>
                <?php if ($can('admin/mizhenhe/index')): ?>
                    <?= Html::a('🎬 米帧盒播放器', ['admin/mizhenhe/index'], ['class' => (strpos($route, 'admin/mizhenhe') === 0 ? 'active' : '')]) ?>
                <?php endif; ?>
                <?php /* MZH MERGE END */ ?>
            <?php endif; ?>

            <?php if ($can('admin/recharge')): ?>
                <?= Html::a('🧾 充值记录', ['admin/recharge/index'], ['class' => ($route === 'admin/recharge/index' || $route === 'admin/recharge/detail' ? 'active' : '')]) ?>
            <?php endif; ?>

            <?php if ($can('admin/book')): ?>
                <?php
                // 小说管理：书本列表 + TXT 导入合并成一个可折叠的分组菜单
                $bookImport = $route === 'admin/book/import';
                $bookOpen = strpos($route, 'admin/book') === 0;
                ?>
                <div class="side-group<?= $bookOpen ? ' open' : '' ?>" id="sideBook" data-force="<?= $bookOpen ? '1' : '0' ?>">
                    <button type="button" class="side-group-t" onclick="swToggleSide('sideBook')">
                        <span>📖 小说管理</span>
                        <span class="side-arrow">▾</span>
                    </button>
                    <div class="side-sub">
                        <?= Html::a('📚 书本管理', ['admin/book/index'], ['class' => (!$bookImport && $bookOpen ? 'active' : '')]) ?>
                        <?= Html::a('➕ 导入新书', ['admin/book/import'], ['class' => ($bookImport ? 'active' : '')]) ?>
                    </div>
                </div>
            <?php endif; ?>

            <?php if ($can('admin/browser')): ?>
                <?= Html::a('🌐 浏览器管理', ['admin/browser/index'], ['class' => (strpos($route, 'admin/browser') === 0 ? 'active' : '')]) ?>
            <?php endif; ?>

            <?php if ($can('admin/user')): ?>
                <?= Html::a('👥 用户管理', ['admin/user/index'], ['class' => (strpos($route, 'admin/user') === 0 ? 'active' : '')]) ?>
            <?php endif; ?>

            <?php if ($can('admin/pack') || $can('admin/pay')): ?>
                <?php
                // 支付相关：套餐 + 支付通道合并成一个可折叠的分组菜单
                $payOpen = strpos($route, 'admin/pack') === 0 || strpos($route, 'admin/pay') === 0
                    || strpos($route, 'admin/vip') === 0 || strpos($route, 'admin/whitelist') === 0;
                ?>
                <div class="side-group<?= $payOpen ? ' open' : '' ?>" id="sidePay" data-force="<?= $payOpen ? '1' : '0' ?>">
                    <button type="button" class="side-group-t" onclick="swToggleSide('sidePay')">
                        <span>💳 支付相关管理</span>
                        <span class="side-arrow">▾</span>
                    </button>
                    <div class="side-sub">
                        <?= Html::a('💰 充值套餐配置', ['admin/pack/index'], ['class' => (strpos($route, 'admin/pack') === 0 ? 'active' : '')]) ?>
                        <?php
                        // VIP 套餐拆成三套：小说 / 浏览器 / 米帧盒播放器（独立档位、独立权益）
                        $vipScope = Yii::$app->request->get('scope', 'novel');
                        $vipActive = strpos($route, 'admin/vip') === 0;
                        ?>
                        <?= Html::a('📖 小说VIP套餐', ['admin/vip/index', 'scope' => 'novel'], ['class' => ($vipActive && $vipScope !== 'browser' && $vipScope !== 'mizhenhe' ? 'active' : '')]) ?>
                        <?= Html::a('🛡️ 浏览器VIP套餐', ['admin/vip/index', 'scope' => 'browser'], ['class' => ($vipActive && $vipScope === 'browser' ? 'active' : '')]) ?>
                        <?= Html::a('📦 米帧盒VIP套餐', ['admin/vip/index', 'scope' => 'mizhenhe'], ['class' => ($vipActive && $vipScope === 'mizhenhe' ? 'active' : '')]) ?>
                        <?= Html::a('💳 支付配置', ['admin/pay/index'], ['class' => (strpos($route, 'admin/pay') === 0 ? 'active' : '')]) ?>
                        <?= Html::a('🧪 支付白名单', ['admin/whitelist/index'], ['class' => (strpos($route, 'admin/whitelist') === 0 ? 'active' : '')]) ?>
                    </div>
                </div>
            <?php endif; ?>

            <?php if ($isSuper): ?>
            <?= Html::a('🧑‍💼 管理员管理', ['admin/manager/index'], ['class' => (strpos($route, 'admin/manager') === 0 && $route !== 'admin/manager/password' ? 'active' : '')]) ?>
            <?= Html::a('📦 包名配置', ['admin/package/index'], ['class' => (strpos($route, 'admin/package') === 0 ? 'active' : '')]) ?>
            <?php endif; ?>

            <?php if ($can('admin/cover-tpl')): ?>
                <?= Html::a('🖼️ 封面模板', ['admin/cover-tpl/index'], ['class' => (strpos($route, 'admin/cover-tpl') === 0 ? 'active' : '')]) ?>
            <?php endif; ?>

            <?php if ($can('admin/conv') || $can('admin/deduct')): ?>
                <?php
                // 「回传管理」分组：推广账号设置 + 转化回传配置 + 回传记录 + 推广扣量
                $isConvAccounts = strpos($route, 'admin/conv/account') === 0;
                $isConvRecords = strpos($route, 'admin/conv/records') === 0;
                $isDeduct = strpos($route, 'admin/deduct') === 0;
                $isConvIndex = !$isConvRecords && !$isConvAccounts && strpos($route, 'admin/conv') === 0;
                $convOpen = $isConvIndex || $isConvRecords || $isConvAccounts || $isDeduct;
                ?>
                <div class="side-group<?= $convOpen ? ' open' : '' ?>" id="sideConv" data-force="<?= $convOpen ? '1' : '0' ?>">
                    <button type="button" class="side-group-t" onclick="swToggleSide('sideConv')">
                        <span>🔗 回传管理</span>
                        <span class="side-arrow">▾</span>
                    </button>
                    <div class="side-sub">
                        <?= Html::a('👤 推广账号设置', ['admin/conv/accounts'], ['class' => ($isConvAccounts ? 'active' : '')]) ?>
                        <?= Html::a('⚙️ 百度转化回传', ['admin/conv/index'], ['class' => ($isConvIndex ? 'active' : '')]) ?>
                        <?= Html::a('📋 回传记录', ['admin/conv/records'], ['class' => ($isConvRecords ? 'active' : '')]) ?>
                        <?php if ($can('admin/deduct')): ?>
                            <?= Html::a('🎯 推广扣量', ['admin/deduct/index'], ['class' => ($isDeduct ? 'active' : '')]) ?>
                        <?php endif; ?>
                    </div>
                </div>
            <?php endif; ?>

            <?php if ($isSuper): ?>
            <?= Html::a('📮 用户反馈中心', ['admin/feedback/index'], ['class' => (strpos($route, 'admin/feedback') === 0 ? 'active' : '')]) ?>
            <?= Html::a('⚙️ 系统设置', ['admin/setting/index'], ['class' => (strpos($route, 'admin/setting') === 0 ? 'active' : '')]) ?>
            <?php endif; ?>
        </aside>
        <main class="admin-main">
            <?php if (Yii::$app->session->hasFlash('success')): ?>
                <div class="flash flash-success"><?= Html::encode(Yii::$app->session->getFlash('success')) ?></div>
            <?php endif; ?>
            <?php if (Yii::$app->session->hasFlash('error')): ?>
                <div class="flash flash-error"><?= Html::encode(Yii::$app->session->getFlash('error')) ?></div>
            <?php endif; ?>
            <?= $content ?>
        </main>
    </div>
</div>
<?php $this->endBody() ?>
<script>
/* 手机端侧栏抽屉 */
function swToggleNav() {
    var shell = document.querySelector('.admin-shell');
    if (shell) shell.classList.toggle('nav-open');
}
function swCloseNav() {
    var shell = document.querySelector('.admin-shell');
    if (shell) shell.classList.remove('nav-open');
}
(function () {
    var links = document.querySelectorAll('.admin-side a');
    for (var i = 0; i < links.length; i++) {
        links[i].addEventListener('click', swCloseNav);
    }
    var lastW = window.innerWidth;
    window.addEventListener('resize', function () {
        if (window.innerWidth > 900 && lastW <= 900) swCloseNav();
        lastW = window.innerWidth;
    });
})();

// 打开百度投放控制台：优先新窗口（两个后台并存），被浏览器拦了才退回当前页
function swOpenBd(a) {
    var url = a.getAttribute('href');
    var w = null;
    try { w = window.open(url, '_blank'); } catch (e) { w = null; }
    if (w && !w.closed) {
        try { w.opener = null; } catch (e) {}
        return false;   // 已开新窗口，当前页保持不动
    }
    return true;        // 被拦截 → 走默认跳转，控制台里有「返回心缘后台」
}
function swToggleSide(id) {
    var g = document.getElementById(id);
    if (!g) return;
    var open = g.classList.toggle('open');
    g.setAttribute('data-force', open ? '1' : '0');
    try { localStorage.setItem('sw_' + id, open ? '1' : '0'); } catch (e) {}
}
function swUserMenu(e) {
    if (e) e.stopPropagation();
    var box = document.getElementById('adminUser');
    if (box) box.classList.toggle('open');
}
document.addEventListener('click', function (e) {
    var box = document.getElementById('adminUser');
    if (box && box.classList.contains('open') && !box.contains(e.target)) box.classList.remove('open');
});
(function () {
    var groups = document.querySelectorAll('.side-group');
    for (var i = 0; i < groups.length; i++) {
        var g = groups[i];
        if (g.getAttribute('data-force') === '1' || !g.id) continue;
        try {
            if (localStorage.getItem('sw_' + g.id) === '1') g.classList.add('open');
        } catch (e) {}
    }
})();
</script>
</body>
</html>
<?php $this->endPage() ?>
